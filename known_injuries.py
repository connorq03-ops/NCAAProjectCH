"""
Known Injuries Database
Manually maintained list of confirmed injuries that the scraping pipeline might miss.
These are always included in injury reports regardless of news scraping results.
Update this file as injuries are confirmed or players return.
"""

# Alias map for common team name variations
TEAM_ALIASES = {
    'unc': 'north carolina',
    'uconn': 'connecticut',
    'ole miss': 'mississippi',
    'smu': 'southern methodist',
    'lsu': 'louisiana state',
    'vcu': 'virginia commonwealth',
    'ucf': 'central florida',
    'usc': 'southern california',
    'pitt': 'pittsburgh',
    'umass': 'massachusetts',
    'uva': 'virginia',
    'nc state': 'north carolina state',
    'byu': 'brigham young',
    'tcu': 'texas christian',
}

# Format: team_name (lowercase) -> list of injury dicts
# Remove entries when players return to action
KNOWN_INJURIES = {
    "texas tech": [
        {
            "player": "JT Toppin",
            "team": "Texas Tech",
            "position": "F",
            "status": "Out",
            "injury": "Torn ACL, out for season",
            "is_starter": True,
            "impact_score": 9,
            "date_reported": "2026-03-07",
            "confidence": "high",
            "source": "known_injuries_db"
        },
    ],
    "north carolina": [
        {
            "player": "Caleb Wilson",
            "team": "North Carolina",
            "position": "F",
            "status": "Out",
            "injury": "Broken thumb, out for season",
            "is_starter": True,
            "impact_score": 8,
            "date_reported": "2026-03-15",
            "confidence": "high",
            "source": "known_injuries_db"
        },
    ],
}


def _normalize_team(team_name: str) -> str:
    """Normalize team name using alias map."""
    key = team_name.lower().strip()
    return TEAM_ALIASES.get(key, key)


def get_known_injuries(team_name: str) -> list:
    """Get known injuries for a specific team."""
    normalized = _normalize_team(team_name)
    return KNOWN_INJURIES.get(normalized, [])


def get_known_matchup_injuries(team1: str, team2: str) -> list:
    """Get all known injuries for both teams in a matchup."""
    return get_known_injuries(team1) + get_known_injuries(team2)


def merge_known_injuries(scraped_injuries: list, team_name: str) -> list:
    """Merge known injuries into scraped results, avoiding duplicates."""
    known = get_known_injuries(team_name)
    if not known:
        return scraped_injuries

    # Build set of existing player names (lowercase) to avoid dupes
    existing = {inj.get('player', '').lower().strip() for inj in scraped_injuries}

    merged = list(scraped_injuries)
    for k in known:
        if k['player'].lower().strip() not in existing:
            merged.append(k)
            print(f"[KnownInjuries] Added {k['player']} ({k['team']}) - {k['injury']}")

    return merged


def merge_known_matchup_injuries(scraped_injuries: list, team1: str, team2: str) -> list:
    """Merge known injuries for both teams into scraped matchup results."""
    known = get_known_matchup_injuries(team1, team2)
    if not known:
        return scraped_injuries

    existing = {inj.get('player', '').lower().strip() for inj in scraped_injuries}

    merged = list(scraped_injuries)
    for k in known:
        if k['player'].lower().strip() not in existing:
            merged.append(k)
            print(f"[KnownInjuries] Added {k['player']} ({k['team']}) - {k['injury']}")

    return merged
