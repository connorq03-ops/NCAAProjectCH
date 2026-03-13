"""
NCAA Team Roster Verifier
Fetches current team rosters from ESPN to verify if players are still on the team.
Filters out graduated/transferred players from injury reports.
"""

import os
import json
import hashlib
import requests
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Set
from bs4 import BeautifulSoup

# ESPN team roster URL pattern
ESPN_ROSTER_URL = "https://www.espn.com/mens-college-basketball/team/roster/_/id/{team_id}"

# Map common team names to ESPN team IDs (expand as needed)
TEAM_ID_MAP = {
    'kansas': '2305',
    'duke': '150',
    'north carolina': '153',
    'kentucky': '96',
    'ucla': '26',
    'gonzaga': '2250',
    'villanova': '222',
    'michigan': '130',
    'michigan state': '127',
    'arizona': '12',
    'tennessee': '2633',
    'alabama': '333',
    'houston': '248',
    'purdue': '2509',
    'uconn': '41',
    'connecticut': '41',
    'arkansas': '8',
    'baylor': '239',
    'texas': '251',
    'kansas state': '2306',
    'iowa state': '66',
    'tcu': '2628',
    'west virginia': '277',
    'oklahoma': '201',
    'texas tech': '2641',
    'indiana': '84',
    'ohio state': '194',
    'wisconsin': '275',
    'illinois': '356',
    'maryland': '120',
    'rutgers': '164',
    'penn state': '213',
    'iowa': '2294',
    'minnesota': '135',
    'nebraska': '158',
    'northwestern': '77',
    'florida': '57',
    'auburn': '2',
    'mississippi state': '344',
    'ole miss': '145',
    'mississippi': '145',
    'south carolina': '2579',
    'missouri': '142',
    'georgia': '61',
    'lsu': '99',
    'texas a&m': '245',
    'vanderbilt': '238',
}


class RosterCache:
    """Cache roster data to minimize API calls."""
    
    def __init__(self, cache_dir: str = ".roster_cache"):
        self.cache_dir = cache_dir
        os.makedirs(cache_dir, exist_ok=True)
    
    def _key(self, team_name: str) -> str:
        return hashlib.md5(team_name.lower().encode()).hexdigest()
    
    def get(self, team_name: str, max_age_hours: int = 24) -> Optional[Set[str]]:
        """Get cached roster, returns set of player names (lowercase)."""
        path = os.path.join(self.cache_dir, f"{self._key(team_name)}.json")
        if not os.path.exists(path):
            return None
        try:
            with open(path, 'r') as f:
                data = json.load(f)
            cached_at = datetime.fromisoformat(data.get('_cached_at', '2000-01-01'))
            if datetime.now() - cached_at > timedelta(hours=max_age_hours):
                return None
            return set(data.get('players', []))
        except (json.JSONDecodeError, ValueError):
            return None
    
    def set(self, team_name: str, players: Set[str]):
        """Cache roster data."""
        data = {
            'team': team_name,
            'players': list(players),
            '_cached_at': datetime.now().isoformat()
        }
        os.makedirs(self.cache_dir, exist_ok=True)
        path = os.path.join(self.cache_dir, f"{self._key(team_name)}.json")
        with open(path, 'w') as f:
            json.dump(data, f)


class RosterVerifier:
    """Fetches and verifies current team rosters from ESPN."""
    
    HEADERS = {
        'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
                      'AppleWebKit/537.36 (KHTML, like Gecko) '
                      'Chrome/120.0.0.0 Safari/537.36',
    }
    
    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update(self.HEADERS)
        self.cache = RosterCache()
    
    def _normalize_name(self, name: str) -> str:
        """Normalize player name for comparison."""
        if not name:
            return ''
        # Remove suffixes, lowercase, strip
        normalized = name.lower().strip()
        normalized = normalized.replace(' jr.', '').replace(' jr', '')
        normalized = normalized.replace(' sr.', '').replace(' sr', '')
        normalized = normalized.replace(' iii', '').replace(' ii', '').replace(' iv', '')
        return normalized
    
    def _get_team_id(self, team_name: str) -> Optional[str]:
        """Get ESPN team ID from team name."""
        normalized = team_name.lower().strip()
        return TEAM_ID_MAP.get(normalized)
    
    def fetch_roster(self, team_name: str, force_refresh: bool = False) -> Set[str]:
        """Fetch current roster for a team from ESPN. Returns set of normalized player names."""
        if not force_refresh:
            cached = self.cache.get(team_name)
            if cached is not None:
                return cached
        
        team_id = self._get_team_id(team_name)
        if not team_id:
            print(f"[RosterVerifier] No ESPN team ID for '{team_name}' - cannot verify roster")
            return set()
        
        try:
            url = ESPN_ROSTER_URL.format(team_id=team_id)
            resp = self.session.get(url, timeout=15)
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, 'html.parser')
            
            # ESPN roster table structure: find all player name links
            players = set()
            # Look for player links in roster table
            for link in soup.find_all('a', href=True):
                href = link.get('href', '')
                if '/player/' in href and '/id/' in href:
                    player_name = link.get_text(strip=True)
                    if player_name:
                        normalized = self._normalize_name(player_name)
                        if normalized:
                            players.add(normalized)
            
            if players:
                print(f"[RosterVerifier] Fetched {len(players)} players for {team_name}")
                self.cache.set(team_name, players)
            else:
                print(f"[RosterVerifier] No players found for {team_name} at {url}")
            
            return players
        except requests.RequestException as e:
            print(f"[RosterVerifier] Error fetching roster for {team_name}: {e}")
            return set()
    
    def is_on_roster(self, player_name: str, team_name: str) -> bool:
        """Check if a player is on the current roster for a team."""
        roster = self.fetch_roster(team_name)
        if not roster:
            # If we can't fetch roster, assume player is valid (fail open)
            return True
        normalized_player = self._normalize_name(player_name)
        return normalized_player in roster
    
    def filter_injuries_by_roster(self, injuries: List[Dict]) -> List[Dict]:
        """Filter injury list to only include players on current rosters."""
        verified = []
        for inj in injuries:
            player = inj.get('player', '')
            team = inj.get('team', '')
            if not player or not team:
                continue
            
            if self.is_on_roster(player, team):
                verified.append(inj)
            else:
                print(f"[RosterVerifier] Filtered out {player} ({team}) - not on current roster")
        
        return verified


# Global instance
_verifier = None

def get_verifier() -> RosterVerifier:
    """Get or create global roster verifier instance."""
    global _verifier
    if _verifier is None:
        _verifier = RosterVerifier()
    return _verifier


def verify_roster(injuries: List[Dict]) -> List[Dict]:
    """Filter injuries to only include players on current rosters."""
    verifier = get_verifier()
    return verifier.filter_injuries_by_roster(injuries)
