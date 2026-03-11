"""
Dynamic Star Player Coverage via ESPN Stats Scraping
Fetches season stat leaders from ESPN to auto-generate star player entries
for ALL D1 teams, not just the ~35 manually entered ones.

Manual entries in STAR_PLAYERS serve as overrides (higher fidelity for
top players whose draft stock / eye-test impact exceeds stats).
"""

import requests
from typing import Dict, List


ESPN_CORE_LEADERS_URL = "https://sports.core.api.espn.com/v2/sports/basketball/leagues/mens-college-basketball/seasons/{season}/types/2/leaders"

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
                   'AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
}

# In-memory team name cache to avoid re-fetching $ref links
_team_name_cache = {}


def _compute_impact(ppg, rpg=0, apg=0, spg=0, fg_pct=0):
    """
    Derive an impact score (6-10) from per-game stats.
    Roughly calibrated to match the manual STAR_PLAYERS tiers:
      10 = 20+ ppg with elite efficiency or all-around
       9 = 17+ ppg or 14+ ppg with 7+ rpg/5+ apg
       8 = 14+ ppg or strong all-around
       7 = 11+ ppg on a ranked team
       6 = rotation-level contributor
    """
    score = 5.0

    if ppg >= 22:
        score += 4.0
    elif ppg >= 19:
        score += 3.5
    elif ppg >= 16:
        score += 2.8
    elif ppg >= 14:
        score += 2.2
    elif ppg >= 12:
        score += 1.5
    elif ppg >= 10:
        score += 1.0
    else:
        score += ppg * 0.08

    if rpg >= 9:
        score += 1.0
    elif rpg >= 7:
        score += 0.6
    elif rpg >= 5:
        score += 0.3

    if apg >= 6:
        score += 1.0
    elif apg >= 4:
        score += 0.5
    elif apg >= 3:
        score += 0.2

    if spg >= 2:
        score += 0.3

    if fg_pct >= 55:
        score += 0.3
    elif fg_pct >= 50:
        score += 0.15

    return min(10, max(6, round(score)))


def _infer_position(pos_str):
    """Normalize ESPN position string."""
    if not pos_str:
        return "G"
    pos = pos_str.upper().strip()
    if pos in ("PG", "SG", "SF", "PF", "C", "G", "F"):
        return pos
    if "GUARD" in pos:
        return "G"
    if "FORWARD" in pos:
        return "F"
    if "CENTER" in pos:
        return "C"
    return pos[:2] if len(pos) >= 2 else "G"


def _resolve_ref(ref_url, timeout=8):
    """Fetch a JSON resource from an ESPN $ref URL."""
    try:
        resp = requests.get(ref_url, headers=HEADERS, timeout=timeout)
        if resp.status_code == 200:
            return resp.json()
    except Exception:
        pass
    return {}


def _get_team_name(team_ref):
    """Resolve a team $ref URL to team displayName, with caching."""
    if not team_ref:
        return ''
    if team_ref in _team_name_cache:
        return _team_name_cache[team_ref]
    data = _resolve_ref(team_ref)
    name = data.get('displayName', data.get('shortDisplayName', ''))
    if name:
        _team_name_cache[team_ref] = name
    return name


def fetch_espn_team_leaders(limit=50, season=2025, d1_teams=None) -> List[Dict]:
    """
    Fetch top scorers from ESPN core API leaders endpoint.
    Resolves $ref links for athlete name/position and team name.
    Filters to D1 teams only if d1_teams set is provided.
    Returns list of {name, team, ppg, position}
    """
    from concurrent.futures import ThreadPoolExecutor

    players = []
    try:
        url = ESPN_CORE_LEADERS_URL.format(season=season)
        resp = requests.get(url, params={'limit': limit},
                            headers=HEADERS, timeout=15)
        resp.raise_for_status()
        data = resp.json()

        categories = data.get('categories', [])
        ppg_cat = None
        for cat in categories:
            if cat.get('name', '') == 'pointsPerGame':
                ppg_cat = cat
                break
        if not ppg_cat:
            print("[star_scraper] No pointsPerGame category found")
            return players

        # Collect all refs to resolve
        leader_entries = []
        for leader in ppg_cat.get('leaders', []):
            ppg = float(leader.get('value', 0))
            if ppg < 10:
                continue
            ath_ref = leader.get('athlete', {}).get('$ref', '')
            team_ref = leader.get('team', {}).get('$ref', '')
            leader_entries.append((ppg, ath_ref, team_ref))

        # Batch-resolve $ref links concurrently
        all_refs = set()
        for _, ath_ref, team_ref in leader_entries:
            if ath_ref:
                all_refs.add(ath_ref)
            if team_ref and team_ref not in _team_name_cache:
                all_refs.add(team_ref)

        ref_data = {}
        if all_refs:
            with ThreadPoolExecutor(max_workers=10) as pool:
                futures = {pool.submit(_resolve_ref, r): r for r in all_refs}
                for fut in futures:
                    ref_url = futures[fut]
                    try:
                        ref_data[ref_url] = fut.result()
                    except Exception:
                        ref_data[ref_url] = {}

        # Build player list from resolved data
        # Build D1 lookup: ESPN names like "Villanova Wildcats" → KenPom "Villanova"
        d1_map = {}  # espn_lower → kenpom_name
        if d1_teams:
            for kp in d1_teams:
                d1_map[kp.lower()] = kp  # exact match

        def _match_d1(espn_name):
            """Match ESPN team name to KenPom name. Returns KenPom name or None."""
            if not d1_teams:
                return espn_name  # No filter, pass through
            el = espn_name.lower()
            if el in d1_map:
                return d1_map[el]
            # Try: KenPom name is prefix of ESPN name (e.g. "villanova" in "villanova wildcats")
            for kp in d1_teams:
                if el.startswith(kp.lower()) or kp.lower() in el:
                    return kp
            return None

        for ppg, ath_ref, team_ref in leader_entries:
            ath = ref_data.get(ath_ref, {})
            name = ath.get('displayName', '')
            pos = ath.get('position', {}).get('abbreviation', '')

            # Resolve team
            if team_ref in _team_name_cache:
                espn_team = _team_name_cache[team_ref]
            else:
                tm = ref_data.get(team_ref, {})
                espn_team = tm.get('displayName', tm.get('shortDisplayName', ''))
                if espn_team:
                    _team_name_cache[team_ref] = espn_team

            if not name or not espn_team:
                continue

            # Map ESPN team name to KenPom name (or filter out non-D1)
            team = _match_d1(espn_team)
            if not team:
                continue

            players.append({
                'name': name,
                'team': team,
                'ppg': ppg,
                'position': _infer_position(pos),
            })

    except Exception as e:
        print(f"[star_scraper] ESPN leaders fetch error: {e}")

    return players


def build_dynamic_stars(manual_stars: Dict = None, min_ppg=10.0, d1_teams=None) -> Dict:
    """
    Build a comprehensive star player database:
    1. Fetch ESPN scoring leaders (top 50)
    2. Compute impact scores from stats
    3. Merge with manual overrides (manual entries take priority)

    Returns: dict mapping team_name → [{ player, team, position, tier, impact, note, source }]
    """
    if manual_stars is None:
        manual_stars = {}

    # Track which players are already manually entered
    manual_names = set(manual_stars.keys())

    # Fetch ESPN leaders (filtered to D1 if team list provided)
    espn_leaders = fetch_espn_team_leaders(limit=50, d1_teams=d1_teams)

    # Build result: start with manual entries grouped by team
    by_team = {}
    for name, info in manual_stars.items():
        team = info['team']
        if team not in by_team:
            by_team[team] = []
        by_team[team].append({
            'player': name,
            **info,
            'source': 'manual',
        })

    # Add ESPN-derived entries for players not already in manual database
    for p in espn_leaders:
        if p['name'] in manual_names:
            continue  # Manual override exists
        if p['ppg'] < min_ppg:
            continue

        impact = _compute_impact(p['ppg'])
        tier = 'superstar' if impact >= 10 else 'star' if impact >= 9 else 'key_star' if impact >= 8 else 'starter' if impact >= 7 else 'rotation'

        team = p['team']
        if team not in by_team:
            by_team[team] = []

        # Check if this team already has enough manual entries
        manual_count = sum(1 for s in by_team[team] if s.get('source') == 'manual')
        if manual_count >= 3:
            continue  # Team is well-covered manually

        by_team[team].append({
            'player': p['name'],
            'team': team,
            'position': p['position'],
            'tier': tier,
            'impact': impact,
            'note': f"{p['ppg']:.1f} ppg (ESPN stats)",
            'source': 'espn',
        })

    # Sort each team's stars by impact descending
    for team in by_team:
        by_team[team].sort(key=lambda x: x.get('impact', 0), reverse=True)

    return by_team


def get_dynamic_stars_flat(manual_stars: Dict = None) -> Dict:
    """
    Returns stars in the same flat format as STAR_PLAYERS dict:
    { "Player Name": { team, position, tier, impact, note } }
    """
    by_team = build_dynamic_stars(manual_stars)
    flat = {}
    for team_stars in by_team.values():
        for s in team_stars:
            flat[s['player']] = {
                'team': s.get('team', ''),
                'position': s.get('position', 'G'),
                'tier': s.get('tier', 'rotation'),
                'impact': s.get('impact', 6),
                'note': s.get('note', ''),
            }
    return flat
