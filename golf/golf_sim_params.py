"""
Golf Simulation Parameter Builder.
Produces the full parameter dict consumed by the Monte Carlo engine (to be built
in Session 4). Analogous to build_matchup_params() in matchup_params.py (lines 296-564).

Data flow:
  1. prefetch_all_player_data()        -> bulk DataGolf API calls, indexed by player name
  2. build_player_sim_params()         -> ~30 param dict per player per course
  3. build_field_sim_params()          -> convenience wrapper for entire field
"""

from golf.datagolf_client import DataGolfClient
from golf.golf_course_profiles import (
    get_course_profile,
    AVG_DRIVING_DIST,
    AVG_DRIVING_ACC,
    AVG_SCRAMBLING,
    COURSES,
)
from golf.golf_course_fit import (
    calc_full_course_fit,
    calc_form_regression,
    calc_field_strength_adj,
    clamp,
)
from golf.golf_weather_scraper import calc_weather_impact, calc_player_weather_resilience
from golf.golf_elite_players import get_player_info, get_player_strengths
from golf.api_field_map import (
    extract_list, get_field,
    RANKINGS_FIELDS, SKILL_FIELDS, FIELD_FIELDS,
    PLAYER_DECOMP_FIELDS, PRED_FIELDS,
    american_odds_to_probability,
)


# ═══════════════════════════════════════════════════════════════
# Data Prefetch
# ═══════════════════════════════════════════════════════════════

def prefetch_all_player_data(client, tournament_id=None):
    """Bulk-fetch all player data from DataGolf API.

    Analogous to prefetch_all_team_data() in matchup_params.py.
    Makes multiple API calls and indexes results by player name.

    Uses centralized field mappings from api_field_map.py to handle
    actual DataGolf API response structures.

    Args:
        client: DataGolfClient instance
        tournament_id: optional, to get field-specific data

    Returns:
        dict keyed by player name, each value is a merged stats dict
    """
    players = {}

    # 1. Rankings -> index by player name, extract dg_skill_estimate + owgr_rank
    #    Response: { "rankings": [ { "player_name", "dg_skill_estimate", "owgr_rank", "dg_id", ... } ] }
    rankings_response = client.get_rankings()
    rankings_list = extract_list(rankings_response, 'rankings')
    for entry in rankings_list:
        name = get_field(entry, 'player_name', RANKINGS_FIELDS, "")
        if not name:
            continue
        players[name] = {
            "dg_skill_estimate": get_field(entry, 'dg_skill_estimate', RANKINGS_FIELDS, 0.0),
            "owgr_rank": get_field(entry, 'owgr_rank', RANKINGS_FIELDS, 999),
            "sg_total": get_field(entry, 'dg_skill_estimate', RANKINGS_FIELDS, 0.0),
            "_player_name": name,
            "_player_id": get_field(entry, 'dg_id', RANKINGS_FIELDS, name),
        }

    # 2. Skill ratings (was "skill decompositions") -> merge SG splits
    #    Endpoint: preds/skill-ratings (preds/skill-decompositions returns 404)
    #    Response: { "players": [ { "player_name", "sg_ott", "sg_app", "sg_arg",
    #               "sg_putt", "sg_total", "driving_dist", "driving_acc" } ] }
    #    NOTE: driving_dist/driving_acc (not driving_distance/driving_accuracy)
    #    NOTE: gir_pct, scrambling_pct, putts_per_round are NOT in this endpoint
    decomps_response = client.get_skill_decompositions()
    decomps_list = extract_list(decomps_response, 'skill_ratings')
    for entry in decomps_list:
        name = get_field(entry, 'player_name', SKILL_FIELDS, "")
        if name in players:
            players[name]["sg_ott"] = get_field(entry, 'sg_ott', SKILL_FIELDS, 0.0)
            players[name]["sg_app"] = get_field(entry, 'sg_app', SKILL_FIELDS, 0.0)
            players[name]["sg_arg"] = get_field(entry, 'sg_arg', SKILL_FIELDS, 0.0)
            players[name]["sg_putt"] = get_field(entry, 'sg_putt', SKILL_FIELDS, 0.0)
            # IMPORTANT: DataGolf API returns driving_dist/driving_acc as
            # RELATIVE values (yards/pct above/below tour avg), e.g. +8.65
            # means 8.65 yards longer than average. Convert to absolute values
            # so downstream code (course fit) can compare against AVG_DRIVING_DIST.
            raw_dist = get_field(entry, 'driving_distance', SKILL_FIELDS, 0.0)
            raw_acc = get_field(entry, 'driving_accuracy', SKILL_FIELDS, 0.0)
            players[name]["driving_distance"] = AVG_DRIVING_DIST + raw_dist
            players[name]["driving_accuracy"] = AVG_DRIVING_ACC + raw_acc
            # These fields are not available from skill-ratings; use defaults
            players[name]["gir_pct"] = 66.0
            players[name]["scrambling_pct"] = AVG_SCRAMBLING
            players[name]["putts_per_round"] = 29.0

    if tournament_id:
        # 3. Field updates -> filter to players in the field
        #    Response: { "field": [ { "player_name", "dg_id", ... } ] }
        field_response = client.get_field_updates()
        field_list = extract_list(field_response, 'field_updates')
        field_names = set()
        for entry in field_list:
            name = get_field(entry, 'player_name', FIELD_FIELDS, "")
            if name:
                field_names.add(name)
                # Ensure player exists even if not in rankings
                if name not in players:
                    players[name] = {
                        "dg_skill_estimate": 0.0,
                        "owgr_rank": 999,
                        "sg_total": 0.0,
                        "_player_name": name,
                        "_player_id": get_field(entry, 'dg_id', FIELD_FIELDS, name),
                    }

        # 4. Player decompositions -> course-specific adjustment data
        #    Response: { "players": [ { "player_name", "baseline_pred", "final_pred",
        #               "total_fit_adjustment", "strokes_gained_category_adjustment", ... } ] }
        #    NOTE: This endpoint does NOT have sg_ott/sg_app/sg_arg/sg_putt.
        #    It has course-fit adjustments instead.
        player_decomps_response = client.get_player_decompositions()
        player_decomps_list = extract_list(player_decomps_response, 'player_decompositions')
        for entry in player_decomps_list:
            name = get_field(entry, 'player_name', PLAYER_DECOMP_FIELDS, "")
            if name in players:
                # Store course-specific adjustment data (not raw SG splits)
                players[name]["course_baseline_pred"] = get_field(
                    entry, 'baseline_pred', PLAYER_DECOMP_FIELDS, None)
                players[name]["course_final_pred"] = get_field(
                    entry, 'final_pred', PLAYER_DECOMP_FIELDS, None)
                players[name]["course_fit_adj"] = get_field(
                    entry, 'total_fit_adjustment', PLAYER_DECOMP_FIELDS, None)
                players[name]["course_history_adj"] = get_field(
                    entry, 'total_course_history_adjustment', PLAYER_DECOMP_FIELDS, None)
                players[name]["course_sg_cat_adj"] = get_field(
                    entry, 'strokes_gained_category_adjustment', PLAYER_DECOMP_FIELDS, None)
                players[name]["course_driving_dist_adj"] = get_field(
                    entry, 'driving_distance_adjustment', PLAYER_DECOMP_FIELDS, None)
                players[name]["course_driving_acc_adj"] = get_field(
                    entry, 'driving_accuracy_adjustment', PLAYER_DECOMP_FIELDS, None)
                players[name]["std_deviation"] = get_field(
                    entry, 'std_deviation', PLAYER_DECOMP_FIELDS, None)

        # 5. Pre-tournament predictions -> win/top5/top10/cut probabilities
        #    Response: { "baseline": [ { "player_name", "win", "top_5", "top_10",
        #               "top_20", "make_cut" } ] }
        #    NOTE: Values are American odds STRINGS (e.g., "+878", "-894"),
        #    not decimal probabilities. Must convert with american_odds_to_probability().
        preds_response = client.get_pre_tournament_preds()
        preds_list = extract_list(preds_response, 'pre_tournament_preds')
        for entry in preds_list:
            name = get_field(entry, 'player_name', PRED_FIELDS, "")
            if name in players:
                players[name]["win_prob"] = american_odds_to_probability(
                    get_field(entry, 'win', PRED_FIELDS))
                players[name]["top5_prob"] = american_odds_to_probability(
                    get_field(entry, 'top_5', PRED_FIELDS))
                players[name]["top10_prob"] = american_odds_to_probability(
                    get_field(entry, 'top_10', PRED_FIELDS))
                players[name]["top20_prob"] = american_odds_to_probability(
                    get_field(entry, 'top_20', PRED_FIELDS))
                players[name]["make_cut_prob"] = american_odds_to_probability(
                    get_field(entry, 'make_cut', PRED_FIELDS))

        # Filter to field players only
        if field_names:
            players = {k: v for k, v in players.items() if k in field_names}

    return players


# ═══════════════════════════════════════════════════════════════
# Scoring Rate Calculations
# ═══════════════════════════════════════════════════════════════

def calc_birdie_rate(player_stats, par, course_profile):
    """Calculate base birdie probability for a given par type.

    Uses player SG splits + course difficulty to estimate birdie rate.
    PGA Tour averages: ~12% birdie rate on par 3s, ~18% on par 4s, ~45% on par 5s.

    Args:
        player_stats: dict with SG splits and sg_total_adj
        par: int, hole par (3, 4, or 5)
        course_profile: dict from COURSES

    Returns:
        float: birdie probability (clamped 0.02-0.60)
    """
    sg_ott = player_stats.get("sg_ott", 0.0)
    sg_app = player_stats.get("sg_app", 0.0)
    sg_putt = player_stats.get("sg_putt", 0.0)
    sg_total_adj = player_stats.get("sg_total_adj", 0.0)

    if par == 3:
        base = 0.12 + sg_app * 0.03 + sg_putt * 0.02
    elif par == 4:
        base = 0.18 + sg_total_adj * 0.04
    elif par == 5:
        base = 0.45 + sg_ott * 0.04 + sg_app * 0.03
    else:
        base = 0.18  # fallback for unusual pars

    return clamp(base, 0.02, 0.60)


def calc_bogey_rate(player_stats, par, course_profile):
    """Calculate base bogey probability for a given par type.

    Args:
        player_stats: dict with SG splits and sg_total_adj
        par: int, hole par (3, 4, or 5)
        course_profile: dict from COURSES

    Returns:
        float: bogey probability (clamped 0.05-0.45)
    """
    sg_ott = player_stats.get("sg_ott", 0.0)
    sg_app = player_stats.get("sg_app", 0.0)
    sg_arg = player_stats.get("sg_arg", 0.0)
    sg_total_adj = player_stats.get("sg_total_adj", 0.0)

    if par == 3:
        base = 0.22 - sg_app * 0.02 - sg_arg * 0.02
    elif par == 4:
        base = 0.20 - sg_total_adj * 0.03
    elif par == 5:
        base = 0.12 - sg_ott * 0.02 - sg_app * 0.02
    else:
        base = 0.20

    return clamp(base, 0.05, 0.45)


def calc_double_bogey_rate(player_stats, course_profile):
    """Calculate base double-bogey-or-worse probability. PGA Tour avg ~3%.

    Args:
        player_stats: dict with sg_total_adj
        course_profile: dict from COURSES

    Returns:
        float: double bogey+ probability (clamped 0.01-0.15)
    """
    base = 0.03 - player_stats.get("sg_total_adj", 0) * 0.005
    # Harder courses increase double rate
    if course_profile.get("historical_scoring_avg", 0) > 0:  # over par = hard
        base += 0.01
    return clamp(base, 0.01, 0.15)


def calc_eagle_rate(player_stats, course_profile):
    """Calculate eagle probability on par 5s. PGA Tour avg ~4%.

    Args:
        player_stats: dict with sg_ott, sg_app
        course_profile: dict from COURSES

    Returns:
        float: eagle probability (clamped 0.005-0.12)
    """
    base = 0.04 + player_stats.get("sg_ott", 0) * 0.01 + player_stats.get("sg_app", 0) * 0.008
    return clamp(base, 0.005, 0.12)


# ═══════════════════════════════════════════════════════════════
# Volatility & Consistency
# ═══════════════════════════════════════════════════════════════

def calc_round_volatility(player_stats):
    """Calculate round-to-round scoring volatility.
    Analogous to t1_vol_mod in matchup_params.py.
    PGA Tour avg round std dev is ~2.8 strokes.

    Args:
        player_stats: dict with sg_total_adj

    Returns:
        float: round volatility (clamped 1.5-4.5)
    """
    base = 2.8
    # Better players are slightly more consistent
    sg_adj = -player_stats.get("sg_total_adj", 0) * 0.15
    return clamp(base + sg_adj, 1.5, 4.5)


def calc_streakiness(player_stats):
    """Calculate streak tendency (hot/cold runs within a round).
    Analogous to t1_streakiness in matchup_params.py.

    Args:
        player_stats: dict with optional recent_form.trend

    Returns:
        float: streakiness on 0-1 scale (clamped 0.2-0.9)
    """
    # Players with high birdie rates but also high bogey rates are streaky
    # Use form trend as a proxy
    trend = player_stats.get("recent_form", {}).get("trend", 0)
    base = 0.5 + abs(trend) * 0.3
    return clamp(base, 0.2, 0.9)


# ═══════════════════════════════════════════════════════════════
# Pressure & Experience
# ═══════════════════════════════════════════════════════════════

def calc_pressure_modifier(player_stats):
    """Calculate pressure performance modifier.
    Analogous to ft_clutch_edge in matchup_params.py.

    Args:
        player_stats: dict with _player_name key

    Returns:
        float: pressure modifier (-0.5 to 0.5, positive = performs better
               under pressure)
    """
    info = get_player_info(player_stats.get("_player_name", ""))
    if info:
        strengths = info.get("strengths", [])
        weaknesses = info.get("weaknesses", [])
        pressure_bonus = 0.3 if "pressure" in strengths else 0
        closing_penalty = -0.2 if "closing" in weaknesses else 0
        major_bonus = min(0.2, info.get("majors_won", 0) * 0.05)
        return clamp(pressure_bonus + closing_penalty + major_bonus, -0.5, 0.5)
    return 0.0


# ═══════════════════════════════════════════════════════════════
# Main Parameter Builder
# ═══════════════════════════════════════════════════════════════

def build_player_sim_params(player_stats, course_profile, weather=None,
                            field_strength=0.0):
    """Build the full parameter dict for golf_mc_engine.simulate_tournament().

    Analogous to build_matchup_params() in matchup_params.py (lines 296-564).
    Produces ~30 parameters per player per course.

    Args:
        player_stats: merged dict from prefetch_all_player_data()
        course_profile: dict from COURSES
        weather: optional dict from calc_weather_impact()
        field_strength: float, average SG of field (0.0 = average)

    Returns:
        dict compatible with golf_mc_engine.sim_round() (to be built in S4)
    """
    # Form regression
    form_adj_val = calc_form_regression(player_stats)
    form_delta = form_adj_val - player_stats.get("sg_total", 0.0)

    # Apply form adjustment to SG total for downstream calculations
    adjusted_stats = dict(player_stats)
    adjusted_stats["sg_total_adj"] = form_adj_val

    # Full course fit
    fit = calc_full_course_fit(adjusted_stats, course_profile, weather)
    sg_total_adj = fit["total_fit"] + form_delta
    adjusted_stats["sg_total_adj"] = sg_total_adj

    # Field strength multiplier
    fs_mult = calc_field_strength_adj(player_stats, field_strength)

    # Scoring rates
    birdie_rate_par3 = calc_birdie_rate(adjusted_stats, 3, course_profile)
    birdie_rate_par4 = calc_birdie_rate(adjusted_stats, 4, course_profile)
    birdie_rate_par5 = calc_birdie_rate(adjusted_stats, 5, course_profile)
    bogey_rate_par3 = calc_bogey_rate(adjusted_stats, 3, course_profile)
    bogey_rate_par4 = calc_bogey_rate(adjusted_stats, 4, course_profile)
    bogey_rate_par5 = calc_bogey_rate(adjusted_stats, 5, course_profile)
    double_rate = calc_double_bogey_rate(adjusted_stats, course_profile)
    eagle_rate_par5 = calc_eagle_rate(adjusted_stats, course_profile)

    # Volatility
    round_volatility = calc_round_volatility(adjusted_stats)
    streakiness = calc_streakiness(adjusted_stats)
    # Consistency: inverse of volatility normalized to 0-1
    consistency_score = clamp(1.0 - (round_volatility - 1.5) / 3.0, 0.0, 1.0)

    # Pressure
    pressure_modifier = calc_pressure_modifier(adjusted_stats)

    # Major experience (0-1)
    info = get_player_info(player_stats.get("_player_name", ""))
    major_experience = 0.0
    tier = "unknown"
    if info:
        major_experience = clamp(
            info.get("majors_won", 0) * 0.15
            + (0.2 if info.get("tier") in ("elite", "star") else 0.0),
            0.0, 1.0,
        )
        tier = info.get("tier", "unknown")

    # Weather
    weather_adj = fit.get("weather_adj", 0.0)
    weather_resilience = 0.5  # default
    if weather is not None:
        raw_resilience = calc_player_weather_resilience(player_stats, weather)
        combined = weather.get("combined_adj", 0.0)
        if combined > 0:
            weather_resilience = clamp(1.0 - raw_resilience / combined, 0.0, 1.0)

    # Fatigue factor: estimate from player tier (elite players manage fatigue better)
    fatigue_factor = 0.5
    if info:
        tier_val = info.get("impact", 7)
        fatigue_factor = clamp(1.0 - tier_val * 0.08, 0.1, 0.8)

    return {
        # Core SG splits (course-adjusted)
        "sg_total_adj": round(sg_total_adj * fs_mult, 4),
        "sg_ott": round(player_stats.get("sg_ott", 0.0), 4),
        "sg_app": round(player_stats.get("sg_app", 0.0), 4),
        "sg_arg": round(player_stats.get("sg_arg", 0.0), 4),
        "sg_putt": round(player_stats.get("sg_putt", 0.0), 4),

        # Per-hole scoring distributions
        "birdie_rate_par3": round(birdie_rate_par3, 4),
        "birdie_rate_par4": round(birdie_rate_par4, 4),
        "birdie_rate_par5": round(birdie_rate_par5, 4),
        "bogey_rate_par3": round(bogey_rate_par3, 4),
        "bogey_rate_par4": round(bogey_rate_par4, 4),
        "bogey_rate_par5": round(bogey_rate_par5, 4),
        "double_rate": round(double_rate, 4),
        "eagle_rate_par5": round(eagle_rate_par5, 4),

        # Volatility & consistency
        "round_volatility": round(round_volatility, 4),
        "streakiness": round(streakiness, 4),
        "consistency_score": round(consistency_score, 4),

        # Pressure & experience
        "pressure_modifier": round(pressure_modifier, 4),
        "major_experience": round(major_experience, 4),

        # Weather
        "weather_adj": round(weather_adj, 4),
        "weather_resilience": round(weather_resilience, 4),

        # Fatigue
        "fatigue_factor": round(fatigue_factor, 4),

        # Course-specific
        "course_history_adj": round(fit.get("history_adj", 0.0), 4),
        "course_fit_score": round(fit.get("base_fit", 0.0), 4),

        # Form
        "form_adj": round(form_delta, 4),

        # Metadata
        "_player_name": player_stats.get("_player_name", ""),
        "_player_id": player_stats.get("_player_id", ""),
        "_owgr_rank": player_stats.get("owgr_rank", 999),
        "_tier": tier,
        "_sg_total_raw": round(player_stats.get("sg_total", 0.0), 4),
    }


# ═══════════════════════════════════════════════════════════════
# Field-Level Builder
# ═══════════════════════════════════════════════════════════════

def build_field_sim_params(client, course_id, tournament_id=None, weather=None):
    """Build sim params for an entire tournament field.

    Convenience function that calls prefetch_all_player_data() then
    build_player_sim_params() for each player.

    Args:
        client: DataGolfClient instance
        course_id: str, key into COURSES dict
        tournament_id: optional str, DataGolf tournament/event ID
        weather: optional dict from calc_weather_impact()

    Returns:
        list of player sim param dicts
    """
    course_profile = get_course_profile(course_id)
    if course_profile is None:
        raise ValueError(f"Unknown course_id: {course_id}")

    # Prefetch all player data
    all_players = prefetch_all_player_data(client, tournament_id=tournament_id)

    # Compute field average SG for field-strength adjustment
    sg_values = [
        p.get("sg_total", 0.0) for p in all_players.values()
        if p.get("sg_total") is not None
    ]
    field_avg_sg = sum(sg_values) / len(sg_values) if sg_values else 0.0

    # Build params for each player
    results = []
    for name, stats in all_players.items():
        params = build_player_sim_params(
            stats, course_profile, weather=weather, field_strength=field_avg_sg,
        )
        results.append(params)

    # Sort by adjusted SG (best first)
    results.sort(key=lambda p: p["sg_total_adj"], reverse=True)
    return results
