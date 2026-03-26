"""
mc_engine.py - Python port of static/mc-worker.js

Faithful possession-level Monte Carlo basketball simulation engine.
Ported for server-side bracket-wide tournament simulation.
"""

import random
import math
from typing import List, Optional


# ─── Utility Functions ───────────────────────────────────────────────────────

def clamp(val: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, val))


def rand_normal(mean: float, sd: float) -> float:
    return random.gauss(mean, sd)


# ─── Game Style Generation ───────────────────────────────────────────────────

def generate_game_style(base_volatility: float, style_bias: float = 0) -> dict:
    """Generate correlated game-style variance factors for one team in one game."""
    interior_axis = rand_normal(style_bias, 1.0) * base_volatility
    discipline_axis = rand_normal(0, 1.0) * base_volatility
    residual_sd = 0.8
    return {
        "fg2_adj": interior_axis * 1.8 - discipline_axis * 0.5 + rand_normal(0, residual_sd),
        "fg3_adj": -interior_axis * 1.2 - discipline_axis * 0.4 + rand_normal(0, residual_sd),
        "to_adj": interior_axis * 0.3 + discipline_axis * 1.5 + rand_normal(0, residual_sd * 0.5),
        "or_adj": interior_axis * 1.0 + discipline_axis * 0.6 + rand_normal(0, residual_sd),
        "rate3_adj": -interior_axis * 2.5 + rand_normal(0, residual_sd * 0.5),
        "ftr_adj": interior_axis * 1.5 + rand_normal(0, residual_sd * 0.3),
        "style_label": "interior" if interior_axis > 0.5 else ("perimeter" if interior_axis < -0.5 else "balanced"),
        "discipline_label": "sloppy" if discipline_axis > 0.5 else ("disciplined" if discipline_axis < -0.5 else "neutral"),
    }


# ─── Star Player Impact ─────────────────────────────────────────────────────

def calc_star_impact(stars: Optional[List[dict]]) -> dict:
    """Compute star usage rate and FG bonuses from player impact tiers."""
    if not stars:
        return {"usage": 0, "fg2_bonus": 0, "fg3_bonus": 0}
    total_usage = 0.0
    w_fg2 = 0.0
    w_fg3 = 0.0
    for s in stars:
        imp = s.get("impact", 5)
        u = 0.30 if imp >= 9 else (0.22 if imp >= 8 else (0.15 if imp >= 7 else 0.08))
        total_usage += u
        w_fg2 += (3.0 if imp >= 9 else (2.0 if imp >= 8 else 1.0)) * u
        w_fg3 += (2.0 if imp >= 9 else (1.5 if imp >= 8 else 0.5)) * u
    cap = min(total_usage, 0.45)
    return {
        "usage": cap,
        "fg2_bonus": w_fg2 / total_usage if total_usage > 0 else 0,
        "fg3_bonus": w_fg3 / total_usage if total_usage > 0 else 0,
    }


# ─── Core Half Simulation ───────────────────────────────────────────────────

def sim_half(cfg: dict) -> dict:
    """Simulate one half of basketball at possession granularity.

    Args:
        cfg: Config dict with keys: half_poss, fg2, fg3, to_pct, or_pct, rate3,
             ftr, ft_pct, def_steal_rate, star_usage, star_fg2, star_fg3,
             init_mom, bench_depth, is_second_half, incoming_lead, def_profile,
             star_foul_state, star_foul_proneness, foul_climate, streakiness
    """
    half_poss = cfg["half_poss"]
    fg2 = cfg["fg2"]
    fg3 = cfg["fg3"]
    to_pct = cfg["to_pct"]
    or_pct = cfg["or_pct"]
    rate3 = cfg["rate3"]
    ftr = cfg["ftr"]
    ft_pct = cfg["ft_pct"]
    def_steal_rate = cfg["def_steal_rate"]
    star_usage = cfg["star_usage"]
    star_fg2 = cfg["star_fg2"]
    star_fg3 = cfg["star_fg3"]
    init_mom = cfg["init_mom"]
    bench_depth = cfg["bench_depth"]
    is_second_half = cfg["is_second_half"]
    incoming_lead = cfg["incoming_lead"]
    def_profile = cfg.get("def_profile") or {"perimeter": 0, "interior": 0, "overall": 0}
    star_foul_state = cfg.get("star_foul_state")
    star_foul_proneness = cfg.get("star_foul_proneness", 0)
    foul_climate = cfg.get("foul_climate", 1.0)
    streakiness = cfg.get("streakiness", 1.0)

    points = 0
    poss_used = 0
    makes2 = 0
    makes3 = 0
    tos = 0
    ft_made = 0
    ft_att = 0
    orebs = 0
    attempts = 0
    transition_pts = 0
    poss_left = round(half_poss)
    max_poss = poss_left + 10
    mom = init_mom
    def_fouls = 0
    total_fatigue_penalty = 0.0
    rest_poss_count = 0

    # Game Clock Phases
    total_half_poss = round(half_poss)
    PHASE_LATE_START = 0.75
    PHASE_CRUNCH_START = 0.90
    running_lead = incoming_lead or 0
    crunch_time_poss = 0
    desperation_poss = 0
    intentional_foul_poss = 0

    # Plan 09: Referee Foul Climate (defined BEFORE Plan 06 to fix scoping)
    ref_climate = foul_climate or 1.0
    bonus_reached_poss = -1

    # Plan 06: Player Foul Trouble
    MAX_FOULS = 5
    FOUL_SIT_THRESHOLD_H1 = 2
    FOUL_SIT_THRESHOLD_H2 = 4
    FOUL_RETURN_PCT = 0.80
    star_fouls = star_foul_state["fouls"] if star_foul_state else 0
    star_is_sitting = star_foul_state["is_sitting"] if star_foul_state else False
    star_sat_poss = 0
    star_fouled_out = False
    base_star_foul_rate = (0.035 + (star_foul_proneness or 0) * 0.02) * math.sqrt(ref_climate)

    # Plan 08: Three-Point Streak Tracking
    team_streakiness = streakiness or 1.0
    streak3 = 0
    STREAK_DECAY = 0.65
    HOT_BONUS_PER = 1.2 * team_streakiness
    COLD_PENALTY_PER = 1.0 * team_streakiness
    MAX_STREAK_EFFECT = 5.0 * team_streakiness
    STREAK_RATE_BONUS = 0.8 * team_streakiness
    STREAK_RATE_PENALTY = 0.6 * team_streakiness
    max_hot_streak = 0
    max_cold_streak = 0
    hot_possessions = 0
    cold_possessions = 0

    while poss_left > 0 and poss_used < max_poss:
        poss_left -= 1
        poss_used += 1

        # Game State Awareness
        progress_pct = poss_used / total_half_poss if total_half_poss > 0 else 0
        is_late_half = is_second_half and progress_pct >= PHASE_LATE_START
        is_crunch_time = is_second_half and progress_pct >= PHASE_CRUNCH_START
        if is_crunch_time:
            crunch_time_poss += 1

        gs_3rate_adj = 0
        gs_to_adj = 0
        gs_ftr_adj = 0
        gs_fg_penalty = 0

        if is_second_half:
            deficit = -running_lead
            if is_crunch_time and deficit >= 6:
                desperation_scale = clamp((deficit - 5) / 15, 0, 1)
                gs_3rate_adj = 8 + desperation_scale * 12
                gs_to_adj = 1.5 + desperation_scale * 2
                gs_fg_penalty = 2 + desperation_scale * 3
                desperation_poss += 1
            elif is_crunch_time and deficit >= 3:
                gs_3rate_adj = 5
                gs_to_adj = 0.8
                gs_fg_penalty = 1
            elif is_late_half and deficit >= 8:
                gs_3rate_adj = 4
                gs_to_adj = 0.5
            elif is_crunch_time and running_lead >= 6:
                gs_3rate_adj = -6
                gs_to_adj = -1
                gs_ftr_adj = 4
                # Plan 07: Leading by 8+ in crunch → burn clock
                if running_lead >= 8 and random.random() < 0.15:
                    if random.random() * 100 < fg2 * 0.60:
                        points += 2
                        makes2 += 1
                        running_lead += 2
                        mom = min(mom + 0.3, 3)
                    poss_left -= 1
                    poss_used += 1
                    streak3 = round(streak3 * STREAK_DECAY)
                    continue
            elif is_crunch_time and (-running_lead) >= 6:
                # Plan 07: Trailing by 6+ in crunch → push pace
                if random.random() < 0.10:
                    poss_left += 1
            elif is_late_half and running_lead >= 10:
                gs_3rate_adj = -3
                gs_to_adj = -0.5

        # Fatigue Curve
        fatigue_onset_pct = 0.55 + (bench_depth / 100) * 0.15
        fatigue_progress = max(0, (poss_used / half_poss) - fatigue_onset_pct) / (1 - fatigue_onset_pct) if fatigue_onset_pct < 1 else 0
        half_multiplier = 1.4 if is_second_half else 1.0
        fatigue_penalty = fatigue_progress * half_multiplier * 0.06
        fatigue_fg_mod = 1 - fatigue_penalty
        fatigue_to_mod = 1 + fatigue_penalty * 0.5
        total_fatigue_penalty += fatigue_penalty

        # Plan 06: Star Foul Trouble Check
        if not star_is_sitting and not star_fouled_out and random.random() < base_star_foul_rate:
            star_fouls += 1
            if star_fouls >= MAX_FOULS:
                star_fouled_out = True
                star_is_sitting = True
            elif not is_second_half and star_fouls >= FOUL_SIT_THRESHOLD_H1:
                star_is_sitting = True
            elif is_second_half and star_fouls >= FOUL_SIT_THRESHOLD_H2:
                star_is_sitting = True

        # Star Return from Foul Trouble
        if star_is_sitting and not star_fouled_out:
            return_threshold = PHASE_CRUNCH_START - 0.05 if is_second_half else FOUL_RETURN_PCT
            if progress_pct >= return_threshold:
                star_is_sitting = False
        if star_is_sitting:
            star_sat_poss += 1

        # Bench Rotation: Star Rest
        rest_window_start = int(half_poss * 0.28)
        rest_window_end = int(half_poss * 0.52)
        in_rest_window = rest_window_start <= poss_used <= rest_window_end
        rest_prob = clamp(0.15 + (bench_depth / 100) * 0.65, 0.15, 0.75) if in_rest_window else 0
        is_rest_poss = random.random() < rest_prob
        if is_rest_poss:
            rest_poss_count += 1
        effective_star_usage = star_usage * 0.15 if is_rest_poss else star_usage

        # Plan 06: Override star usage when sitting due to foul trouble
        foul_trouble_star_usage = star_usage * 0.10 if star_is_sitting else effective_star_usage

        is_star = random.random() < foul_trouble_star_usage
        s_fg2 = star_fg2 if is_star else 0
        s_fg3 = star_fg3 if is_star else 0

        # Defensive Disruption: Shot Quality
        def_p = def_profile
        is_disrupted_poss = random.random() < def_p["overall"] * 0.6
        disrupt3_mod = -(def_p["perimeter"] * 5.0) if is_disrupted_poss else 0
        disrupt2_mod = -(def_p["interior"] * 4.0) if is_disrupted_poss else 0
        disrupt_star_mod = (1 - def_p["overall"] * 0.5) if is_disrupted_poss else 1.0

        # Plan 08: Elite perimeter defense cools hot streaks faster
        if streak3 > 0 and is_disrupted_poss and def_p["perimeter"] > 0.4:
            streak3 = max(0, streak3 - 1)

        mom_fg = mom * 0.4

        # Turnover check
        if random.random() * 100 < (to_pct + gs_to_adj) * fatigue_to_mod:
            tos += 1
            mom = max(mom - 1, -2)
            if random.random() < (def_steal_rate / max(to_pct, 8)) * 0.65:
                r = random.random()
                if r < 0.55:
                    transition_pts += 2
                    running_lead -= 2
                elif r < 0.70:
                    transition_pts += 3
                    running_lead -= 3
            streak3 = round(streak3 * STREAK_DECAY)
            continue

        # Plan 09: Foul probability scaled by referee climate
        base_foul_prob = 0.20 * ref_climate
        drew_foul = random.random() < base_foul_prob
        if drew_foul:
            def_fouls += 1

        if drew_foul and def_fouls >= 7 and bonus_reached_poss == -1:
            bonus_reached_poss = poss_used

        # Bonus free throws
        if drew_foul and def_fouls >= 7 and random.random() < 0.45:
            bonus_made = 0
            if def_fouls >= 10:
                for _ in range(2):
                    ft_att += 1
                    if random.random() * 100 < ft_pct:
                        points += 1
                        ft_made += 1
                        bonus_made += 1
                        running_lead += 1
            else:
                ft_att += 1
                if random.random() * 100 < ft_pct:
                    points += 1
                    ft_made += 1
                    bonus_made += 1
                    running_lead += 1
                    ft_att += 1
                    if random.random() * 100 < ft_pct:
                        points += 1
                        ft_made += 1
                        bonus_made += 1
                        running_lead += 1
            mom = min(mom + 0.5, 3) if bonus_made > 0 else max(mom - 0.5, -2)
            streak3 = round(streak3 * STREAK_DECAY)
            continue

        # Plan 09: FTR-based shooting foul, scaled by referee foul climate
        if not drew_foul and random.random() < (ftr * ref_climate) / 100 * 0.38:
            def_fouls += 1
            num_fts = 3 if random.random() < 0.25 else 2
            made = 0
            for _ in range(num_fts):
                ft_att += 1
                if random.random() * 100 < ft_pct:
                    points += 1
                    ft_made += 1
                    made += 1
                    running_lead += 1
            mom = min(mom + 0.5, 3) if made > 0 else max(mom - 0.5, -2)
            streak3 = round(streak3 * STREAK_DECAY)
            continue

        # Intentional Fouling (when leading in crunch time)
        if is_crunch_time and is_second_half and running_lead >= 6 and gs_ftr_adj > 0 and intentional_foul_poss < 6:
            if random.random() * 100 < gs_ftr_adj * 6:
                intentional_foul_poss += 1
                def_fouls += 1
                made = 0
                for _ in range(2):
                    ft_att += 1
                    if random.random() * 100 < ft_pct:
                        points += 1
                        ft_made += 1
                        made += 1
                        running_lead += 1
                mom = min(mom + 0.3, 3) if made > 0 else max(mom - 0.3, -2)
                streak3 = round(streak3 * STREAK_DECAY)
                continue

        attempts += 1

        # Plan 08: Streak-modified 3PT rate and accuracy
        streak_rate_adj = min(streak3 * STREAK_RATE_BONUS, 4) if streak3 > 0 else max(streak3 * STREAK_RATE_PENALTY, -3)
        streak_fg_adj = min(streak3 * HOT_BONUS_PER, MAX_STREAK_EFFECT) if streak3 > 0 else max(streak3 * COLD_PENALTY_PER, -MAX_STREAK_EFFECT)

        is_3pt = random.random() * 100 < clamp(rate3 + gs_3rate_adj + streak_rate_adj, 15, 65)
        if is_3pt:
            effective_fg3 = clamp(fg3 + s_fg3 * disrupt_star_mod + mom_fg * 0.5 - gs_fg_penalty + disrupt3_mod + streak_fg_adj, 15, 50)
            if random.random() * 100 < effective_fg3 * fatigue_fg_mod:
                points += 3
                makes3 += 1
                running_lead += 3
                mom = min(mom + 1.5, 3)
                streak3 = streak3 + 1 if streak3 > 0 else 1
                if random.random() < 0.02:
                    def_fouls += 1
                    ft_att += 1
                    if random.random() * 100 < ft_pct:
                        points += 1
                        ft_made += 1
                        running_lead += 1
            else:
                mom = max(mom - 0.5, -2)
                streak3 = streak3 - 1 if streak3 < 0 else -1
                if random.random() * 100 < or_pct * 0.80:
                    poss_left += 1
                    orebs += 1
        else:
            streak3 = round(streak3 * STREAK_DECAY)
            effective_fg2 = clamp(fg2 + s_fg2 * disrupt_star_mod + mom_fg * 0.7 - gs_fg_penalty * 0.5 + disrupt2_mod, 25, 70)
            if random.random() * 100 < effective_fg2 * fatigue_fg_mod:
                points += 2
                makes2 += 1
                running_lead += 2
                mom = min(mom + 1, 3)
                if random.random() < 0.06:
                    def_fouls += 1
                    ft_att += 1
                    if random.random() * 100 < ft_pct:
                        points += 1
                        ft_made += 1
                        running_lead += 1
            else:
                mom = max(mom - 0.5, -2)
                if random.random() * 100 < or_pct:
                    poss_left += 1
                    orebs += 1

        # Plan 08: Track streak extremes
        if streak3 > max_hot_streak:
            max_hot_streak = streak3
        if streak3 < -max_cold_streak:
            max_cold_streak = -streak3
        if streak3 >= 2:
            hot_possessions += 1
        if streak3 <= -2:
            cold_possessions += 1

    return {
        "points": points,
        "poss_used": poss_used,
        "makes2": makes2,
        "makes3": makes3,
        "tos": tos,
        "ft_made": ft_made,
        "ft_att": ft_att,
        "orebs": orebs,
        "attempts": attempts,
        "transition_pts": transition_pts,
        "momentum": mom,
        "def_fouls": def_fouls,
        "avg_fatigue_penalty": total_fatigue_penalty / poss_used if poss_used > 0 else 0,
        "rest_possessions": rest_poss_count,
        "crunch_time_poss": crunch_time_poss,
        "desperation_poss": desperation_poss,
        "intentional_foul_poss": intentional_foul_poss,
        "final_lead": running_lead,
        "star_foul_state": {"fouls": star_fouls, "is_sitting": star_is_sitting, "fouled_out": star_fouled_out},
        "star_sat_poss": star_sat_poss,
        "star_fouled_out": star_fouled_out,
        "bonus_reached_at_poss": bonus_reached_poss,
        "max_hot_streak": max_hot_streak,
        "max_cold_streak": max_cold_streak,
        "hot_possessions": hot_possessions,
        "cold_possessions": cold_possessions,
    }


# ─── Interleaved Half Simulation ────────────────────────────────────────────

def sim_half_interleaved(cfg: dict) -> dict:
    """Simulate one half with alternating possessions so lead is always exact.

    Args:
        cfg: Config dict with shared keys (half_poss, is_second_half, incoming_lead,
             foul_climate) and per-team keys prefixed with t1_ / t2_.
    Returns:
        Dict with 't1', 't2' sub-dicts (same shape as sim_half return) and 'final_lead'.
    """
    half_poss = cfg["half_poss"]
    is_second_half = cfg["is_second_half"]
    foul_climate = cfg.get("foul_climate", 1.0)

    # ── Shared state ──
    lead = cfg.get("incoming_lead") or 0  # positive = T1 leads

    # ── Referee Foul Climate ──
    ref_climate = foul_climate or 1.0

    # ── Game Clock Phases ──
    total_half_poss = round(half_poss)
    PHASE_LATE_START = 0.75
    PHASE_CRUNCH_START = 0.90

    # ── Plan 06: Star Foul Trouble constants ──
    MAX_FOULS = 5
    FOUL_SIT_THRESHOLD_H1 = 2
    FOUL_SIT_THRESHOLD_H2 = 4
    FOUL_RETURN_PCT = 0.80
    STREAK_DECAY = 0.65

    # ── Build per-team state dicts ──
    def _build_team_state(prefix):
        star_foul_state = cfg.get(f"{prefix}_star_foul_state")
        star_foul_proneness = cfg.get(f"{prefix}_star_foul_proneness", 0)
        streakiness = cfg.get(f"{prefix}_streakiness", 1.0) or 1.0
        return {
            "fg2": cfg[f"{prefix}_fg2"], "fg3": cfg[f"{prefix}_fg3"],
            "to_pct": cfg[f"{prefix}_to_pct"], "or_pct": cfg[f"{prefix}_or_pct"],
            "rate3": cfg[f"{prefix}_rate3"], "ftr": cfg[f"{prefix}_ftr"],
            "ft_pct": cfg[f"{prefix}_ft_pct"],
            "def_steal_rate": cfg[f"{prefix}_def_steal_rate"],
            "star_usage": cfg[f"{prefix}_star_usage"],
            "star_fg2": cfg[f"{prefix}_star_fg2"], "star_fg3": cfg[f"{prefix}_star_fg3"],
            "bench_depth": cfg[f"{prefix}_bench_depth"],
            "def_profile": cfg.get(f"{prefix}_def_profile") or {"perimeter": 0, "interior": 0, "overall": 0},
            # Mutable game state
            "mom": cfg[f"{prefix}_init_mom"],
            "def_fouls": 0,
            "poss_used": 0,
            "poss_left": round(half_poss),
            "max_poss": round(half_poss) + 10,
            "points": 0, "makes2": 0, "makes3": 0, "tos": 0,
            "ft_made": 0, "ft_att": 0, "orebs": 0, "attempts": 0,
            "total_fatigue_penalty": 0.0, "rest_poss_count": 0,
            "crunch_time_poss": 0, "desperation_poss": 0,
            "intentional_foul_poss": 0, "bonus_reached_poss": -1,
            "star_fouls": star_foul_state["fouls"] if star_foul_state else 0,
            "star_is_sitting": star_foul_state["is_sitting"] if star_foul_state else False,
            "star_sat_poss": 0, "star_fouled_out": False,
            "base_star_foul_rate": (0.035 + (star_foul_proneness or 0) * 0.02) * math.sqrt(ref_climate),
            "streak3": 0,
            "max_hot_streak": 0, "max_cold_streak": 0,
            "hot_possessions": 0, "cold_possessions": 0,
            "HOT_BONUS_PER": 1.2 * streakiness,
            "COLD_PENALTY_PER": 1.0 * streakiness,
            "MAX_STREAK_EFFECT": 5.0 * streakiness,
            "STREAK_RATE_BONUS": 0.8 * streakiness,
            "STREAK_RATE_PENALTY": 0.6 * streakiness,
        }

    t1 = _build_team_state("t1")
    t2 = _build_team_state("t2")

    # References to the opponent's points for transition scoring
    # We'll track them via a mutable list so the inner function can update them
    # t1_opp_pts[0] will accumulate transition pts scored against t1 (credited to t2)
    # and vice versa

    def _sim_one_possession(st, team_sign):
        """Simulate one possession. team_sign: +1 for T1, -1 for T2."""
        nonlocal lead

        if st["poss_left"] <= 0 or st["poss_used"] >= st["max_poss"]:
            return False
        st["poss_left"] -= 1
        st["poss_used"] += 1

        # ── Game State Awareness ──
        total_poss_used = t1["poss_used"] + t2["poss_used"]
        progress_pct = total_poss_used / (total_half_poss * 2) if total_half_poss > 0 else 0
        is_late_half = is_second_half and progress_pct >= PHASE_LATE_START
        is_crunch_time = is_second_half and progress_pct >= PHASE_CRUNCH_START
        if is_crunch_time:
            st["crunch_time_poss"] += 1

        team_lead = lead * team_sign  # positive = this team leads

        gs_3rate_adj = 0
        gs_to_adj = 0
        gs_ftr_adj = 0
        gs_fg_penalty = 0

        if is_second_half:
            deficit = -team_lead
            if is_crunch_time and deficit >= 6:
                desperation_scale = clamp((deficit - 5) / 15, 0, 1)
                gs_3rate_adj = 8 + desperation_scale * 12
                gs_to_adj = 1.5 + desperation_scale * 2
                gs_fg_penalty = 2 + desperation_scale * 3
                st["desperation_poss"] += 1
            elif is_crunch_time and deficit >= 3:
                gs_3rate_adj = 5
                gs_to_adj = 0.8
                gs_fg_penalty = 1
            elif is_late_half and deficit >= 8:
                gs_3rate_adj = 4
                gs_to_adj = 0.5
            elif is_crunch_time and team_lead >= 6:
                gs_3rate_adj = -6
                gs_to_adj = -1
                gs_ftr_adj = 4
                if team_lead >= 8 and random.random() < 0.15:
                    if random.random() * 100 < st["fg2"] * 0.60:
                        st["points"] += 2
                        st["makes2"] += 1
                        lead += 2 * team_sign
                        st["mom"] = min(st["mom"] + 0.3, 3)
                    st["poss_left"] -= 1
                    st["poss_used"] += 1
                    st["streak3"] = round(st["streak3"] * STREAK_DECAY)
                    return True
            elif is_crunch_time and deficit >= 6:
                if random.random() < 0.10:
                    st["poss_left"] += 1
            elif is_late_half and team_lead >= 10:
                gs_3rate_adj = -3
                gs_to_adj = -0.5

        # ── Fatigue Curve ──
        fatigue_onset_pct = 0.55 + (st["bench_depth"] / 100) * 0.15
        fatigue_progress = max(0, (st["poss_used"] / half_poss) - fatigue_onset_pct) / (1 - fatigue_onset_pct) if fatigue_onset_pct < 1 else 0
        half_multiplier = 1.4 if is_second_half else 1.0
        fatigue_penalty = fatigue_progress * half_multiplier * 0.06
        fatigue_fg_mod = 1 - fatigue_penalty
        fatigue_to_mod = 1 + fatigue_penalty * 0.5
        st["total_fatigue_penalty"] += fatigue_penalty

        # ── Plan 06: Star Foul Trouble Check ──
        if not st["star_is_sitting"] and not st["star_fouled_out"] and random.random() < st["base_star_foul_rate"]:
            st["star_fouls"] += 1
            if st["star_fouls"] >= MAX_FOULS:
                st["star_fouled_out"] = True
                st["star_is_sitting"] = True
            elif not is_second_half and st["star_fouls"] >= FOUL_SIT_THRESHOLD_H1:
                st["star_is_sitting"] = True
            elif is_second_half and st["star_fouls"] >= FOUL_SIT_THRESHOLD_H2:
                st["star_is_sitting"] = True

        if st["star_is_sitting"] and not st["star_fouled_out"]:
            return_threshold = PHASE_CRUNCH_START - 0.05 if is_second_half else FOUL_RETURN_PCT
            if progress_pct >= return_threshold:
                st["star_is_sitting"] = False
        if st["star_is_sitting"]:
            st["star_sat_poss"] += 1

        # ── Bench Rotation: Star Rest ──
        rest_window_start = int(half_poss * 0.28)
        rest_window_end = int(half_poss * 0.52)
        in_rest_window = rest_window_start <= st["poss_used"] <= rest_window_end
        rest_prob = clamp(0.15 + (st["bench_depth"] / 100) * 0.65, 0.15, 0.75) if in_rest_window else 0
        is_rest_poss = random.random() < rest_prob
        if is_rest_poss:
            st["rest_poss_count"] += 1
        effective_star_usage = st["star_usage"] * 0.15 if is_rest_poss else st["star_usage"]

        foul_trouble_star_usage = st["star_usage"] * 0.10 if st["star_is_sitting"] else effective_star_usage

        is_star = random.random() < foul_trouble_star_usage
        s_fg2 = st["star_fg2"] if is_star else 0
        s_fg3 = st["star_fg3"] if is_star else 0

        # ── Defensive Disruption ──
        def_p = st["def_profile"]
        is_disrupted_poss = random.random() < def_p["overall"] * 0.6
        disrupt3_mod = -(def_p["perimeter"] * 5.0) if is_disrupted_poss else 0
        disrupt2_mod = -(def_p["interior"] * 4.0) if is_disrupted_poss else 0
        disrupt_star_mod = (1 - def_p["overall"] * 0.5) if is_disrupted_poss else 1.0

        if st["streak3"] > 0 and is_disrupted_poss and def_p["perimeter"] > 0.4:
            st["streak3"] = max(0, st["streak3"] - 1)

        mom_fg = st["mom"] * 0.4

        # ── Turnover check ──
        if random.random() * 100 < (st["to_pct"] + gs_to_adj) * fatigue_to_mod:
            st["tos"] += 1
            st["mom"] = max(st["mom"] - 1, -2)
            if random.random() < (st["def_steal_rate"] / max(st["to_pct"], 8)) * 0.65:
                r = random.random()
                if r < 0.55:
                    lead -= 2 * team_sign
                    if team_sign == 1:
                        t2["points"] += 2
                    else:
                        t1["points"] += 2
                elif r < 0.70:
                    lead -= 3 * team_sign
                    if team_sign == 1:
                        t2["points"] += 3
                    else:
                        t1["points"] += 3
            st["streak3"] = round(st["streak3"] * STREAK_DECAY)
            return True

        # ── Foul probability ──
        base_foul_prob = 0.20 * ref_climate
        drew_foul = random.random() < base_foul_prob
        if drew_foul:
            st["def_fouls"] += 1

        if drew_foul and st["def_fouls"] >= 7 and st["bonus_reached_poss"] == -1:
            st["bonus_reached_poss"] = st["poss_used"]

        if drew_foul and st["def_fouls"] >= 7 and random.random() < 0.45:
            bonus_made = 0
            if st["def_fouls"] >= 10:
                for _ in range(2):
                    st["ft_att"] += 1
                    if random.random() * 100 < st["ft_pct"]:
                        st["points"] += 1
                        st["ft_made"] += 1
                        bonus_made += 1
                        lead += 1 * team_sign
            else:
                st["ft_att"] += 1
                if random.random() * 100 < st["ft_pct"]:
                    st["points"] += 1
                    st["ft_made"] += 1
                    bonus_made += 1
                    lead += 1 * team_sign
                    st["ft_att"] += 1
                    if random.random() * 100 < st["ft_pct"]:
                        st["points"] += 1
                        st["ft_made"] += 1
                        bonus_made += 1
                        lead += 1 * team_sign
            st["mom"] = min(st["mom"] + 0.5, 3) if bonus_made > 0 else max(st["mom"] - 0.5, -2)
            st["streak3"] = round(st["streak3"] * STREAK_DECAY)
            return True

        # FTR-based shooting foul
        if not drew_foul and random.random() < (st["ftr"] * ref_climate) / 100 * 0.38:
            st["def_fouls"] += 1
            num_fts = 3 if random.random() < 0.25 else 2
            made = 0
            for _ in range(num_fts):
                st["ft_att"] += 1
                if random.random() * 100 < st["ft_pct"]:
                    st["points"] += 1
                    st["ft_made"] += 1
                    made += 1
                    lead += 1 * team_sign
            st["mom"] = min(st["mom"] + 0.5, 3) if made > 0 else max(st["mom"] - 0.5, -2)
            st["streak3"] = round(st["streak3"] * STREAK_DECAY)
            return True

        # ── Intentional Fouling ──
        if is_crunch_time and is_second_half and team_lead >= 6 and gs_ftr_adj > 0 and st["intentional_foul_poss"] < 6:
            if random.random() * 100 < gs_ftr_adj * 6:
                st["intentional_foul_poss"] += 1
                st["def_fouls"] += 1
                made = 0
                for _ in range(2):
                    st["ft_att"] += 1
                    if random.random() * 100 < st["ft_pct"]:
                        st["points"] += 1
                        st["ft_made"] += 1
                        made += 1
                        lead += 1 * team_sign
                st["mom"] = min(st["mom"] + 0.3, 3) if made > 0 else max(st["mom"] - 0.3, -2)
                st["streak3"] = round(st["streak3"] * STREAK_DECAY)
                return True

        # ── Shot selection ──
        st["attempts"] += 1
        streak_rate_adj = min(st["streak3"] * st["STREAK_RATE_BONUS"], 4) if st["streak3"] > 0 else max(st["streak3"] * st["STREAK_RATE_PENALTY"], -3)
        streak_fg_adj = min(st["streak3"] * st["HOT_BONUS_PER"], st["MAX_STREAK_EFFECT"]) if st["streak3"] > 0 else max(st["streak3"] * st["COLD_PENALTY_PER"], -st["MAX_STREAK_EFFECT"])

        is_3pt = random.random() * 100 < clamp(st["rate3"] + gs_3rate_adj + streak_rate_adj, 15, 65)
        if is_3pt:
            effective_fg3 = clamp(st["fg3"] + s_fg3 * disrupt_star_mod + mom_fg * 0.5 - gs_fg_penalty + disrupt3_mod + streak_fg_adj, 15, 50)
            if random.random() * 100 < effective_fg3 * fatigue_fg_mod:
                st["points"] += 3
                st["makes3"] += 1
                lead += 3 * team_sign
                st["mom"] = min(st["mom"] + 1.5, 3)
                st["streak3"] = st["streak3"] + 1 if st["streak3"] > 0 else 1
                if random.random() < 0.02:
                    st["def_fouls"] += 1
                    st["ft_att"] += 1
                    if random.random() * 100 < st["ft_pct"]:
                        st["points"] += 1
                        st["ft_made"] += 1
                        lead += 1 * team_sign
            else:
                st["mom"] = max(st["mom"] - 0.5, -2)
                st["streak3"] = st["streak3"] - 1 if st["streak3"] < 0 else -1
                if random.random() * 100 < st["or_pct"] * 0.80:
                    st["poss_left"] += 1
                    st["orebs"] += 1
        else:
            st["streak3"] = round(st["streak3"] * STREAK_DECAY)
            effective_fg2 = clamp(st["fg2"] + s_fg2 * disrupt_star_mod + mom_fg * 0.7 - gs_fg_penalty * 0.5 + disrupt2_mod, 25, 70)
            if random.random() * 100 < effective_fg2 * fatigue_fg_mod:
                st["points"] += 2
                st["makes2"] += 1
                lead += 2 * team_sign
                st["mom"] = min(st["mom"] + 1, 3)
                if random.random() < 0.06:
                    st["def_fouls"] += 1
                    st["ft_att"] += 1
                    if random.random() * 100 < st["ft_pct"]:
                        st["points"] += 1
                        st["ft_made"] += 1
                        lead += 1 * team_sign
            else:
                st["mom"] = max(st["mom"] - 0.5, -2)
                if random.random() * 100 < st["or_pct"]:
                    st["poss_left"] += 1
                    st["orebs"] += 1

        # Track streak extremes
        if st["streak3"] > st["max_hot_streak"]:
            st["max_hot_streak"] = st["streak3"]
        if st["streak3"] < -st["max_cold_streak"]:
            st["max_cold_streak"] = -st["streak3"]
        if st["streak3"] >= 2:
            st["hot_possessions"] += 1
        if st["streak3"] <= -2:
            st["cold_possessions"] += 1

        return True

    # ── Main alternating loop ──
    while t1["poss_left"] > 0 or t2["poss_left"] > 0:
        t1_ran = False
        t2_ran = False
        if t1["poss_left"] > 0 and t1["poss_used"] < t1["max_poss"]:
            _sim_one_possession(t1, +1)
            t1_ran = True
        if t2["poss_left"] > 0 and t2["poss_used"] < t2["max_poss"]:
            _sim_one_possession(t2, -1)
            t2_ran = True
        if not t1_ran and not t2_ran:
            break  # safety: avoid infinite loop if both hit max_poss

    # ── Build return dicts matching sim_half() shape ──
    def _build_result(st, final_lead_val):
        return {
            "points": st["points"],
            "poss_used": st["poss_used"],
            "makes2": st["makes2"],
            "makes3": st["makes3"],
            "tos": st["tos"],
            "ft_made": st["ft_made"],
            "ft_att": st["ft_att"],
            "orebs": st["orebs"],
            "attempts": st["attempts"],
            "transition_pts": 0,  # folded into opponent's points
            "momentum": st["mom"],
            "def_fouls": st["def_fouls"],
            "avg_fatigue_penalty": st["total_fatigue_penalty"] / st["poss_used"] if st["poss_used"] > 0 else 0,
            "rest_possessions": st["rest_poss_count"],
            "crunch_time_poss": st["crunch_time_poss"],
            "desperation_poss": st["desperation_poss"],
            "intentional_foul_poss": st["intentional_foul_poss"],
            "final_lead": final_lead_val,
            "star_foul_state": {"fouls": st["star_fouls"], "is_sitting": st["star_is_sitting"], "fouled_out": st["star_fouled_out"]},
            "star_sat_poss": st["star_sat_poss"],
            "star_fouled_out": st["star_fouled_out"],
            "bonus_reached_at_poss": st["bonus_reached_poss"],
            "max_hot_streak": st["max_hot_streak"],
            "max_cold_streak": st["max_cold_streak"],
            "hot_possessions": st["hot_possessions"],
            "cold_possessions": st["cold_possessions"],
        }

    return {
        "t1": _build_result(t1, lead),
        "t2": _build_result(t2, -lead),
        "final_lead": lead,
    }


# ─── Overtime Simulation ────────────────────────────────────────────────────

def sim_overtime(fg2: float, fg3: float, to_pct: float, or_pct: float,
                 rate3: float, ftr: float, ft_pct: float,
                 def_steal_rate: float, star_usage: float,
                 star_fg2: float, star_fg3: float, momentum: float,
                 ot_number: int, star_fouled_out: bool,
                 foul_climate: float = 1.0) -> dict:
    """Simulate one overtime period (5 possessions)."""
    OT_POSSESSIONS = 5
    points = 0
    poss_used = 0
    makes2 = 0
    makes3 = 0
    tos_count = 0
    ft_made = 0
    ft_att = 0
    orebs = 0
    mom = momentum * 0.5

    ref_climate = foul_climate or 1.0
    ot_fatigue_penalty = 0.04 + (ot_number - 1) * 0.025
    fatigue_fg_mod = 1 - ot_fatigue_penalty
    fatigue_to_mod = 1 + ot_fatigue_penalty * 0.6
    ot_ftr_boost = 8
    effective_ftr = (ftr + ot_ftr_boost) * ref_climate

    poss_left = OT_POSSESSIONS
    streak3 = 0
    STREAK_DECAY = 0.65

    while poss_left > 0:
        poss_left -= 1
        poss_used += 1

        eff_star_usage = 0 if star_fouled_out else star_usage
        is_star = random.random() < eff_star_usage
        s_fg2 = star_fg2 * fatigue_fg_mod if is_star else 0
        s_fg3 = star_fg3 * fatigue_fg_mod if is_star else 0
        mom_fg = mom * 0.3

        # Turnover
        if random.random() * 100 < to_pct * fatigue_to_mod:
            tos_count += 1
            mom = max(mom - 1, -2)
            streak3 = round(streak3 * STREAK_DECAY)
            continue

        # Shooting foul
        if random.random() < effective_ftr / 100 * 0.45:
            num_fts = 3 if random.random() < 0.20 else 2
            made = 0
            for _ in range(num_fts):
                ft_att += 1
                clutch_ft_pct = ft_pct - 2
                if random.random() * 100 < clutch_ft_pct:
                    points += 1
                    ft_made += 1
                    made += 1
            mom = min(mom + 0.5, 2) if made > 0 else max(mom - 0.5, -2)
            streak3 = round(streak3 * STREAK_DECAY)
            continue

        streak_fg_adj = min(streak3 * 1.2, 5.0) if streak3 > 0 else max(streak3 * 1.0, -5.0)
        streak_rate_adj = min(streak3 * 0.8, 4) if streak3 > 0 else max(streak3 * 0.6, -3)

        is_3pt = random.random() * 100 < clamp(rate3 + streak_rate_adj, 15, 65)
        if is_3pt:
            if random.random() * 100 < (fg3 + s_fg3 + mom_fg * 0.5 + streak_fg_adj) * fatigue_fg_mod:
                points += 3
                makes3 += 1
                mom = min(mom + 1.5, 2)
                streak3 = streak3 + 1 if streak3 > 0 else 1
            else:
                mom = max(mom - 0.5, -2)
                streak3 = streak3 - 1 if streak3 < 0 else -1
                if random.random() * 100 < or_pct * 0.75:
                    poss_left += 1
                    orebs += 1
        else:
            streak3 = round(streak3 * STREAK_DECAY)
            if random.random() * 100 < (fg2 + s_fg2 + mom_fg * 0.7) * fatigue_fg_mod:
                points += 2
                makes2 += 1
                mom = min(mom + 1, 2)
            else:
                mom = max(mom - 0.5, -2)
                if random.random() * 100 < or_pct * 0.90:
                    poss_left += 1
                    orebs += 1

    return {
        "points": points,
        "poss_used": poss_used,
        "makes2": makes2,
        "makes3": makes3,
        "tos": tos_count,
        "ft_made": ft_made,
        "ft_att": ft_att,
        "orebs": orebs,
    }


# ─── Main Simulation Loop ───────────────────────────────────────────────────

def simulate_game(p: dict, num_sims: int = 500) -> dict:
    """Run N Monte Carlo simulations for a single game matchup.

    Args:
        p: Dict of pre-computed matchup parameters (same keys as JS workerParams,
           but using snake_case).
        num_sims: Number of simulations to run.

    Returns:
        Dict with t1_win_prob, avg scores, margin distribution, and diagnostics.
    """
    t1_star = calc_star_impact(p.get("stars1"))
    t2_star = calc_star_impact(p.get("stars2"))

    t1_wins = 0
    t2_wins = 0
    ties = 0
    total_t1_score = 0
    total_t2_score = 0
    margin_dist = []
    blowouts = 0
    close_games = 0
    upsets = 0

    # Plan 09: Foul stats accumulators for ref_stats
    total_t1_def_fouls = 0
    total_t2_def_fouls = 0
    total_t1_ft_att = 0
    total_t2_ft_att = 0
    t1_early_bonus_games = 0
    t2_early_bonus_games = 0

    # OT tracking accumulators
    ot_sims = 0
    total_ot_points = 0
    # Plan 07: Tempo
    t1_pull = p.get("t1_preferred_tempo") or p.get("game_tempo_ctr", 67.5)
    t2_pull = p.get("t2_preferred_tempo") or p.get("game_tempo_ctr", 67.5)
    t1_ctrl = p.get("t1_tempo_control", 0.50)
    t2_ctrl = p.get("t2_tempo_control", 0.50)

    # Plan 09: Referee
    ref_climate = p.get("ref_foul_climate", 1.0) or 1.0

    for _ in range(num_sims):
        # Shared game environment factor
        game_factor = rand_normal(0, 1.2)

        # Plan 07: Contested Tempo
        total_ctrl = t1_ctrl + t2_ctrl
        contested_tempo = (t1_pull * t1_ctrl + t2_pull * t2_ctrl) / total_ctrl if total_ctrl > 0 else 67.5
        tempo_noise = rand_normal(0, 2.5)
        def_tempo_edge = -0.5 if (t1_ctrl > t2_ctrl and t1_pull < t2_pull) else (-0.5 if (t2_ctrl > t1_ctrl and t2_pull < t1_pull) else 0)
        game_poss = clamp(contested_tempo + tempo_noise + def_tempo_edge, 55, 85)

        # Tempo Mismatch Chaos
        tempo_mismatch = abs(t1_pull - t2_pull)
        mismatch_chaos = clamp((tempo_mismatch - 6) * 0.008, 0, 0.04) if tempo_mismatch > 6 else 0

        # Correlated game-style factors
        t1_style = generate_game_style(p.get("t1_vol_mod", 1.0), p.get("t1_style_bias", 0))
        t2_style = generate_game_style(p.get("t2_vol_mod", 1.0), p.get("t2_style_bias", 0))

        # Apply correlated style adjustments
        g_t1_fg2 = clamp(p["m_t1_fg2"] + t1_style["fg2_adj"] + game_factor * 0.25 + rand_normal(0, mismatch_chaos * 15), 28, 68)
        g_t2_fg2 = clamp(p["m_t2_fg2"] + t2_style["fg2_adj"] + game_factor * 0.25 + rand_normal(0, mismatch_chaos * 15), 28, 68)
        g_t1_fg3 = clamp(p["m_t1_fg3"] + t1_style["fg3_adj"] + game_factor * 0.15 + rand_normal(0, mismatch_chaos * 10), 18, 48)
        g_t2_fg3 = clamp(p["m_t2_fg3"] + t2_style["fg3_adj"] + game_factor * 0.15 + rand_normal(0, mismatch_chaos * 10), 18, 48)

        # Plan 07: Tempo Winner Bonus
        t1_tempo_delta = abs(game_poss - t1_pull)
        t2_tempo_delta = abs(game_poss - t2_pull)
        if t1_tempo_delta < t2_tempo_delta - 2:
            g_t1_fg2 += 0.2
            g_t2_fg2 -= 0.12
        elif t2_tempo_delta < t1_tempo_delta - 2:
            g_t2_fg2 += 0.2
            g_t1_fg2 -= 0.12

        g_t1_to = clamp(p["m_t1_to"] + t1_style["to_adj"], 6, 30)
        g_t2_to = clamp(p["m_t2_to"] + t2_style["to_adj"], 6, 30)
        g_t1_or = clamp(p["m_t1_or"] + p.get("t1_hgt_or_bonus", 0) + t1_style["or_adj"], 12, 45)
        g_t2_or = clamp(p["m_t2_or"] - p.get("t1_hgt_or_bonus", 0) + t2_style["or_adj"], 12, 45)

        g_t1_3rate = clamp(p.get("t1_3rate", 35) + t1_style["rate3_adj"], 20, 55)
        g_t2_3rate = clamp(p.get("t2_3rate", 35) + t2_style["rate3_adj"], 20, 55)
        g_t1_ftr = clamp(p.get("m_t1_ftr", 30) + t1_style["ftr_adj"], 15, 50)
        g_t2_ftr = clamp(p.get("m_t2_ftr", 30) + t2_style["ftr_adj"], 15, 50)

        # Opponent Reaction
        reaction_strength = 0.15
        if t1_style["fg2_adj"] > 2:
            g_t2_fg3 = clamp(g_t2_fg3 + reaction_strength * 1.5, 18, 48)
        if t2_style["fg2_adj"] > 2:
            g_t1_fg3 = clamp(g_t1_fg3 + reaction_strength * 1.5, 18, 48)
        chaos_factor = (t1_style["to_adj"] + t2_style["to_adj"]) * 0.08
        g_t1_to = clamp(g_t1_to + chaos_factor, 6, 30)
        g_t2_to = clamp(g_t2_to + chaos_factor, 6, 30)

        # Defensive Disruption: TO Variance Boost
        t2_to_var = (p.get("t1_def_profile", {}).get("overall", 0)) * 2.5
        t1_to_var = (p.get("t2_def_profile", {}).get("overall", 0)) * 2.5
        if t2_to_var > 0:
            g_t2_to = clamp(g_t2_to + rand_normal(0, t2_to_var), 6, 30)
        if t1_to_var > 0:
            g_t1_to = clamp(g_t1_to + rand_normal(0, t1_to_var), 6, 30)

        s1 = 0.0
        s2 = 0.0
        t1_mom = 0.0
        t2_mom = 0.0

        # Plan 06: Per-game foul state (carried between halves)
        t1_star_foul_state = {"fouls": 0, "is_sitting": False}
        t2_star_foul_state = {"fouls": 0, "is_sitting": False}
        game_t1_fouled_out = False
        game_t2_fouled_out = False

        for half in range(2):
            # Plan 07: Half-Specific Tempo
            half_tempo_adj = 0
            if half == 1:
                halftime_margin = s1 - s2
                if abs(halftime_margin) > 5:
                    trailing_wants_fast = (t2_pull > contested_tempo) if halftime_margin > 0 else (t1_pull > contested_tempo)
                    pace_shift = clamp(abs(halftime_margin) * 0.15, 0, 3)
                    half_tempo_adj = pace_shift if trailing_wants_fast else -pace_shift * 0.5
                if abs(halftime_margin) <= 3:
                    half_tempo_adj -= 0.8

            half_poss = round((game_poss + half_tempo_adj) / 2)
            interleaved_incoming_lead = 0 if half == 0 else (s1 - s2)

            half_result = sim_half_interleaved({
                "half_poss": half_poss, "is_second_half": half == 1,
                "incoming_lead": interleaved_incoming_lead,
                "foul_climate": ref_climate,
                # T1 params
                "t1_fg2": g_t1_fg2, "t1_fg3": g_t1_fg3,
                "t1_to_pct": g_t1_to, "t1_or_pct": g_t1_or,
                "t1_rate3": clamp(g_t1_3rate, 20, 55), "t1_ftr": g_t1_ftr,
                "t1_ft_pct": p.get("t1_ftp", 72),
                "t1_def_steal_rate": p.get("m_t2_steal_rate", 9),
                "t1_star_usage": t1_star["usage"],
                "t1_star_fg2": t1_star["fg2_bonus"], "t1_star_fg3": t1_star["fg3_bonus"],
                "t1_init_mom": t1_mom,
                "t1_bench_depth": p.get("t1_bench", 30),
                "t1_def_profile": p.get("t2_def_profile", {"perimeter": 0, "interior": 0, "overall": 0}),
                "t1_star_foul_state": t1_star_foul_state,
                "t1_star_foul_proneness": p.get("t1_star_foul_proneness", 0),
                "t1_streakiness": p.get("t1_streakiness", 1.0),
                # T2 params
                "t2_fg2": g_t2_fg2, "t2_fg3": g_t2_fg3,
                "t2_to_pct": g_t2_to, "t2_or_pct": g_t2_or,
                "t2_rate3": clamp(g_t2_3rate, 20, 55), "t2_ftr": g_t2_ftr,
                "t2_ft_pct": p.get("t2_ftp", 72),
                "t2_def_steal_rate": p.get("m_t1_steal_rate", 9),
                "t2_star_usage": t2_star["usage"],
                "t2_star_fg2": t2_star["fg2_bonus"], "t2_star_fg3": t2_star["fg3_bonus"],
                "t2_init_mom": t2_mom,
                "t2_bench_depth": p.get("t2_bench", 30),
                "t2_def_profile": p.get("t1_def_profile", {"perimeter": 0, "interior": 0, "overall": 0}),
                "t2_star_foul_state": t2_star_foul_state,
                "t2_star_foul_proneness": p.get("t2_star_foul_proneness", 0),
                "t2_streakiness": p.get("t2_streakiness", 1.0),
            })
            r1 = half_result["t1"]
            r2 = half_result["t2"]

            # Plan 06: Carry foul state to next half
            t1_star_foul_state = r1.get("star_foul_state", {"fouls": 0, "is_sitting": False})
            t2_star_foul_state = r2.get("star_foul_state", {"fouls": 0, "is_sitting": False})
            if half == 0:
                if t1_star_foul_state["fouls"] < 4:
                    t1_star_foul_state["is_sitting"] = False
                if t2_star_foul_state["fouls"] < 4:
                    t2_star_foul_state["is_sitting"] = False
            if r1.get("star_fouled_out"):
                game_t1_fouled_out = True
            if r2.get("star_fouled_out"):
                game_t2_fouled_out = True

            # Plan 09: Accumulate foul stats (r1 = T1 offense, r2 = T2 offense)
            total_t1_def_fouls += r1.get("def_fouls", 0)  # fouls drawn by T1
            total_t2_def_fouls += r2.get("def_fouls", 0)  # fouls drawn by T2
            total_t1_ft_att += r1.get("ft_att", 0)
            total_t2_ft_att += r2.get("ft_att", 0)
            if r1.get("bonus_reached_at_poss", -1) >= 0 and r1["bonus_reached_at_poss"] < round(game_poss / 2 * 0.55):
                t1_early_bonus_games += 0.5  # per-half, so 0.5 per half occurrence
            if r2.get("bonus_reached_at_poss", -1) >= 0 and r2["bonus_reached_at_poss"] < round(game_poss / 2 * 0.55):
                t2_early_bonus_games += 0.5

            # Transition pts already folded into each team's points in interleaved sim
            s1 += r1["points"]
            s2 += r2["points"]

            t1_mom = r1["momentum"] * (0.3 if half == 0 else 1)
            t2_mom = r2["momentum"] * (0.3 if half == 0 else 1)

        # KenPom Anchor Blend (configurable, default 82% sim / 18% KenPom)
        kp_blend = p.get("kp_blend_ratio", 0.18)  # 0.0 = pure sim, 0.18 = default
        kp_s1 = p.get("kp_t1_exp_oe", 100) * (game_poss / 100)
        kp_s2 = p.get("kp_t2_exp_oe", 100) * (game_poss / 100)
        s1 = s1 * (1 - kp_blend) + kp_s1 * kp_blend
        s2 = s2 * (1 - kp_blend) + kp_s2 * kp_blend

        # Adjustments (injury edge, HCA)
        s1 += p.get("total_adj", 0) / 2 + p.get("hca1", 0)
        s2 += -p.get("total_adj", 0) / 2 + p.get("hca2", 0)

        # Coach Edge (close games only)
        raw_margin = abs(s1 - s2)
        if raw_margin <= 6:
            clutch_scale = 1 - (raw_margin / 6)
            coach_edge = p.get("coach_edge", 0)
            ft_clutch_edge = p.get("ft_clutch_edge", 0)
            s1 += coach_edge * clutch_scale
            s2 -= coach_edge * clutch_scale
            s1 += ft_clutch_edge * clutch_scale * 0.5
            s2 -= ft_clutch_edge * clutch_scale * 0.5

        # Overtime Resolution
        ot_periods = 0
        MAX_OT = 4
        reg_s1, reg_s2 = s1, s2  # snapshot regulation scores before OT
        while abs(s1 - s2) <= 2 and ot_periods < MAX_OT:
            ot_periods += 1
            t1_last_poss = (ot_periods % 2 == 1)

            ot1 = sim_overtime(
                g_t1_fg2, g_t1_fg3, g_t1_to, g_t1_or,
                g_t1_3rate, g_t1_ftr, p.get("t1_ftp", 72),
                p.get("m_t2_steal_rate", 9), t1_star["usage"],
                t1_star["fg2_bonus"], t1_star["fg3_bonus"],
                t1_mom, ot_periods, game_t1_fouled_out, ref_climate)
            ot2 = sim_overtime(
                g_t2_fg2, g_t2_fg3, g_t2_to, g_t2_or,
                g_t2_3rate, g_t2_ftr, p.get("t2_ftp", 72),
                p.get("m_t1_steal_rate", 9), t2_star["usage"],
                t2_star["fg2_bonus"], t2_star["fg3_bonus"],
                t2_mom, ot_periods, game_t2_fouled_out, ref_climate)

            s1 += ot1["points"]
            s2 += ot2["points"]

            if abs(s1 - s2) <= 1:
                if t1_last_poss:
                    if random.random() < 0.38:
                        s1 += 3 if random.random() < 0.30 else 2
                else:
                    if random.random() < 0.38:
                        s2 += 3 if random.random() < 0.30 else 2

            t1_mom = 1 if ot1["points"] > ot2["points"] else (-1 if ot1["points"] < ot2["points"] else 0)
            t2_mom = -t1_mom

        # Accumulate OT stats
        if ot_periods > 0:
            ot_sims += 1
            total_ot_points += (s1 + s2) - (reg_s1 + reg_s2)

        # Score rounding and final tie-break
        s1 = round(s1)
        s2 = round(s2)
        if s1 == s2:
            if random.random() < 0.5:
                s1 += 1
            else:
                s2 += 1

        total_t1_score += s1
        total_t2_score += s2
        m = s1 - s2
        margin_dist.append(m)

        if s1 > s2:
            t1_wins += 1
            if not p.get("t1_favored", True):
                upsets += 1
        elif s2 > s1:
            t2_wins += 1
            if p.get("t1_favored", True):
                upsets += 1
        else:
            ties += 1

        if abs(m) >= 15:
            blowouts += 1
        if abs(m) <= 5:
            close_games += 1

    # Aggregate results
    t1_win_pct = (t1_wins + ties * 0.5) / num_sims
    avg_t1_score = total_t1_score / num_sims
    avg_t2_score = total_t2_score / num_sims
    avg_margin = sum(margin_dist) / num_sims

    margin_dist.sort()
    median_margin = margin_dist[num_sims // 2] if margin_dist else 0

    return {
        "t1_win_prob": t1_win_pct,
        "t1_score": avg_t1_score,
        "t2_score": avg_t2_score,
        "margin": avg_margin,
        "median_margin": median_margin,
        "t1_wins": t1_wins,
        "t2_wins": t2_wins,
        "ties": ties,
        "blowouts": blowouts,
        "close_games": close_games,
        "upsets": upsets,
        "num_sims": num_sims,
        "ot_rate": ot_sims / num_sims,
        "avg_ot_points": total_ot_points / ot_sims if ot_sims > 0 else 0,
        "ref_stats": {
            "foul_climate": ref_climate,
            "t1_avg_fouls_drawn": total_t1_def_fouls / num_sims,
            "t2_avg_fouls_drawn": total_t2_def_fouls / num_sims,
            "t1_avg_ft_att": total_t1_ft_att / num_sims,
            "t2_avg_ft_att": total_t2_ft_att / num_sims,
            "t1_early_bonus_rate": t1_early_bonus_games / num_sims,
            "t2_early_bonus_rate": t2_early_bonus_games / num_sims,
        },
    }


# ─── Self-test ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("=" * 60)
    print("  mc_engine.py — Python MC Simulation Engine Self-Test")
    print("=" * 60)

    # Test with a sample matchup: Duke (strong) vs Siena (weak)
    test_params = {
        "m_t1_fg2": 52.0, "m_t1_fg3": 35.5, "m_t1_to": 16.0, "m_t1_or": 30.0,
        "m_t1_ftr": 32.0, "t1_ftp": 76.0, "t1_3rate": 38.0, "m_t2_steal_rate": 9.0,
        "m_t2_fg2": 44.0, "m_t2_fg3": 30.0, "m_t2_to": 20.0, "m_t2_or": 26.0,
        "m_t2_ftr": 28.0, "t2_ftp": 70.0, "t2_3rate": 34.0, "m_t1_steal_rate": 10.5,
        "game_tempo_ctr": 68.0,
        "t1_preferred_tempo": 70.0, "t2_preferred_tempo": 66.0,
        "t1_tempo_control": 0.52, "t2_tempo_control": 0.48,
        "t1_vol_mod": 1.0, "t2_vol_mod": 1.15,
        "t1_style_bias": 0, "t2_style_bias": 0,
        "kp_t1_exp_oe": 118.0, "kp_t2_exp_oe": 98.0,
        "total_adj": 0, "hca1": 0, "hca2": 0,
        "coach_edge": 0.3, "ft_clutch_edge": 0.1,
        "t1_hgt_or_bonus": 1.5,
        "t1_bench": 35, "t2_bench": 25,
        "stars1": [{"impact": 10}, {"impact": 9}, {"impact": 9}],
        "stars2": [],
        "t1_def_profile": {"perimeter": 0.35, "interior": 0.40, "overall": 0.38},
        "t2_def_profile": {"perimeter": 0.15, "interior": 0.20, "overall": 0.18},
        "t1_star_foul_proneness": 0, "t2_star_foul_proneness": 0,
        "t1_streakiness": 1.0, "t2_streakiness": 1.0,
        "ref_foul_climate": 1.0,
        "t1_favored": True,
    }

    print("\nRunning 1000 sims: Duke vs Siena (1 vs 16 proxy)...")
    result = simulate_game(test_params, num_sims=1000)
    print(f"  Team 1 win prob: {result['t1_win_prob']:.1%}")
    print(f"  Avg score: {result['t1_score']:.1f} - {result['t2_score']:.1f}")
    print(f"  Avg margin: {result['margin']:+.1f}")
    print(f"  Blowouts (15+): {result['blowouts']} ({result['blowouts']/10:.1f}%)")
    print(f"  Close games (5-): {result['close_games']} ({result['close_games']/10:.1f}%)")

    # Test a closer matchup
    close_params = dict(test_params)
    close_params.update({
        "m_t2_fg2": 50.0, "m_t2_fg3": 34.0, "m_t2_to": 17.0, "m_t2_or": 29.0,
        "m_t2_ftr": 31.0, "t2_ftp": 74.0,
        "kp_t2_exp_oe": 115.0,
        "stars2": [{"impact": 9}, {"impact": 8}],
        "t2_def_profile": {"perimeter": 0.30, "interior": 0.35, "overall": 0.33},
        "t2_bench": 32,
    })
    print("\nRunning 1000 sims: Duke vs UConn-proxy (1 vs 2 proxy)...")
    result2 = simulate_game(close_params, num_sims=1000)
    print(f"  Team 1 win prob: {result2['t1_win_prob']:.1%}")
    print(f"  Avg score: {result2['t1_score']:.1f} - {result2['t2_score']:.1f}")
    print(f"  Avg margin: {result2['margin']:+.1f}")
    print(f"  Blowouts (15+): {result2['blowouts']} ({result2['blowouts']/10:.1f}%)")
    print(f"  Close games (5-): {result2['close_games']} ({result2['close_games']/10:.1f}%)")

    print("\nSanity checks:")
    print(f"  1v16 win prob should be ~95-99%: {result['t1_win_prob']:.1%} {'OK' if 0.90 <= result['t1_win_prob'] <= 1.0 else 'WARN'}")
    print(f"  1v2 win prob should be ~55-70%: {result2['t1_win_prob']:.1%} {'OK' if 0.45 <= result2['t1_win_prob'] <= 0.80 else 'WARN'}")
    print(f"  Scores should be 60-90 range: T1={result['t1_score']:.0f} T2={result['t2_score']:.0f} {'OK' if 55 < result['t1_score'] < 95 else 'WARN'}")

    # ── Plan 09: Referee Foul Climate Validation ──
    print("\n" + "=" * 60)
    print("  Referee Foul Climate Validation")
    print("=" * 60)

    N_CLIMATE_SIMS = 1000

    # Baseline: foulClimate = 1.0
    baseline_params = dict(test_params)
    baseline_params["ref_foul_climate"] = 1.0
    print(f"\nRunning {N_CLIMATE_SIMS} sims with ref_foul_climate = 1.0 (baseline)...")
    baseline = simulate_game(baseline_params, num_sims=N_CLIMATE_SIMS)
    baseline_rs = baseline.get("ref_stats", {})
    baseline_fouls = baseline_rs.get("t1_avg_fouls_drawn", 0) + baseline_rs.get("t2_avg_fouls_drawn", 0)
    baseline_fta = baseline_rs.get("t1_avg_ft_att", 0) + baseline_rs.get("t2_avg_ft_att", 0)
    print(f"  Avg score: {baseline['t1_score']:.1f} - {baseline['t2_score']:.1f}")
    print(f"  Avg total fouls drawn: {baseline_fouls:.1f}, Avg total FTA: {baseline_fta:.1f}")

    # Whistle-happy: foulClimate = 1.15
    whistle_params = dict(test_params)
    whistle_params["ref_foul_climate"] = 1.15
    print(f"\nRunning {N_CLIMATE_SIMS} sims with ref_foul_climate = 1.15 (whistle-happy)...")
    whistle = simulate_game(whistle_params, num_sims=N_CLIMATE_SIMS)
    whistle_rs = whistle.get("ref_stats", {})
    whistle_fouls = whistle_rs.get("t1_avg_fouls_drawn", 0) + whistle_rs.get("t2_avg_fouls_drawn", 0)
    whistle_fta = whistle_rs.get("t1_avg_ft_att", 0) + whistle_rs.get("t2_avg_ft_att", 0)
    print(f"  Avg score: {whistle['t1_score']:.1f} - {whistle['t2_score']:.1f}")
    print(f"  Avg total fouls drawn: {whistle_fouls:.1f}, Avg total FTA: {whistle_fta:.1f}")

    # Let-them-play: foulClimate = 0.85
    loose_params = dict(test_params)
    loose_params["ref_foul_climate"] = 0.85
    print(f"\nRunning {N_CLIMATE_SIMS} sims with ref_foul_climate = 0.85 (let-them-play)...")
    loose = simulate_game(loose_params, num_sims=N_CLIMATE_SIMS)
    loose_rs = loose.get("ref_stats", {})
    loose_fouls = loose_rs.get("t1_avg_fouls_drawn", 0) + loose_rs.get("t2_avg_fouls_drawn", 0)
    loose_fta = loose_rs.get("t1_avg_ft_att", 0) + loose_rs.get("t2_avg_ft_att", 0)
    print(f"  Avg score: {loose['t1_score']:.1f} - {loose['t2_score']:.1f}")
    print(f"  Avg total fouls drawn: {loose_fouls:.1f}, Avg total FTA: {loose_fta:.1f}")

    # Validation checks
    print("\nFoul Climate Validation Results:")

    if baseline_fouls > 0:
        whistle_foul_pct = (whistle_fouls - baseline_fouls) / baseline_fouls * 100
        loose_foul_pct = (baseline_fouls - loose_fouls) / baseline_fouls * 100
        print(f"  Whistle-happy (1.15) fouls vs baseline: {whistle_foul_pct:+.1f}% {'OK' if 5 <= whistle_foul_pct <= 30 else 'WARN (expected 15-20%)'}")
        print(f"  Let-them-play (0.85) fouls vs baseline: {loose_foul_pct:+.1f}% fewer {'OK' if 5 <= loose_foul_pct <= 25 else 'WARN (expected 10-15%)'}")
    else:
        print("  [WARN] Baseline fouls drawn is 0 — cannot compute foul percentages")

    if baseline_fta > 0:
        whistle_fta_pct = (whistle_fta - baseline_fta) / baseline_fta * 100
        loose_fta_pct = (baseline_fta - loose_fta) / baseline_fta * 100
        print(f"  Whistle-happy (1.15) FTA vs baseline: {whistle_fta_pct:+.1f}% {'OK' if 3 <= whistle_fta_pct <= 35 else 'WARN'}")
        print(f"  Let-them-play (0.85) FTA vs baseline: {loose_fta_pct:+.1f}% fewer {'OK' if 3 <= loose_fta_pct <= 30 else 'WARN'}")
    else:
        print("  [WARN] Baseline FTA is 0 — cannot compute FTA percentages")

    score_diff_whistle = abs((whistle['t1_score'] + whistle['t2_score']) - (baseline['t1_score'] + baseline['t2_score']))
    score_diff_loose = abs((loose['t1_score'] + loose['t2_score']) - (baseline['t1_score'] + baseline['t2_score']))
    print(f"  Total score change (whistle): {score_diff_whistle:.1f} pts {'OK' if score_diff_whistle < 8 else 'WARN (expected <5)'}")
    print(f"  Total score change (loose): {score_diff_loose:.1f} pts {'OK' if score_diff_loose < 8 else 'WARN (expected <5)'}")

    # Early bonus rate comparison
    baseline_eb = baseline_rs.get("t1_early_bonus_rate", 0) + baseline_rs.get("t2_early_bonus_rate", 0)
    whistle_eb = whistle_rs.get("t1_early_bonus_rate", 0) + whistle_rs.get("t2_early_bonus_rate", 0)
    loose_eb = loose_rs.get("t1_early_bonus_rate", 0) + loose_rs.get("t2_early_bonus_rate", 0)
    print(f"  Early bonus rate — baseline: {baseline_eb:.2f}, whistle: {whistle_eb:.2f}, loose: {loose_eb:.2f}")

    # Graceful degradation: missing/None climate should default to 1.0
    default_params = dict(test_params)
    del default_params["ref_foul_climate"]
    default_result = simulate_game(default_params, num_sims=100)
    print(f"\n  Graceful degradation (no ref_foul_climate key): score={default_result['t1_score']:.1f}-{default_result['t2_score']:.1f} OK")
