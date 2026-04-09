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


# ═══════════════════════════════════════════════════════════════
# Data Prefetch
# ═══════════════════════════════════════════════════════════════

def prefetch_all_player_data(client, tournament_id=None):
    """Bulk-fetch all player data from DataGolf API.

    Analogous to prefetch_all_team_data() in matchup_params.py.
    Makes multiple API calls and indexes results by player name.

    Args:
        client: DataGolfClient instance
        tournament_id: optional, to get field-specific data

    Returns:
        dict keyed by player name, each value is a merged stats dict
    """
    players = {}

    # 1. Rankings -> index by player name, extract dg_skill_estimate + owgr_rank
    rankings = client.get_rankings()
    if isinstance(rankings, dict):
        rankings = rankings.get("rankings", [])
    for entry in (rankings or []):
        name = entry.get("player_name", "")
        if not name:
            continue
        players[name] = {
            "dg_skill_estimate": entry.get("dg_skill_estimate", 0.0),
            "owgr_rank": entry.get("owgr_rank", 999),
            "sg_total": entry.get("dg_skill_estimate", 0.0),
            "_player_name": name,
            "_player_id": entry.get("dg_id", name),
        }

    # 2. Skill decompositions -> merge SG splits
    decomps = client.get_skill_decompositions()
    if isinstance(decomps, dict):
        decomps = decomps.get("decompositions", decomps.get("players", []))
    for entry in (decomps or []):
        name = entry.get("player_name", "")
        if name in players:
            players[name]["sg_ott"] = entry.get("sg_ott", 0.0)
            players[name]["sg_app"] = entry.get("sg_app", 0.0)
            players[name]["sg_arg"] = entry.get("sg_arg", 0.0)
            players[name]["sg_putt"] = entry.get("sg_putt", 0.0)
            players[name]["driving_distance"] = entry.get(
                "driving_distance", AVG_DRIVING_DIST
            )
            players[name]["driving_accuracy"] = entry.get(
                "driving_accuracy", AVG_DRIVING_ACC
            )
            players[name]["gir_pct"] = entry.get("gir_pct", 66.0)
            players[name]["scrambling_pct"] = entry.get(
                "scrambling_pct", AVG_SCRAMBLING
            )
            players[name]["putts_per_round"] = entry.get("putts_per_round", 29.0)

    if tournament_id:
        # 3. Field updates -> filter to players in the field
        field = client.get_field_updates()
        if isinstance(field, dict):
            field = field.get("field", [])
        field_names = set()
        for entry in (field or []):
            name = entry.get("player_name", "")
            if name:
                field_names.add(name)
                # Ensure player exists even if not in rankings
                if name not in players:
                    players[name] = {
                        "dg_skill_estimate": 0.0,
                        "owgr_rank": 999,
                        "sg_total": 0.0,
                        "_player_name": name,
                        "_player_id": entry.get("dg_id", name),
                    }

        # 4. Player decompositions -> course-specific data
        player_decomps = client.get_player_decompositions()
        if isinstance(player_decomps, dict):
            player_decomps = player_decomps.get("decompositions",
                                                 player_decomps.get("players", []))
        for entry in (player_decomps or []):
            name = entry.get("player_name", "")
            if name in players:
                # Merge course-specific decompositions
                players[name]["course_sg_ott"] = entry.get("sg_ott", None)
                players[name]["course_sg_app"] = entry.get("sg_app", None)
                players[name]["course_sg_arg"] = entry.get("sg_arg", None)
                players[name]["course_sg_putt"] = entry.get("sg_putt", None)

        # 5. Pre-tournament predictions -> win/top5/top10/cut probabilities
        preds = client.get_pre_tournament_preds()
        if isinstance(preds, dict):
            preds = preds.get("predictions", preds.get("players", []))
        for entry in (preds or []):
            name = entry.get("player_name", "")
            if name in players:
                players[name]["win_prob"] = entry.get("win_prob", 0.0)
                players[name]["top5_prob"] = entry.get("top_5", 0.0)
                players[name]["top10_prob"] = entry.get("top_10", 0.0)
                players[name]["top20_prob"] = entry.get("top_20", 0.0)
                players[name]["make_cut_prob"] = entry.get("make_cut", 0.0)

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
    sg_total_adj = fit["total_fit"]
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
