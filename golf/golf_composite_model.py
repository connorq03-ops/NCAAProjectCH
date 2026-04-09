"""
golf_composite_model.py - Golf composite prediction pipeline

The golf equivalent of composite_model.py (~820 lines) which blends 4 independent
models with dynamic weights. Faithfully mirrors the basketball composite pipeline:

  - gaussian_cdf, calibrate_finish
  - calc_golf_rat (15-layer custom golf rating, analogous to calc_con_rat)
  - model_sg_efficiency (analogous to model_efficiency)
  - model_course_fit (analogous to model_similar_opponents)
  - model_golf_rat (analogous to model_con_rat)
  - Model 4: MC simulation results passed in externally
  - compute_golf_composite (dynamic-weight blending + calibration)
  - predict_field (batch prediction for full tournament field)

All constants, weights, and clamp ranges are tuned for golf (finish positions,
strokes gained, win probabilities) rather than basketball (margins, scores).
"""

import math

from golf.golf_course_profiles import get_course_profile, AVG_SG_TOTAL, COURSES
from golf.golf_course_fit import (
    calc_full_course_fit,
    calc_form_regression,
    calc_field_strength_adj,
    clamp,
)
from golf.golf_elite_players import get_player_info, get_player_strengths
from golf.golf_sim_params import (
    calc_birdie_rate,
    calc_bogey_rate,
    calc_double_bogey_rate,
    calc_eagle_rate,
    calc_round_volatility,
    calc_pressure_modifier,
)


# ─── Helpers ──────────────────────────────────────────────────────────────────

def gaussian_cdf(x):
    """Abramowitz & Stegun approximation. Same as composite_model.py line 27."""
    a1 = 0.254829592
    a2 = -0.284496736
    a3 = 1.421413741
    a4 = -1.453152027
    a5 = 1.061405429
    p = 0.3275911
    sign = -1 if x < 0 else 1
    x = abs(x) / math.sqrt(2)
    t = 1.0 / (1.0 + p * x)
    y = 1.0 - (((((a5 * t + a4) * t) + a3) * t + a2) * t + a1) * t * math.exp(-x * x)
    return 0.5 * (1.0 + sign * y)


def calibrate_finish(raw_finish, coeffs=None):
    """Calibrate predicted finish position. Analogous to calibrate_spread().
    Compress extreme predictions toward the median (35th place)."""
    center = 35.0
    compression = 0.90
    if coeffs:
        center = coeffs.get('center', center)
        compression = coeffs.get('compression', compression)
    deviation = raw_finish - center
    return center + deviation * compression


# ─── Probability Helpers ──────────────────────────────────────────────────────

def _finish_to_probs(predicted_finish, sg_adj):
    """Convert a predicted finish position + SG adjustment to probability dict.

    Uses gaussian_cdf to estimate probability of finishing in various buckets.
    The SG adjustment controls the spread of the distribution.

    Args:
        predicted_finish: float, expected finish position (1-80)
        sg_adj: float, form-adjusted strokes gained (higher = better)

    Returns:
        dict with win_prob, top5_prob, top10_prob, top20_prob, make_cut_prob
    """
    win_prob = clamp(gaussian_cdf((sg_adj - 1.5) / 0.8) * 0.25, 0.001, 0.30)
    top5_prob = clamp(gaussian_cdf((sg_adj - 1.0) / 0.7) * 0.40, 0.005, 0.50)
    top10_prob = clamp(gaussian_cdf((sg_adj - 0.5) / 0.7) * 0.55, 0.01, 0.65)
    top20_prob = clamp(gaussian_cdf((sg_adj - 0.0) / 0.7) * 0.70, 0.02, 0.80)

    # Make-cut probability: most PGA Tour players above ~-0.5 SG make cuts
    make_cut_prob = clamp(gaussian_cdf((sg_adj + 0.5) / 0.6) * 0.95, 0.10, 0.98)

    return {
        'win_prob': round(win_prob, 4),
        'top5_prob': round(top5_prob, 4),
        'top10_prob': round(top10_prob, 4),
        'top20_prob': round(top20_prob, 4),
        'make_cut_prob': round(make_cut_prob, 4),
    }


# ─── GolfRat: 15-Layer Custom Golf Rating ────────────────────────────────────
# Analogous to calc_con_rat() in composite_model.py (lines 108-253, 17 layers)

def calc_golf_rat(player_dict):
    """15-layer custom golf rating. Analogous to calc_con_rat() in composite_model.py.

    Returns: float on 0-10 scale (10 = elite, 0 = replacement level)
    """
    sg_total = player_dict.get("sg_total", 0.0)

    # Layer 1: SG:Total baseline (analogous to AdjEM baseline)
    base = sg_total

    # Layer 2: Luck/form regression (analogous to ConRat layer 1: luck regression)
    recent_form = player_dict.get("recent_form", {})
    recent_sg = recent_form.get("last_4", sg_total) if recent_form else sg_total
    luck_adj = (recent_sg - sg_total) * 0.40
    regressed = recent_sg - luck_adj

    # Layer 3: Field strength adjustment (analogous to ConRat layer 2: SOS weighting)
    avg_field = player_dict.get("avg_field_strength", 0.0)
    sos_mult = clamp(1 + avg_field * 0.15, 0.92, 1.08)
    sos_adj = regressed * sos_mult

    # Layer 4: Cut-making consistency (analogous to ConRat layer 3: record quality)
    cut_rate = player_dict.get("cuts_made_pct", 65.0)
    cut_adj = clamp((cut_rate - 65) * 0.008, -0.5, 0.5)

    # Layer 5: Major/big-event experience (analogous to ConRat layer 4: coach experience)
    info = get_player_info(player_dict.get("_player_name", ""))
    major_bonus = 0.0
    if info:
        majors = info.get("majors_won", 0)
        career_wins = info.get("career_wins", 0)
        major_bonus = clamp(majors * 0.12 + career_wins * 0.02, 0, 0.8)
    else:
        owgr = player_dict.get("owgr_rank", 200)
        if owgr <= 25:
            major_bonus = 0.1

    # Layer 6: Driving distance x accuracy balance (analogous to ConRat layer 6: O/D balance)
    sg_ott = player_dict.get("sg_ott", 0.0)
    driving_acc = player_dict.get("driving_accuracy", 60.0)
    acc_dev = driving_acc - 60.0
    # Reward players who are both long AND accurate
    if sg_ott > 0.3 and acc_dev > 3:
        balance_bonus = clamp(sg_ott * acc_dev * 0.01, 0, 0.5)
    elif sg_ott < -0.3 and acc_dev < -3:
        balance_bonus = clamp(-(sg_ott * acc_dev * 0.005), -0.3, 0)  # both bad = penalty
    else:
        balance_bonus = 0.0

    # Layer 7: Short game floor premium (analogous to ConRat layer 7: defensive floor)
    sg_arg = player_dict.get("sg_arg", 0.0)
    short_game_premium = clamp(sg_arg * 0.15, 0, 0.5) if sg_arg > 0.2 else 0

    # Layer 8: SG split balance (analogous to ConRat layer 8: SOS split)
    sg_app = player_dict.get("sg_app", 0.0)
    sg_putt = player_dict.get("sg_putt", 0.0)
    splits = [sg_ott, sg_app, sg_arg, sg_putt]
    split_range = max(splits) - min(splits)
    # Reward well-rounded players (small range between best and worst SG)
    if split_range < 0.8 and sg_total > 0.5:
        split_bonus = clamp((0.8 - split_range) * 0.3, 0, 0.3)
    else:
        split_bonus = 0.0

    # Layer 9: Experience & continuity (analogous to ConRat layer 9)
    # Use tier as proxy for experience
    experience_adj = 0.0
    if info:
        tier = info.get("tier", "unknown")
        if tier == "elite":
            experience_adj = 0.3
        elif tier == "star":
            experience_adj = 0.2
        elif tier == "key":
            experience_adj = 0.1

    # Layer 10: Putting variance (analogous to ConRat layer 10: 3PT variance)
    # High putting variance = risky (can win or miss cuts)
    putt_var_adj = 0.0
    if abs(sg_putt) > 0.5:
        putt_var_adj = -abs(sg_putt) * 0.05  # slight penalty for extreme putting (volatile)

    # Layer 11: Scrambling discipline (analogous to ConRat layer 11: TO discipline)
    scrambling = player_dict.get("scrambling_pct", 58.0)
    scramble_adj = clamp((scrambling - 58) * 0.008, -0.3, 0.3)

    # Layer 12: Scoring volatility (analogous to ConRat layer 12: tempo volatility)
    # Consistent scorers get a bonus in tournament format
    consistency = player_dict.get("consistency_score", 0.5)
    consistency_adj = clamp((consistency - 0.5) * 0.4, -0.3, 0.3)

    # Layer 13: Seasonal trend (analogous to ConRat layer 13: possession length)
    trend = recent_form.get("trend", 0.0) if recent_form else 0.0
    trend_adj = clamp(trend * 0.5, -0.3, 0.3)

    # Layer 14: Rest/fatigue factor (analogous to ConRat layer 15: bench depth)
    # Players who play fewer events may be fresher
    fatigue = player_dict.get("fatigue_factor", 0.5)
    fatigue_adj = clamp((0.5 - fatigue) * 0.3, -0.2, 0.2)

    # Layer 15: Aggregate + final scaling (analogous to ConRat layer 16-17)
    raw = (sos_adj + cut_adj + major_bonus + balance_bonus
           + short_game_premium + split_bonus + experience_adj
           + putt_var_adj + scramble_adj + consistency_adj
           + trend_adj + fatigue_adj)

    # Power curve scaling to 0-10 range (same pattern as ConRat's 0-30 range)
    sign = 1 if raw >= 0 else -1
    abs_raw = abs(raw)
    golf_rat = sign * (abs_raw ** 0.82) * (10 / (8 ** 0.82))

    # Shift to 0-10 range (center at 5)
    golf_rat = clamp(golf_rat + 5.0, 0.0, 10.0)

    return round(golf_rat * 10) / 10


# ─── Model 1: SG Efficiency ──────────────────────────────────────────────────
# Analogous to model_efficiency() in basketball (composite_model.py lines 376-415)

def model_sg_efficiency(player_stats, course_profile):
    """Raw SG-based projection. Analogous to model_efficiency() in basketball.

    Steps:
      1. Form-regress SG:Total via calc_form_regression()
      2. Apply field strength multiplier via calc_field_strength_adj()
      3. Convert adjusted SG to expected finish position
      4. Derive win/placement probabilities

    Args:
        player_stats: dict with SG splits, recent_form, etc.
        course_profile: dict from COURSES

    Returns:
        dict with predicted_finish, win_prob, top5_prob, top10_prob,
              top20_prob, make_cut_prob, details
    """
    # 1. Form-regressed SG:Total
    sg_regressed = calc_form_regression(player_stats)

    # 2. Field strength adjustment
    field_avg = player_stats.get("avg_field_strength", 0.0)
    fs_mult = calc_field_strength_adj(player_stats, field_avg)
    sg_adj = sg_regressed * fs_mult

    # 3. Convert SG to finish position
    #   SG +2.5 -> ~3rd,  SG +1.5 -> ~12th,  SG +0.5 -> ~30th
    #   SG  0.0 -> ~40th, SG -0.5 -> ~55th,  SG -1.5 -> ~70th
    predicted_finish = clamp(40 - sg_adj * 15, 1, 80)

    # 4. Probabilities
    probs = _finish_to_probs(predicted_finish, sg_adj)

    return {
        'predicted_finish': round(predicted_finish, 2),
        **probs,
        'details': {
            'sg_regressed': round(sg_regressed, 4),
            'fs_mult': round(fs_mult, 4),
            'sg_adj': round(sg_adj, 4),
        },
    }


# ─── Model 2: Course Fit ─────────────────────────────────────────────────────
# Analogous to model_similar_opponents() in basketball (composite_model.py lines 421-463)

def model_course_fit(player_stats, course_profile, weather=None):
    """Course-specific projection. Analogous to model_similar_opponents() in basketball.

    Steps:
      1. Compute full course fit (SG weights, history, weather, etc.)
      2. Use total_fit score to adjust baseline SG projection
      3. Add course history bonus
      4. Convert to finish position and probabilities

    Args:
        player_stats: dict with SG splits, course_history, etc.
        course_profile: dict from COURSES
        weather: optional weather impact dict

    Returns:
        dict with predicted_finish, win_prob, top5_prob, top10_prob,
              top20_prob, make_cut_prob, details
    """
    # 1. Full course fit
    fit = calc_full_course_fit(player_stats, course_profile, weather)

    # 2. Baseline SG + total fit adjustment
    sg_total = player_stats.get("sg_total", 0.0)
    total_fit = fit.get("total_fit", 0.0)

    # 3. Course history bonus (already in fit as history_adj, add extra weight)
    history_adj = fit.get("history_adj", 0.0)

    # Combined adjusted SG
    sg_combined = sg_total + total_fit

    # 4. Convert to finish position (slightly different coefficient than Model 1
    #    to reflect course-specific nature)
    predicted_finish = clamp(40 - sg_combined * 14, 1, 80)

    # Probabilities based on combined SG
    probs = _finish_to_probs(predicted_finish, sg_combined)

    return {
        'predicted_finish': round(predicted_finish, 2),
        **probs,
        'details': {
            'sg_total': round(sg_total, 4),
            'total_fit': round(total_fit, 4),
            'base_fit': fit.get('base_fit', 0.0),
            'history_adj': round(history_adj, 4),
            'weather_adj': fit.get('weather_adj', 0.0),
            'sg_combined': round(sg_combined, 4),
        },
    }


# ─── Model 3: GolfRat ────────────────────────────────────────────────────────
# Analogous to model_con_rat() in basketball (composite_model.py lines 469-501)

def model_golf_rat(player_stats, course_profile):
    """GolfRat-based projection. Analogous to model_con_rat() in basketball.

    Steps:
      1. Compute 15-layer GolfRat rating (0-10 scale)
      2. Convert to finish position
      3. Derive probabilities

    Args:
        player_stats: dict with SG splits, recent_form, tier info, etc.
        course_profile: dict from COURSES

    Returns:
        dict with predicted_finish, win_prob, top5_prob, top10_prob,
              top20_prob, make_cut_prob, golf_rat_score, details
    """
    # 1. 15-layer GolfRat
    golf_rat_score = calc_golf_rat(player_stats)

    # 2. Convert GolfRat (0-10) to finish position
    #   10 -> ~0th (elite), 7.5 -> ~11th, 5.0 -> ~22nd,
    #   2.5 -> ~33rd, 0 -> ~45th
    predicted_finish = clamp(45 - golf_rat_score * 4.5, 1, 80)

    # 3. Probabilities — use GolfRat deviation from average (5.0) as SG proxy
    sg_proxy = (golf_rat_score - 5.0) / 2.0  # map 0-10 scale back to ~SG range
    probs = _finish_to_probs(predicted_finish, sg_proxy)

    return {
        'predicted_finish': round(predicted_finish, 2),
        **probs,
        'golf_rat_score': golf_rat_score,
        'details': {
            'golf_rat_score': golf_rat_score,
            'sg_proxy': round(sg_proxy, 4),
        },
    }


# ─── Composite Blending ──────────────────────────────────────────────────────
# Analogous to compute_composite() in basketball (composite_model.py lines 507-640)

def compute_golf_composite(sg_eff, course_fit, golf_rat, mc,
                           player_stats, course_profile,
                           weight_overrides=None, context=None):
    """Blend 4 model outputs with dynamic weights, apply finish calibration.

    Analogous to compute_composite() in basketball (composite_model.py lines 504-540).

    Args:
        sg_eff: dict from model_sg_efficiency()
        course_fit: dict from model_course_fit()
        golf_rat: dict from model_golf_rat()
        mc: dict from golf_mc_engine (must have predicted_finish, win_prob, etc.)
        player_stats: raw player stats dict
        course_profile: dict from COURSES
        weight_overrides: optional dict {'sg_efficiency': float, 'course_fit': float,
                          'golf_rat': float, 'mc': float}
        context: optional string ('major', 'windy', 'signature', etc.)

    Returns:
        dict with predicted_finish, win_prob, top5_prob, top10_prob,
              top20_prob, make_cut_prob, weights_used, golf_rat_score,
              model_details
    """
    # ── Base weights ──
    if weight_overrides:
        w_sg = weight_overrides.get('sg_efficiency', 0.25)
        w_fit = weight_overrides.get('course_fit', 0.25)
        w_rat = weight_overrides.get('golf_rat', 0.25)
        w_mc = weight_overrides.get('mc', 0.25)
    else:
        w_sg = 0.25
        w_fit = 0.25
        w_rat = 0.25
        w_mc = 0.25

    # ── Dynamic weight adjustments based on data quality ──
    # (same pattern as basketball: shift weights based on available data)

    # Rich SG split data -> boost course_fit and MC (more accurate projections)
    has_sg_splits = all(
        player_stats.get(k) is not None
        for k in ("sg_ott", "sg_app", "sg_arg", "sg_putt")
    )
    if has_sg_splits:
        w_fit += 0.03
        w_mc += 0.03
        w_sg -= 0.03
        w_rat -= 0.03

    # Course history available -> boost course_fit
    if player_stats.get("course_history"):
        w_fit += 0.05
        w_sg -= 0.025
        w_rat -= 0.025

    # Player in elite_players database -> boost golf_rat (more reliable rating)
    player_info = get_player_info(player_stats.get("_player_name", ""))
    if player_info:
        w_rat += 0.03
        w_sg -= 0.015
        w_mc -= 0.015

    # MC sim had enough iterations -> boost MC
    mc_iterations = mc.get("iterations", 0)
    if mc_iterations >= 5000:
        w_mc += 0.05
        w_sg -= 0.025
        w_rat -= 0.025
    elif mc_iterations < 1000 and mc_iterations > 0:
        w_mc -= 0.05
        w_sg += 0.025
        w_rat += 0.025

    # Context-based adjustments
    if context == "major":
        # Experience and course fit matter more at majors
        w_rat += 0.04
        w_fit += 0.04
        w_sg -= 0.04
        w_mc -= 0.04
    elif context == "windy":
        # Course fit captures weather resilience
        w_fit += 0.06
        w_sg -= 0.02
        w_rat -= 0.02
        w_mc -= 0.02
    elif context == "signature":
        # Strong fields — SG efficiency matters more
        w_sg += 0.03
        w_mc += 0.03
        w_fit -= 0.03
        w_rat -= 0.03

    # ── Normalize weights to sum to 1.0 ──
    w_total = w_sg + w_fit + w_rat + w_mc
    w_sg /= w_total
    w_fit /= w_total
    w_rat /= w_total
    w_mc /= w_total

    # ── Blend predicted finish positions ──
    raw_finish = (w_sg * sg_eff['predicted_finish']
                  + w_fit * course_fit['predicted_finish']
                  + w_rat * golf_rat['predicted_finish']
                  + w_mc * mc['predicted_finish'])

    # Apply calibration to compress extremes
    composite_finish = calibrate_finish(raw_finish)

    # ── Blend probabilities ──
    composite_win = (w_sg * sg_eff['win_prob']
                     + w_fit * course_fit['win_prob']
                     + w_rat * golf_rat['win_prob']
                     + w_mc * mc['win_prob'])
    composite_top5 = (w_sg * sg_eff['top5_prob']
                      + w_fit * course_fit['top5_prob']
                      + w_rat * golf_rat['top5_prob']
                      + w_mc * mc['top5_prob'])
    composite_top10 = (w_sg * sg_eff['top10_prob']
                       + w_fit * course_fit['top10_prob']
                       + w_rat * golf_rat['top10_prob']
                       + w_mc * mc['top10_prob'])
    composite_top20 = (w_sg * sg_eff['top20_prob']
                       + w_fit * course_fit['top20_prob']
                       + w_rat * golf_rat['top20_prob']
                       + w_mc * mc['top20_prob'])
    composite_cut = (w_sg * sg_eff['make_cut_prob']
                     + w_fit * course_fit['make_cut_prob']
                     + w_rat * golf_rat['make_cut_prob']
                     + w_mc * mc['make_cut_prob'])

    # ── Model agreement score (0-100%) ──
    # Same pattern as basketball composite (lines 609-619)
    model_finishes = [
        sg_eff['predicted_finish'],
        course_fit['predicted_finish'],
        golf_rat['predicted_finish'],
        mc['predicted_finish'],
    ]
    finish_mean = sum(model_finishes) / 4
    finish_variance = sum((f - finish_mean) ** 2 for f in model_finishes) / 4
    # Normalize: max expected variance is ~400 (20-position spread)
    model_agreement = round(max(0, (1 - finish_variance / 400)) * 100)
    if model_agreement >= 85:
        confidence = 'High'
    elif model_agreement >= 65:
        confidence = 'Moderate'
    else:
        confidence = 'Low'

    # Extract GolfRat score
    golf_rat_score = golf_rat.get('golf_rat_score', 5.0)

    return {
        'predicted_finish': round(composite_finish, 2),
        'win_prob': round(composite_win, 4),
        'top5_prob': round(composite_top5, 4),
        'top10_prob': round(composite_top10, 4),
        'top20_prob': round(composite_top20, 4),
        'make_cut_prob': round(composite_cut, 4),
        'weights_used': {
            'sg_efficiency': round(w_sg, 4),
            'course_fit': round(w_fit, 4),
            'golf_rat': round(w_rat, 4),
            'mc': round(w_mc, 4),
        },
        'golf_rat_score': golf_rat_score,
        'model_agreement': model_agreement,
        'confidence': confidence,
        'model_details': {
            'sg_efficiency': sg_eff,
            'course_fit': course_fit,
            'golf_rat': golf_rat,
            'mc': mc,
        },
    }


# ─── Field-Level Prediction ──────────────────────────────────────────────────

def predict_field(players, course_profile, mc_results=None, weather=None,
                  weight_overrides=None, context=None):
    """Run composite prediction for an entire tournament field.

    Analogous to running compute_composite() across all matchups in basketball.

    Args:
        players: list of player stat dicts (from prefetch_all_player_data or similar)
        course_profile: dict from COURSES
        mc_results: optional dict keyed by player name -> MC result dict.
                    If None, a placeholder MC result is used.
        weather: optional weather impact dict
        weight_overrides: optional dict passed to compute_golf_composite()
        context: optional string ('major', 'windy', 'signature', etc.)

    Returns:
        list of dicts sorted by predicted_finish (best first), each containing:
          player_name, predicted_finish, win_prob, top5_prob, top10_prob,
          top20_prob, make_cut_prob, golf_rat_score, weights_used,
          model_agreement, confidence, model_details
    """
    results = []

    for player_stats in players:
        player_name = player_stats.get("_player_name", "Unknown")

        # Run 3 internal models
        sg_eff = model_sg_efficiency(player_stats, course_profile)
        cf = model_course_fit(player_stats, course_profile, weather)
        gr = model_golf_rat(player_stats, course_profile)

        # Get MC results or use placeholder
        if mc_results and player_name in mc_results:
            mc = mc_results[player_name]
        else:
            # Placeholder MC: use average of the 3 models as stand-in
            avg_finish = (sg_eff['predicted_finish'] + cf['predicted_finish']
                          + gr['predicted_finish']) / 3
            avg_win = (sg_eff['win_prob'] + cf['win_prob'] + gr['win_prob']) / 3
            avg_top5 = (sg_eff['top5_prob'] + cf['top5_prob'] + gr['top5_prob']) / 3
            avg_top10 = (sg_eff['top10_prob'] + cf['top10_prob'] + gr['top10_prob']) / 3
            avg_top20 = (sg_eff['top20_prob'] + cf['top20_prob'] + gr['top20_prob']) / 3
            avg_cut = (sg_eff['make_cut_prob'] + cf['make_cut_prob']
                       + gr['make_cut_prob']) / 3
            mc = {
                'predicted_finish': round(avg_finish, 2),
                'win_prob': round(avg_win, 4),
                'top5_prob': round(avg_top5, 4),
                'top10_prob': round(avg_top10, 4),
                'top20_prob': round(avg_top20, 4),
                'make_cut_prob': round(avg_cut, 4),
                'iterations': 0,
            }

        # Composite blend
        composite = compute_golf_composite(
            sg_eff, cf, gr, mc,
            player_stats, course_profile,
            weight_overrides=weight_overrides,
            context=context,
        )

        results.append({
            'player_name': player_name,
            **composite,
        })

    # Sort by predicted finish (best first)
    results.sort(key=lambda r: r['predicted_finish'])

    return results


# ─── Self-test ────────────────────────────────────────────────────────────────

def self_test():
    """Run validation tests. Returns dict of {test_name: 'PASS'/'FAIL (reason)'}."""
    results = {}

    # --- gaussian_cdf ---
    cases_cdf = [(0, 0.5, 0.01), (1, 0.8413, 0.01), (-1, 0.1587, 0.01)]
    for inp, expected, tol in cases_cdf:
        got = gaussian_cdf(inp)
        ok = abs(got - expected) < tol
        results[f'gaussian_cdf({inp})'] = (
            'PASS' if ok else f'FAIL (got {got:.4f}, expected ~{expected})'
        )

    # --- calibrate_finish ---
    cf35 = calibrate_finish(35.0)
    ok = abs(cf35 - 35.0) < 0.01  # center should stay at center
    results['calibrate_finish(35) == 35'] = (
        'PASS' if ok else f'FAIL (got {cf35:.3f})'
    )

    cf5 = calibrate_finish(5.0)
    ok = cf5 > 5.0 and cf5 < 35.0  # compressed toward center
    results['calibrate_finish(5) compressed toward 35'] = (
        'PASS' if ok else f'FAIL (got {cf5:.3f})'
    )

    cf70 = calibrate_finish(70.0)
    ok = cf70 < 70.0 and cf70 > 35.0  # compressed toward center
    results['calibrate_finish(70) compressed toward 35'] = (
        'PASS' if ok else f'FAIL (got {cf70:.3f})'
    )

    # --- calc_golf_rat ---
    elite_player = {
        "sg_total": 2.0,
        "sg_ott": 0.5, "sg_app": 0.7, "sg_arg": 0.4, "sg_putt": 0.4,
        "driving_accuracy": 65.0, "scrambling_pct": 62.0,
        "cuts_made_pct": 90.0, "consistency_score": 0.7,
        "fatigue_factor": 0.3,
        "_player_name": "Scottie Scheffler",
        "recent_form": {"last_4": 2.2, "trend": 0.1},
    }
    gr_elite = calc_golf_rat(elite_player)
    ok = gr_elite > 7.0  # elite player should rate highly
    results['calc_golf_rat(elite) > 7.0'] = (
        'PASS' if ok else f'FAIL (got {gr_elite})'
    )

    avg_player = {
        "sg_total": 0.0,
        "sg_ott": 0.0, "sg_app": 0.0, "sg_arg": 0.0, "sg_putt": 0.0,
        "driving_accuracy": 60.0, "scrambling_pct": 58.0,
        "cuts_made_pct": 65.0, "consistency_score": 0.5,
        "fatigue_factor": 0.5,
        "_player_name": "Unknown Player",
    }
    gr_avg = calc_golf_rat(avg_player)
    ok = 4.0 < gr_avg < 6.0  # average player should be near center
    results['calc_golf_rat(average) ~5.0'] = (
        'PASS' if ok else f'FAIL (got {gr_avg})'
    )

    # --- model_sg_efficiency ---
    augusta = COURSES.get("augusta_national", {})
    eff = model_sg_efficiency(elite_player, augusta)
    ok = 1 <= eff['predicted_finish'] <= 20  # elite player at Augusta
    results['model_sg_efficiency(elite, augusta) top 20'] = (
        'PASS' if ok else f'FAIL (finish={eff["predicted_finish"]})'
    )
    ok = eff['win_prob'] > 0.01  # should have non-trivial win prob
    results['model_sg_efficiency(elite) win_prob > 1%'] = (
        'PASS' if ok else f'FAIL (win_prob={eff["win_prob"]})'
    )

    # --- model_course_fit ---
    cf_result = model_course_fit(elite_player, augusta)
    ok = 1 <= cf_result['predicted_finish'] <= 25
    results['model_course_fit(elite, augusta) top 25'] = (
        'PASS' if ok else f'FAIL (finish={cf_result["predicted_finish"]})'
    )

    # --- model_golf_rat ---
    gr_result = model_golf_rat(elite_player, augusta)
    ok = 1 <= gr_result['predicted_finish'] <= 20
    results['model_golf_rat(elite, augusta) top 20'] = (
        'PASS' if ok else f'FAIL (finish={gr_result["predicted_finish"]})'
    )
    ok = gr_result['golf_rat_score'] > 7.0
    results['model_golf_rat(elite) score > 7.0'] = (
        'PASS' if ok else f'FAIL (score={gr_result["golf_rat_score"]})'
    )

    # --- compute_golf_composite weights sum to 1 ---
    mc_fake = {
        'predicted_finish': 10.0,
        'win_prob': 0.08,
        'top5_prob': 0.25,
        'top10_prob': 0.45,
        'top20_prob': 0.70,
        'make_cut_prob': 0.95,
        'iterations': 5000,
    }
    comp = compute_golf_composite(eff, cf_result, gr_result, mc_fake,
                                  elite_player, augusta)
    w_sum = sum(comp['weights_used'].values())
    ok = abs(w_sum - 1.0) < 0.001
    results['composite_weights_sum_to_1'] = (
        'PASS' if ok else f'FAIL (sum={w_sum:.4f})'
    )

    ok = comp['predicted_finish'] < 25  # elite player should predict top 25
    results['composite_elite_top25'] = (
        'PASS' if ok else f'FAIL (finish={comp["predicted_finish"]})'
    )

    ok = comp['win_prob'] > 0.01
    results['composite_elite_win_prob > 1%'] = (
        'PASS' if ok else f'FAIL (win_prob={comp["win_prob"]})'
    )

    # --- predict_field ---
    field = [elite_player, avg_player]
    field_results = predict_field(field, augusta)
    ok = len(field_results) == 2
    results['predict_field returns 2 players'] = (
        'PASS' if ok else f'FAIL (got {len(field_results)})'
    )
    # Elite should be ranked ahead of average
    ok = field_results[0]['player_name'] == "Scottie Scheffler"
    results['predict_field elite ranked first'] = (
        'PASS' if ok else f'FAIL (first={field_results[0]["player_name"]})'
    )

    return results


if __name__ == '__main__':
    print('=' * 60)
    print('  golf_composite_model.py - Self-Test')
    print('=' * 60)
    test_results = self_test()
    all_pass = True
    for name, result in test_results.items():
        status = 'PASS' if result == 'PASS' else 'FAIL'
        if status == 'FAIL':
            all_pass = False
        print(f'  [{status}] {name}: {result}')
    print()
    if all_pass:
        print('All tests PASSED.')
    else:
        print('Some tests FAILED!')
