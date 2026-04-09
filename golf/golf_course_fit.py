"""
Golf Course-Fit Engine.
Goes beyond the basic calc_course_fit_score() in golf_course_profiles.py by adding
course history, weather interaction, driving distance x course length correlation,
green type adjustments, and other contextual modifiers.

This is the golf equivalent of asymmetric_matchup() + the full matchup parameter
computation in matchup_params.py.
"""

from golf.golf_course_profiles import (
    get_course_profile,
    calc_course_fit_score,
    AVG_DRIVING_DIST,
    AVG_DRIVING_ACC,
    AVG_SCRAMBLING,
)
from golf.golf_weather_scraper import calc_weather_impact, calc_player_weather_resilience
from golf.golf_elite_players import get_player_info, get_player_strengths


# ═══════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════

def clamp(val, lo, hi):
    """Clamp a value between lo and hi."""
    return max(lo, min(hi, val))


# ═══════════════════════════════════════════════════════════════
# Full Course-Fit Projection
# ═══════════════════════════════════════════════════════════════

def calc_full_course_fit(player_stats, course_profile, weather=None):
    """Full course-fit projection combining SG weights, course history,
    driving interactions, green type, and weather.

    This is the golf equivalent of asymmetric_matchup() + the full
    matchup parameter computation in matchup_params.py.

    Args:
        player_stats: dict with keys: sg_ott, sg_app, sg_arg, sg_putt,
                      driving_distance, driving_accuracy, gir_pct,
                      scrambling_pct, putts_per_round, course_history (optional),
                      recent_form (optional dict with last_4, last_8, last_12, trend)
        course_profile: dict from COURSES in golf_course_profiles.py
        weather: optional dict from calc_weather_impact()

    Returns:
        dict with: base_fit, history_adj, length_adj, accuracy_adj,
                   green_adj, scramble_adj, weather_adj, total_fit
    """
    # 1. Base fit: weighted SG from golf_course_profiles
    player_sg = {
        "sg_ott": player_stats.get("sg_ott", 0.0),
        "sg_app": player_stats.get("sg_app", 0.0),
        "sg_arg": player_stats.get("sg_arg", 0.0),
        "sg_putt": player_stats.get("sg_putt", 0.0),
    }
    base_fit = calc_course_fit_score(player_sg, course_profile)

    # 2. Course history adjustment
    #    Prefer DataGolf's course_history_adj when available (based on years
    #    of historical data); fall back to our local calculation.
    history_adj = 0.0
    dg_history_adj = player_stats.get("course_history_adj")
    if dg_history_adj is not None:
        history_adj = clamp(dg_history_adj, -0.8, 0.8)
    else:
        course_history = player_stats.get("course_history")
        if course_history and "avg_finish_vs_field" in course_history:
            # Negative finish_vs_field means player does well here -> positive adj
            history_adj = clamp(
                course_history["avg_finish_vs_field"] * -0.15, -0.5, 0.5
            )

    # 3. Driving distance x course length
    length_adj = 0.0
    yardage = course_profile.get("yardage", 7200)
    driving_distance = player_stats.get("driving_distance", AVG_DRIVING_DIST)
    if yardage > 7400:
        length_adj = (driving_distance - AVG_DRIVING_DIST) * 0.008
    elif yardage < 7000:
        length_adj = -(driving_distance - AVG_DRIVING_DIST) * 0.003

    # 4. Accuracy x fairway width
    accuracy_adj = 0.0
    fairway_width = course_profile.get("fairway_width", "medium")
    driving_accuracy = player_stats.get("driving_accuracy", AVG_DRIVING_ACC)
    if fairway_width == "narrow":
        accuracy_adj = (driving_accuracy - AVG_DRIVING_ACC) * 0.012
    elif fairway_width == "wide":
        accuracy_adj = -(driving_accuracy - AVG_DRIVING_ACC) * 0.004

    # 5. Green speed x putting
    green_adj = 0.0
    green_speed = course_profile.get("green_speed", "medium")
    sg_putt = player_stats.get("sg_putt", 0.0)
    if green_speed in ("fast", "very_fast"):
        green_adj = sg_putt * 0.15
    elif green_speed == "slow":
        green_adj = -sg_putt * 0.05

    # 6. Scrambling x rough severity
    scramble_adj = 0.0
    rough_severity = course_profile.get("rough_severity", "medium")
    scrambling_pct = player_stats.get("scrambling_pct", AVG_SCRAMBLING)
    if rough_severity == "heavy":
        scramble_adj = (scrambling_pct - AVG_SCRAMBLING) * 0.008

    # 7. Weather adjustment
    weather_adj = 0.0
    if weather is not None:
        weather_adj = -calc_player_weather_resilience(player_stats, weather)

    # 8. Blend with DataGolf's precomputed course_fit_adj when available.
    #    DataGolf's adjustment is based on years of course-specific data and
    #    is one of the most accurate components of their model.  We use a
    #    40/60 blend (40% DataGolf anchor, 60% our calculation) so we benefit
    #    from their data without completely overriding our own signals.
    our_total = (base_fit + history_adj + length_adj + accuracy_adj
                 + green_adj + scramble_adj + weather_adj)
    dg_fit_adj = player_stats.get("course_fit_adj")
    if dg_fit_adj is not None:
        total_fit = clamp(our_total * 0.60 + dg_fit_adj * 0.40, -3.0, 3.0)
    else:
        total_fit = clamp(our_total, -3.0, 3.0)

    return {
        "base_fit": round(base_fit, 4),
        "history_adj": round(history_adj, 4),
        "length_adj": round(length_adj, 4),
        "accuracy_adj": round(accuracy_adj, 4),
        "green_adj": round(green_adj, 4),
        "scramble_adj": round(scramble_adj, 4),
        "weather_adj": round(weather_adj, 4),
        "dg_fit_adj": round(dg_fit_adj, 4) if dg_fit_adj is not None else None,
        "total_fit": round(total_fit, 4),
    }


# ═══════════════════════════════════════════════════════════════
# Form Regression
# ═══════════════════════════════════════════════════════════════

def calc_form_regression(player_stats):
    """Regress recent form toward career mean.
    Analogous to luck regression in ConRat (composite_model.py line 119).

    If recent_form dict exists in player_stats, blend recent performance
    with career SG:Total using a 60/40 split (keep 60% of recent signal,
    regress 40% toward career mean -- same 0.40 factor as basketball's
    luck * 30 * 0.80).

    Args:
        player_stats: dict with optional 'recent_form' sub-dict (keys:
                      last_4, last_8, last_12, trend) and 'sg_total'

    Returns:
        float: form-adjusted SG estimate
    """
    recent_form = player_stats.get("recent_form")
    career = player_stats.get("sg_total", 0.0)

    if not recent_form or "last_4" not in recent_form:
        return career

    recent = recent_form["last_4"]
    # Keep 60% of recent signal, regress 40% toward career
    return career + (recent - career) * 0.60


# ═══════════════════════════════════════════════════════════════
# Field Strength Adjustment
# ═══════════════════════════════════════════════════════════════

def calc_field_strength_adj(player_stats, field_avg_sg=0.0):
    """Adjust for field strength. Analogous to SOS weighting in ConRat.

    Normalizes the average SG of the field (0.5 SG avg = strong field)
    and returns a multiplier between 0.92 and 1.08.

    Args:
        player_stats: dict (currently unused, reserved for future per-player SOS)
        field_avg_sg: float, average SG:Total of the tournament field (0.0 = avg)

    Returns:
        float: multiplier (0.92 to 1.08)
    """
    field_strength = field_avg_sg / 0.5  # normalize: 0.5 SG avg = strong field
    return clamp(1 + field_strength * 0.15, 0.92, 1.08)
