"""
Tests for golf_wd_scraper.py

Tests with mocked HTTP and mocked Anthropic client.
"""

import json
import os
import tempfile
import pytest
from unittest.mock import patch, MagicMock, PropertyMock

from golf.golf_wd_scraper import (
    GolfWDCache,
    GolfWDFetcher,
    GolfWDAnalyzer,
)


# ═══════════════════════════════════════════════════════════════
# GolfWDCache Tests
# ═══════════════════════════════════════════════════════════════

class TestGolfWDCache:
    """Tests for GolfWDCache with file-based storage."""

    def test_set_and_get(self):
        """Cache stores and retrieves correctly."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cache = GolfWDCache(cache_dir=tmpdir)
            data = {"status": "withdrawn", "player": "Tiger Woods"}
            cache.set("test_key", data)
            result = cache.get("test_key", max_age_minutes=60)
            assert result is not None
            assert result["status"] == "withdrawn"
            assert result["player"] == "Tiger Woods"

    def test_get_returns_none_for_missing(self):
        """Cache returns None for missing keys."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cache = GolfWDCache(cache_dir=tmpdir)
            result = cache.get("nonexistent_key", max_age_minutes=60)
            assert result is None

    def test_cache_respects_ttl(self):
        """Cache returns None for expired entries."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cache = GolfWDCache(cache_dir=tmpdir)
            data = {"status": "active"}
            cache.set("ttl_test", data)
            # Set max_age to 0 so it's immediately expired
            result = cache.get("ttl_test", max_age_minutes=0)
            assert result is None

    def test_cache_returns_within_ttl(self):
        """Cache returns data that is still within TTL."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cache = GolfWDCache(cache_dir=tmpdir)
            data = {"status": "active"}
            cache.set("fresh_test", data)
            result = cache.get("fresh_test", max_age_minutes=120)
            assert result is not None
            assert result["status"] == "active"

    def test_cache_handles_corrupt_json(self):
        """Cache returns None for corrupt JSON files."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cache = GolfWDCache(cache_dir=tmpdir)
            # Write corrupt data directly
            key_hash = cache._key("corrupt_test")
            path = os.path.join(tmpdir, f"{key_hash}.json")
            with open(path, 'w') as f:
                f.write("not valid json{{{")
            result = cache.get("corrupt_test", max_age_minutes=60)
            assert result is None

    def test_cache_key_is_deterministic(self):
        """Same identifier always produces same cache key."""
        cache = GolfWDCache(cache_dir=tempfile.mkdtemp())
        key1 = cache._key("Tiger Woods")
        key2 = cache._key("Tiger Woods")
        assert key1 == key2


# ═══════════════════════════════════════════════════════════════
# GolfWDFetcher Tests
# ═══════════════════════════════════════════════════════════════

class TestGolfWDFetcher:
    """Tests for GolfWDFetcher with mocked HTTP responses."""

    def test_fetch_espn_golf_news_parses_response(self):
        """fetch_espn_golf_news parses mocked ESPN response correctly."""
        mock_espn_response = {
            "articles": [
                {
                    "headline": "Tiger Woods withdraws from Masters with foot injury",
                    "description": "Tiger Woods has withdrawn from the 2026 Masters due to a plantar fascia injury.",
                    "published": "2026-04-01T12:00:00Z",
                },
                {
                    "headline": "Scottie Scheffler wins Arnold Palmer Invitational",
                    "description": "World number one cruises to victory at Bay Hill.",
                    "published": "2026-03-28T18:00:00Z",
                },
                {
                    "headline": "Rory McIlroy questionable with back injury",
                    "description": "McIlroy is day-to-day with a lingering back issue.",
                    "published": "2026-04-02T10:00:00Z",
                },
            ]
        }

        fetcher = GolfWDFetcher()
        with patch.object(fetcher.session, 'get') as mock_get:
            mock_resp = MagicMock()
            mock_resp.json.return_value = mock_espn_response
            mock_resp.raise_for_status = MagicMock()
            mock_get.return_value = mock_resp

            articles = fetcher.fetch_espn_golf_news(limit=50)

        # Should pick up Tiger WD and Rory injury, NOT Scheffler win
        assert len(articles) == 2
        assert any("Tiger Woods" in a['headline'] for a in articles)
        assert any("McIlroy" in a['headline'] for a in articles)
        assert all(a['source'] == 'espn_golf' for a in articles)

    def test_fetch_espn_golf_news_handles_error(self):
        """fetch_espn_golf_news returns empty list on HTTP error."""
        import requests

        fetcher = GolfWDFetcher()
        with patch.object(fetcher.session, 'get', side_effect=requests.RequestException("timeout")):
            articles = fetcher.fetch_espn_golf_news()
        assert articles == []

    def test_fetch_google_news_parses_rss(self):
        """fetch_google_news parses mocked RSS XML correctly."""
        from datetime import datetime, timezone
        from email.utils import format_datetime

        now = datetime.now(timezone.utc)
        pub_date = format_datetime(now)

        mock_rss = f"""<?xml version="1.0" encoding="UTF-8"?>
        <rss version="2.0">
        <channel>
            <item>
                <title>Jon Rahm withdraws from Players Championship</title>
                <pubDate>{pub_date}</pubDate>
                <source url="https://espn.com">ESPN</source>
            </item>
            <item>
                <title>PGA Tour field updates for upcoming event</title>
                <pubDate>{pub_date}</pubDate>
                <source url="https://golf.com">Golf.com</source>
            </item>
        </channel>
        </rss>"""

        fetcher = GolfWDFetcher()
        with patch.object(fetcher.session, 'get') as mock_get:
            mock_resp = MagicMock()
            mock_resp.text = mock_rss
            mock_resp.raise_for_status = MagicMock()
            mock_get.return_value = mock_resp

            results = fetcher.fetch_google_news("Jon Rahm", max_results=10)

        assert len(results) == 2
        assert results[0]['headline'] == "Jon Rahm withdraws from Players Championship"
        assert results[0]['source'] == "ESPN"

    def test_fetch_google_news_handles_error(self):
        """fetch_google_news returns empty list on HTTP error."""
        import requests

        fetcher = GolfWDFetcher()
        with patch.object(fetcher.session, 'get', side_effect=requests.RequestException("timeout")):
            results = fetcher.fetch_google_news("test query")
        assert results == []

    def test_fetch_player_news_combines_sources(self):
        """fetch_player_news combines ESPN and Google News results."""
        fetcher = GolfWDFetcher()

        espn_articles = [
            {
                'headline': 'Brooks Koepka withdraws with knee issue',
                'description': 'Koepka pulls out of tournament.',
                'published': '2026-04-01T12:00:00Z',
                'source': 'espn_golf',
            }
        ]
        google_articles = [
            {
                'headline': 'Koepka knee update: MRI results pending',
                'description': '',
                'published': '2026-04-02',
                'source': 'Golf Digest',
            }
        ]

        with patch.object(fetcher, 'fetch_espn_golf_news', return_value=espn_articles), \
             patch.object(fetcher, 'fetch_google_news', return_value=google_articles), \
             patch.object(fetcher.cache, 'get', return_value=None), \
             patch.object(fetcher.cache, 'set'):
            results = fetcher.fetch_player_news("Brooks Koepka")

        assert len(results) == 2

    def test_fetch_tournament_wd_news_deduplicates(self):
        """fetch_tournament_wd_news deduplicates articles by headline."""
        fetcher = GolfWDFetcher()

        wd_articles = [
            {'headline': 'Player X withdraws from The Masters', 'description': '', 'published': '', 'source': 'ESPN'},
        ]
        inj_articles = [
            {'headline': 'Player X withdraws from The Masters', 'description': '', 'published': '', 'source': 'ESPN'},
            {'headline': 'Player Y injury update for The Masters', 'description': '', 'published': '', 'source': 'Golf.com'},
        ]

        with patch.object(fetcher, 'fetch_google_news', side_effect=[wd_articles, inj_articles]), \
             patch.object(fetcher.cache, 'get', return_value=None), \
             patch.object(fetcher.cache, 'set'):
            results = fetcher.fetch_tournament_wd_news("The Masters")

        # Should have 2 unique articles, not 3
        assert len(results) == 2


# ═══════════════════════════════════════════════════════════════
# GolfWDAnalyzer Tests
# ═══════════════════════════════════════════════════════════════

class TestGolfWDAnalyzer:
    """Tests for GolfWDAnalyzer with mocked Claude client."""

    def _make_analyzer(self):
        """Create a GolfWDAnalyzer with mocked dependencies."""
        with patch.dict(os.environ, {'ANTHROPIC_API_KEY': 'test-key-123'}):
            with patch('golf.golf_wd_scraper.anthropic.Anthropic'):
                analyzer = GolfWDAnalyzer()
        return analyzer

    def test_missing_api_key_raises_error(self):
        """GolfWDAnalyzer raises ValueError when ANTHROPIC_API_KEY is missing."""
        with patch.dict(os.environ, {}, clear=True):
            # Also patch load_dotenv to prevent loading from .env file
            with patch('golf.golf_wd_scraper.load_dotenv'):
                with pytest.raises(ValueError, match="ANTHROPIC_API_KEY required"):
                    GolfWDAnalyzer(api_key=None)

    def test_analyze_player_status_returns_correct_structure(self):
        """analyze_player_status returns correct structure with mocked Claude response."""
        analyzer = self._make_analyzer()

        # Mock Claude response
        claude_response = json.dumps({
            "status": "withdrawn",
            "confidence": 0.95,
            "impact_pct": 100,
            "injury_type": "foot",
            "expected_return": "2 weeks",
            "details": "Withdrew with plantar fasciitis"
        })
        mock_content = MagicMock()
        mock_content.text = claude_response
        mock_message = MagicMock()
        mock_message.content = [mock_content]
        analyzer.client.messages.create = MagicMock(return_value=mock_message)

        # Mock fetcher to return some articles
        mock_articles = [
            {'headline': 'Tiger Woods withdraws', 'description': 'Foot injury',
             'published': '2026-04-01', 'source': 'ESPN'}
        ]
        with patch.object(analyzer.fetcher, 'fetch_player_news', return_value=mock_articles), \
             patch.object(analyzer.cache, 'get', return_value=None), \
             patch.object(analyzer.cache, 'set'):
            result = analyzer.analyze_player_status("Tiger Woods")

        assert result["player_name"] == "Tiger Woods"
        assert result["status"] == "withdrawn"
        assert result["confidence"] == 0.95
        assert result["impact_pct"] == 100
        assert result["injury_type"] == "foot"
        assert "source" in result

    def test_analyze_player_status_no_news_returns_active(self):
        """analyze_player_status returns active when no news found."""
        analyzer = self._make_analyzer()

        with patch.object(analyzer.fetcher, 'fetch_player_news', return_value=[]), \
             patch.object(analyzer.cache, 'get', return_value=None), \
             patch.object(analyzer.cache, 'set'):
            result = analyzer.analyze_player_status("Scottie Scheffler")

        assert result["status"] == "active"
        assert result["source"] == "no_news"

    def test_get_sim_adjustment_withdrawn_returns_zero(self):
        """get_sim_adjustment returns 0.0 for withdrawn players."""
        analyzer = self._make_analyzer()

        withdrawn_status = {
            "player_name": "Tiger Woods",
            "status": "withdrawn",
            "confidence": 0.95,
            "impact_pct": 100,
            "injury_type": "foot",
            "expected_return": "2 weeks",
            "details": "Withdrew",
            "source": "test",
        }
        with patch.object(analyzer, 'analyze_player_status', return_value=withdrawn_status):
            adj = analyzer.get_sim_adjustment("Tiger Woods")

        assert adj == 0.0

    def test_get_sim_adjustment_active_returns_one(self):
        """get_sim_adjustment returns 1.0 for active players."""
        analyzer = self._make_analyzer()

        active_status = {
            "player_name": "Scottie Scheffler",
            "status": "active",
            "confidence": 0.5,
            "impact_pct": 0,
            "injury_type": None,
            "expected_return": None,
            "details": "No news",
            "source": "no_news",
        }
        with patch.object(analyzer, 'analyze_player_status', return_value=active_status):
            adj = analyzer.get_sim_adjustment("Scottie Scheffler")

        assert adj == 1.0

    def test_get_sim_adjustment_questionable_returns_partial(self):
        """get_sim_adjustment returns partial value for questionable players."""
        analyzer = self._make_analyzer()

        questionable_status = {
            "player_name": "Rory McIlroy",
            "status": "questionable",
            "confidence": 0.7,
            "impact_pct": 40,
            "injury_type": "back",
            "expected_return": "game-time decision",
            "details": "Day-to-day with back issue",
            "source": "test",
        }
        with patch.object(analyzer, 'analyze_player_status', return_value=questionable_status):
            adj = analyzer.get_sim_adjustment("Rory McIlroy")

        assert 0.0 < adj < 1.0

    def test_get_sim_adjustment_injured_returns_partial(self):
        """get_sim_adjustment returns partial value for injured (but not WD) players."""
        analyzer = self._make_analyzer()

        injured_status = {
            "player_name": "Jon Rahm",
            "status": "injured",
            "confidence": 0.8,
            "impact_pct": 50,
            "injury_type": "wrist",
            "expected_return": "monitoring",
            "details": "Playing through wrist soreness",
            "source": "test",
        }
        with patch.object(analyzer, 'analyze_player_status', return_value=injured_status):
            adj = analyzer.get_sim_adjustment("Jon Rahm")

        assert 0.0 < adj < 1.0
        assert adj == 0.5  # 1.0 - (50/100) = 0.5

    def test_parse_json_response_handles_markdown_fences(self):
        """_parse_json_response extracts JSON from markdown code fences."""
        analyzer = self._make_analyzer()

        text = '```json\n{"status": "active", "confidence": 0.9}\n```'
        result = analyzer._parse_json_response(text)
        assert result["status"] == "active"

    def test_parse_json_response_handles_array(self):
        """_parse_json_response extracts JSON arrays."""
        analyzer = self._make_analyzer()

        text = '[{"player": "Tiger"}, {"player": "Rory"}]'
        result = analyzer._parse_json_response(text)
        assert isinstance(result, list)
        assert len(result) == 2

    def test_parse_json_response_handles_invalid(self):
        """_parse_json_response returns None for invalid JSON."""
        analyzer = self._make_analyzer()

        result = analyzer._parse_json_response("not json at all")
        assert result is None

    def test_analyze_tournament_field_returns_dict(self):
        """analyze_tournament_field returns dict keyed by player name."""
        analyzer = self._make_analyzer()

        field = ["Scottie Scheffler", "Rory McIlroy", "Jon Rahm"]

        # No tournament news found, so all should be active
        with patch.object(analyzer.fetcher, 'fetch_tournament_wd_news', return_value=[]), \
             patch.object(analyzer.cache, 'get', return_value=None), \
             patch.object(analyzer.cache, 'set'):
            result = analyzer.analyze_tournament_field("The Masters", field=field)

        assert len(result) == 3
        for name in field:
            assert name in result
            assert result[name]["status"] == "active"
