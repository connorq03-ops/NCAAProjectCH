"""
golf_mc_engine.py - Golf Monte Carlo Tournament Simulation Engine

Hole-by-hole granularity: 18 holes x 4 rounds x N players, with cut modeling
after Round 2. The golf equivalent of mc_engine.py (possession-level basketball).

Data flow:
  1. golf_sim_params.build_player_sim_params()  -> per-player param dict
  2. golf_course_profiles.COURSES[x]["holes"]    -> 18-hole course data
  3. sim_hole()                                  -> atomic scoring unit
  4. sim_round()                                 -> 18 holes for one player
  5. sim_tournament_single()                     -> full 4-round tournament
  6. simulate_tournament()                       -> N sims, aggregated stats
  7. simulate_matchup()                          -> H2H comparison
"""

import random
import math
from typing import List, Dict, Optional, Any


# ─── Utility Functions ───────────────────────────────────────────────────────

def clamp(val: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, val))


def rand_normal(mean: float, sd: float) -> float:
    return random.gauss(mean, sd)


# ─── Constants ───────────────────────────────────────────────────────────────

STREAK_DECAY = 0.6
CUT_LINE_POSITION = 65
MOMENTUM_CARRY_DECAY = 0.3
DEFAULT_PAR = 72


# ─── Round Style Generation ─────────────────────────────────────────────────

def generate_round_style(volatility: float, streakiness: float) -> dict:
    """Generate correlated round-level variance factors for one player in one round.

    Analogous to generate_game_style() in mc_engine.py (lines 25-39).
    Uses an aggression axis and consistency axis to create realistic
    correlations between birdie and bogey rates.

    Args:
        volatility: round_volatility from sim params (1.5-4.5)
        streakiness: streakiness from sim params (0.2-0.9)

    Returns:
        dict with birdie_adj, bogey_adj, putting_adj, driving_adj, style_label
    """
    aggression_axis = rand_normal(0, 1.0) * volatility * 0.15
    consistency_axis = rand_normal(0, 1.0) * volatility * 0.12
    residual_sd = 0.3

    # Aggressive rounds: more birdies AND more bogeys (correlated)
    birdie_adj = aggression_axis * 0.04 + consistency_axis * 0.02 + rand_normal(0, residual_sd * 0.01)
    bogey_adj = -aggression_axis * 0.02 + consistency_axis * 0.03 + rand_normal(0, residual_sd * 0.01)

    # Putting is more independent (less correlated with ball-striking)
    putting_adj = rand_normal(0, 0.3) * streakiness * 0.05
    # Driving correlates with aggression
    driving_adj = aggression_axis * 0.03 + rand_normal(0, residual_sd * 0.01)

    if aggression_axis > 0.3:
        style_label = "aggressive"
    elif aggression_axis < -0.3:
        style_label = "conservative"
    else:
        style_label = "balanced"

    return {
        "birdie_adj": birdie_adj,
        "bogey_adj": bogey_adj,
        "putting_adj": putting_adj,
        "driving_adj": driving_adj,
        "style_label": style_label,
    }


# ─── Hole Simulation ────────────────────────────────────────────────────────

def sim_hole(cfg: dict) -> dict:
    """Simulate one hole for one player. The atomic scoring unit.

    Analogous to one possession in sim_half() for basketball.

    Args:
        cfg: dict with keys:
            par, difficulty_rank, birdie_rate, bogey_rate, double_rate,
            eagle_rate, momentum, streakiness, weather_adj, pressure_adj,
            fatigue_adj, hole_key_stat, player_sg_for_key_stat,
            round_style (dict from generate_round_style)

    Returns:
        dict with score_relative_to_par, momentum_after, is_birdie,
        is_bogey, is_double, is_eagle
    """
    par = cfg["par"]
    difficulty_rank = cfg["difficulty_rank"]
    birdie_rate = cfg["birdie_rate"]
    bogey_rate = cfg["bogey_rate"]
    double_rate = cfg["double_rate"]
    eagle_rate = cfg.get("eagle_rate", 0.0)
    momentum = cfg.get("momentum", 0.0)
    streakiness = cfg.get("streakiness", 0.5)
    weather_adj = cfg.get("weather_adj", 0.0)
    pressure_adj = cfg.get("pressure_adj", 0.0)
    fatigue_adj = cfg.get("fatigue_adj", 0.0)
    player_sg_for_key_stat = cfg.get("player_sg_for_key_stat", 0.0)
    round_style = cfg.get("round_style", {})

    # Derived adjustments
    difficulty_adj = (19 - difficulty_rank) / 18 * 0.03
    key_stat_bonus = player_sg_for_key_stat * 0.015
    weather_penalty = abs(weather_adj) * 0.02
    fatigue_penalty = fatigue_adj * 0.01

    round_birdie_adj = round_style.get("birdie_adj", 0.0)
    round_bogey_adj = round_style.get("bogey_adj", 0.0)

    # Compute effective rates
    eff_birdie = clamp(
        birdie_rate + round_birdie_adj + momentum * streakiness * 0.02
        + key_stat_bonus - weather_penalty - fatigue_penalty - pressure_adj * 0.01,
        0.02, 0.55
    )
    eff_bogey = clamp(
        bogey_rate - round_bogey_adj - momentum * streakiness * 0.01
        + weather_penalty + fatigue_penalty + difficulty_adj + pressure_adj * 0.005,
        0.05, 0.45
    )
    eff_double = clamp(
        double_rate + weather_penalty * 0.5 + fatigue_penalty * 0.3
        + pressure_adj * 0.003,
        0.01, 0.15
    )

    # Eagle only on par 5s
    if par == 5 and eagle_rate > 0:
        eff_eagle = clamp(eagle_rate + key_stat_bonus * 0.5, 0.005, 0.10)
    else:
        eff_eagle = 0.0

    # Normalize so probabilities sum to <= 1.0
    total_prob = eff_eagle + eff_birdie + eff_bogey + eff_double
    if total_prob > 0.95:
        scale = 0.95 / total_prob
        eff_eagle *= scale
        eff_birdie *= scale
        eff_bogey *= scale
        eff_double *= scale

    # Roll outcome
    roll = random.random()
    cumulative = 0.0

    # Eagle
    cumulative += eff_eagle
    if roll < cumulative:
        new_momentum = clamp(momentum + 2.0, -3, 3)
        return {
            "score_relative_to_par": -2,
            "momentum_after": new_momentum,
            "is_birdie": False,
            "is_bogey": False,
            "is_double": False,
            "is_eagle": True,
        }

    # Birdie
    cumulative += eff_birdie
    if roll < cumulative:
        new_momentum = clamp(momentum + 1.0, -3, 3)
        return {
            "score_relative_to_par": -1,
            "momentum_after": new_momentum,
            "is_birdie": True,
            "is_bogey": False,
            "is_double": False,
            "is_eagle": False,
        }

    # Bogey
    cumulative += eff_bogey
    if roll < cumulative:
        new_momentum = clamp(momentum - 1.0, -3, 3)
        return {
            "score_relative_to_par": 1,
            "momentum_after": new_momentum,
            "is_birdie": False,
            "is_bogey": True,
            "is_double": False,
            "is_eagle": False,
        }

    # Double bogey or worse
    cumulative += eff_double
    if roll < cumulative:
        new_momentum = clamp(momentum - 2.0, -3, 3)
        return {
            "score_relative_to_par": 2,
            "momentum_after": new_momentum,
            "is_birdie": False,
            "is_bogey": False,
            "is_double": True,
            "is_eagle": False,
        }

    # Par — momentum decays toward zero
    new_momentum = momentum * STREAK_DECAY
    return {
        "score_relative_to_par": 0,
        "momentum_after": new_momentum,
        "is_birdie": False,
        "is_bogey": False,
        "is_double": False,
        "is_eagle": False,
    }


# ─── Round Simulation ────────────────────────────────────────────────────────

def _get_birdie_rate_for_par(player_params: dict, par: int) -> float:
    """Look up the per-par birdie rate from player sim params."""
    if par == 3:
        return player_params.get("birdie_rate_par3", 0.12)
    elif par == 5:
        return player_params.get("birdie_rate_par5", 0.45)
    else:
        return player_params.get("birdie_rate_par4", 0.18)


def _get_bogey_rate_for_par(player_params: dict, par: int) -> float:
    """Look up the per-par bogey rate from player sim params."""
    if par == 3:
        return player_params.get("bogey_rate_par3", 0.22)
    elif par == 5:
        return player_params.get("bogey_rate_par5", 0.12)
    else:
        return player_params.get("bogey_rate_par4", 0.20)


def _generate_synthetic_holes(course_par: int) -> List[dict]:
    """Generate synthetic 18-hole layout when per-hole data is unavailable."""
    holes = []
    # Standard distribution: 4 par-3s, 10 par-4s, 4 par-5s for par 72
    if course_par <= 70:
        pars = [3, 3, 3, 3, 4, 4, 4, 4, 4, 4, 4, 4, 4, 4, 5, 5, 5, 5]
        # Adjust to hit target par
        diff = course_par - sum(pars)
        idx = 0
        while diff < 0 and idx < len(pars):
            if pars[idx] == 5:
                pars[idx] = 4
                diff += 1
            idx += 1
    elif course_par >= 73:
        pars = [3, 3, 3, 3, 4, 4, 4, 4, 4, 4, 4, 4, 4, 4, 5, 5, 5, 5]
        diff = course_par - sum(pars)
        idx = 0
        while diff > 0 and idx < len(pars):
            if pars[idx] == 4:
                pars[idx] = 5
                diff -= 1
            idx += 1
    else:
        pars = [3, 3, 3, 3, 4, 4, 4, 4, 4, 4, 4, 4, 4, 4, 5, 5, 5, 5]

    sg_cats = ["sg_ott", "sg_app", "sg_arg", "sg_putt"]
    for i in range(18):
        holes.append({
            "hole": i + 1,
            "par": pars[i],
            "yardage": 180 if pars[i] == 3 else (430 if pars[i] == 4 else 540),
            "difficulty_rank": ((i * 7 + 3) % 18) + 1,  # pseudo-random spread
            "key_stat": sg_cats[i % 4],
            "water": False,
            "bunkers": 2,
        })
    return holes


def sim_round(player_params: dict, holes: List[dict], round_number: int,
              round_config: Optional[dict] = None) -> dict:
    """Simulate 18 holes for one player in one round.

    Analogous to sim_half() in basketball (mc_engine.py lines 67-76).

    Args:
        player_params: dict from build_player_sim_params() in golf_sim_params.py
        holes: list of 18 hole dicts from course profile
        round_number: 1-4
        round_config: optional dict with carry_momentum, is_weekend,
                      current_position, weather

    Returns:
        dict with score_to_par, total_score, hole_scores, birdies, bogeys,
        doubles_plus, eagles, max_hot_streak, max_cold_streak, momentum
    """
    if round_config is None:
        round_config = {}

    # Generate round style
    volatility = player_params.get("round_volatility", 2.8)
    streakiness = player_params.get("streakiness", 0.5)
    style = generate_round_style(volatility, streakiness)

    # Round-level adjustments
    pressure_adj = 0.0
    if round_number >= 3:
        pressure_adj = player_params.get("pressure_modifier", 0.0) * 0.02 * (round_number - 2)
        # Position pressure: top 5 on weekend increases pressure
        current_pos = round_config.get("current_position", 999)
        if current_pos <= 5:
            pressure_adj *= 1.5

    fatigue_base = player_params.get("fatigue_factor", 0.5) * 0.01 * (round_number - 1)

    weather_adj = player_params.get("weather_adj", 0.0)
    weather_resilience = player_params.get("weather_resilience", 0.5)
    # Scale weather impact by resilience (higher resilience = less impact)
    effective_weather = weather_adj * (1.0 - weather_resilience * 0.5)

    # Per-round weather from config
    if round_config.get("weather"):
        rw = round_config["weather"]
        effective_weather = rw.get("combined_adj", effective_weather)

    # Initialize tracking
    momentum = round_config.get("carry_momentum", 0.0)
    score_to_par = 0
    hole_scores = []
    birdies = 0
    bogeys = 0
    doubles_plus = 0
    eagles = 0

    # Streak tracking
    current_hot = 0
    current_cold = 0
    max_hot = 0
    max_cold = 0

    course_par = sum(h["par"] for h in holes)

    for i, hole in enumerate(holes):
        par = hole["par"]
        difficulty_rank = hole.get("difficulty_rank", 9)
        key_stat = hole.get("key_stat", "sg_app")

        # Fatigue increases through the round, especially holes 14-18
        hole_progress = (i + 1) / 18.0
        fatigue_adj = fatigue_base + (0.5 if i >= 13 else 0.0) * player_params.get("fatigue_factor", 0.5) * 0.01

        # Player SG for the key stat this hole demands
        player_sg_for_key = player_params.get(key_stat, 0.0)

        hole_result = sim_hole({
            "par": par,
            "difficulty_rank": difficulty_rank,
            "birdie_rate": _get_birdie_rate_for_par(player_params, par),
            "bogey_rate": _get_bogey_rate_for_par(player_params, par),
            "double_rate": player_params.get("double_rate", 0.03),
            "eagle_rate": player_params.get("eagle_rate_par5", 0.04) if par == 5 else 0.0,
            "momentum": momentum,
            "streakiness": streakiness,
            "weather_adj": effective_weather,
            "pressure_adj": pressure_adj,
            "fatigue_adj": fatigue_adj,
            "hole_key_stat": key_stat,
            "player_sg_for_key_stat": player_sg_for_key,
            "round_style": style,
        })

        rel_score = hole_result["score_relative_to_par"]
        momentum = hole_result["momentum_after"]
        score_to_par += rel_score
        hole_scores.append(rel_score)

        if hole_result["is_eagle"]:
            eagles += 1
            current_hot += 1
            current_cold = 0
        elif hole_result["is_birdie"]:
            birdies += 1
            current_hot += 1
            current_cold = 0
        elif hole_result["is_bogey"]:
            bogeys += 1
            current_cold += 1
            current_hot = 0
        elif hole_result["is_double"]:
            doubles_plus += 1
            current_cold += 1
            current_hot = 0
        else:
            # Par: reset streaks
            current_hot = 0
            current_cold = 0

        max_hot = max(max_hot, current_hot)
        max_cold = max(max_cold, current_cold)

    return {
        "score_to_par": score_to_par,
        "total_score": course_par + score_to_par,
        "hole_scores": hole_scores,
        "birdies": birdies,
        "bogeys": bogeys,
        "doubles_plus": doubles_plus,
        "eagles": eagles,
        "max_hot_streak": max_hot,
        "max_cold_streak": max_cold,
        "momentum": momentum,
    }


# ─── Tournament Simulation ──────────────────────────────────────────────────

def sim_tournament_single(players: List[dict], holes: List[dict],
                          weather_per_round: Optional[List[dict]] = None) -> dict:
    """Simulate one complete 4-round tournament with cut after Round 2.

    Analogous to one bracket simulation in bracket_simulator.py.

    Args:
        players: list of player param dicts from build_player_sim_params()
        holes: list of 18 hole dicts from course profile
        weather_per_round: optional list of 4 weather dicts

    Returns:
        dict with standings, cut_line, players_made_cut, winner
    """
    if not holes or len(holes) != 18:
        # Try to generate synthetic holes
        holes = _generate_synthetic_holes(DEFAULT_PAR)

    course_par = sum(h["par"] for h in holes)

    # Track player state across rounds
    player_state = {}
    for p in players:
        name = p.get("_player_name", f"Player_{id(p)}")
        player_state[name] = {
            "params": p,
            "rounds": [],
            "total_to_par": 0,
            "momentum": 0.0,
            "made_cut": True,
            "birdies": 0,
            "bogeys": 0,
            "doubles_plus": 0,
            "eagles": 0,
        }

    # ── Rounds 1 & 2: Full field ──
    for rd in range(1, 3):
        weather = None
        if weather_per_round and len(weather_per_round) >= rd:
            weather = weather_per_round[rd - 1]

        for name, state in player_state.items():
            rd_config = {
                "carry_momentum": state["momentum"] * MOMENTUM_CARRY_DECAY,
                "is_weekend": False,
                "current_position": 999,
                "weather": weather,
            }
            result = sim_round(state["params"], holes, rd, rd_config)
            state["rounds"].append(result["score_to_par"])
            state["total_to_par"] += result["score_to_par"]
            state["momentum"] = result["momentum"]
            state["birdies"] += result["birdies"]
            state["bogeys"] += result["bogeys"]
            state["doubles_plus"] += result["doubles_plus"]
            state["eagles"] += result["eagles"]

    # ── Cut after Round 2 ──
    # Sort by 36-hole total
    sorted_after_r2 = sorted(
        player_state.items(),
        key=lambda x: x[1]["total_to_par"]
    )

    # Cut at position 65 + ties
    if len(sorted_after_r2) > CUT_LINE_POSITION:
        cut_score = sorted_after_r2[CUT_LINE_POSITION - 1][1]["total_to_par"]
        for name, state in player_state.items():
            if state["total_to_par"] > cut_score:
                state["made_cut"] = False
    else:
        cut_score = sorted_after_r2[-1][1]["total_to_par"] if sorted_after_r2 else 0

    players_made_cut = sum(1 for s in player_state.values() if s["made_cut"])

    # ── Rounds 3 & 4: Weekend rounds, only players who made cut ──
    for rd in range(3, 5):
        weather = None
        if weather_per_round and len(weather_per_round) >= rd:
            weather = weather_per_round[rd - 1]

        # Compute current positions for weekend pressure
        active = [(n, s) for n, s in player_state.items() if s["made_cut"]]
        active.sort(key=lambda x: x[1]["total_to_par"])
        position_map = {}
        for pos, (n, _) in enumerate(active, 1):
            position_map[n] = pos

        for name, state in player_state.items():
            if not state["made_cut"]:
                continue

            rd_config = {
                "carry_momentum": state["momentum"] * MOMENTUM_CARRY_DECAY,
                "is_weekend": True,
                "current_position": position_map.get(name, 999),
                "weather": weather,
            }
            result = sim_round(state["params"], holes, rd, rd_config)
            state["rounds"].append(result["score_to_par"])
            state["total_to_par"] += result["score_to_par"]
            state["momentum"] = result["momentum"]
            state["birdies"] += result["birdies"]
            state["bogeys"] += result["bogeys"]
            state["doubles_plus"] += result["doubles_plus"]
            state["eagles"] += result["eagles"]

    # ── Build final standings ──
    standings = []
    for name, state in player_state.items():
        total = course_par * len(state["rounds"]) + state["total_to_par"]
        standings.append({
            "player_name": name,
            "total_to_par": state["total_to_par"],
            "total": total,
            "rounds": list(state["rounds"]),
            "made_cut": state["made_cut"],
            "birdies": state["birdies"],
            "bogeys": state["bogeys"],
            "doubles_plus": state["doubles_plus"],
            "eagles": state["eagles"],
        })

    # Sort: made-cut players by total_to_par, then missed-cut players
    standings.sort(key=lambda x: (not x["made_cut"], x["total_to_par"]))

    # Assign positions
    pos = 1
    for i, entry in enumerate(standings):
        if i > 0 and entry["total_to_par"] == standings[i - 1]["total_to_par"] and entry["made_cut"] == standings[i - 1]["made_cut"]:
            entry["position"] = standings[i - 1]["position"]
        else:
            entry["position"] = pos
        pos += 1

    # Determine winner (handle ties with playoff)
    made_cut_standings = [s for s in standings if s["made_cut"]]
    if made_cut_standings:
        best_score = made_cut_standings[0]["total_to_par"]
        tied_for_lead = [s for s in made_cut_standings if s["total_to_par"] == best_score]
        if len(tied_for_lead) == 1:
            winner = tied_for_lead[0]["player_name"]
        else:
            # Playoff: sudden death, weighted by SG
            winner = _simulate_playoff(tied_for_lead, player_state)
    else:
        winner = standings[0]["player_name"] if standings else ""

    return {
        "standings": standings,
        "cut_line": cut_score,
        "players_made_cut": players_made_cut,
        "winner": winner,
    }


def _simulate_playoff(tied_players: List[dict], player_state: dict) -> str:
    """Simulate a sudden-death playoff between tied players.

    Better players (higher SG) have a weighted advantage.
    """
    # Weight by SG total adj
    weights = []
    names = []
    for tp in tied_players:
        name = tp["player_name"]
        names.append(name)
        sg = player_state[name]["params"].get("sg_total_adj", 0.0)
        # Convert SG to a positive weight (higher SG = more likely to win)
        weights.append(math.exp(sg * 0.3))

    total_weight = sum(weights)
    if total_weight == 0:
        return random.choice(names)

    roll = random.random() * total_weight
    cumulative = 0.0
    for i, w in enumerate(weights):
        cumulative += w
        if roll < cumulative:
            return names[i]
    return names[-1]


# ─── Multi-Simulation Aggregation ────────────────────────────────────────────

def simulate_tournament(players: List[dict], holes: List[dict],
                        num_sims: int = 1000,
                        weather_per_round: Optional[List[dict]] = None) -> dict:
    """Run N tournament simulations and aggregate results.

    Analogous to simulate_game() in basketball (mc_engine.py line 941).

    Args:
        players: list of player param dicts
        holes: list of 18 hole dicts
        num_sims: number of simulations
        weather_per_round: optional list of 4 weather dicts

    Returns:
        dict keyed by player name with win_pct, top5_pct, top10_pct,
        top20_pct, cut_pct, avg_finish, avg_score, avg_birdies_per_round,
        avg_bogeys_per_round, best_finish, worst_finish
    """
    # Initialize accumulators
    accum = {}
    for p in players:
        name = p.get("_player_name", f"Player_{id(p)}")
        accum[name] = {
            "wins": 0,
            "top5": 0,
            "top10": 0,
            "top20": 0,
            "cuts_made": 0,
            "total_finish": 0,
            "total_score_to_par": 0,
            "total_birdies": 0,
            "total_bogeys": 0,
            "total_rounds": 0,
            "best_finish": 999,
            "worst_finish": 0,
        }

    for _ in range(num_sims):
        result = sim_tournament_single(players, holes, weather_per_round)

        winner = result["winner"]
        if winner in accum:
            accum[winner]["wins"] += 1

        for entry in result["standings"]:
            name = entry["player_name"]
            if name not in accum:
                continue

            pos = entry["position"]
            accum[name]["total_finish"] += pos
            accum[name]["total_score_to_par"] += entry["total_to_par"]
            accum[name]["total_birdies"] += entry.get("birdies", 0)
            accum[name]["total_bogeys"] += entry.get("bogeys", 0)

            num_rounds = len(entry["rounds"])
            accum[name]["total_rounds"] += num_rounds

            if entry["made_cut"]:
                accum[name]["cuts_made"] += 1
            if pos <= 5:
                accum[name]["top5"] += 1
            if pos <= 10:
                accum[name]["top10"] += 1
            if pos <= 20:
                accum[name]["top20"] += 1

            accum[name]["best_finish"] = min(accum[name]["best_finish"], pos)
            accum[name]["worst_finish"] = max(accum[name]["worst_finish"], pos)

    # Build output
    output = {}
    for name, a in accum.items():
        total_rounds = max(a["total_rounds"], 1)
        output[name] = {
            "win_pct": a["wins"] / num_sims * 100,
            "top5_pct": a["top5"] / num_sims * 100,
            "top10_pct": a["top10"] / num_sims * 100,
            "top20_pct": a["top20"] / num_sims * 100,
            "cut_pct": a["cuts_made"] / num_sims * 100,
            "avg_finish": a["total_finish"] / num_sims,
            "avg_score": a["total_score_to_par"] / num_sims,
            "avg_birdies_per_round": a["total_birdies"] / total_rounds,
            "avg_bogeys_per_round": a["total_bogeys"] / total_rounds,
            "best_finish": a["best_finish"] if a["best_finish"] < 999 else 0,
            "worst_finish": a["worst_finish"],
        }

    return output


# ─── Head-to-Head Matchup Simulation ────────────────────────────────────────

def simulate_matchup(p1_params: dict, p2_params: dict, holes: List[dict],
                     num_sims: int = 1000) -> dict:
    """H2H matchup simulation: who finishes higher in a tournament.

    For matchup betting markets.

    Args:
        p1_params: player 1 sim params dict
        p2_params: player 2 sim params dict
        holes: list of 18 hole dicts
        num_sims: number of simulations

    Returns:
        dict with p1_name, p2_name, p1_win_pct, p2_win_pct, tie_pct,
        p1_avg_margin
    """
    p1_name = p1_params.get("_player_name", "Player1")
    p2_name = p2_params.get("_player_name", "Player2")

    p1_wins = 0
    p2_wins = 0
    ties = 0
    total_margin = 0.0

    for _ in range(num_sims):
        # Simulate 4 rounds for each player independently
        p1_total = 0
        p2_total = 0
        p1_mom = 0.0
        p2_mom = 0.0

        for rd in range(1, 5):
            p1_config = {
                "carry_momentum": p1_mom * MOMENTUM_CARRY_DECAY,
                "is_weekend": rd >= 3,
                "current_position": 999,
            }
            p2_config = {
                "carry_momentum": p2_mom * MOMENTUM_CARRY_DECAY,
                "is_weekend": rd >= 3,
                "current_position": 999,
            }

            p1_result = sim_round(p1_params, holes, rd, p1_config)
            p2_result = sim_round(p2_params, holes, rd, p2_config)

            p1_total += p1_result["score_to_par"]
            p2_total += p2_result["score_to_par"]
            p1_mom = p1_result["momentum"]
            p2_mom = p2_result["momentum"]

        margin = p2_total - p1_total  # positive = p1 better
        total_margin += margin

        if p1_total < p2_total:
            p1_wins += 1
        elif p2_total < p1_total:
            p2_wins += 1
        else:
            ties += 1

    return {
        "p1_name": p1_name,
        "p2_name": p2_name,
        "p1_win_pct": p1_wins / num_sims * 100,
        "p2_win_pct": p2_wins / num_sims * 100,
        "tie_pct": ties / num_sims * 100,
        "p1_avg_margin": total_margin / num_sims,
    }
