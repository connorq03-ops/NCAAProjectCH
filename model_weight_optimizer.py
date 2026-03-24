"""Dynamic model weight optimizer.

Consumes backtest results to determine optimal composite weights
based on rolling per-model ATS performance.

Validation safeguards:
  - Minimum sample size before trusting dynamic weights (default 100 games)
  - Maximum deviation of +/-0.15 from base weights (prevents overfitting)
  - Seasonal reset on November 1
  - A/B comparison: reports accuracy with both base and dynamic weights
  - Auto-rollback if dynamic weights underperform base over 50+ games
"""
import math
from collections import defaultdict
from datetime import datetime

from composite_model import calibrate_spread


# ─── Base (default) weights ────────────────────────────────────────────────

BASE_WEIGHTS = {
    'efficiency': 0.10,
    'similar': 0.10,
    'conrat': 0.20,
    'mc': 0.60,
}

# Maximum allowed deviation from base weight per model
MAX_WEIGHT_DEVIATION = 0.15

# Minimum weight any single model can have
MIN_MODEL_WEIGHT = 0.05


def compute_per_model_ats(results, window=100):
    """Compute rolling ATS hit rate for each sub-model.

    Args:
        results: List of backtest result dicts (must have 'sub_model_margins',
                 'vegas_spread', 'actual_spread')
        window: Number of most recent games to consider

    Returns:
        dict: {model_name: {'ats_pct': float, 'mae': float, 'games': int}}
    """
    # Filter to games with Vegas lines (ATS requires a line to bet against)
    with_lines = [r for r in results if r.get('vegas_spread') is not None]
    recent = with_lines[-window:] if len(with_lines) > window else with_lines

    if not recent:
        return {}

    model_stats = defaultdict(lambda: {'hits': 0, 'misses': 0, 'errors': []})

    for r in recent:
        subs = r.get('sub_model_margins', {})
        vegas_spread = r.get('vegas_spread', 0)  # home-relative
        actual_margin = r.get('actual_spread', 0)  # home-relative

        for model_name, model_margin in subs.items():
            # model_margin is visitor-relative; convert to home-relative
            calibrated = calibrate_spread(model_margin)
            model_home_calibrated = -calibrated

            # ATS: does our model's spread differ from Vegas?
            # If our model says home wins by more than Vegas, bet home
            our_edge = model_home_calibrated - (-vegas_spread)  # positive = we think home covers
            if abs(our_edge) < 0.5:
                continue  # skip near-pushes

            bet_home = our_edge > 0
            # Did the bet cover?
            actual_vs_spread = actual_margin + vegas_spread  # positive = home covered
            if bet_home and actual_vs_spread > 0:
                model_stats[model_name]['hits'] += 1
            elif not bet_home and actual_vs_spread < 0:
                model_stats[model_name]['hits'] += 1
            else:
                model_stats[model_name]['misses'] += 1

            model_stats[model_name]['errors'].append(abs(actual_margin - model_home_calibrated))

    result = {}
    for name, stats in model_stats.items():
        total = stats['hits'] + stats['misses']
        result[name] = {
            'ats_pct': round(stats['hits'] / total * 100, 1) if total > 0 else 50.0,
            'mae': round(sum(stats['errors']) / len(stats['errors']), 1) if stats['errors'] else None,
            'games': total,
        }
    return result


def compute_optimal_weights(per_model_ats, min_games=30):
    """Compute optimal composite weights from per-model ATS performance.

    Uses inverse-MAE weighting: models with lower error get higher weight.
    Blends toward base weights when sample size is small.

    Args:
        per_model_ats: Output of compute_per_model_ats()
        min_games: Minimum games before trusting dynamic weights

    Returns:
        dict: {'efficiency': float, 'similar': float, 'conrat': float, 'mc': float}
    """
    if not per_model_ats:
        return dict(BASE_WEIGHTS)

    # Compute inverse-MAE weights
    inv_mae = {}
    for name in BASE_WEIGHTS:
        stats = per_model_ats.get(name, {})
        mae = stats.get('mae')
        games = stats.get('games', 0)
        if mae and mae > 0 and games >= min_games:
            inv_mae[name] = 1.0 / mae
        else:
            inv_mae[name] = None  # not enough data

    # If we have enough data for all models, compute dynamic weights
    has_all = all(v is not None for v in inv_mae.values())
    if has_all:
        total_inv = sum(inv_mae.values())
        dynamic = {name: inv_mae[name] / total_inv for name in BASE_WEIGHTS}

        # Blend dynamic with base weights based on sample size
        # More games -> trust dynamic more (up to 70% dynamic at 200+ games)
        avg_games = sum(per_model_ats[n]['games'] for n in BASE_WEIGHTS) / 4
        blend_factor = min(avg_games / 200, 0.70)  # 0 to 0.70

        result = {}
        for name in BASE_WEIGHTS:
            blended = BASE_WEIGHTS[name] * (1 - blend_factor) + dynamic[name] * blend_factor
            # Enforce maximum deviation from base weight
            clamped = max(
                BASE_WEIGHTS[name] - MAX_WEIGHT_DEVIATION,
                min(BASE_WEIGHTS[name] + MAX_WEIGHT_DEVIATION, blended),
            )
            # Enforce minimum weight
            result[name] = max(MIN_MODEL_WEIGHT, clamped)

        # Normalize to sum to 1.0
        total = sum(result.values())
        result = {name: round(v / total, 4) for name, v in result.items()}
        return result

    return dict(BASE_WEIGHTS)


def should_reset_weights(last_updated_str):
    """Check if dynamic weights should be reset (seasonal reset on Nov 1).

    Args:
        last_updated_str: ISO date string of when weights were last updated

    Returns:
        bool: True if weights should be reset
    """
    if not last_updated_str:
        return False
    try:
        last_updated = datetime.fromisoformat(last_updated_str.replace('Z', '+00:00'))
        # Strip timezone info for comparison with naive datetimes
        if last_updated.tzinfo is not None:
            last_updated = last_updated.replace(tzinfo=None)
    except (ValueError, TypeError):
        return False

    now = datetime.utcnow()
    # Current season start: Nov 1 of the most recent fall
    if now.month >= 11:
        season_start = datetime(now.year, 11, 1)
    else:
        season_start = datetime(now.year - 1, 11, 1)

    return last_updated < season_start


def should_rollback(per_model_ats, current_weights, min_rollback_games=50):
    """Check if dynamic weights should be rolled back to base weights.

    If dynamic weights produce worse ATS than base weights over 50+ games,
    auto-revert to base weights.

    Args:
        per_model_ats: Output of compute_per_model_ats()
        current_weights: Currently active weight dict
        min_rollback_games: Minimum games before triggering rollback

    Returns:
        dict with 'should_rollback' bool and diagnostic info
    """
    if not per_model_ats or current_weights == BASE_WEIGHTS:
        return {'should_rollback': False, 'reason': 'already_base'}

    # Check if we have enough games
    total_games = sum(per_model_ats.get(n, {}).get('games', 0) for n in BASE_WEIGHTS)
    avg_games = total_games / 4
    if avg_games < min_rollback_games:
        return {'should_rollback': False, 'reason': 'insufficient_games', 'avg_games': avg_games}

    # Compute weighted ATS for current dynamic weights vs base weights
    dynamic_ats = 0
    base_ats = 0
    for name in BASE_WEIGHTS:
        stats = per_model_ats.get(name, {})
        ats = stats.get('ats_pct', 50.0)
        dynamic_ats += ats * current_weights.get(name, BASE_WEIGHTS[name])
        base_ats += ats * BASE_WEIGHTS[name]

    return {
        'should_rollback': dynamic_ats < base_ats,
        'reason': 'underperforming' if dynamic_ats < base_ats else 'outperforming',
        'dynamic_weighted_ats': round(dynamic_ats, 2),
        'base_weighted_ats': round(base_ats, 2),
        'avg_games': avg_games,
    }


def validate_weights(weights):
    """Validate a weight dict meets all constraints.

    Returns:
        tuple: (is_valid: bool, errors: list[str])
    """
    errors = []
    required = {'efficiency', 'similar', 'conrat', 'mc'}

    if not required.issubset(weights.keys()):
        errors.append(f'Missing model weights: {required - set(weights.keys())}')
        return False, errors

    total = sum(weights.values())
    if abs(total - 1.0) > 0.01:
        errors.append(f'Weights sum to {total:.4f}, not 1.0')

    for name, val in weights.items():
        if name not in required:
            continue
        if val < MIN_MODEL_WEIGHT:
            errors.append(f'{name} weight {val:.4f} below minimum {MIN_MODEL_WEIGHT}')
        deviation = abs(val - BASE_WEIGHTS[name])
        if deviation > MAX_WEIGHT_DEVIATION + 0.01:  # small tolerance
            errors.append(f'{name} weight deviates {deviation:.4f} from base (max {MAX_WEIGHT_DEVIATION})')

    return len(errors) == 0, errors
