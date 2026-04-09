"""
Golf Withdrawal/Injury Intelligence Module.
Fetches PGA Tour withdrawal and injury news from ESPN Golf API + Google News RSS,
uses Claude (Anthropic) to extract structured WD/injury data, and provides
impact estimates for golf simulations.

Mirrors injury_scraper.py (root of repo, ~674 lines) which does the same for
NCAA basketball.
"""

import os
import json
import hashlib
import requests
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any
from bs4 import BeautifulSoup
from dotenv import load_dotenv
import anthropic

from golf.golf_elite_players import (
    get_player_info,
    get_players_by_tier,
    build_player_context,
    ELITE_PLAYERS,
)

load_dotenv()

# ESPN Golf API endpoints (structured JSON, no scraping needed)
ESPN_GOLF_NEWS_API = "https://site.api.espn.com/apis/site/v2/sports/golf/pga/news"
ESPN_GOLF_ATHLETES_API = "https://site.api.espn.com/apis/site/v2/sports/golf/pga/athletes"
GOOGLE_NEWS_RSS = "https://news.google.com/rss/search?hl=en-US&gl=US&ceid=US:en&q="


# ═══════════════════════════════════════════════════════════════
# Cache (mirrors InjuryCache from injury_scraper.py)
# ═══════════════════════════════════════════════════════════════

class GolfWDCache:
    """File-based cache for WD/injury data. Same pattern as InjuryCache."""

    def __init__(self, cache_dir: str = ".golf_wd_cache"):
        self.cache_dir = cache_dir
        os.makedirs(cache_dir, exist_ok=True)

    def _key(self, identifier: str) -> str:
        return hashlib.md5(identifier.encode()).hexdigest()

    def get(self, identifier: str, max_age_minutes: int = 60) -> Optional[Dict]:
        path = os.path.join(self.cache_dir, f"{self._key(identifier)}.json")
        if not os.path.exists(path):
            return None
        try:
            with open(path, 'r') as f:
                data = json.load(f)
            cached_at = datetime.fromisoformat(data.get('_cached_at', '2000-01-01'))
            if datetime.now() - cached_at > timedelta(minutes=max_age_minutes):
                return None
            return data
        except (json.JSONDecodeError, ValueError):
            return None

    def set(self, identifier: str, data: Dict):
        data['_cached_at'] = datetime.now().isoformat()
        os.makedirs(self.cache_dir, exist_ok=True)
        path = os.path.join(self.cache_dir, f"{self._key(identifier)}.json")
        with open(path, 'w') as f:
            json.dump(data, f)


# ═══════════════════════════════════════════════════════════════
# Fetcher (mirrors InjuryFetcher from injury_scraper.py)
# ═══════════════════════════════════════════════════════════════

class GolfWDFetcher:
    """Fetches golf withdrawal and injury news from ESPN Golf API and Google News RSS."""

    HEADERS = {
        'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
                       'AppleWebKit/537.36 (KHTML, like Gecko) '
                       'Chrome/120.0.0.0 Safari/537.36',
    }

    # Keywords indicating withdrawal or injury in golf context
    WD_KEYWORDS = [
        'withdraw', 'withdrawn', 'WD', 'pulls out', 'pulled out',
        'injur', 'out indefinitely', 'sidelined', 'surgery',
        'wrist', 'back', 'knee', 'ankle', 'shoulder', 'neck',
        'rib', 'hip', 'elbow', 'illness', 'virus',
        'questionable', 'day-to-day', 'miss ', 'misses ',
    ]

    def __init__(self):
        self.cache = GolfWDCache()
        self.session = requests.Session()
        self.session.headers.update(self.HEADERS)

    def fetch_espn_golf_news(self, limit: int = 50) -> List[Dict]:
        """Fetch latest golf news from ESPN API, filter for WD/injury relevance."""
        try:
            resp = self.session.get(
                ESPN_GOLF_NEWS_API, params={'limit': limit}, timeout=15
            )
            resp.raise_for_status()
            articles = resp.json().get('articles', [])

            wd_articles = []
            for a in articles:
                text = (a.get('headline', '') + ' ' + a.get('description', '')).lower()
                if any(kw.lower() in text for kw in self.WD_KEYWORDS):
                    wd_articles.append({
                        'headline': a.get('headline', ''),
                        'description': a.get('description', ''),
                        'published': a.get('published', ''),
                        'source': 'espn_golf',
                    })
            return wd_articles
        except requests.RequestException as e:
            print(f"[GolfWDFetcher] ESPN Golf news error: {e}")
            return []

    def fetch_google_news(self, query: str, max_results: int = 10) -> List[Dict]:
        """Fetch Google News RSS for golf-specific queries."""
        full_query = f"{query} PGA Tour withdrawal injury"
        try:
            resp = self.session.get(
                GOOGLE_NEWS_RSS + requests.utils.quote(full_query),
                timeout=15,
            )
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, 'xml')
            items = soup.find_all('item')

            results = []
            cutoff = datetime.now() - timedelta(days=14)
            for item in items[:max_results]:
                title = item.find('title').text if item.find('title') else ''
                pub_date = item.find('pubDate').text if item.find('pubDate') else ''
                source_tag = item.find('source')
                source = source_tag.text if source_tag else 'Google News'
                # Only include recent articles
                try:
                    from email.utils import parsedate_to_datetime
                    article_date = parsedate_to_datetime(pub_date)
                    if article_date.replace(tzinfo=None) < cutoff:
                        continue
                except Exception:
                    pass  # keep articles with unparseable dates
                results.append({
                    'headline': title,
                    'description': '',
                    'published': pub_date,
                    'source': source,
                })
            return results
        except requests.RequestException as e:
            print(f"[GolfWDFetcher] Google News error for '{query}': {e}")
            return []

    def fetch_player_news(self, player_name: str) -> List[Dict]:
        """Fetch WD/injury news for a specific player.

        Combines ESPN search filtering + Google News for the player name.
        Caches results for 60 minutes.
        """
        cache_key = f"player_news_{player_name}"
        cached = self.cache.get(cache_key, max_age_minutes=60)
        if cached and 'articles' in cached:
            return cached['articles']

        # ESPN: filter general golf news for player name mentions
        espn_articles = self.fetch_espn_golf_news(limit=50)
        name_lower = player_name.lower()
        player_espn = [
            a for a in espn_articles
            if name_lower in a.get('headline', '').lower()
            or name_lower in a.get('description', '').lower()
        ]

        # Google News: search for player-specific WD/injury news
        google_articles = self.fetch_google_news(f'"{player_name}"', max_results=8)

        combined = player_espn + google_articles
        self.cache.set(cache_key, {'articles': combined})
        return combined

    def fetch_tournament_wd_news(self, tournament_name: str) -> List[Dict]:
        """Fetch WD/injury news for a specific tournament.

        Searches for "{tournament_name} withdrawal" and
        "{tournament_name} injury" via Google News.
        """
        cache_key = f"tournament_wd_{tournament_name}"
        cached = self.cache.get(cache_key, max_age_minutes=60)
        if cached and 'articles' in cached:
            return cached['articles']

        wd_articles = self.fetch_google_news(
            f'"{tournament_name}" withdrawal', max_results=10
        )
        inj_articles = self.fetch_google_news(
            f'"{tournament_name}" injury', max_results=10
        )

        # Deduplicate by headline
        seen = set()
        combined = []
        for a in wd_articles + inj_articles:
            headline = a.get('headline', '')
            if headline not in seen:
                seen.add(headline)
                combined.append(a)

        self.cache.set(cache_key, {'articles': combined})
        return combined


# ═══════════════════════════════════════════════════════════════
# Claude Prompt Template
# ═══════════════════════════════════════════════════════════════

GOLF_WD_PROMPT = """You are a PGA Tour injury/withdrawal analyst. Given the following news articles about {player_name}, determine their current playing status.

Today's date is {today}.

Player context:
{player_context}

Recent news:
{news_text}

Respond in JSON format ONLY (no markdown, no explanation):
{{
    "status": "active" | "questionable" | "withdrawn" | "injured",
    "confidence": 0.0-1.0,
    "impact_pct": 0-100,
    "injury_type": "string or null",
    "expected_return": "string or null",
    "details": "brief explanation"
}}
"""

GOLF_TOURNAMENT_WD_PROMPT = """You are a PGA Tour injury/withdrawal analyst. Analyze the following news articles about the {tournament_name} and identify any players who have withdrawn or are injured.

Today's date is {today}.

News articles:
{news_text}

For each player mentioned with a withdrawal or injury, provide their status.
Respond with a JSON array ONLY (no markdown, no explanation). If no WDs/injuries found, return [].

Each entry:
{{
    "player_name": "Full Name",
    "status": "active" | "questionable" | "withdrawn" | "injured",
    "confidence": 0.0-1.0,
    "impact_pct": 0-100,
    "injury_type": "string or null",
    "expected_return": "string or null",
    "details": "brief explanation"
}}
"""


# ═══════════════════════════════════════════════════════════════
# Analyzer (mirrors InjuryAnalyzer from injury_scraper.py)
# ═══════════════════════════════════════════════════════════════

class GolfWDAnalyzer:
    """Uses Claude to analyze golf WD/injury news and estimate impact.

    Mirrors InjuryAnalyzer from injury_scraper.py.
    """

    def __init__(self, api_key: Optional[str] = None):
        load_dotenv()
        api_key = api_key or os.getenv('ANTHROPIC_API_KEY')
        if not api_key:
            raise ValueError("ANTHROPIC_API_KEY required for WD analysis")
        self.client = anthropic.Anthropic(api_key=api_key)
        self.fetcher = GolfWDFetcher()
        self.cache = GolfWDCache()

    def _ask_claude(self, prompt: str) -> str:
        """Send a prompt to Claude and return the text response."""
        response = self.client.messages.create(
            model="claude-3-haiku-20240307",
            max_tokens=4096,
            messages=[{"role": "user", "content": prompt}],
        )
        return response.content[0].text.strip()

    def _parse_json_response(self, text: str) -> Any:
        """Extract JSON object or array from Claude's response."""
        if '```' in text:
            text = text.split('```')[1]
            if text.startswith('json'):
                text = text[4:]
            text = text.strip()
        # Try object first
        obj_start = text.find('{')
        arr_start = text.find('[')
        if arr_start >= 0 and (obj_start < 0 or arr_start < obj_start):
            end = text.rfind(']')
            if end > arr_start:
                text = text[arr_start:end + 1]
        elif obj_start >= 0:
            end = text.rfind('}')
            if end > obj_start:
                text = text[obj_start:end + 1]
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return None

    def analyze_player_status(self, player_name: str) -> Dict[str, Any]:
        """Analyze a player's current health/WD status.

        Returns dict with:
        - status: "active" | "questionable" | "withdrawn" | "injured"
        - confidence: 0-1
        - impact_pct: 0-100 (how much this affects their expected performance)
        - details: str (explanation)
        - source: str (where the info came from)
        """
        cache_key = f"player_status_{player_name}"
        cached = self.cache.get(cache_key, max_age_minutes=60)
        if cached and 'status' in cached:
            return cached

        # 1. Fetch news for player
        articles = self.fetcher.fetch_player_news(player_name)

        # If no news, assume active
        if not articles:
            result = {
                "player_name": player_name,
                "status": "active",
                "confidence": 0.5,
                "impact_pct": 0,
                "injury_type": None,
                "expected_return": None,
                "details": "No recent WD/injury news found",
                "source": "no_news",
            }
            self.cache.set(cache_key, result)
            return result

        # 2. Build player context from elite players database
        player_context = build_player_context(player_name)

        # 3. Build news text block
        news_text = '\n'.join(
            f"[{a.get('published', '')[:16]}] {a['headline']} ({a.get('source', '')})"
            f" {a.get('description', '')[:150]}"
            for a in articles
        )[:5000]

        # 4. Send to Claude
        prompt = GOLF_WD_PROMPT.format(
            player_name=player_name,
            today=datetime.now().strftime('%Y-%m-%d'),
            player_context=player_context,
            news_text=news_text,
        )

        try:
            response_text = self._ask_claude(prompt)
            parsed = self._parse_json_response(response_text)
            if not isinstance(parsed, dict):
                parsed = {}
        except Exception as e:
            print(f"[GolfWDAnalyzer] Claude error for {player_name}: {e}")
            parsed = {}

        result = {
            "player_name": player_name,
            "status": parsed.get("status", "active"),
            "confidence": parsed.get("confidence", 0.5),
            "impact_pct": parsed.get("impact_pct", 0),
            "injury_type": parsed.get("injury_type"),
            "expected_return": parsed.get("expected_return"),
            "details": parsed.get("details", "Unable to determine status"),
            "source": "espn_golf+google_news+claude",
            "articles_analyzed": len(articles),
            "fetched_at": datetime.now().isoformat(),
        }
        self.cache.set(cache_key, result)
        return result

    def analyze_tournament_field(
        self,
        tournament_name: str,
        field: Optional[List[str]] = None,
    ) -> Dict[str, Dict[str, Any]]:
        """Analyze WD/injury status for entire tournament field.

        Returns dict keyed by player name with status dicts.
        """
        cache_key = f"tournament_field_{tournament_name}"
        cached = self.cache.get(cache_key, max_age_minutes=30)
        if cached and 'statuses' in cached:
            return cached['statuses']

        # 1. Fetch tournament-wide WD news
        tournament_articles = self.fetcher.fetch_tournament_wd_news(tournament_name)

        # 2. Determine which players to check
        if field is None:
            # Default: check top 50 elite players
            field = list(ELITE_PLAYERS.keys())[:50]

        # 3. Check which players have mentions in news
        news_text_combined = ' '.join(
            a.get('headline', '') + ' ' + a.get('description', '')
            for a in tournament_articles
        ).lower()

        statuses = {}
        for player_name in field:
            # Only call Claude for players with relevant news hits
            name_lower = player_name.lower()
            last_name = player_name.split()[-1].lower() if player_name.split() else ''
            if name_lower in news_text_combined or last_name in news_text_combined:
                statuses[player_name] = self.analyze_player_status(player_name)
            else:
                # No news = assume active
                statuses[player_name] = {
                    "player_name": player_name,
                    "status": "active",
                    "confidence": 0.5,
                    "impact_pct": 0,
                    "injury_type": None,
                    "expected_return": None,
                    "details": "No WD/injury news found",
                    "source": "no_mention",
                }

        self.cache.set(cache_key, {'statuses': statuses})
        return statuses

    def get_sim_adjustment(self, player_name: str) -> float:
        """Get simulation adjustment factor for a player.

        Returns float: 0.0 (withdrawn, skip) to 1.0 (fully healthy).
        Analogous to injury_adj in build_matchup_params().
        """
        status_data = self.analyze_player_status(player_name)
        status = status_data.get("status", "active")
        impact_pct = status_data.get("impact_pct", 0)

        if status == "withdrawn":
            return 0.0
        elif status == "injured":
            # Injured but not withdrawn: scale by impact
            return max(0.0, 1.0 - (impact_pct / 100.0))
        elif status == "questionable":
            # Questionable: moderate reduction
            return max(0.1, 1.0 - (impact_pct / 100.0) * 0.5)
        else:
            # Active or unknown
            return 1.0
