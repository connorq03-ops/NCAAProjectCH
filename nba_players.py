"""
NBA Players Database
Tracks college players who have declared for the NBA draft or transferred to professional leagues.
Used to filter out stale injury reports for players no longer in college basketball.
"""

# Players who declared for NBA draft or went pro (2024-2025 season)
# Format: player name (lowercase for fuzzy matching)
NBA_PLAYERS = {
    # 2024 NBA Draft notable college players
    # Store WITHOUT suffixes (jr, sr, etc.) — normalization strips them from input
    'hunter dickinson',
    'zach edey',
    'donovan clingan',
    'reed sheppard',
    'rob dillingham',
    'stephon castle',
    'dalton knecht',
    'tyler kolek',
    'ryan dunn',
    'tristen newton',
    'baylor scheierman',
    'johnny furphy',
    'jared mccain',
    'devin carter',
    'tyler smith',
    'kel el ware',
    'ja kobe walter',
    'carlton carrington',
    'daron holmes',
    'yves missi',
    'isaiah collier',
    'cody williams',
    'nikola topic',
    'tidjane salaun',
    'pacome dadiet',
    'kyshawn george',
    'terrence shannon',
    'antonio reeves',
    'ajay mitchell',
    'cam spencer',
    'pj hall',
    'jamal shead',
    'kevin mccullar',
    'harrison ingram',
    'keshad johnson',
    'trentyn flowers',
    'bronny james',
    'dajuan harris',
    
    # Add more as needed - update this list periodically
}


def is_nba_player(player_name: str) -> bool:
    """Check if a player has declared for the NBA or gone pro."""
    if not player_name:
        return False
    normalized = player_name.lower().strip()
    # Remove common suffixes
    normalized = normalized.replace(' jr.', '').replace(' jr', '').replace(' sr', '').replace(' iii', '').replace(' ii', '')
    return normalized in NBA_PLAYERS


def filter_nba_players(injuries: list) -> list:
    """Remove injuries for players who are no longer in college basketball."""
    return [inj for inj in injuries if not is_nba_player(inj.get('player', ''))]
