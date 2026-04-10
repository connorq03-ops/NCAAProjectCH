"""
Golf Historical Analysis Engine.

Analyzes historical tournament data from DataGolf to identify which stats
best predict performance at each course/archetype, then generates data-driven
SG weights to replace static guesses in golf_course_profiles.py.

Three analysis approaches:
  1. Decomposition Analysis: Use player decompositions (per-player course
     adjustments from DataGolf) to see which factors differentiate players
     most at each course. Higher variance = more predictive power.
  2. Historical Prediction Accuracy: Compare baseline vs baseline_history_fit
     models across multiple years to quantify course-fit importance.
  3. SG-to-Finish Correlation: Cross-reference player SG splits with actual
     finish positions from prediction archives to find which SG categories
     best predict finishes at each course type.

Usage:
    from golf.golf_historical_analysis import get_dynamic_sg_weights
    weights = get_dynamic_sg_weights(client, course_profile)
    # Returns: {"sg_ott": 0.28, "sg_app": 0.32, "sg_arg": 0.18, "sg_putt": 0.22}
"""

import re
import json
import os
import time
from typing import Dict, List, Optional, Tuple

from golf.api_field_map import (
    extract_list, get_field, american_odds_to_probability,
    RANKINGS_FIELDS, SKILL_FIELDS, PRED_FIELDS,
    PRED_ARCHIVE_FIELDS,
)

# ═══════════════════════════════════════════════════════════════
# Course-to-Event ID Mapping
# ═══════════════════════════════════════════════════════════════

# Maps our course_id to DataGolf event_id for historical lookups.
# Built from DataGolf's historical-raw-data/event-list endpoint.
COURSE_EVENT_MAP = {
    "augusta_national": {"event_id": 14, "years": [2025, 2024, 2023, 2022, 2021, 2020, 2019]},
    "pinehurst_no2":    {"event_id": 26, "years": [2024, 2014]},  # US Open at Pinehurst
    "royal_troon":      {"event_id": 100, "years": [2024, 2016]},  # Open Championship at Troon
    "valhalla":         {"event_id": 33, "years": [2024, 2014]},   # PGA Championship at Valhalla
    "oakmont":          {"event_id": 26, "years": [2016, 2007]},   # US Open at Oakmont
    "quail_hollow":     {"event_id": 33, "years": [2025]},         # PGA Championship 2025
    "tpc_sawgrass":     {"event_id": 11, "years": [2026, 2025, 2024, 2023, 2022]},
    "riviera":          {"event_id": 7,  "years": [2026, 2025, 2024, 2023, 2022]},
    "bay_hill":         {"event_id": 9,  "years": [2026, 2025, 2024, 2023, 2022]},
    "tpc_scottsdale":   {"event_id": 3,  "years": [2026, 2025, 2024, 2023, 2022]},
    "torrey_pines_south": {"event_id": 4, "years": [2026, 2025, 2024, 2023, 2022]},
    "pebble_beach":     {"event_id": 5,  "years": [2026, 2025, 2024, 2023, 2022]},
    "east_lake":        {"event_id": 60, "years": [2025, 2024, 2023, 2022]},
    "tpc_southwind":    {"event_id": 27, "years": [2025, 2024, 2023, 2022]},
    "muirfield_village": {"event_id": 23, "years": [2025, 2024, 2023, 2022]},
    "harbour_town":     {"event_id": 12, "years": [2025, 2024, 2023, 2022]},
}

# Cache directory for historical analysis results
_CACHE_DIR = os.path.join(os.path.dirname(__file__), '.historical_cache')


# ═══════════════════════════════════════════════════════════════
# Parsing Helpers
# ═══════════════════════════════════════════════════════════════

def _parse_fin_text(fin_text: str) -> Optional[float]:
    """Parse DataGolf finish text (e.g., '1', 'T14', 'CUT', 'WD') to numeric.

    Returns None for CUT/WD/DQ (non-finishers).
    For ties like 'T14', returns 14.0.
    """
    if not fin_text or not isinstance(fin_text, str):
        return None
    fin_text = fin_text.strip().upper()
    if fin_text in ('CUT', 'WD', 'DQ', 'MDF', ''):
        return None
    # Remove 'T' prefix for ties
    cleaned = fin_text.lstrip('T')
    try:
        return float(cleaned)
    except (ValueError, TypeError):
        return None


def _normalize_name(name: str) -> str:
    """Normalize player name for matching across datasets."""
    if not name:
        return ""
    name = name.strip()
    # Handle "Last, First" format from archives
    if ',' in name:
        parts = name.split(',', 1)
        name = f"{parts[1].strip()} {parts[0].strip()}"
    return re.sub(r'\s+', ' ', name).lower()


# ═══════════════════════════════════════════════════════════════
# Analysis 1: Decomposition-Based Weight Inference
# ═══════════════════════════════════════════════════════════════

def analyze_decompositions(client) -> Dict:
    """Analyze current player decompositions to infer what matters at this course.

    DataGolf's player decompositions include per-player adjustment factors:
      - strokes_gained_category_adjustment (how SG category mix affects fit)
      - driving_distance_adjustment
      - driving_accuracy_adjustment
      - cf_approach_comp (approach component of course fit)
      - cf_short_comp (short game component of course fit)
      - total_fit_adjustment
      - total_course_history_adjustment

    By looking at variance of each adjustment across all players, we can
    determine which factors differentiate players the most at this course.
    Higher variance = more differentiation power = more important factor.

    Returns:
        dict with:
            course_name: str
            factor_importance: dict of factor -> importance (0-1 scale)
            sg_weight_adjustments: dict of sg_ott/sg_app/sg_arg/sg_putt -> delta
            n_players: int
    """
    try:
        decomps = client.get_player_decompositions()
    except Exception as e:
        return {"error": str(e)}

    players_list = extract_list(decomps, 'player_decompositions')
    if not players_list:
        return {"error": "No player decompositions available"}

    course_name = decomps.get("course_name", "Unknown")
    event_name = decomps.get("event_name", "Unknown")

    # Extract adjustment factors for all players
    factors = {
        'sg_category_adj': [],
        'driving_dist_adj': [],
        'driving_acc_adj': [],
        'cf_approach': [],
        'cf_short': [],
        'total_fit_adj': [],
        'course_history_adj': [],
        'baseline_pred': [],
        'final_pred': [],
    }

    for p in players_list:
        factors['sg_category_adj'].append(p.get('strokes_gained_category_adjustment', 0.0) or 0.0)
        factors['driving_dist_adj'].append(p.get('driving_distance_adjustment', 0.0) or 0.0)
        factors['driving_acc_adj'].append(p.get('driving_accuracy_adjustment', 0.0) or 0.0)
        factors['cf_approach'].append(p.get('cf_approach_comp', 0.0) or 0.0)
        factors['cf_short'].append(p.get('cf_short_comp', 0.0) or 0.0)
        factors['total_fit_adj'].append(p.get('total_fit_adjustment', 0.0) or 0.0)
        factors['course_history_adj'].append(p.get('total_course_history_adjustment', 0.0) or 0.0)
        factors['baseline_pred'].append(p.get('baseline_pred', 0.0) or 0.0)
        factors['final_pred'].append(p.get('final_pred', 0.0) or 0.0)

    # Compute variance for each factor
    def _variance(vals):
        if len(vals) < 2:
            return 0.0
        mean = sum(vals) / len(vals)
        return sum((v - mean) ** 2 for v in vals) / len(vals)

    def _std(vals):
        return _variance(vals) ** 0.5

    factor_std = {k: _std(v) for k, v in factors.items()
                  if k not in ('baseline_pred', 'final_pred')}

    # Normalize to importance scores (0-1 scale)
    max_std = max(factor_std.values()) if factor_std else 1.0
    factor_importance = {k: round(v / max_std, 4) if max_std > 0 else 0.0
                         for k, v in factor_std.items()}

    # Compute how much course fit matters overall at this course
    # = std(final_pred - baseline_pred) / std(baseline_pred)
    fit_deltas = [f - b for f, b in zip(factors['final_pred'], factors['baseline_pred'])]
    fit_impact_ratio = _std(fit_deltas) / max(_std(factors['baseline_pred']), 0.001)

    # Infer SG weight adjustments from decomposition patterns
    # The sg_category_adj captures how SG category mix affects course fit.
    # The approach/short components tell us if approach or short game matters more.
    sg_weight_adjustments = _infer_sg_adjustments_from_decomps(
        factors, factor_importance, fit_impact_ratio
    )

    return {
        "course_name": course_name,
        "event_name": event_name,
        "n_players": len(players_list),
        "factor_importance": factor_importance,
        "factor_std": {k: round(v, 6) for k, v in factor_std.items()},
        "fit_impact_ratio": round(fit_impact_ratio, 4),
        "sg_weight_adjustments": sg_weight_adjustments,
    }


def _infer_sg_adjustments_from_decomps(factors, importance, fit_impact_ratio):
    """Infer SG weight adjustments from decomposition patterns.

    Logic:
    - If driving_dist_adj has high variance -> sg_ott matters more (length matters)
    - If driving_acc_adj has high variance -> sg_ott matters (accuracy off tee)
    - If cf_approach has high variance -> sg_app matters more
    - If cf_short has high variance -> sg_arg matters more
    - If sg_category_adj has high variance -> overall SG fit matters (boost all)
    - If course_history_adj has high variance -> course-specific experience matters

    Returns dict with adjustment deltas for each SG category.
    """
    adjustments = {"sg_ott": 0.0, "sg_app": 0.0, "sg_arg": 0.0, "sg_putt": 0.0}

    # Driving distance importance -> boost sg_ott
    dist_imp = importance.get('driving_dist_adj', 0.0)
    acc_imp = importance.get('driving_acc_adj', 0.0)
    approach_imp = importance.get('cf_approach', 0.0)
    short_imp = importance.get('cf_short', 0.0)

    # Scale adjustments: max adjustment is +/- 0.08 per category
    scale = 0.08

    # sg_ott: driven by driving distance and accuracy importance
    adjustments["sg_ott"] = (dist_imp * 0.6 + acc_imp * 0.4) * scale

    # sg_app: driven by approach component importance
    adjustments["sg_app"] = approach_imp * scale

    # sg_arg: driven by short game component importance
    adjustments["sg_arg"] = short_imp * scale

    # sg_putt: inversely related - if other factors dominate, putting matters less
    # But if nothing else differentiates, putting becomes more important
    other_importance = max(dist_imp, acc_imp, approach_imp, short_imp)
    adjustments["sg_putt"] = (1.0 - other_importance) * scale * 0.5

    return {k: round(v, 4) for k, v in adjustments.items()}


# ═══════════════════════════════════════════════════════════════
# Analysis 2: Historical Prediction Accuracy
# ═══════════════════════════════════════════════════════════════

def analyze_historical_predictions(client, course_id: str,
                                   max_years: int = 4) -> Dict:
    """Compare baseline vs baseline_history_fit model accuracy across years.

    For each historical year at this course, we:
    1. Fetch pre-tournament prediction archive
    2. Compare both models' predicted odds with actual finish positions
    3. Compute which model was more accurate
    4. Quantify how much course-fit/history improves predictions

    Args:
        client: DataGolfClient
        course_id: key into COURSE_EVENT_MAP
        max_years: max historical years to analyze

    Returns:
        dict with accuracy metrics for both models
    """
    mapping = COURSE_EVENT_MAP.get(course_id)
    if not mapping:
        return {"error": f"No event mapping for course_id: {course_id}"}

    event_id = mapping['event_id']
    years = mapping['years'][:max_years]

    results_by_year = {}
    for year in years:
        try:
            archive = client.get_pre_tournament_pred_archive(
                event_id=str(event_id), year=year
            )
        except Exception as e:
            results_by_year[year] = {"error": str(e)}
            continue

        if not isinstance(archive, dict):
            results_by_year[year] = {"error": "Unexpected response format"}
            continue

        baseline = archive.get('baseline', [])
        baseline_hf = archive.get('baseline_history_fit', [])

        if not baseline:
            results_by_year[year] = {"error": "No baseline predictions"}
            continue

        # Score each model: lower is better
        # Metric: mean absolute rank error (predicted rank vs actual finish)
        baseline_score = _score_predictions(baseline)
        hf_score = _score_predictions(baseline_hf) if baseline_hf else None

        results_by_year[year] = {
            "n_players": len(baseline),
            "n_finishers": baseline_score['n_scored'],
            "baseline_mae": baseline_score['mae'],
            "baseline_hf_mae": hf_score['mae'] if hf_score else None,
            "hf_improvement": (
                round(baseline_score['mae'] - hf_score['mae'], 4)
                if hf_score else None
            ),
        }

    # Aggregate across years
    all_baseline_mae = [r['baseline_mae'] for r in results_by_year.values()
                        if isinstance(r, dict) and 'baseline_mae' in r
                        and r['baseline_mae'] is not None]
    all_hf_mae = [r['baseline_hf_mae'] for r in results_by_year.values()
                  if isinstance(r, dict) and r.get('baseline_hf_mae') is not None]
    all_improvements = [r['hf_improvement'] for r in results_by_year.values()
                        if isinstance(r, dict) and r.get('hf_improvement') is not None]

    avg_baseline_mae = sum(all_baseline_mae) / len(all_baseline_mae) if all_baseline_mae else None
    avg_hf_mae = sum(all_hf_mae) / len(all_hf_mae) if all_hf_mae else None
    avg_improvement = sum(all_improvements) / len(all_improvements) if all_improvements else None

    # Course fit importance: how much does history/fit improve predictions?
    # Positive improvement = history_fit model is better = course fit matters
    course_fit_importance = 0.5  # default: moderate
    if avg_improvement is not None:
        # Scale: 0.5 MAE improvement → high importance (0.8)
        #         0.0 MAE improvement → neutral (0.5)
        #        -0.5 MAE improvement → low importance (0.2)
        course_fit_importance = max(0.1, min(0.9,
            0.5 + avg_improvement * 0.6
        ))

    return {
        "course_id": course_id,
        "event_id": event_id,
        "years_analyzed": list(results_by_year.keys()),
        "per_year": results_by_year,
        "avg_baseline_mae": round(avg_baseline_mae, 4) if avg_baseline_mae else None,
        "avg_hf_mae": round(avg_hf_mae, 4) if avg_hf_mae else None,
        "avg_hf_improvement": round(avg_improvement, 4) if avg_improvement else None,
        "course_fit_importance": round(course_fit_importance, 4),
    }


def _score_predictions(predictions: List[Dict]) -> Dict:
    """Score a set of predictions against actual finishes.

    Uses predicted rank (position in prediction list, sorted by win odds)
    vs actual finish position (from fin_text).

    Returns dict with mae (mean absolute error) and n_scored.
    """
    # Sort by win odds (lowest/most negative American odds = highest probability)
    scored = []
    for i, p in enumerate(predictions):
        fin = _parse_fin_text(p.get('fin_text', ''))
        if fin is None:
            continue
        # Predicted rank is position in list (already sorted by DG model)
        pred_rank = i + 1
        scored.append(abs(pred_rank - fin))

    if not scored:
        return {'mae': None, 'n_scored': 0}

    return {
        'mae': round(sum(scored) / len(scored), 4),
        'n_scored': len(scored),
    }


# ═══════════════════════════════════════════════════════════════
# Analysis 3: SG-to-Finish Correlation
# ═══════════════════════════════════════════════════════════════

def analyze_sg_correlations(client, course_id: str,
                            max_years: int = 4) -> Dict:
    """Correlate player SG splits with actual finish positions at a course.

    Approach:
    1. Get current skill ratings (SG splits for all players)
    2. For each historical year, get prediction archive with actual finishes
    3. Match players across datasets by name
    4. Compute correlation between each SG category and finish position

    A negative correlation means higher SG → lower (better) finish = good predictor.

    Args:
        client: DataGolfClient
        course_id: key into COURSE_EVENT_MAP
        max_years: max years to analyze

    Returns:
        dict with correlations per SG category and inferred weights
    """
    mapping = COURSE_EVENT_MAP.get(course_id)
    if not mapping:
        return {"error": f"No event mapping for course_id: {course_id}"}

    # Get current SG splits for all players
    try:
        skill_data = client.get_skill_decompositions()
    except Exception as e:
        return {"error": f"Failed to get skill ratings: {e}"}

    skill_list = extract_list(skill_data, 'skill_ratings')
    if not skill_list:
        return {"error": "No skill ratings data"}

    # Index by normalized name
    sg_by_name = {}
    for p in skill_list:
        name = _normalize_name(p.get('player_name', ''))
        if name:
            sg_by_name[name] = {
                'sg_ott': p.get('sg_ott', 0.0) or 0.0,
                'sg_app': p.get('sg_app', 0.0) or 0.0,
                'sg_arg': p.get('sg_arg', 0.0) or 0.0,
                'sg_putt': p.get('sg_putt', 0.0) or 0.0,
                'sg_total': p.get('sg_total', 0.0) or 0.0,
            }

    # Collect (SG, finish) pairs across years
    event_id = mapping['event_id']
    years = mapping['years'][:max_years]

    all_pairs = []  # list of (sg_dict, finish_pos)
    years_used = []

    for year in years:
        try:
            archive = client.get_pre_tournament_pred_archive(
                event_id=str(event_id), year=year
            )
        except Exception:
            continue

        if not isinstance(archive, dict):
            continue

        baseline = archive.get('baseline', [])
        if not baseline:
            continue

        year_pairs = 0
        for p in baseline:
            fin = _parse_fin_text(p.get('fin_text', ''))
            if fin is None:
                continue
            name = _normalize_name(p.get('player_name', ''))
            if name in sg_by_name:
                all_pairs.append((sg_by_name[name], fin))
                year_pairs += 1

        if year_pairs > 0:
            years_used.append(year)

    if len(all_pairs) < 10:
        return {
            "error": f"Insufficient data: only {len(all_pairs)} matched pairs",
            "n_pairs": len(all_pairs),
        }

    # Compute correlation between each SG category and finish position
    categories = ['sg_ott', 'sg_app', 'sg_arg', 'sg_putt', 'sg_total']
    correlations = {}

    for cat in categories:
        sg_vals = [pair[0][cat] for pair in all_pairs]
        fin_vals = [pair[1] for pair in all_pairs]
        corr = _pearson_correlation(sg_vals, fin_vals)
        correlations[cat] = round(corr, 4) if corr is not None else None

    # Convert correlations to inferred weights
    # More negative correlation = stronger predictor = higher weight
    sg_weights = _correlations_to_weights(correlations)

    return {
        "course_id": course_id,
        "event_id": event_id,
        "years_used": years_used,
        "n_pairs": len(all_pairs),
        "correlations": correlations,
        "inferred_weights": sg_weights,
    }


def _pearson_correlation(x: List[float], y: List[float]) -> Optional[float]:
    """Compute Pearson correlation coefficient between two lists."""
    n = len(x)
    if n < 3:
        return None

    mean_x = sum(x) / n
    mean_y = sum(y) / n

    cov = sum((xi - mean_x) * (yi - mean_y) for xi, yi in zip(x, y)) / n
    std_x = (sum((xi - mean_x) ** 2 for xi in x) / n) ** 0.5
    std_y = (sum((yi - mean_y) ** 2 for yi in y) / n) ** 0.5

    if std_x < 1e-10 or std_y < 1e-10:
        return None

    return cov / (std_x * std_y)


def _correlations_to_weights(correlations: Dict[str, Optional[float]]) -> Dict[str, float]:
    """Convert SG-to-finish correlations to normalized SG weights.

    More negative correlation = stronger predictor = higher weight.
    We use absolute value of correlation, then normalize to sum to 1.0.
    """
    categories = ['sg_ott', 'sg_app', 'sg_arg', 'sg_putt']

    # Use absolute correlation as raw importance
    raw = {}
    for cat in categories:
        corr = correlations.get(cat)
        if corr is not None:
            # Negative correlation is what we want (higher SG -> lower finish)
            # Use absolute value, but give extra credit for negative correlations
            raw[cat] = abs(corr) if corr < 0 else abs(corr) * 0.5
        else:
            raw[cat] = 0.25  # default equal weight

    # Normalize to sum to 1.0
    total = sum(raw.values())
    if total < 1e-10:
        return {cat: 0.25 for cat in categories}

    return {cat: round(v / total, 4) for cat, v in raw.items()}


# ═══════════════════════════════════════════════════════════════
# Combined Analysis: Dynamic SG Weight Generation
# ═══════════════════════════════════════════════════════════════

def get_dynamic_sg_weights(client, course_profile: Dict,
                           use_cache: bool = True) -> Dict:
    """Generate data-driven SG weights for a course by combining all analyses.

    Priority order:
    1. Decomposition analysis (current tournament, most relevant)
    2. Historical SG correlation analysis (multi-year, course-specific)
    3. Static weights from course profile (fallback)

    The final weights are a blend of all available analyses.

    Args:
        client: DataGolfClient
        course_profile: dict from COURSES
        use_cache: whether to use cached results

    Returns:
        dict with:
            sg_weights: {"sg_ott": float, "sg_app": float, "sg_arg": float, "sg_putt": float}
            static_weights: original weights from course profile
            analysis_sources: list of analyses that contributed
            confidence: float (0-1) indicating how much data backed the weights
            details: dict with per-analysis results
    """
    course_id = course_profile.get("course_id", "unknown")
    static_weights = course_profile.get("sg_weights", {
        "sg_ott": 0.25, "sg_app": 0.25, "sg_arg": 0.25, "sg_putt": 0.25,
    })

    # Check cache
    if use_cache:
        cached = _load_cache(course_id)
        if cached:
            return cached

    analysis_sources = []
    all_weight_estimates = []
    details = {}

    # Analysis 1: Decomposition (most valuable - current tournament data)
    decomp_result = analyze_decompositions(client)
    if "error" not in decomp_result:
        details["decomposition"] = decomp_result

        # Use decomposition adjustments to modify static weights
        adj = decomp_result.get("sg_weight_adjustments", {})
        decomp_weights = {
            cat: max(0.05, static_weights.get(cat, 0.25) + adj.get(cat, 0.0))
            for cat in ["sg_ott", "sg_app", "sg_arg", "sg_putt"]
        }
        # Normalize
        total = sum(decomp_weights.values())
        decomp_weights = {k: round(v / total, 4) for k, v in decomp_weights.items()}

        all_weight_estimates.append(("decomposition", decomp_weights, 0.4))
        analysis_sources.append("decomposition")

    # Analysis 2: Historical SG correlations (multi-year course data)
    sg_corr_result = analyze_sg_correlations(client, course_id, max_years=4)
    if "error" not in sg_corr_result and sg_corr_result.get("n_pairs", 0) >= 20:
        details["sg_correlations"] = sg_corr_result
        corr_weights = sg_corr_result.get("inferred_weights", {})
        if corr_weights:
            all_weight_estimates.append(("sg_correlations", corr_weights, 0.35))
            analysis_sources.append("sg_correlations")

    # Analysis 3: Historical prediction accuracy (course fit importance)
    hist_result = analyze_historical_predictions(client, course_id, max_years=3)
    if "error" not in hist_result:
        details["historical_accuracy"] = hist_result
        # Use course_fit_importance to scale how much we trust course-specific
        # adjustments vs raw skill
        cfi = hist_result.get("course_fit_importance", 0.5)
        details["course_fit_importance"] = cfi
        analysis_sources.append("historical_accuracy")

    # Always include static weights as a baseline
    all_weight_estimates.append(("static", dict(static_weights), 0.25))
    analysis_sources.append("static_baseline")

    # Blend all weight estimates
    final_weights = _blend_weight_estimates(all_weight_estimates)

    # Compute confidence based on how much data we have
    confidence = _compute_confidence(analysis_sources, details)

    result = {
        "sg_weights": final_weights,
        "static_weights": dict(static_weights),
        "analysis_sources": analysis_sources,
        "confidence": round(confidence, 4),
        "details": details,
    }

    # Cache result
    if use_cache:
        _save_cache(course_id, result)

    return result


def _blend_weight_estimates(estimates: List[Tuple[str, Dict, float]]) -> Dict[str, float]:
    """Blend multiple SG weight estimates using meta-weights.

    Args:
        estimates: list of (source_name, weights_dict, meta_weight) tuples

    Returns:
        Normalized blended weights
    """
    categories = ["sg_ott", "sg_app", "sg_arg", "sg_putt"]
    blended = {cat: 0.0 for cat in categories}

    # Normalize meta-weights to sum to 1.0
    total_meta = sum(w for _, _, w in estimates)
    if total_meta < 1e-10:
        return {"sg_ott": 0.25, "sg_app": 0.25, "sg_arg": 0.25, "sg_putt": 0.25}

    for source, weights, meta_weight in estimates:
        normalized_meta = meta_weight / total_meta
        for cat in categories:
            blended[cat] += weights.get(cat, 0.25) * normalized_meta

    # Normalize blended weights to sum to 1.0
    total = sum(blended.values())
    if total < 1e-10:
        return {"sg_ott": 0.25, "sg_app": 0.25, "sg_arg": 0.25, "sg_putt": 0.25}

    return {cat: round(v / total, 4) for cat, v in blended.items()}


def _compute_confidence(sources: List[str], details: Dict) -> float:
    """Compute confidence score (0-1) based on available data.

    Higher confidence when:
    - More analysis sources contributed
    - More historical years of data
    - More player pairs in correlation analysis
    """
    score = 0.0

    if "decomposition" in sources:
        score += 0.3  # Current tournament data is very valuable

    if "sg_correlations" in sources:
        n_pairs = details.get("sg_correlations", {}).get("n_pairs", 0)
        # More pairs = more confidence, maxing at 0.35 with 100+ pairs
        score += min(0.35, 0.15 + n_pairs * 0.002)

    if "historical_accuracy" in sources:
        n_years = len(details.get("historical_accuracy", {}).get("years_analyzed", []))
        score += min(0.2, 0.05 + n_years * 0.05)

    # Static baseline always contributes a small amount
    score += 0.1

    return min(1.0, score)


# ═══════════════════════════════════════════════════════════════
# Composite Model Weight Optimization
# ═══════════════════════════════════════════════════════════════

def get_dynamic_model_weights(client, course_profile: Dict) -> Optional[Dict]:
    """Generate data-driven composite model weights (5-model blend).

    Uses historical prediction accuracy to determine how much to weight
    each of the 5 models (SG efficiency, course fit, GolfRat, MC, DG preds)
    for this specific course.

    Key insight: At courses where course fit is very important (high
    course_fit_importance), we boost the course_fit model weight and
    reduce SG efficiency weight. At courses where raw skill dominates,
    we do the opposite.

    Args:
        client: DataGolfClient
        course_profile: dict from COURSES

    Returns:
        dict with model weight overrides, or None if insufficient data
    """
    course_id = course_profile.get("course_id", "unknown")

    # Get historical accuracy analysis
    hist_result = analyze_historical_predictions(client, course_id, max_years=3)
    if "error" in hist_result:
        return None

    cfi = hist_result.get("course_fit_importance", 0.5)

    # Determine if this is a major (context affects model weighting)
    is_major = course_profile.get("is_major", False)

    # Base weights (from compute_golf_composite default)
    base = {
        'sg_efficiency': 0.20,
        'course_fit': 0.20,
        'golf_rat': 0.20,
        'mc': 0.15,
        'dg_preds': 0.25,
    }

    # Adjust based on course fit importance
    # cfi > 0.5 = course fit matters more → boost course_fit, reduce sg_efficiency
    # cfi < 0.5 = raw skill matters more → boost sg_efficiency, reduce course_fit
    fit_delta = (cfi - 0.5) * 0.12  # max +/- 0.06

    adjusted = dict(base)
    adjusted['course_fit'] += fit_delta
    adjusted['sg_efficiency'] -= fit_delta * 0.5
    adjusted['golf_rat'] -= fit_delta * 0.5

    # Majors: slightly boost pressure/experience factors (GolfRat includes these)
    if is_major:
        adjusted['golf_rat'] += 0.02
        adjusted['mc'] -= 0.02

    # Normalize to sum to 1.0
    total = sum(adjusted.values())
    adjusted = {k: round(v / total, 4) for k, v in adjusted.items()}

    return adjusted


# ═══════════════════════════════════════════════════════════════
# Cache Management
# ═══════════════════════════════════════════════════════════════

def _load_cache(course_id: str) -> Optional[Dict]:
    """Load cached analysis result if fresh (< 6 hours old)."""
    cache_file = os.path.join(_CACHE_DIR, f"{course_id}_weights.json")
    if not os.path.exists(cache_file):
        return None

    try:
        with open(cache_file, 'r') as f:
            data = json.load(f)
        # Check freshness (6 hours)
        cached_at = data.get("_cached_at", 0)
        if time.time() - cached_at > 6 * 3600:
            return None
        return data
    except (json.JSONDecodeError, OSError):
        return None


def _save_cache(course_id: str, result: Dict):
    """Save analysis result to cache."""
    os.makedirs(_CACHE_DIR, exist_ok=True)
    cache_file = os.path.join(_CACHE_DIR, f"{course_id}_weights.json")
    result["_cached_at"] = time.time()
    try:
        with open(cache_file, 'w') as f:
            json.dump(result, f, indent=2)
    except OSError:
        pass  # Cache write failure is non-fatal


def clear_cache():
    """Clear all cached analysis results."""
    if os.path.exists(_CACHE_DIR):
        for f in os.listdir(_CACHE_DIR):
            try:
                os.remove(os.path.join(_CACHE_DIR, f))
            except OSError:
                pass


# ═══════════════════════════════════════════════════════════════
# CLI Entry Point
# ═══════════════════════════════════════════════════════════════

def main():
    """Run historical analysis for all courses and print results."""
    from golf.datagolf_client import DataGolfClient
    from golf.golf_course_profiles import COURSES

    client = DataGolfClient()

    print("=" * 70)
    print("Golf Historical Analysis: Dynamic SG Weight Generation")
    print("=" * 70)

    # Run decomposition analysis (current tournament)
    print("\n--- Analysis 1: Player Decompositions (Current Tournament) ---")
    decomp = analyze_decompositions(client)
    if "error" not in decomp:
        print(f"Course: {decomp['course_name']} ({decomp['event_name']})")
        print(f"Players: {decomp['n_players']}")
        print(f"Fit impact ratio: {decomp['fit_impact_ratio']}")
        print(f"Factor importance:")
        for k, v in sorted(decomp['factor_importance'].items(),
                           key=lambda x: -x[1]):
            print(f"  {k:25s} {v:.4f}")
        print(f"SG weight adjustments: {decomp['sg_weight_adjustments']}")
    else:
        print(f"Error: {decomp['error']}")

    # Run full dynamic weight generation for key courses
    print("\n--- Dynamic SG Weights for Each Course ---")
    for course_id in ["augusta_national", "tpc_sawgrass", "riviera",
                      "tpc_scottsdale", "harbour_town"]:
        cp = COURSES.get(course_id)
        if not cp:
            continue

        print(f"\n{cp['name']} ({cp['archetype']}):")
        print(f"  Static weights:  {cp.get('sg_weights', {})}")

        result = get_dynamic_sg_weights(client, cp, use_cache=False)
        print(f"  Dynamic weights: {result['sg_weights']}")
        print(f"  Sources: {result['analysis_sources']}")
        print(f"  Confidence: {result['confidence']}")

        if "sg_correlations" in result.get("details", {}):
            corrs = result["details"]["sg_correlations"]["correlations"]
            print(f"  Correlations: {corrs}")

    # Run composite model weight optimization
    print("\n--- Dynamic Composite Model Weights ---")
    for course_id in ["augusta_national", "tpc_sawgrass"]:
        cp = COURSES.get(course_id)
        if not cp:
            continue
        model_weights = get_dynamic_model_weights(client, cp)
        if model_weights:
            print(f"\n{cp['name']}:")
            print(f"  Model weights: {model_weights}")


if __name__ == "__main__":
    main()
