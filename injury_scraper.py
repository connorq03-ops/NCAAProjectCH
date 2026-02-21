"""
Injury Intelligence Module
Fetches NCAA basketball injury news from ESPN API + Google News RSS,
uses Claude (Anthropic) to extract structured injury data, and provides
impact estimates for game simulations.
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
from star_players import get_star_player, get_team_stars, build_star_context

load_dotenv()

# ESPN API endpoints (structured JSON, no scraping needed)
ESPN_NEWS_API = "https://site.api.espn.com/apis/site/v2/sports/basketball/mens-college-basketball/news"
GOOGLE_NEWS_RSS = "https://news.google.com/rss/search?hl=en-US&gl=US&ceid=US:en&q="


class InjuryCache:
    """Simple file-based cache to minimize API calls."""

    def __init__(self, cache_dir: str = ".injury_cache"):
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


class InjuryFetcher:
    """Fetches injury news from ESPN API and Google News RSS."""

    HEADERS = {
        'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
                       'AppleWebKit/537.36 (KHTML, like Gecko) '
                       'Chrome/120.0.0.0 Safari/537.36',
    }

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update(self.HEADERS)

    def fetch_espn_news(self, limit: int = 50) -> List[Dict]:
        """Fetch recent NCAA basketball news from ESPN API, filter for injuries."""
        try:
            resp = self.session.get(ESPN_NEWS_API, params={'limit': limit}, timeout=15)
            resp.raise_for_status()
            articles = resp.json().get('articles', [])
            injury_kws = ['injur', 'out for', 'out indefinitely', 'miss ', 'misses ',
                          'sidelined', 'questionable', 'day-to-day', 'surgery',
                          'sprain', 'torn', 'fracture', 'concussion', 'knee',
                          'ankle', 'shoulder', 'hamstring', 'ACL', 'MCL']
            injury_articles = []
            for a in articles:
                text = (a.get('headline', '') + ' ' + a.get('description', '')).lower()
                if any(kw.lower() in text for kw in injury_kws):
                    injury_articles.append({
                        'headline': a.get('headline', ''),
                        'description': a.get('description', ''),
                        'published': a.get('published', ''),
                        'source': 'espn'
                    })
            return injury_articles
        except requests.RequestException as e:
            print(f"[InjuryFetcher] ESPN news error: {e}")
            return []

    def fetch_google_news(self, team_name: str, max_results: int = 10) -> List[Dict]:
        """Fetch team-specific injury news from Google News RSS."""
        # Use quotes around team name to avoid partial matches (e.g. "Arkansas" matching "Kansas")
        query = f'"{team_name}" college basketball injury 2026'
        try:
            resp = self.session.get(
                GOOGLE_NEWS_RSS + requests.utils.quote(query),
                timeout=15
            )
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, 'xml')
            items = soup.find_all('item')
            results = []
            cutoff = datetime.now() - timedelta(days=45)
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
                    'source': source
                })
            return results
        except requests.RequestException as e:
            print(f"[InjuryFetcher] Google News error for {team_name}: {e}")
            return []

    def fetch_matchup_news(self, team1: str, team2: str) -> str:
        """Aggregate injury news for two teams into a text block for Claude."""
        espn_news = self.fetch_espn_news()
        t1_google = self.fetch_google_news(team1)
        t2_google = self.fetch_google_news(team2)

        lines = []
        lines.append(f"=== ESPN Recent Injury News ===")
        for a in espn_news:
            lines.append(f"[{a['published'][:10]}] {a['headline']}")
            if a['description']:
                lines.append(f"  {a['description'][:200]}")

        lines.append(f"\n=== {team1} Injury News (Google) ===")
        for a in t1_google:
            lines.append(f"[{a['published'][:16]}] {a['headline']} ({a['source']})")

        lines.append(f"\n=== {team2} Injury News (Google) ===")
        for a in t2_google:
            lines.append(f"[{a['published'][:16]}] {a['headline']} ({a['source']})")

        text = '\n'.join(lines)
        # Limit to ~6000 chars for Claude prompt
        return text[:6000] if len(text) > 6000 else text


class InjuryAnalyzer:
    """Uses Claude to extract structured injury data and estimate game impact."""

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or os.getenv('ANTHROPIC_API_KEY')
        if not self.api_key:
            raise ValueError("ANTHROPIC_API_KEY is required")
        self.client = anthropic.Anthropic(api_key=self.api_key)
        self.fetcher = InjuryFetcher()
        self.cache = InjuryCache()

    def _ask_claude(self, prompt: str) -> str:
        """Send a prompt to Claude and return the text response."""
        response = self.client.messages.create(
            model="claude-3-haiku-20240307",
            max_tokens=4096,
            messages=[{"role": "user", "content": prompt}]
        )
        return response.content[0].text.strip()

    def _parse_json_response(self, text: str) -> List[Dict]:
        """Extract JSON array from Claude's response."""
        if '```' in text:
            text = text.split('```')[1]
            if text.startswith('json'):
                text = text[4:]
            text = text.strip()
        # Try to find JSON array in text
        start = text.find('[')
        end = text.rfind(']')
        if start >= 0 and end > start:
            text = text[start:end + 1]
        try:
            result = json.loads(text)
            return result if isinstance(result, list) else []
        except json.JSONDecodeError:
            return []

    def get_all_injuries(self, force_refresh: bool = False) -> Dict[str, Any]:
        """Get all current NCAA basketball injuries from ESPN news."""
        cache_key = "all_injuries_news"
        if not force_refresh:
            cached = self.cache.get(cache_key, max_age_minutes=120)
            if cached and 'injuries' in cached:
                return cached

        espn_articles = self.fetcher.fetch_espn_news(limit=50)
        if not espn_articles:
            return {"injuries": [], "source": "espn_news", "error": "No injury news found"}

        news_text = '\n'.join(
            f"[{a['published'][:10]}] {a['headline']}. {a['description']}"
            for a in espn_articles
        )

        prompt = f"""Analyze these recent college basketball injury news headlines and extract current injury statuses.
For each injured player mentioned, provide:
- team: Team name (common name like "Duke", "North Carolina", "Tennessee")
- player: Full player name
- position: Position (PG/SG/SF/PF/C/G/F) — infer if not stated
- status: One of "Out", "Doubtful", "Questionable", "Probable", "Day-to-Day"
- injury: Brief injury type (e.g. "knee", "ankle sprain", "foot")
- is_starter: true/false — infer from context (leading scorer, key player = starter)
- impact_score: 1-10 (10=star player out, 7-9=key starter, 4-6=role player, 1-3=bench/probable)
- date_reported: Date from the article (YYYY-MM-DD)

Focus on players who are currently OUT or QUESTIONABLE. Ignore old resolved injuries.
Return ONLY a JSON array. If nothing found, return [].

NEWS HEADLINES:
{news_text}"""

        try:
            response_text = self._ask_claude(prompt)
            injuries = self._parse_json_response(response_text)
        except Exception as e:
            print(f"[InjuryAnalyzer] Claude error: {e}")
            injuries = []

        result = {
            "injuries": injuries,
            "source": "espn_news+claude",
            "articles_analyzed": len(espn_articles),
            "fetched_at": datetime.now().isoformat(),
            "count": len(injuries)
        }
        self.cache.set(cache_key, result)
        return result

    def get_team_injuries(self, team_name: str, force_refresh: bool = False) -> Dict[str, Any]:
        """Get injuries for a specific team using Google News + Claude."""
        cache_key = f"team_injuries_{team_name}"
        if not force_refresh:
            cached = self.cache.get(cache_key, max_age_minutes=120)
            if cached and 'injuries' in cached:
                return cached

        google_articles = self.fetcher.fetch_google_news(team_name, max_results=15)
        espn_articles = self.fetcher.fetch_espn_news(limit=50)
        # Filter ESPN for this team
        team_lower = team_name.lower()
        espn_filtered = [a for a in espn_articles
                         if team_lower in a.get('headline', '').lower()
                         or team_lower in a.get('description', '').lower()]

        all_articles = espn_filtered + google_articles
        if not all_articles:
            return {"injuries": [], "team": team_name, "source": "none", "fetched_at": datetime.now().isoformat()}

        news_text = '\n'.join(
            f"[{a.get('published', '')[:16]}] {a['headline']} ({a.get('source','')}) {a.get('description','')[:150]}"
            for a in all_articles
        )[:5000]

        # Build star player reference for this team
        star_context = ""
        team_stars = get_team_stars(team_name)
        if team_stars:
            star_context = f"\n\nKEY PLAYERS REFERENCE for {team_name} (use these impact scores):\n"
            for s in team_stars:
                star_context += f"  - {s['player']} ({s['position']}) — {s['tier'].upper()}, impact={s['impact']}/10 — {s['note']}\n"
            star_context += "If an injured player is listed above, USE the impact_score from this reference. If not listed, estimate 4-6 for rotation players, 1-3 for bench.\n"

        prompt = f"""Analyze these news articles about {team_name} basketball and extract CURRENT injury statuses.
Today's date is {datetime.now().strftime('%Y-%m-%d')}.

CRITICAL: Only include players who ACTUALLY PLAY FOR {team_name}. Do NOT include players from other teams even if they appear in the articles. Be careful with similar team names (e.g. "Kansas" vs "Arkansas", "Mississippi" vs "Mississippi State", "Indiana" vs "Indiana State"). Verify each player's team from the article context.
{star_context}
For each injured player on {team_name}, provide:
- team: "{team_name}"
- player: Full player name
- position: Position (PG/SG/SF/PF/C/G/F)
- status: One of "Out", "Doubtful", "Questionable", "Probable", "Day-to-Day", "Indefinite"
- injury: Brief injury type
- is_starter: true/false
- impact_score: 1-10 (USE the KEY PLAYERS REFERENCE above if the player is listed there)
- date_reported: Most recent report date (YYYY-MM-DD)

Only include players with ACTIVE injuries (not returned players). Use the most recent article for each player's status.
Return ONLY a JSON array. If no current injuries for {team_name}, return [].

NEWS:
{news_text}"""

        try:
            response_text = self._ask_claude(prompt)
            injuries = self._parse_json_response(response_text)
            # Post-processing: drop any injuries misattributed to wrong team
            injuries = [i for i in injuries if self._team_match(i.get('team', ''), team_name)]
            # Override impact scores with star player database
            injuries = self._apply_star_overrides(injuries)
        except Exception as e:
            print(f"[InjuryAnalyzer] Claude error for {team_name}: {e}")
            injuries = []

        result = {
            "injuries": injuries,
            "team": team_name,
            "source": "google_news+espn+claude",
            "articles_analyzed": len(all_articles),
            "fetched_at": datetime.now().isoformat()
        }
        self.cache.set(cache_key, result)
        return result

    def get_matchup_injuries(self, team1: str, team2: str) -> Dict[str, Any]:
        """Get injury impact analysis for a specific matchup."""
        cache_key = f"matchup_{team1}_vs_{team2}"
        cached = self.cache.get(cache_key, max_age_minutes=60)
        if cached and 'team1_injuries' in cached:
            return cached

        # Gather news for both teams
        news_text = self.fetcher.fetch_matchup_news(team1, team2)

        # Build star player reference for both teams
        star_ref = build_star_context(team1, team2)

        prompt = f"""Analyze these injury news articles for an upcoming game: {team1} vs {team2}.
Today's date is {datetime.now().strftime('%Y-%m-%d')}.

CRITICAL: Only include players who ACTUALLY PLAY FOR either {team1} or {team2}. Do NOT include players from other teams that happen to appear in the articles. Be very careful with similar team names (e.g. "Kansas" vs "Arkansas", "Mississippi" vs "Mississippi State", "Indiana" vs "Indiana State", "Michigan" vs "Michigan State"). Verify each player's team from the article context before including them.

KEY PLAYERS REFERENCE (use these impact scores when the player matches):
{star_ref}
If an injured player is listed in the reference above, you MUST use that impact_score. If not listed, estimate 4-6 for rotation players, 1-3 for bench.

For each player with a CURRENT injury affecting either {team1} or {team2}, provide:
- team: The team name (must be exactly "{team1}" or "{team2}")
- player: Full player name
- position: Position (PG/SG/SF/PF/C/G/F)
- status: One of "Out", "Doubtful", "Questionable", "Probable", "Day-to-Day", "Indefinite"
- injury: Brief injury type
- is_starter: true/false
- impact_score: 1-10 (USE the KEY PLAYERS REFERENCE above if the player is listed there)

Only include ACTIVE injuries — not players who have returned. Use most recent information.
Return ONLY a JSON array. If both teams are healthy, return [].

NEWS ARTICLES:
{news_text}"""

        try:
            response_text = self._ask_claude(prompt)
            all_injuries = self._parse_json_response(response_text)
            # Post-processing: override impact scores with star player database
            all_injuries = self._apply_star_overrides(all_injuries)
        except Exception as e:
            print(f"[InjuryAnalyzer] Claude matchup error: {e}")
            all_injuries = []

        t1_injuries = [i for i in all_injuries if self._team_match(i.get('team', ''), team1)]
        t2_injuries = [i for i in all_injuries if self._team_match(i.get('team', ''), team2)]

        t1_impact = self._compute_team_impact(t1_injuries)
        t2_impact = self._compute_team_impact(t2_injuries)

        result = {
            "team1": team1,
            "team2": team2,
            "team1_injuries": t1_injuries,
            "team2_injuries": t2_injuries,
            "team1_impact": t1_impact,
            "team2_impact": t2_impact,
            "net_injury_edge": round(t2_impact['adj_em_penalty'] - t1_impact['adj_em_penalty'], 2),
            "fetched_at": datetime.now().isoformat()
        }
        self.cache.set(cache_key, result)
        return result

    def _compute_team_impact(self, injuries: List[Dict]) -> Dict[str, Any]:
        """Estimate the collective impact of injuries on team performance."""
        if not injuries:
            return {
                "adj_em_penalty": 0.0,
                "variance_boost": 0.0,
                "tempo_adj": 0.0,
                "summary": "Fully healthy",
                "severity": "none",
                "out_starters": 0,
                "out_players": 0,
                "key_players_out": [],
                "total_impact_score": 0.0
            }

        total_impact = 0
        out_starters = 0
        out_players = 0
        key_players_out = []

        for inj in injuries:
            score = inj.get('impact_score', 3)
            status = inj.get('status', 'Questionable')
            is_starter = inj.get('is_starter', False)

            status_weight = {
                'Out': 1.0, 'Doubtful': 0.85, 'Questionable': 0.4,
                'Day-to-Day': 0.5, 'Probable': 0.15
            }.get(status, 0.5)

            weighted = score * status_weight
            total_impact += weighted

            if status in ('Out', 'Doubtful') and is_starter:
                out_starters += 1
                key_players_out.append(inj.get('player', 'Unknown'))
            if status in ('Out', 'Doubtful'):
                out_players += 1

        # AdjEM penalty: each weighted impact point ≈ 0.4 pts of efficiency
        adj_em_penalty = round(total_impact * 0.4, 2)
        # Variance boost: missing players = less predictable
        variance_boost = round(out_players * 0.15 + out_starters * 0.25, 2)
        # Tempo: missing key players often slows pace
        tempo_adj = round(-out_starters * 0.3, 2)

        if adj_em_penalty >= 5:
            severity = "critical"
        elif adj_em_penalty >= 2.5:
            severity = "significant"
        elif adj_em_penalty >= 1:
            severity = "moderate"
        elif adj_em_penalty > 0:
            severity = "minor"
        else:
            severity = "none"

        summary_parts = []
        if key_players_out:
            summary_parts.append(f"Key out: {', '.join(key_players_out)}")
        if out_players > len(key_players_out):
            summary_parts.append(f"+{out_players - len(key_players_out)} others out/doubtful")
        summary = "; ".join(summary_parts) if summary_parts else "Minor injuries only"

        return {
            "adj_em_penalty": adj_em_penalty,
            "variance_boost": variance_boost,
            "tempo_adj": tempo_adj,
            "out_starters": out_starters,
            "out_players": out_players,
            "total_impact_score": round(total_impact, 1),
            "key_players_out": key_players_out,
            "summary": summary,
            "severity": severity
        }

    def _apply_star_overrides(self, injuries: List[Dict]) -> List[Dict]:
        """Override Claude's impact scores with our star player database when we have a match."""
        for inj in injuries:
            player_name = inj.get('player', '')
            star = get_star_player(player_name)
            if star:
                old_impact = inj.get('impact_score', 5)
                inj['impact_score'] = star['impact']
                inj['is_starter'] = star['tier'] in ('superstar', 'star', 'key_star', 'starter')
                inj['star_verified'] = True
                if old_impact != star['impact']:
                    print(f"[InjuryAnalyzer] Star override: {player_name} impact {old_impact} -> {star['impact']} ({star['tier']})")
            else:
                inj['star_verified'] = False
        return injuries

    def _team_match(self, scraped_name: str, target_name: str) -> bool:
        """Fuzzy match between scraped team name and target name."""
        if not scraped_name or not target_name:
            return False
        s = scraped_name.lower().strip()
        t = target_name.lower().strip()
        if s == t:
            return True
        # Normalize common variations
        replacements = {'.': '', "'": '', 'st ': 'state ', 'uconn': 'connecticut'}
        s_clean = s
        t_clean = t
        for old, new in replacements.items():
            s_clean = s_clean.replace(old, new)
            t_clean = t_clean.replace(old, new)
        if s_clean == t_clean:
            return True
        if len(t_clean) >= 5 and (s_clean.startswith(t_clean) or t_clean.startswith(s_clean)):
            return True
        return False
