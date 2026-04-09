"""
api_field_map.py - Centralized DataGolf API response field name mappings.

Single source of truth for mapping DataGolf API field names to internal field names.
If DataGolf changes their API, only this file needs updating.

Validated against real API responses on 2026-04-09.

API Response Structures (actual):
  - get_rankings():               dict { rankings: [...] }
  - get_skill_ratings():          dict { players: [...] }  (was get_skill_decompositions, endpoint 404)
  - get_field_updates():          dict { field: [...] }
  - get_player_decompositions():  dict { players: [...] }  (course-fit adjustments, not SG splits)
  - get_pre_tournament_preds():   dict { baseline: [...], baseline_history_fit: [...] }
  - get_pre_tournament_pred_archive(): dict { baseline: [...], baseline_history_fit: [...] }
  - get_outright_odds():          dict { odds: [...] }
  - get_matchup_odds():           dict { match_list: [...] }  (requires market param)
  - get_historical_events():      bare list [...]
  - get_historical_rounds():      403 (higher tier)
  - get_live_model():             dict { data: [...], info: {...} }
"""

# ═══════════════════════════════════════════════════════════════
# Wrapper key mappings — how to extract the list from each response
# ═══════════════════════════════════════════════════════════════

WRAPPER_KEYS = {
    'rankings': 'rankings',
    'skill_ratings': 'players',
    'field_updates': 'field',
    'player_decompositions': 'players',
    'pre_tournament_preds': 'baseline',           # NOT 'predictions' or 'players'
    'pre_tournament_pred_archive': 'baseline',     # same structure as live preds
    'outright_odds': 'odds',
    'matchup_odds': 'match_list',
    'historical_events': None,                     # bare list, no wrapper
    'live_model': 'data',
}


# ═══════════════════════════════════════════════════════════════
# Rankings endpoint field mappings
# Endpoint: preds/get-dg-rankings
# ═══════════════════════════════════════════════════════════════

RANKINGS_FIELDS = {
    # internal_name -> actual DataGolf field name
    'player_name': 'player_name',
    'dg_skill_estimate': 'dg_skill_estimate',
    'owgr_rank': 'owgr_rank',
    'dg_id': 'dg_id',
    'datagolf_rank': 'datagolf_rank',
    'country': 'country',
    'primary_tour': 'primary_tour',
    'am': 'am',
}


# ═══════════════════════════════════════════════════════════════
# Skill ratings endpoint field mappings
# Endpoint: preds/skill-ratings (NOT preds/skill-decompositions which 404s)
# ═══════════════════════════════════════════════════════════════

SKILL_FIELDS = {
    # internal_name -> actual DataGolf field name
    'player_name': 'player_name',
    'dg_id': 'dg_id',
    'sg_ott': 'sg_ott',
    'sg_app': 'sg_app',
    'sg_arg': 'sg_arg',
    'sg_putt': 'sg_putt',
    'sg_total': 'sg_total',
    'driving_distance': 'driving_dist',     # NOT 'driving_distance'
    'driving_accuracy': 'driving_acc',       # NOT 'driving_accuracy'
}

# Fields that do NOT exist in the real API response (code incorrectly expects them):
SKILL_FIELDS_NOT_IN_API = [
    'gir_pct',           # not available from skill-ratings
    'scrambling_pct',    # not available from skill-ratings
    'putts_per_round',   # not available from skill-ratings
]


# ═══════════════════════════════════════════════════════════════
# Field updates endpoint field mappings
# Endpoint: field-updates
# ═══════════════════════════════════════════════════════════════

FIELD_FIELDS = {
    'player_name': 'player_name',
    'dg_id': 'dg_id',
    'dg_rank': 'dg_rank',
    'owgr_rank': 'owgr_rank',
    'country': 'country',
}


# ═══════════════════════════════════════════════════════════════
# Player decompositions endpoint field mappings
# Endpoint: preds/player-decompositions
# NOTE: This is course-fit adjustment data, NOT raw SG splits
# ═══════════════════════════════════════════════════════════════

PLAYER_DECOMP_FIELDS = {
    'player_name': 'player_name',
    'dg_id': 'dg_id',
    'baseline_pred': 'baseline_pred',
    'final_pred': 'final_pred',
    'total_fit_adjustment': 'total_fit_adjustment',
    'total_course_history_adjustment': 'total_course_history_adjustment',
    'strokes_gained_category_adjustment': 'strokes_gained_category_adjustment',
    'driving_distance_adjustment': 'driving_distance_adjustment',
    'driving_accuracy_adjustment': 'driving_accuracy_adjustment',
    'course_history_adjustment': 'course_history_adjustment',
    'course_experience_adjustment': 'course_experience_adjustment',
    'major_adjustment': 'major_adjustment',
    'timing_adjustment': 'timing_adjustment',
    'age_adjustment': 'age_adjustment',
    'std_deviation': 'std_deviation',
    'cf_approach_comp': 'cf_approach_comp',
    'cf_short_comp': 'cf_short_comp',
}

# Fields the code incorrectly expects from this endpoint (these do NOT exist here):
PLAYER_DECOMP_FIELDS_NOT_IN_API = [
    'sg_ott',    # NOT in player decompositions — use skill_ratings instead
    'sg_app',
    'sg_arg',
    'sg_putt',
]


# ═══════════════════════════════════════════════════════════════
# Pre-tournament predictions field mappings
# Endpoint: preds/pre-tournament
# NOTE: Values are American odds STRINGS (e.g., "+878", "-894"),
#        not decimal probabilities!
# ═══════════════════════════════════════════════════════════════

PRED_FIELDS = {
    'player_name': 'player_name',
    'dg_id': 'dg_id',
    'win': 'win',               # NOT 'win_prob' — American odds string
    'top_5': 'top_5',           # American odds string
    'top_10': 'top_10',         # American odds string
    'top_20': 'top_20',         # American odds string
    'make_cut': 'make_cut',     # American odds string
}

# The archive endpoint returns decimal IMPLIED PROBABILITIES (floats), not odds strings
PRED_ARCHIVE_FIELDS = {
    'player_name': 'player_name',
    'dg_id': 'dg_id',
    'win': 'win',               # float: implied probability (e.g., 12.18)
    'top_5': 'top_5',           # float
    'top_10': 'top_10',         # float
    'top_20': 'top_20',         # float
    'top_30': 'top_30',         # float (extra)
    'make_cut': 'make_cut',     # float
    'fin_text': 'fin_text',     # str: actual finish (e.g., "T30")
}


# ═══════════════════════════════════════════════════════════════
# Historical events field mappings
# Endpoint: historical-raw-data/event-list
# NOTE: Response is a bare list (no wrapper key)
# ═══════════════════════════════════════════════════════════════

HISTORICAL_EVENT_FIELDS = {
    'event_id': 'event_id',
    'event_name': 'event_name',
    'date': 'date',
    'calendar_year': 'calendar_year',
    'tour': 'tour',
    'sg_categories': 'sg_categories',
    'traditional_stats': 'traditional_stats',
}


# ═══════════════════════════════════════════════════════════════
# Live model field mappings
# Endpoint: preds/in-play
# NOTE: Odds fields are American odds STRINGS
# ═══════════════════════════════════════════════════════════════

LIVE_MODEL_FIELDS = {
    'player_name': 'player_name',
    'dg_id': 'dg_id',
    'win': 'win',
    'top_5': 'top_5',
    'top_10': 'top_10',
    'top_20': 'top_20',
    'make_cut': 'make_cut',
    'current_pos': 'current_pos',
    'current_score': 'current_score',
    'thru': 'thru',
    'round': 'round',
    'today': 'today',
    'course': 'course',
    'country': 'country',
}


# ═══════════════════════════════════════════════════════════════
# Helper functions
# ═══════════════════════════════════════════════════════════════

def get_field(data_dict, internal_name, field_map, default=None):
    """Get a field from a DataGolf response dict using the centralized mapping.

    Args:
        data_dict: dict from API response item
        internal_name: the internal field name we use in our code
        field_map: one of the *_FIELDS dicts above
        default: value to return if field not found

    Returns:
        The field value, or default if not found
    """
    actual_key = field_map.get(internal_name, internal_name)
    return data_dict.get(actual_key, default)


def extract_list(response, endpoint_key):
    """Extract the list data from an API response using the correct wrapper key.

    Args:
        response: raw API response (dict or list)
        endpoint_key: key into WRAPPER_KEYS (e.g., 'rankings', 'skill_ratings')

    Returns:
        list of items, or empty list if extraction fails
    """
    wrapper_key = WRAPPER_KEYS.get(endpoint_key)

    if wrapper_key is None:
        # Bare list response (e.g., historical_events)
        if isinstance(response, list):
            return response
        return []

    if isinstance(response, dict):
        return response.get(wrapper_key, [])
    elif isinstance(response, list):
        return response
    return []


def american_odds_to_probability(odds_str):
    """Convert American odds string to implied probability (0-1 scale).

    Pre-tournament predictions return American odds strings like "+878", "-894".
    This converts them to probabilities.

    Args:
        odds_str: American odds string (e.g., "+878", "-894")

    Returns:
        float: implied probability (0.0 to 1.0)
    """
    if odds_str is None:
        return 0.0
    if isinstance(odds_str, (int, float)):
        # Already numeric — if it looks like a probability, return as-is
        if 0 <= odds_str <= 1:
            return float(odds_str)
        # Could be American odds as a number
        odds_val = float(odds_str)
    else:
        try:
            odds_val = float(str(odds_str).replace(',', ''))
        except (ValueError, TypeError):
            return 0.0

    if odds_val > 0:
        # Positive odds: probability = 100 / (odds + 100)
        return 100.0 / (odds_val + 100.0)
    elif odds_val < 0:
        # Negative odds: probability = |odds| / (|odds| + 100)
        return abs(odds_val) / (abs(odds_val) + 100.0)
    else:
        return 0.5  # Even odds


def archive_value_to_probability(val):
    """Convert archive prediction value to probability (0-1 scale).

    The archive endpoint returns ALL values as percentages on a 0-100 scale.
    For example, 12.18 means 12.18% win probability, and 0.3 means 0.3%.
    We always divide by 100 to convert to a 0-1 probability.

    Args:
        val: value from archive response (can be float or None)

    Returns:
        float: probability (0.0 to 1.0)
    """
    if val is None:
        return 0.0
    try:
        v = float(val)
    except (ValueError, TypeError):
        return 0.0
    # Archive values are always percentages (0-100 scale), so always divide by 100.
    # e.g., 12.18 -> 0.1218 (12.18%), 0.3 -> 0.003 (0.3%)
    return v / 100.0
