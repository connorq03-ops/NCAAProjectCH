"""
matchup_params.py - Build MC simulation parameters from KenPom data

Mirrors the parameter computation logic in static/index.html modelMonteCarlo()
for server-side bracket simulation use.

Data flow:
  1. prefetch_all_team_data() → bulk KenPom API calls, indexed by TeamName
  2. build_matchup_params(t1, t2, team_data) → 40+ param dict for mc_engine
  3. flip_matchup_params(params) → swap t1/t2 perspective (for cache reuse)
"""

import os
import json


# ─── League Averages (same as index.html) ────────────────────────────────────

AVG_TO = 17.5
AVG_OR = 28
AVG_FTR = 30
AVG_FTP = 71
AVG_FG2 = 50
AVG_FG3 = 34
AVG_3RATE = 36
AVG_BLK = 9.5
AVG_STL = 9.5
AVG_BENCH = 30
AVG_EFF = 100.0
AVG_TEMPO = 67.5


# ─── Helpers ──────────────────────────────────────────────────────────────────

def clamp(val, lo, hi):
    return max(lo, min(hi, val))


def asymmetric_matchup(off_rate, def_rate, avg_rate, elite_threshold=3.0):
    """Asymmetric weighting: elite extremes get more influence."""
    off_dev = off_rate - avg_rate
    def_dev = def_rate - avg_rate
    off_weight = 0.50
    def_weight = 0.50

    def_extremeness = abs(def_dev) / elite_threshold
    if def_extremeness > 1.0:
        shift = min(def_extremeness * 0.08, 0.22)
        def_weight += shift
        off_weight -= shift

    off_extremeness = abs(off_dev) / elite_threshold
    if off_extremeness > 1.0:
        shift = min(off_extremeness * 0.06, 0.15)
        off_weight += shift
        def_weight -= shift

    total = off_weight + def_weight
    off_weight /= total
    def_weight /= total

    matchup_rate = avg_rate + (off_dev * off_weight) + (def_dev * def_weight)

    same_direction = (off_dev > 0 and def_dev > 0) or (off_dev < 0 and def_dev < 0)
    compound_bonus = min(abs(off_dev * def_dev) * 0.015, 1.5) if same_direction else 0
    compound_sign = 1 if (off_dev + def_dev) > 0 else -1

    return matchup_rate + compound_bonus * compound_sign


def calc_tempo_control(adj_de, to_rate, adj_tempo):
    """Compute how effectively a team imposes its preferred pace (0.30–0.70)."""
    control = 0.50
    def_eliteness = clamp((100 - adj_de) / 10, -1, 1)
    control += def_eliteness * 0.06
    to_carefulness = clamp((AVG_TO - to_rate) / 5, -1, 1)
    control += to_carefulness * 0.04
    tempo_extremeness = abs(adj_tempo - AVG_TEMPO)
    control += tempo_extremeness * 0.008
    return clamp(control, 0.30, 0.70)


def calc_defensive_profile(dto, dor, dopp_fg2, dopp_fg3, stl, blk):
    """Compute perimeter/interior disruption profile (0–1 each)."""
    perimeter = clamp(
        clamp((AVG_FG3 - dopp_fg3) / 4, 0, 1) * 0.40
        + clamp((stl - AVG_STL) / 3, 0, 1) * 0.35
        + clamp((dto - AVG_TO) / 5, 0, 1) * 0.25,
        0, 1)
    interior = clamp(
        clamp((AVG_FG2 - dopp_fg2) / 5, 0, 1) * 0.35
        + clamp((blk - AVG_BLK) / 3, 0, 1) * 0.35
        + clamp((AVG_OR - dor) / 6, 0, 1) * 0.30,
        0, 1)
    return {"perimeter": perimeter, "interior": interior, "overall": (perimeter + interior) / 2}


def calc_3pt_streakiness(rate3, fg3, tempo):
    """Compute 3PT streak volatility modifier (0.6–1.5)."""
    s = 1.0
    s += (rate3 - 33) / 10 * 0.15
    s -= abs(fg3 - 33) * 0.02
    s += (tempo - 67.5) * 0.01
    return clamp(s, 0.6, 1.5)


# ─── Coach Data ───────────────────────────────────────────────────────────────

_coach_data = None

def _load_coach_data():
    global _coach_data
    if _coach_data is None:
        coach_path = os.path.join(os.path.dirname(__file__), "static", "coach_data.json")
        try:
            with open(coach_path, "r") as f:
                _coach_data = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            _coach_data = {}
    return _coach_data


def get_coach_info(team_name):
    """Look up coach data by team name, with dot-normalized fallback."""
    data = _load_coach_data()
    if team_name in data:
        return data[team_name]
    norm = team_name.replace(".", "").strip()
    for key, val in data.items():
        if key.replace(".", "").strip() == norm:
            return val
    return None


# ─── Data Prefetch ────────────────────────────────────────────────────────────

def prefetch_all_team_data(client, cache, year=2026):
    """Fetch all KenPom datasets in bulk and index by TeamName.

    Makes ~5 bulk API calls (ratings, four_factors, height, misc_stats, pointdist).
    All responses are cached via the SQLiteCache.

    Args:
        client: KenpomClient instance
        cache: SQLiteCache instance
        year: Season year

    Returns:
        dict: {TeamName: {ratings, ff, ht, ms, pd, stars, coach}} for all teams
    """
    def cached_fetch(endpoint, fetch_fn, ttl=3600):
        params = {"year": year}
        cached = cache.get(endpoint, params, ttl=ttl)
        if cached is not None:
            return cached
        data = fetch_fn()
        cache.set(endpoint, params, data)
        return data

    ratings_all = cached_fetch("ratings", lambda: client.get_ratings(year=year))
    ff_all = cached_fetch("four-factors", lambda: client.get_four_factors(year=year))
    ht_all = cached_fetch("height", lambda: client.get_height(year=year))
    ms_all = cached_fetch("misc-stats", lambda: client.get_misc_stats(year=year))
    pd_all = cached_fetch("pointdist", lambda: client.get_point_distribution(year=year))

    # Fetch star data (manual + scraped)
    stars_by_team = {}
    try:
        from star_scraper import build_dynamic_stars
        from star_players import STAR_PLAYERS
        d1_teams = {r.get("TeamName", "") for r in (ratings_all if isinstance(ratings_all, list) else [])}
        stars_by_team = build_dynamic_stars(manual_stars=STAR_PLAYERS, d1_teams=d1_teams)
    except Exception:
        # Fallback to manual stars only
        from star_players import STAR_PLAYERS
        for name, info in STAR_PLAYERS.items():
            t = info["team"]
            if t not in stars_by_team:
                stars_by_team[t] = []
            stars_by_team[t].append({"player": name, **info, "source": "manual"})

    # Index everything by TeamName
    def index_by_name(arr):
        if not isinstance(arr, list):
            return {}
        return {item.get("TeamName", ""): item for item in arr if item.get("TeamName")}

    ratings_map = index_by_name(ratings_all)
    ff_map = index_by_name(ff_all)
    ht_map = index_by_name(ht_all)
    ms_map = index_by_name(ms_all)
    pd_map = index_by_name(pd_all)

    team_data = {}
    for team_name in ratings_map:
        team_data[team_name] = {
            "ratings": ratings_map.get(team_name, {}),
            "ff": ff_map.get(team_name, {}),
            "ht": ht_map.get(team_name, {}),
            "ms": ms_map.get(team_name, {}),
            "pd": pd_map.get(team_name, {}),
            "stars": stars_by_team.get(team_name, []),
            "coach": get_coach_info(team_name),
        }

    return team_data


# ─── Main Parameter Builder ──────────────────────────────────────────────────

def build_matchup_params(team1_name, team2_name, team_data,
                         hca1=0, hca2=0, injury_adj=0):
    """Build the full parameter dict for mc_engine.simulate_game().

    Faithfully mirrors modelMonteCarlo() in index.html.

    Args:
        team1_name: KenPom TeamName for team 1
        team2_name: KenPom TeamName for team 2
        team_data: dict from prefetch_all_team_data()
        hca1: Home court advantage for team 1 (0 for neutral/tournament)
        hca2: Home court advantage for team 2 (0 for neutral/tournament)
        injury_adj: Net injury adjustment (positive favors team 1)

    Returns:
        dict compatible with mc_engine.simulate_game()
    """
    d1 = team_data.get(team1_name, {})
    d2 = team_data.get(team2_name, {})

    r1 = d1.get("ratings", {})
    r2 = d2.get("ratings", {})
    ff1 = d1.get("ff", {})
    ff2 = d2.get("ff", {})
    ht1 = d1.get("ht", {})
    ht2 = d2.get("ht", {})
    ms1 = d1.get("ms", {})
    ms2 = d2.get("ms", {})
    pd1 = d1.get("pd", {})
    pd2 = d2.get("pd", {})
    stars1 = d1.get("stars", [])
    stars2 = d2.get("stars", [])
    coach1 = d1.get("coach")
    coach2 = d2.get("coach")

    # ── Offensive Stats ──
    t1_to = ff1.get("TO_Pct") or AVG_TO
    t2_to = ff2.get("TO_Pct") or AVG_TO
    t1_or = ff1.get("OR_Pct") or AVG_OR
    t2_or = ff2.get("OR_Pct") or AVG_OR
    t1_ftr = ff1.get("FT_Rate") or AVG_FTR
    t2_ftr = ff2.get("FT_Rate") or AVG_FTR
    t1_fg2 = ms1.get("FG2Pct") or AVG_FG2
    t2_fg2 = ms2.get("FG2Pct") or AVG_FG2
    t1_fg3 = ms1.get("FG3Pct") or AVG_FG3
    t2_fg3 = ms2.get("FG3Pct") or AVG_FG3
    t1_3rate_base = ms1.get("F3GRate") or AVG_3RATE
    t2_3rate_base = ms2.get("F3GRate") or AVG_3RATE
    t1_ftp = ms1.get("FTPct") or AVG_FTP
    t2_ftp = ms2.get("FTPct") or AVG_FTP

    # Point distribution → 3PT rate adjustment
    t1_pd3_adj = ((pd2.get("DefFg3") or 33) - 33) * 0.15 if pd1 and pd2 else 0
    t2_pd3_adj = ((pd1.get("DefFg3") or 33) - 33) * 0.15 if pd1 and pd2 else 0
    t1_3rate = clamp(t1_3rate_base + t1_pd3_adj, 20, 55)
    t2_3rate = clamp(t2_3rate_base + t2_pd3_adj, 20, 55)

    # ── Defensive Stats ──
    t1_dto = ff1.get("DTO_Pct") or AVG_TO
    t2_dto = ff2.get("DTO_Pct") or AVG_TO
    t1_dor = ff1.get("DOR_Pct") or AVG_OR
    t2_dor = ff2.get("DOR_Pct") or AVG_OR
    t1_dftr = ff1.get("DFT_Rate") or AVG_FTR
    t2_dftr = ff2.get("DFT_Rate") or AVG_FTR
    t1_opp_fg2 = ms1.get("OppFG2Pct") or AVG_FG2
    t2_opp_fg2 = ms2.get("OppFG2Pct") or AVG_FG2
    t1_opp_fg3 = ms1.get("OppFG3Pct") or AVG_FG3
    t2_opp_fg3 = ms2.get("OppFG3Pct") or AVG_FG3
    t1_blk = ms1.get("BlockPct") or AVG_BLK
    t2_blk = ms2.get("BlockPct") or AVG_BLK
    _stl1 = ms1.get("StlRate")
    t1_stl = (_stl1 * 100) if _stl1 is not None else AVG_STL
    _stl2 = ms2.get("StlRate")
    t2_stl = (_stl2 * 100) if _stl2 is not None else AVG_STL

    # ── Matchup-Adjusted Rates (Asymmetric Weighting) ──
    m_t1_to = clamp(asymmetric_matchup(t1_to, t2_dto, AVG_TO, 3.0), 8, 28)
    m_t2_to = clamp(asymmetric_matchup(t2_to, t1_dto, AVG_TO, 3.0), 8, 28)
    m_t1_or = clamp(asymmetric_matchup(t1_or, t2_dor, AVG_OR, 4.0), 15, 42)
    m_t2_or = clamp(asymmetric_matchup(t2_or, t1_dor, AVG_OR, 4.0), 15, 42)
    m_t1_ftr = clamp(asymmetric_matchup(t1_ftr, t2_dftr, AVG_FTR, 4.0), 15, 50)
    m_t2_ftr = clamp(asymmetric_matchup(t2_ftr, t1_dftr, AVG_FTR, 4.0), 15, 50)

    t1_blk_adj = (t2_blk - AVG_BLK) * 0.18
    t2_blk_adj = (t1_blk - AVG_BLK) * 0.18
    m_t1_fg2 = clamp(asymmetric_matchup(t1_fg2, t2_opp_fg2, AVG_FG2, 3.0) - t1_blk_adj, 30, 65)
    m_t2_fg2 = clamp(asymmetric_matchup(t2_fg2, t1_opp_fg2, AVG_FG2, 3.0) - t2_blk_adj, 30, 65)
    m_t1_fg3 = clamp(asymmetric_matchup(t1_fg3, t2_opp_fg3, AVG_FG3, 2.5), 22, 44)
    m_t2_fg3 = clamp(asymmetric_matchup(t2_fg3, t1_opp_fg3, AVG_FG3, 2.5), 22, 44)
    m_t2_steal_rate = clamp(asymmetric_matchup(AVG_STL, t2_stl, AVG_STL, 2.0), 5, 16)
    m_t1_steal_rate = clamp(asymmetric_matchup(AVG_STL, t1_stl, AVG_STL, 2.0), 5, 16)

    # ── Height → Rebounding Edge ──
    t1_hgt = ht1.get("AvgHgt") or 77.5
    t2_hgt = ht2.get("AvgHgt") or 77.5
    t1_hgt_eff = ht1.get("HgtEff") or 0
    t2_hgt_eff = ht2.get("HgtEff") or 0
    t1_hgt_or_bonus = clamp((t1_hgt - t2_hgt) * 0.8 + (t1_hgt_eff - t2_hgt_eff) * 0.4, -3, 3)

    # ── Tempo Tug-of-War (Plan 07) ──
    t1_tempo = r1.get("AdjTempo") or 67
    t2_tempo = r2.get("AdjTempo") or 67
    t1_adj_de = r1.get("AdjDE") or 100
    t2_adj_de = r2.get("AdjDE") or 100
    t1_tempo_control = calc_tempo_control(t1_adj_de, t1_to, t1_tempo)
    t2_tempo_control = calc_tempo_control(t2_adj_de, t2_to, t2_tempo)
    total_ctrl = t1_tempo_control + t2_tempo_control
    game_tempo_ctr = (t1_tempo * t1_tempo_control + t2_tempo * t2_tempo_control) / total_ctrl

    # ── Defensive Disruption Profiles ──
    t1_def_profile = calc_defensive_profile(t1_dto, t1_dor, t1_opp_fg2, t1_opp_fg3, t1_stl, t1_blk)
    t2_def_profile = calc_defensive_profile(t2_dto, t2_dor, t2_opp_fg2, t2_opp_fg3, t2_stl, t2_blk)

    # ── Matchup-Specific 3PT Suppression ──
    t1_3heavy = (t1_3rate_base - AVG_3RATE) / AVG_3RATE
    t2_3heavy = (t2_3rate_base - AVG_3RATE) / AVG_3RATE
    t1_perim_penalty = clamp(t1_3heavy * t2_def_profile["perimeter"] * 3.0, 0, 2.5)
    t2_perim_penalty = clamp(t2_3heavy * t1_def_profile["perimeter"] * 3.0, 0, 2.5)
    m_t1_fg3_final = clamp(m_t1_fg3 - t1_perim_penalty, 22, 44)
    m_t2_fg3_final = clamp(m_t2_fg3 - t2_perim_penalty, 22, 44)

    # ── Style Tendency ──
    t1_style_bias = (t1_3rate_base - AVG_3RATE) * -0.04 + (t1_ftr - AVG_FTR) * 0.03
    t2_style_bias = (t2_3rate_base - AVG_3RATE) * -0.04 + (t2_ftr - AVG_FTR) * 0.03

    # ── Bench Depth → Volatility ──
    t1_bench = ht1.get("Bench") or AVG_BENCH
    t2_bench = ht2.get("Bench") or AVG_BENCH
    t1_vol_mod = 1 + (AVG_BENCH - t1_bench) * 0.012
    t2_vol_mod = 1 + (AVG_BENCH - t2_bench) * 0.012

    # ── Coach Experience ──
    c1_exp = ((coach1.get("elite8", 0) if coach1 else 0)
              + (coach1.get("finalFour", 0) if coach1 else 0) * 1.5
              + (coach1.get("titles", 0) if coach1 else 0) * 2)
    c2_exp = ((coach2.get("elite8", 0) if coach2 else 0)
              + (coach2.get("finalFour", 0) if coach2 else 0) * 1.5
              + (coach2.get("titles", 0) if coach2 else 0) * 2)
    coach_edge = clamp((c1_exp - c2_exp) * 0.04, -0.8, 0.8)
    ft_clutch_edge = (t1_ftp - t2_ftp) * 0.08

    # ── Plan 06: Star Foul Proneness ──
    s1_has_high_usage = any((s.get("impact", 0) >= 8) for s in stars1)
    s2_has_high_usage = any((s.get("impact", 0) >= 8) for s in stars2)
    t1_star_foul_proneness = clamp(
        (t1_ftr - AVG_FTR) * 0.02
        + (t2_stl - AVG_STL) * 0.03
        + (0.15 if s1_has_high_usage else 0),
        0, 1)
    t2_star_foul_proneness = clamp(
        (t2_ftr - AVG_FTR) * 0.02
        + (t1_stl - AVG_STL) * 0.03
        + (0.15 if s2_has_high_usage else 0),
        0, 1)

    # ── Plan 08: 3PT Streak Volatility ──
    t1_streakiness = calc_3pt_streakiness(t1_3rate_base, t1_fg3, t1_tempo)
    t2_streakiness = calc_3pt_streakiness(t2_3rate_base, t2_fg3, t2_tempo)

    # ── Plan 09: Referee Foul Climate ──
    ref_foul_climate = 1.0  # Default; future: integrate actual referee data

    # ── Enrichment Adjustments ──
    total_adj = injury_adj * (game_tempo_ctr / 100)

    # ── KenPom Calibration ──
    kp_t1_exp_oe = (r1.get("AdjOE") or AVG_EFF) + (r2.get("AdjDE") or AVG_EFF) - AVG_EFF
    kp_t2_exp_oe = (r2.get("AdjOE") or AVG_EFF) + (r1.get("AdjDE") or AVG_EFF) - AVG_EFF

    t1_adj_em = r1.get("AdjEM") or 0
    t2_adj_em = r2.get("AdjEM") or 0
    t1_favored = (t1_adj_em + hca1 * 2) > (t2_adj_em + hca2 * 2)

    # ── Build final params dict (snake_case for mc_engine) ──
    return {
        "hca1": hca1,
        "hca2": hca2,
        "game_tempo_ctr": game_tempo_ctr,
        "t1_preferred_tempo": t1_tempo,
        "t2_preferred_tempo": t2_tempo,
        "t1_tempo_control": t1_tempo_control,
        "t2_tempo_control": t2_tempo_control,
        "m_t1_fg2": m_t1_fg2,
        "m_t2_fg2": m_t2_fg2,
        "m_t1_fg3": m_t1_fg3_final,
        "m_t2_fg3": m_t2_fg3_final,
        "m_t1_to": m_t1_to,
        "m_t2_to": m_t2_to,
        "m_t1_or": m_t1_or,
        "m_t2_or": m_t2_or,
        "m_t1_ftr": m_t1_ftr,
        "m_t2_ftr": m_t2_ftr,
        "m_t1_steal_rate": m_t1_steal_rate,
        "m_t2_steal_rate": m_t2_steal_rate,
        "t1_3rate": t1_3rate,
        "t2_3rate": t2_3rate,
        "t1_ftp": t1_ftp,
        "t2_ftp": t2_ftp,
        "t1_vol_mod": t1_vol_mod,
        "t2_vol_mod": t2_vol_mod,
        "t1_hgt_or_bonus": t1_hgt_or_bonus,
        "t1_bench": t1_bench,
        "t2_bench": t2_bench,
        "t1_style_bias": t1_style_bias,
        "t2_style_bias": t2_style_bias,
        "t1_def_profile": t1_def_profile,
        "t2_def_profile": t2_def_profile,
        "coach_edge": coach_edge,
        "ft_clutch_edge": ft_clutch_edge,
        "total_adj": total_adj,
        "kp_t1_exp_oe": kp_t1_exp_oe,
        "kp_t2_exp_oe": kp_t2_exp_oe,
        "t1_favored": t1_favored,
        "stars1": stars1,
        "stars2": stars2,
        "t1_star_foul_proneness": t1_star_foul_proneness,
        "t2_star_foul_proneness": t2_star_foul_proneness,
        "t1_streakiness": t1_streakiness,
        "t2_streakiness": t2_streakiness,
        "ref_foul_climate": ref_foul_climate,
        # Metadata (not used by sim, useful for display)
        "_team1": team1_name,
        "_team2": team2_name,
        "_t1_adj_em": t1_adj_em,
        "_t2_adj_em": t2_adj_em,
    }


def flip_matchup_params(params):
    """Swap t1/t2 perspective in a params dict.

    Useful for cache reuse: if we have Duke vs UConn params,
    we can derive UConn vs Duke without recomputation.
    """
    return {
        "hca1": params["hca2"],
        "hca2": params["hca1"],
        "game_tempo_ctr": params["game_tempo_ctr"],
        "t1_preferred_tempo": params["t2_preferred_tempo"],
        "t2_preferred_tempo": params["t1_preferred_tempo"],
        "t1_tempo_control": params["t2_tempo_control"],
        "t2_tempo_control": params["t1_tempo_control"],
        "m_t1_fg2": params["m_t2_fg2"],
        "m_t2_fg2": params["m_t1_fg2"],
        "m_t1_fg3": params["m_t2_fg3"],
        "m_t2_fg3": params["m_t1_fg3"],
        "m_t1_to": params["m_t2_to"],
        "m_t2_to": params["m_t1_to"],
        "m_t1_or": params["m_t2_or"],
        "m_t2_or": params["m_t1_or"],
        "m_t1_ftr": params["m_t2_ftr"],
        "m_t2_ftr": params["m_t1_ftr"],
        "m_t1_steal_rate": params["m_t2_steal_rate"],
        "m_t2_steal_rate": params["m_t1_steal_rate"],
        "t1_3rate": params["t2_3rate"],
        "t2_3rate": params["t1_3rate"],
        "t1_ftp": params["t2_ftp"],
        "t2_ftp": params["t1_ftp"],
        "t1_vol_mod": params["t2_vol_mod"],
        "t2_vol_mod": params["t1_vol_mod"],
        "t1_hgt_or_bonus": -params["t1_hgt_or_bonus"],
        "t1_bench": params["t2_bench"],
        "t2_bench": params["t1_bench"],
        "t1_style_bias": params["t2_style_bias"],
        "t2_style_bias": params["t1_style_bias"],
        "t1_def_profile": params["t2_def_profile"],
        "t2_def_profile": params["t1_def_profile"],
        "coach_edge": -params["coach_edge"],
        "ft_clutch_edge": -params["ft_clutch_edge"],
        "total_adj": -params["total_adj"],
        "kp_t1_exp_oe": params["kp_t2_exp_oe"],
        "kp_t2_exp_oe": params["kp_t1_exp_oe"],
        "t1_favored": not params["t1_favored"],
        "stars1": params["stars2"],
        "stars2": params["stars1"],
        "t1_star_foul_proneness": params["t2_star_foul_proneness"],
        "t2_star_foul_proneness": params["t1_star_foul_proneness"],
        "t1_streakiness": params["t2_streakiness"],
        "t2_streakiness": params["t1_streakiness"],
        "ref_foul_climate": params["ref_foul_climate"],
        "_team1": params["_team2"],
        "_team2": params["_team1"],
        "_t1_adj_em": params["_t2_adj_em"],
        "_t2_adj_em": params["_t1_adj_em"],
    }


def matchup_cache_key(team1, team2):
    """Generate a canonical cache key for a matchup (sorted alphabetically)."""
    return tuple(sorted([team1, team2]))


# ─── Self-test (offline, no API calls) ────────────────────────────────────────

if __name__ == "__main__":
    print("=" * 60)
    print("  matchup_params.py — Parameter Builder Self-Test")
    print("=" * 60)

    # Test helper functions
    print("\nasymmetric_matchup tests:")
    print(f"  avg off vs avg def: {asymmetric_matchup(50, 50, 50, 3.0):.1f} (expect ~50.0)")
    print(f"  elite off vs avg def: {asymmetric_matchup(56, 50, 50, 3.0):.1f} (expect >53)")
    print(f"  avg off vs elite def: {asymmetric_matchup(50, 44, 50, 3.0):.1f} (expect <47)")
    print(f"  elite vs elite (opposing): {asymmetric_matchup(56, 44, 50, 3.0):.1f} (expect ~50)")

    print("\ncalc_tempo_control tests:")
    print(f"  elite D (90), low TO, fast: {calc_tempo_control(90, 14, 73):.3f} (expect >0.55)")
    print(f"  avg D (100), avg TO, avg tempo: {calc_tempo_control(100, 17.5, 67.5):.3f} (expect ~0.50)")
    print(f"  poor D (110), high TO, slow: {calc_tempo_control(110, 22, 62):.3f} (expect <0.48)")

    print("\ncalc_defensive_profile tests:")
    dp = calc_defensive_profile(20, 25, 47, 30, 12, 12)
    print(f"  elite D: peri={dp['perimeter']:.2f} int={dp['interior']:.2f} ovr={dp['overall']:.2f}")
    dp2 = calc_defensive_profile(17, 28, 50, 34, 9, 9)
    print(f"  avg D:   peri={dp2['perimeter']:.2f} int={dp2['interior']:.2f} ovr={dp2['overall']:.2f}")

    print("\ncoach_data loaded:", len(_load_coach_data()), "teams")
    duke_coach = get_coach_info("Duke")
    print(f"  Duke coach: {duke_coach}")
    msu_coach = get_coach_info("Michigan St.")
    print(f"  Michigan St. coach: {msu_coach}")

    print("\nflip_matchup_params test:")
    fake = {
        "hca1": 3, "hca2": 0, "game_tempo_ctr": 68,
        "t1_preferred_tempo": 72, "t2_preferred_tempo": 64,
        "t1_tempo_control": 0.55, "t2_tempo_control": 0.45,
        "m_t1_fg2": 52, "m_t2_fg2": 48,
        "m_t1_fg3": 36, "m_t2_fg3": 32,
        "m_t1_to": 16, "m_t2_to": 18,
        "m_t1_or": 30, "m_t2_or": 26,
        "m_t1_ftr": 32, "m_t2_ftr": 28,
        "m_t1_steal_rate": 10, "m_t2_steal_rate": 9,
        "t1_3rate": 38, "t2_3rate": 34,
        "t1_ftp": 76, "t2_ftp": 70,
        "t1_vol_mod": 1.0, "t2_vol_mod": 1.1,
        "t1_hgt_or_bonus": 1.5,
        "t1_bench": 35, "t2_bench": 25,
        "t1_style_bias": 0.1, "t2_style_bias": -0.1,
        "t1_def_profile": {"perimeter": 0.4, "interior": 0.3, "overall": 0.35},
        "t2_def_profile": {"perimeter": 0.2, "interior": 0.25, "overall": 0.225},
        "coach_edge": 0.3, "ft_clutch_edge": 0.2,
        "total_adj": 0.5,
        "kp_t1_exp_oe": 115, "kp_t2_exp_oe": 100,
        "t1_favored": True,
        "stars1": [{"impact": 10}], "stars2": [],
        "t1_star_foul_proneness": 0.2, "t2_star_foul_proneness": 0.1,
        "t1_streakiness": 1.1, "t2_streakiness": 0.9,
        "ref_foul_climate": 1.0,
        "_team1": "Duke", "_team2": "Siena",
        "_t1_adj_em": 30, "_t2_adj_em": -5,
    }
    flipped = flip_matchup_params(fake)
    print(f"  Original: {fake['_team1']} vs {fake['_team2']}, coach_edge={fake['coach_edge']}")
    print(f"  Flipped:  {flipped['_team1']} vs {flipped['_team2']}, coach_edge={flipped['coach_edge']}")
    print(f"  FG2 swapped: {fake['m_t1_fg2']}→{flipped['m_t1_fg2']}, {fake['m_t2_fg2']}→{flipped['m_t2_fg2']}")
    assert flipped["m_t1_fg2"] == fake["m_t2_fg2"], "FG2 swap failed"
    assert flipped["coach_edge"] == -fake["coach_edge"], "coach_edge flip failed"
    assert flipped["t1_hgt_or_bonus"] == -fake["t1_hgt_or_bonus"], "hgt bonus flip failed"
    print("  All flip assertions passed!")
