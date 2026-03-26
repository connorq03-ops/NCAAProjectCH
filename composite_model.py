"""
composite_model.py - Python ports of the 4-model composite prediction pipeline

Faithfully ports the following JS functions from static/index.html to Python:
  - gaussianCDF, calibrateSpread, getHCA
  - calcConRat, calcStyleClash, calcExperienceAdj, calcMomentum, calcConfAdj
  - modelEfficiency, modelSimilarOpponents, modelConRat
  - compute_composite (dynamic-weight blending + calibration)

All constants, weights, and clamp ranges are identical to the JS originals.
"""

import math

from matchup_params import get_coach_info


# ─── Helpers ──────────────────────────────────────────────────────────────────

def clamp(val, lo, hi):
    return max(lo, min(hi, val))


# ─── 1. Gaussian CDF (Abramowitz & Stegun approximation) ─────────────────────
# Port of gaussianCDF() — index.html line 997-1005

def gaussian_cdf(x):
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


# ─── 2. Spread Calibration ───────────────────────────────────────────────────
# Port of calibrateSpread() — index.html line 706-724

def calibrate_spread(raw_margin, coeffs=None):
    """Apply piecewise spread calibration.

    Args:
        raw_margin: Raw predicted margin.
        coeffs: Optional dict with keys 'close', 'moderate', 'logMult'
                to override the default 0.92 / 0.85 / 3.5 coefficients.
    """
    close_coeff = 0.92
    moderate_coeff = 0.85
    log_mult = 3.5
    if coeffs:
        close_coeff = coeffs.get('close', close_coeff)
        moderate_coeff = coeffs.get('moderate', moderate_coeff)
        log_mult = coeffs.get('logMult', log_mult)

    sign = 1 if raw_margin >= 0 else -1
    abs_val = abs(raw_margin)
    if abs_val <= 7:
        calibrated = abs_val * close_coeff
    elif abs_val <= 14:
        calibrated = 7 * close_coeff + (abs_val - 7) * moderate_coeff
    else:
        base = 7 * close_coeff + 7 * moderate_coeff
        calibrated = base + math.log(1 + (abs_val - 14) * 0.5) * log_mult
    return sign * calibrated


def calibrate_total(raw_total, coeffs=None):
    """Apply total calibration — compress extreme totals toward the mean.

    NCAA average total is ~140 points. Very high (160+) and very low (<120)
    totals are overpredicted by raw efficiency models.

    Args:
        raw_total: Raw predicted total (t1_score + t2_score)
        coeffs: Optional dict with keys 'center', 'compression' to override defaults.
    """
    center = 140.0  # NCAA average total
    compression = 0.90  # Pull extreme totals 10% toward center
    if coeffs:
        center = coeffs.get('center', center)
        compression = coeffs.get('compression', compression)

    deviation = raw_total - center
    calibrated = center + deviation * compression
    return calibrated


# ─── 3. Conference-tier HCA ──────────────────────────────────────────────────
# Port of getHCA() — index.html line 730-734

POWER_HCA_CONFS = ['SEC', 'B12', 'B10', 'BE', 'ACC', 'P12']
MID_HCA_CONFS = ['MWC', 'Amer', 'A10', 'WCC', 'MVC']


def get_hca(home_conf):
    if home_conf in POWER_HCA_CONFS:
        return 4.2
    if home_conf in MID_HCA_CONFS:
        return 3.6
    return 3.0


# ─── 4. ConRat (Connor Rating v3) — 17-layer composite ──────────────────────
# Port of calcConRat() — index.html line 421-607

def calc_con_rat(team_dict):
    adj_em = team_dict.get('AdjEM', 0) or 0
    luck = team_dict.get('Luck', 0) or 0
    sos = team_dict.get('SOS', 0) or 0
    wins = team_dict.get('Wins', 0) or 0
    losses = team_dict.get('Losses', 0) or 0
    total_games = (wins + losses) or 1
    win_pct = wins / total_games

    ff = team_dict.get('_ff') or {}
    ms = team_dict.get('_ms') or {}
    ht = team_dict.get('_ht') or {}

    # 1. Luck regression
    luck_adj = luck * 30 * 0.80
    regressed_em = adj_em - luck_adj

    # 2. SOS weighting
    sos_multiplier = clamp(1 + sos / 125, 0.92, 1.08)
    sos_adj_em = regressed_em * sos_multiplier

    # 3. Record quality (Pythag-based)
    pythag = team_dict.get('Pythag') or (0.5 + adj_em / 40)
    record_delta = win_pct - pythag
    record_adj = clamp(record_delta * 8, -1.0, 1.0)

    # 4. Coach experience bonus/penalty
    coach = get_coach_info(team_dict.get('TeamName', ''))
    coach_bonus = 0
    if coach:
        e8 = coach.get('elite8', 0) or 0
        f4 = coach.get('finalFour', 0) or 0
        titles = coach.get('titles', 0) or 0
        coach_bonus = min(0.8, (e8 * 0.05 + f4 * 0.08 + titles * 0.12))
    else:
        rank_em = team_dict.get('RankAdjEM', 200) or 200
        if rank_em <= 25:
            coach_bonus = -0.3
        elif rank_em <= 50:
            coach_bonus = -0.2
        elif rank_em <= 80:
            coach_bonus = -0.1

    # 5. NCSOS
    ncsos = team_dict.get('NCSOS', 0) or 0
    ncsos_adj = clamp(ncsos * 0.08, -1.5, 1.5)

    # 6. O/D Balance
    adj_oe_rank = team_dict.get('RankAdjOE', 175) or 175
    adj_de_rank = team_dict.get('RankAdjDE', 175) or 175
    adj_oe = team_dict.get('AdjOE', 100) or 100
    adj_de_val = team_dict.get('AdjDE', 100) or 100
    rank_gap = abs(adj_oe_rank - adj_de_rank)
    if rank_gap <= 100:
        balance_base = 1.2 * (1 - rank_gap / 100) * (1 - rank_gap / 100)
    else:
        balance_base = -0.8 * min(1, (rank_gap - 100) / 150)
    worst_rank = max(adj_oe_rank, adj_de_rank)
    if worst_rank <= 50:
        quality_mult = 1.0
    elif worst_rank <= 100:
        quality_mult = 0.75
    elif worst_rank <= 150:
        quality_mult = 0.5
    else:
        quality_mult = 0.3
    two_way_bonus = (
        min(0.6, ((adj_oe - 104) * 0.04 + (98 - adj_de_val) * 0.04))
        if (adj_oe > 104 and adj_de_val < 98) else 0
    )
    balance_adj = balance_base * quality_mult + two_way_bonus

    # 7. Defensive floor premium
    adj_de = team_dict.get('AdjDE', 100) or 100
    avg_de = 100
    def_premium = min(1.0, (avg_de - adj_de) * 0.06) if adj_de < avg_de else 0

    # 8. SOS Split
    soso = team_dict.get('SOSO', 100) or 100
    sosd = team_dict.get('SOSD', 100) or 100
    sos_split_adj = clamp((soso - 100) * 0.04 + (sosd - 100) * 0.04, -0.8, 0.8)

    # 9. Continuity & Experience
    continuity = ht.get('Continuity', 0.45) or 0.45
    experience = ht.get('Exp', 2.0) or 2.0
    cont_adj = (continuity - 0.45) * 6.0
    exp_adj = (experience - 2.0) * 1.0
    continuity_adj = clamp(cont_adj + exp_adj, -1.8, 1.8)

    # 10. 3PT Variance
    f3g_rate = ms.get('F3GRate', 36) or 36
    fg3_pct = ms.get('FG3Pct', 34) or 34
    three_reliance = max(0, f3g_rate - 36) * 0.08
    three_accuracy_offset = min(0.4, (fg3_pct - 36) * 0.05) if fg3_pct > 36 else 0
    fg2_pct = ms.get('FG2Pct', 50) or 50
    balanced_shot_bonus = 0.3 if (f3g_rate < 34 and fg2_pct > 52) else 0
    three_var_adj = clamp(-three_reliance + three_accuracy_offset + balanced_shot_bonus, -0.8, 0.8)

    # 11. Turnover Discipline
    to_pct = ff.get('TO_Pct', 17.5) or 17.5
    dto_pct = ff.get('DTO_Pct', 17.5) or 17.5
    to_bonus = (17.5 - to_pct) * 0.12
    dto_bonus = (dto_pct - 17.5) * 0.08
    to_discipline_adj = clamp(to_bonus + dto_bonus, -0.7, 0.7)

    # 12. Tempo Volatility
    adj_tempo = team_dict.get('AdjTempo', 67) or 67
    slow_bonus = min(0.3, (65 - adj_tempo) * 0.15) if adj_tempo < 65 else 0
    fast_penalty = min(0.3, (adj_tempo - 71) * 0.08) if adj_tempo > 71 else 0
    tempo_vol_adj = clamp(slow_bonus - fast_penalty, -0.5, 0.5)

    # 13. Possession Length
    apl_off = team_dict.get('APL_Off', 17) or 17
    apl_def = team_dict.get('APL_Def', 17) or 17
    off_patience_adj = (apl_off - 17) * 0.15
    def_grind_adj = (apl_def - 17) * 0.15
    apl_adj = clamp(off_patience_adj + def_grind_adj, -0.5, 0.5)

    # 14. FT Rate Reliability
    ft_rate = ff.get('FT_Rate', 30) or 30
    ft_pct = ms.get('FTPct', 71) or 71
    ft_rate_bonus = max(0, ft_rate - 30) * 0.04
    ft_pct_bonus = min(0.2, (ft_pct - 74) * 0.03) if ft_pct > 74 else 0
    ft_pct_penalty = min(0.2, (68 - ft_pct) * 0.03) if ft_pct < 68 else 0
    ft_adj = clamp(ft_rate_bonus + ft_pct_bonus - ft_pct_penalty, -0.5, 0.5)

    # 15. Bench Depth
    bench = ht.get('Bench', 30) or 30
    bench_bonus = min(0.4, (bench - 30) * 0.04) if bench > 30 else 0
    bench_penalty = min(0.4, (30 - bench) * 0.06) if bench < 30 else 0
    bench_adj = clamp(bench_bonus - bench_penalty, -0.8, 0.8)

    # 16. Aggregate
    raw = (sos_adj_em + record_adj + coach_bonus + ncsos_adj + balance_adj
           + def_premium + sos_split_adj
           + continuity_adj + three_var_adj + to_discipline_adj
           + tempo_vol_adj + apl_adj + ft_adj + bench_adj)
    sign = 1 if raw >= 0 else -1

    # 17. Final scaling — power curve for readable 0-30 range
    abs_raw = abs(raw)
    con_rat = sign * (abs_raw ** 0.82) * (30 / (48 ** 0.82))
    return round(con_rat * 10) / 10


# ─── 5. Four Factors Style Clash ─────────────────────────────────────────────
# Port of calcStyleClash() — index.html line 617-643

def calc_style_clash(ff1, ff2):
    if not ff1 or not ff2:
        return {'adj': 0, 'details': None}

    # T1 offense vs T2 defense
    t1_efg = (ff1.get('eFG_Pct') or 50) - (ff2.get('DeFG_Pct') or 50)
    t1_to = (ff2.get('DTO_Pct') or 19) - (ff1.get('TO_Pct') or 19)
    t1_or = (ff1.get('OR_Pct') or 28) - (ff2.get('DOR_Pct') or 28)
    t1_ftr = (ff1.get('FT_Rate') or 33) - (ff2.get('DFT_Rate') or 33)

    # T2 offense vs T1 defense
    t2_efg = (ff2.get('eFG_Pct') or 50) - (ff1.get('DeFG_Pct') or 50)
    t2_to = (ff1.get('DTO_Pct') or 19) - (ff2.get('TO_Pct') or 19)
    t2_or = (ff2.get('OR_Pct') or 28) - (ff1.get('DOR_Pct') or 28)
    t2_ftr = (ff2.get('FT_Rate') or 33) - (ff1.get('DFT_Rate') or 33)

    # Dean Oliver weights
    w_efg = 0.40
    w_to = 0.25
    w_or = 0.20
    w_ftr = 0.15

    t1_adv = t1_efg * w_efg + t1_to * w_to + t1_or * w_or + t1_ftr * w_ftr
    t2_adv = t2_efg * w_efg + t2_to * w_to + t2_or * w_or + t2_ftr * w_ftr

    raw = (t1_adv - t2_adv) * 0.50
    adj = clamp(raw, -3.0, 3.0)

    return {
        'adj': adj,
        't1_efg': t1_efg, 't1_to': t1_to, 't1_or': t1_or, 't1_ftr': t1_ftr,
        't2_efg': t2_efg, 't2_to': t2_to, 't2_or': t2_or, 't2_ftr': t2_ftr,
        't1Adv': t1_adv, 't2Adv': t2_adv,
    }


# ─── 6. Experience & Continuity Adjustment ───────────────────────────────────
# Port of calcExperienceAdj() — index.html line 647-663

def calc_experience_adj(ht1, ht2):
    if not ht1 or not ht2:
        return {'adj': 0, 'exp1': 0, 'exp2': 0, 'cont1': 0, 'cont2': 0}

    exp1 = ht1.get('Exp') or 2.0
    exp2 = ht2.get('Exp') or 2.0
    cont1 = (ht1.get('Continuity') or 0.5) * 100
    cont2 = (ht2.get('Continuity') or 0.5) * 100

    exp_adj = (exp1 - exp2) * 0.3
    cont_adj = ((cont1 - cont2) / 10) * 0.2

    adj = clamp(exp_adj + cont_adj, -1.5, 1.5)

    return {'adj': adj, 'exp1': exp1, 'exp2': exp2, 'cont1': cont1, 'cont2': cont2,
            'expAdj': exp_adj, 'contAdj': cont_adj}


# ─── 7. Momentum (AdjEM trend) ──────────────────────────────────────────────
# Port of calcMomentum() — index.html line 685-699

def calc_momentum(arch1, arch2, t1, t2):
    if not arch1 or not arch2:
        return {'adj': 0, 't1Trend': 0, 't2Trend': 0}

    t1_now = t1.get('AdjEM', 0) or 0
    t2_now = t2.get('AdjEM', 0) or 0
    t1_then = arch1.get('AdjEM') or t1_now
    t2_then = arch2.get('AdjEM') or t2_now

    t1_trend = t1_now - t1_then
    t2_trend = t2_now - t2_then

    adj = clamp((t1_trend - t2_trend) * 0.15, -1.0, 1.0)

    return {'adj': adj, 't1Trend': t1_trend, 't2Trend': t2_trend}


# ─── 8. Conference Strength Adjustment ───────────────────────────────────────
# Port of calcConfAdj() — index.html line 667-681

def calc_conf_adj(t1, t2, conf_map, conf_overrides=None):
    """Conference strength adjustment.

    Args:
        t1, t2: Team dicts with ConfShort.
        conf_map: {conf_short: rating} from KenPom conf ratings.
        conf_overrides: Optional {conf_short: scale_factor} dict.
                        Default per-conference scale is 0.06.
    """
    if not conf_map:
        return {'adj': 0, 'r1': 0, 'r2': 0, 'conf1': '', 'conf2': ''}

    conf1 = t1.get('ConfShort', '') or ''
    conf2 = t2.get('ConfShort', '') or ''
    r1 = conf_map.get(conf1, 0) or 0
    r2 = conf_map.get(conf2, 0) or 0

    # Per-conference scaling (default 0.06)
    if conf_overrides:
        scale1 = conf_overrides.get(conf1, 0.06)
        scale2 = conf_overrides.get(conf2, 0.06)
        avg_scale = (scale1 + scale2) / 2
    else:
        avg_scale = 0.06
        scale1 = 0.06
        scale2 = 0.06

    raw = (r1 - r2) * avg_scale
    adj = clamp(raw, -1.5, 1.5)

    return {'adj': adj, 'r1': r1, 'r2': r2, 'conf1': conf1, 'conf2': conf2,
            'scale1': scale1, 'scale2': scale2}


# ─── 9. Model 1: KenPom Efficiency (additive) ───────────────────────────────
# Port of modelEfficiency() — index.html line 1018-1057

def model_efficiency(t1, t2, hca1, hca2, extra=None):
    if extra is None:
        extra = {}

    AVG_EFF = 100.0
    avg_tempo = ((t1.get('AdjTempo') or 67) + (t2.get('AdjTempo') or 67)) / 2
    GAME_SD = 11.0 * math.sqrt(avg_tempo / 67)

    t1_luck = t1.get('Luck', 0) or 0
    t2_luck = t2.get('Luck', 0) or 0
    t1_luck_adj = t1_luck * 30 * 0.75
    t2_luck_adj = t2_luck * 30 * 0.75

    t1_exp_oe = (t1.get('AdjOE') or AVG_EFF) + (t2.get('AdjDE') or AVG_EFF) - AVG_EFF
    t2_exp_oe = (t2.get('AdjOE') or AVG_EFF) + (t1.get('AdjDE') or AVG_EFF) - AVG_EFF

    t1_score = t1_exp_oe * (avg_tempo / 100) + hca1 - t1_luck_adj / 2
    t2_score = t2_exp_oe * (avg_tempo / 100) + hca2 - t2_luck_adj / 2

    # Enrichment adjustments
    style_adj = extra.get('style_clash', {}).get('adj', 0) if extra.get('style_clash') else 0
    exp_adj = extra.get('experience', {}).get('adj', 0) if extra.get('experience') else 0
    mom_adj = extra.get('momentum', {}).get('adj', 0) if extra.get('momentum') else 0
    conf_adj = extra.get('conf_strength', {}).get('adj', 0) if extra.get('conf_strength') else 0
    inj_adj = extra.get('injury_adj', 0) or 0

    tempo_scale = avg_tempo / 100
    total_adj = (style_adj + exp_adj + mom_adj + conf_adj + inj_adj) * tempo_scale
    t1_score += total_adj / 2
    t2_score -= total_adj / 2

    margin = t1_score - t2_score

    return {
        't1_score': t1_score,
        't2_score': t2_score,
        'margin': margin,
        't1_win_prob': gaussian_cdf(margin / GAME_SD),
        'tempo': avg_tempo,
    }


# ─── 10. Model 2: Similar Opponents ─────────────────────────────────────────
# Port of modelSimilarOpponents() — index.html line 1065-1120

def model_similar_opponents(t1, t2, hca1, hca2, extra=None):
    if extra is None:
        extra = {}

    AVG_EFF = 100.0

    t1_sos = t1.get('SOS', 0) or 0
    t2_sos = t2.get('SOS', 0) or 0
    avg_sos = (t1_sos + t2_sos) / 2

    t1_sos_bonus = clamp(1 + (t1_sos - avg_sos) / 40, 0.85, 1.15) if avg_sos != 0 else 1
    t2_sos_bonus = clamp(1 + (t2_sos - avg_sos) / 40, 0.85, 1.15) if avg_sos != 0 else 1

    t1_win_pct = (t1.get('Wins', 0) or 0) / max(1, (t1.get('Wins', 0) or 0) + (t1.get('Losses', 0) or 0))
    t2_win_pct = (t2.get('Wins', 0) or 0) / max(1, (t2.get('Wins', 0) or 0) + (t2.get('Losses', 0) or 0))

    t1_adj_em = (t1.get('AdjEM', 0) or 0) * t1_sos_bonus
    t2_adj_em = (t2.get('AdjEM', 0) or 0) * t2_sos_bonus

    t1_record_bonus = (t1_win_pct - 0.5) * 2
    t2_record_bonus = (t2_win_pct - 0.5) * 2

    # Enrichment adjustments
    style_adj = extra.get('style_clash', {}).get('adj', 0) if extra.get('style_clash') else 0
    exp_adj = extra.get('experience', {}).get('adj', 0) if extra.get('experience') else 0
    inj_adj = extra.get('injury_adj', 0) or 0
    enrich_adj = style_adj + exp_adj + inj_adj

    margin = (t1_adj_em - t2_adj_em) + (t1_record_bonus - t2_record_bonus) * 0.5 + enrich_adj + (hca1 - hca2)
    avg_tempo = ((t1.get('AdjTempo') or 67) + (t2.get('AdjTempo') or 67)) / 2
    GAME_SD = 11.0 * math.sqrt(avg_tempo / 67)
    scaled_margin = margin * (avg_tempo / 67)

    t1_score = (AVG_EFF + scaled_margin / 2) * (avg_tempo / 100)
    t2_score = (AVG_EFF - scaled_margin / 2) * (avg_tempo / 100)

    return {
        't1_score': t1_score,
        't2_score': t2_score,
        'margin': scaled_margin,
        't1_win_prob': gaussian_cdf(scaled_margin / GAME_SD),
        'tempo': avg_tempo,
    }


# ─── 11. Model 3: ConRat Model ──────────────────────────────────────────────
# Port of modelConRat() — index.html line 1128-1159

def model_con_rat(t1, t2, hca1, hca2, extra=None):
    if extra is None:
        extra = {}

    avg_tempo = ((t1.get('AdjTempo') or 67) + (t2.get('AdjTempo') or 67)) / 2
    GAME_SD = 11.0 * math.sqrt(avg_tempo / 67)

    cr1 = calc_con_rat(t1)
    cr2 = calc_con_rat(t2)

    # ConRat already captures continuity/experience at team level,
    # so use reduced weights for matchup-relative enrichments
    style_adj = (extra.get('style_clash', {}).get('adj', 0) * 0.5) if extra.get('style_clash') else 0
    exp_adj = (extra.get('experience', {}).get('adj', 0) * 0.3) if extra.get('experience') else 0
    inj_adj = extra.get('injury_adj', 0) or 0
    mom_adj = (extra.get('momentum', {}).get('adj', 0) * 0.5) if extra.get('momentum') else 0
    enrich_adj = style_adj + exp_adj + inj_adj + mom_adj

    margin = (cr1 - cr2) + enrich_adj + (hca1 - hca2)

    AVG_EFF = 100.0
    t1_score = (AVG_EFF + margin / 2) * (avg_tempo / 100)
    t2_score = (AVG_EFF - margin / 2) * (avg_tempo / 100)

    return {
        't1_score': t1_score,
        't2_score': t2_score,
        'margin': margin,
        't1_win_prob': gaussian_cdf(margin / GAME_SD),
        'tempo': avg_tempo,
        'cr1': cr1,
        'cr2': cr2,
    }


# ─── 12. Composite Blending ─────────────────────────────────────────────────
# Port of composite logic — index.html line 1582-1607

def compute_composite(eff, sim, cr, mc, t1, t2, calibration_coeffs=None,
                      weight_overrides=None, context=None,
                      total_calibration_coeffs=None,
                      total_situational_overrides=None,
                      total_weight_overrides=None):
    """Blend 4 model outputs with dynamic weights, apply spread calibration.

    Args:
        calibration_coeffs: Optional dict with keys 'close', 'moderate', 'logMult'
                            passed through to calibrate_spread().
        weight_overrides: Optional dict {'efficiency': float, 'similar': float,
                          'conrat': float, 'mc': float} to override base weights.
                          Data-quality adjustments are still applied on top.
        context: Optional dict with boolean flags for situational adjustments
                 (neutral_site, conf_tournament, ncaa_tournament, early_season).
        total_calibration_coeffs: Optional dict with keys 'center', 'compression'
                                  passed through to calibrate_total().
        total_weight_overrides: Optional dict {'efficiency': float, 'similar': float,
                                'conrat': float, 'mc': float} to override weights
                                used for total (score/tempo) blending. Falls back to
                                spread weights when not provided.
    """

    # Dynamic weights based on data quality
    has_rich_data = bool(t1.get('_ff') and t2.get('_ff')
                         and t1.get('_ms') and t2.get('_ms')
                         and t1.get('_ht') and t2.get('_ht'))
    avg_sos = ((t1.get('SOS', 0) or 0) + (t2.get('SOS', 0) or 0)) / 2
    avg_games = ((t1.get('Wins', 0) or 0) + (t1.get('Losses', 0) or 0)
                 + (t2.get('Wins', 0) or 0) + (t2.get('Losses', 0) or 0)) / 2

    # Start from overrides or base weights
    if weight_overrides:
        w_eff = weight_overrides.get('efficiency', 0.10)
        w_sim = weight_overrides.get('similar', 0.10)
        w_cr = weight_overrides.get('conrat', 0.20)
        w_mc = weight_overrides.get('mc', 0.60)
    else:
        w_eff = 0.10
        w_sim = 0.10
        w_cr = 0.20
        w_mc = 0.60

    if not has_rich_data:
        w_mc -= 0.10
        w_eff += 0.05
        w_cr += 0.05
    if avg_games < 15:
        w_mc -= 0.05
        w_cr += 0.05
    if abs(avg_sos) > 5:
        w_sim += 0.03
        w_mc -= 0.03

    # Normalize to sum to 1
    w_total = w_eff + w_sim + w_cr + w_mc
    w_eff /= w_total
    w_sim /= w_total
    w_cr /= w_total
    w_mc /= w_total

    # Total prediction weights (may differ from spread weights)
    if total_weight_overrides:
        tw_eff = total_weight_overrides.get('efficiency', w_eff)
        tw_sim = total_weight_overrides.get('similar', w_sim)
        tw_cr = total_weight_overrides.get('conrat', w_cr)
        tw_mc = total_weight_overrides.get('mc', w_mc)
        # Normalize
        tw_total = tw_eff + tw_sim + tw_cr + tw_mc
        tw_eff /= tw_total; tw_sim /= tw_total; tw_cr /= tw_total; tw_mc /= tw_total
    else:
        tw_eff, tw_sim, tw_cr, tw_mc = w_eff, w_sim, w_cr, w_mc

    raw_composite_margin = (eff['margin'] * w_eff + sim['margin'] * w_sim
                            + cr['margin'] * w_cr + mc['margin'] * w_mc)
    composite_margin = calibrate_spread(raw_composite_margin, coeffs=calibration_coeffs)

    # Apply situational adjustment if context is provided
    if context:
        composite_margin = apply_situational_adjustment(composite_margin, context)

    raw_t1_score = (eff['t1_score'] * tw_eff + sim['t1_score'] * tw_sim
                    + cr['t1_score'] * tw_cr + mc['t1_score'] * tw_mc)
    raw_t2_score = (eff['t2_score'] * tw_eff + sim['t2_score'] * tw_sim
                    + cr['t2_score'] * tw_cr + mc['t2_score'] * tw_mc)
    raw_total = raw_t1_score + raw_t2_score
    # Apply total calibration to the midpoint (independent of spread calibration)
    calibrated_total = calibrate_total(raw_total, coeffs=total_calibration_coeffs)
    if context:
        calibrated_total = apply_situational_total_adjustment(
            calibrated_total, context, overrides=total_situational_overrides)
    score_mid = calibrated_total / 2
    composite_t1_score = score_mid + composite_margin / 2
    composite_t2_score = score_mid - composite_margin / 2

    # Tempo feeds into SD which converts spread-weighted margin to win prob,
    # so it must use spread weights (w_*) to stay consistent with composite_margin.
    composite_avg_tempo = (eff['tempo'] * w_eff + sim['tempo'] * w_sim
                           + cr['tempo'] * w_cr + mc['tempo'] * w_mc)
    composite_sd = 11.0 * math.sqrt(composite_avg_tempo / 67)
    composite_t1_win = gaussian_cdf(composite_margin / composite_sd)

    # Model agreement score (0-100%)
    model_probs = [eff['t1_win_prob'], sim['t1_win_prob'], cr['t1_win_prob'], mc['t1_win_prob']]
    prob_mean = sum(model_probs) / 4
    prob_variance = sum((p - prob_mean) ** 2 for p in model_probs) / 4
    model_agreement = round(max(0, (1 - prob_variance / 0.0625)) * 100)
    if model_agreement >= 85:
        confidence = 'High'
    elif model_agreement >= 65:
        confidence = 'Moderate'
    else:
        confidence = 'Low'

    return {
        'margin': composite_margin,
        't1_score': composite_t1_score,
        't2_score': composite_t2_score,
        't1_win_prob': composite_t1_win,
        'model_agreement': model_agreement,
        'confidence': confidence,
        'weights': {
            'efficiency': round(w_eff, 4),
            'similar': round(w_sim, 4),
            'conrat': round(w_cr, 4),
            'mc': round(w_mc, 4),
        },
        'total_weights': {
            'efficiency': round(tw_eff, 4),
            'similar': round(tw_sim, 4),
            'conrat': round(tw_cr, 4),
            'mc': round(tw_mc, 4),
        },
    }


# ─── Situational Spot Adjustments ─────────────────────────────────────────

SITUATIONAL_DEFAULTS = {
    'neutral_site_dampening': 0.90,      # Reduce margin by 10% for neutral site
    'conf_tournament_dampening': 0.88,    # Reduce margin by 12% for conf tournament
    'ncaa_tournament_dampening': 0.85,    # Reduce margin by 15% for NCAA tournament
    'early_season_dampening': 0.95,       # Reduce margin by 5% for early season
}

TOTAL_SITUATIONAL_DEFAULTS = {
    'neutral_site_total_factor': 0.98,       # Totals ~3 pts lower at neutral sites
    'conf_tournament_total_factor': 0.975,   # Totals ~3-4 pts lower in conf tournaments
    'ncaa_tournament_total_factor': 0.97,    # Totals ~4-5 pts lower in NCAA tournament
    'early_season_total_factor': 0.98,       # Totals ~3 pts lower early season (teams not gelled)
}


def apply_situational_adjustment(margin, context, overrides=None):
    """Apply situational dampening to a predicted margin.

    Args:
        margin: Raw composite margin
        context: dict with boolean flags (neutral_site, conf_tournament, etc.)
        overrides: Optional dict of custom dampening factors

    Returns:
        Adjusted margin
    """
    factors = dict(SITUATIONAL_DEFAULTS)
    if overrides:
        factors.update(overrides)

    adjusted = margin

    # Apply dampening factors (multiplicative, not additive)
    # Most specific context wins (NCAA > conf tournament > neutral)
    if context.get('ncaa_tournament'):
        adjusted *= factors['ncaa_tournament_dampening']
    elif context.get('conf_tournament'):
        adjusted *= factors['conf_tournament_dampening']
    elif context.get('neutral_site'):
        adjusted *= factors['neutral_site_dampening']

    if context.get('early_season'):
        adjusted *= factors['early_season_dampening']

    return adjusted


def apply_situational_total_adjustment(total, context, overrides=None):
    """Apply situational dampening to a predicted total.

    Args:
        total: Calibrated predicted total
        context: dict with boolean flags (neutral_site, conf_tournament, etc.)
        overrides: Optional dict of custom total dampening factors

    Returns:
        Adjusted total
    """
    factors = dict(TOTAL_SITUATIONAL_DEFAULTS)
    if overrides:
        factors.update(overrides)

    adjusted = total

    # Apply dampening factors (multiplicative)
    # Most specific context wins (NCAA > conf tournament > neutral)
    if context.get('ncaa_tournament'):
        adjusted *= factors['ncaa_tournament_total_factor']
    elif context.get('conf_tournament'):
        adjusted *= factors['conf_tournament_total_factor']
    elif context.get('neutral_site'):
        adjusted *= factors['neutral_site_total_factor']

    if context.get('early_season'):
        adjusted *= factors['early_season_total_factor']

    return adjusted


# ─── Self-test ───────────────────────────────────────────────────────────────

def self_test():
    """Run validation tests. Returns dict of {test_name: 'PASS'/'FAIL (reason)'}."""
    results = {}

    # --- calibrate_spread ---
    cases = [(0, 0), (5, 4.6), (10, 7 * 0.92 + 3 * 0.85)]
    for inp, expected in cases:
        got = calibrate_spread(inp)
        ok = abs(got - expected) < 0.01
        results[f'calibrate_spread({inp})'] = 'PASS' if ok else f'FAIL (got {got:.3f}, expected {expected:.3f})'

    cs20 = calibrate_spread(20)
    # base = 7*0.92 + 7*0.85 = 12.39; 12.39 + log(1+3)*3.5 = 12.39+4.85 = 17.24
    ok = 16 < cs20 < 18
    results['calibrate_spread(20) < 20 (compressed)'] = 'PASS' if ok else f'FAIL (got {cs20:.3f})'

    # --- calibrate_total ---
    ct140 = calibrate_total(140)
    ok = abs(ct140 - 140.0) < 0.01  # center should stay at center
    results['calibrate_total(140) == 140'] = 'PASS' if ok else f'FAIL (got {ct140:.3f})'

    ct160 = calibrate_total(160)
    ok = ct160 < 160 and ct160 > 140  # compressed toward center
    results['calibrate_total(160) compressed'] = 'PASS' if ok else f'FAIL (got {ct160:.3f})'

    ct120 = calibrate_total(120)
    ok = ct120 > 120 and ct120 < 140  # compressed toward center
    results['calibrate_total(120) compressed'] = 'PASS' if ok else f'FAIL (got {ct120:.3f})'

    # --- gaussian_cdf ---
    cases_cdf = [(0, 0.5, 0.01), (1, 0.8413, 0.01), (-1, 0.1587, 0.01)]
    for inp, expected, tol in cases_cdf:
        got = gaussian_cdf(inp)
        ok = abs(got - expected) < tol
        results[f'gaussian_cdf({inp})'] = 'PASS' if ok else f'FAIL (got {got:.4f}, expected ~{expected})'

    # --- get_hca ---
    cases_hca = [('SEC', 4.2), ('WCC', 3.6), ('Ivy', 3.0)]
    for conf, expected in cases_hca:
        got = get_hca(conf)
        results[f'get_hca({conf})'] = 'PASS' if got == expected else f'FAIL (got {got})'

    # --- calc_style_clash with identical teams ---
    identical_ff = {'eFG_Pct': 50, 'TO_Pct': 19, 'OR_Pct': 28, 'FT_Rate': 33,
                    'DeFG_Pct': 50, 'DTO_Pct': 19, 'DOR_Pct': 28, 'DFT_Rate': 33}
    sc = calc_style_clash(identical_ff, identical_ff)
    ok = abs(sc['adj']) < 0.01
    results['calc_style_clash(identical)'] = 'PASS' if ok else f'FAIL (adj={sc["adj"]:.3f})'

    # --- model_efficiency with Duke-like vs Siena-like ---
    duke = {'AdjEM': 28, 'AdjOE': 120, 'AdjDE': 92, 'AdjTempo': 70,
            'Luck': 0.02, 'SOS': 8, 'Wins': 28, 'Losses': 3,
            'RankAdjEM': 3, 'RankAdjOE': 5, 'RankAdjDE': 10,
            'ConfShort': 'ACC', 'TeamName': 'FakeElite',
            '_ff': {}, '_ms': {}, '_ht': {}}
    siena = {'AdjEM': -5, 'AdjOE': 98, 'AdjDE': 103, 'AdjTempo': 66,
             'Luck': -0.01, 'SOS': -8, 'Wins': 12, 'Losses': 18,
             'RankAdjEM': 220, 'RankAdjOE': 200, 'RankAdjDE': 180,
             'ConfShort': 'MAAC', 'TeamName': 'FakeWeak',
             '_ff': {}, '_ms': {}, '_ht': {}}
    eff_result = model_efficiency(duke, siena, 0, 0)
    ok = 10 < eff_result['margin'] < 25
    results['model_efficiency(duke_like vs siena_like)'] = (
        'PASS' if ok else f'FAIL (margin={eff_result["margin"]:.1f})'
    )

    # --- compute_composite weights sum to 1 ---
    sim_result = model_similar_opponents(duke, siena, 0, 0)
    cr_result = model_con_rat(duke, siena, 0, 0)
    mc_fake = dict(eff_result)  # use eff as stand-in for MC
    comp = compute_composite(eff_result, sim_result, cr_result, mc_fake, duke, siena)
    w_sum = sum(comp['weights'].values())
    ok = abs(w_sum - 1.0) < 0.001
    results['composite_weights_sum_to_1'] = 'PASS' if ok else f'FAIL (sum={w_sum:.4f})'

    ok = comp['margin'] > 0
    results['composite_duke_wins'] = 'PASS' if ok else f'FAIL (margin={comp["margin"]:.1f})'

    return results


if __name__ == '__main__':
    print('=' * 60)
    print('  composite_model.py — Self-Test')
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
