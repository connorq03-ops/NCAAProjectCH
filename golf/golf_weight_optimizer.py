"""Golf dynamic model weight optimizer.

Consumes backtest results to determine optimal composite weights
based on rolling per-model accuracy.

Mirrors model_weight_optimizer.py (basketball) with golf-specific models
and metrics.

Validation safeguards (same as basketball):
  - Minimum sample size before trusting dynamic weights (default 100 players)
  - Maximum deviation of +/-0.15 from base weights
  - Seasonal reset on January 1 (PGA Tour season start)
  - A/B comparison: reports accuracy with both base and dynamic weights
  - Auto-rollback if dynamic weights underperform base
"""
import math
from collections import defaultdict
from datetime import datetime


# ─── Base (default) weights ────────────────────────────────────────────────

BASE_WEIGHTS = {
    'sg_efficiency': 0.25,
    'course_fit': 0.25,
    'golf_rat': 0.25,
    'mc': 0.25,
}

# Maximum allowed deviation from base weight per model
MAX_WEIGHT_DEVIATION = 0.15

# Minimum weight any single model can have
MIN_MODEL_WEIGHT = 0.05


def compute_per_model_accuracy(results, window=200):
    """Compute rolling per-model accuracy using finish position MAE and top-10 hit rate.

    Analogous to compute_per_model_ats() in model_weight_optimizer.py (lines 36-92).
    Instead of ATS hit rate, we compute finish position MAE and top-10 hit rate.

    Args:
        results: List of backtest result dicts (must have 'sub_model_finishes',
                 'actual_finish', 'actual_top10')
        window: Number of most recent player-results to consider

    Returns:
        dict: {model_name: {'mae': float, 'top10_hit_rate': float, 'players': int}}
    """
    recent = results[-window:] if len(results) > window else results

    if not recent:
        return {}

    model_stats = defaultdict(lambda: {'errors': [], 'top10_pred': 0, 'top10_actual': 0})

    for r in recent:
        subs = r.get('sub_model_finishes', {})
        actual_finish = r.get('actual_finish')
        actual_top10 = r.get('actual_top10', False)

        if actual_finish is None:
            continue

        for model_name, model_finish in subs.items():
            if model_finish is None:
                continue
            model_stats[model_name]['errors'].append(abs(model_finish - actual_finish))
            # Count top-10 predictions: model predicted top 10 and player actually finished top 10
            if model_finish <= 10:
                model_stats[model_name]['top10_pred'] += 1
                if actual_top10:
                    model_stats[model_name]['top10_actual'] += 1

    result = {}
    for name, stats in model_stats.items():
        errors = stats['errors']
        if not errors:
            continue
        top10_pred = stats['top10_pred']
        top10_hit = stats['top10_actual'] / top10_pred * 100 if top10_pred > 0 else 0.0
        result[name] = {
            'mae': round(sum(errors) / len(errors), 2),
            'top10_hit_rate': round(top10_hit, 1),
            'players': len(errors),
        }
    return result


def compute_optimal_weights(per_model_accuracy, min_players=100):
    """Compute optimal composite weights from per-model accuracy.

    Uses inverse-MAE weighting: models with lower error get higher weight.
    Blends toward base weights when sample size is small.

    Mirrors compute_optimal_weights() in model_weight_optimizer.py (lines 95-149).

    Args:
        per_model_accuracy: Output of compute_per_model_accuracy()
        min_players: Minimum players evaluated before trusting dynamic weights

    Returns:
        dict: {'sg_efficiency': float, 'course_fit': float, 'golf_rat': float, 'mc': float}
    """
    if not per_model_accuracy:
        return dict(BASE_WEIGHTS)

    # Compute inverse-MAE weights
    inv_mae = {}
    for name in BASE_WEIGHTS:
        stats = per_model_accuracy.get(name, {})
        mae = stats.get('mae')
        players = stats.get('players', 0)
        if mae and mae > 0 and players >= min_players:
            inv_mae[name] = 1.0 / mae
        else:
            inv_mae[name] = None  # not enough data

    # If we have enough data for all models, compute dynamic weights
    has_all = all(v is not None for v in inv_mae.values())
    if has_all:
        total_inv = sum(inv_mae.values())
        dynamic = {name: inv_mae[name] / total_inv for name in BASE_WEIGHTS}

        # Blend dynamic with base weights based on sample size
        # More players -> trust dynamic more (up to 70% dynamic at 500+ players)
        avg_players = sum(per_model_accuracy[n]['players'] for n in BASE_WEIGHTS) / 4
        blend_factor = min(avg_players / 500, 0.70)  # 0 to 0.70

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

        # Normalize to sum to 1.0, then re-clamp to respect constraints
        # (normalization can push weights outside bounds)
        for _iteration in range(5):
            total = sum(result.values())
            result = {name: v / total for name, v in result.items()}
            # Re-clamp after normalization
            clamped = False
            for name in BASE_WEIGHTS:
                lo = max(MIN_MODEL_WEIGHT, BASE_WEIGHTS[name] - MAX_WEIGHT_DEVIATION)
                hi = BASE_WEIGHTS[name] + MAX_WEIGHT_DEVIATION
                if result[name] < lo:
                    result[name] = lo
                    clamped = True
                elif result[name] > hi:
                    result[name] = hi
                    clamped = True
            if not clamped:
                break
        total = sum(result.values())
        result = {name: round(v / total, 4) for name, v in result.items()}
        return result

    return dict(BASE_WEIGHTS)


def should_reset_weights(last_updated_str):
    """Check if dynamic weights should be reset (seasonal reset on Jan 1).

    PGA Tour season resets around January (not November like basketball).

    Mirrors should_reset_weights() in model_weight_optimizer.py (lines 152-178).

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
    # PGA Tour season start: January 1
    # If we're in January or later, season started Jan 1 of this year
    season_start = datetime(now.year, 1, 1)

    return last_updated < season_start


def should_rollback(per_model_accuracy, current_weights, min_rollback_players=150):
    """Check if dynamic weights should be rolled back to base weights.

    If dynamic weights produce worse MAE than base weights over enough data,
    auto-revert to base weights.

    Mirrors should_rollback() in model_weight_optimizer.py (lines 181-219).

    Args:
        per_model_accuracy: Output of compute_per_model_accuracy()
        current_weights: Currently active weight dict
        min_rollback_players: Minimum players before triggering rollback

    Returns:
        dict with 'should_rollback' bool and diagnostic info
    """
    if not per_model_accuracy or current_weights == BASE_WEIGHTS:
        return {'should_rollback': False, 'reason': 'already_base'}

    # Check if we have enough data
    total_players = sum(per_model_accuracy.get(n, {}).get('players', 0) for n in BASE_WEIGHTS)
    avg_players = total_players / 4
    if avg_players < min_rollback_players:
        return {'should_rollback': False, 'reason': 'insufficient_data', 'avg_players': avg_players}

    # Compute weighted MAE for current dynamic weights vs base weights
    # Lower MAE is better, so if dynamic MAE > base MAE, rollback
    dynamic_mae = 0
    base_mae = 0
    for name in BASE_WEIGHTS:
        stats = per_model_accuracy.get(name, {})
        mae = stats.get('mae', 20.0)
        dynamic_mae += mae * current_weights.get(name, BASE_WEIGHTS[name])
        base_mae += mae * BASE_WEIGHTS[name]

    return {
        'should_rollback': dynamic_mae > base_mae,
        'reason': 'underperforming' if dynamic_mae > base_mae else 'outperforming',
        'dynamic_weighted_mae': round(dynamic_mae, 2),
        'base_weighted_mae': round(base_mae, 2),
        'avg_players': avg_players,
    }


def validate_weights(weights):
    """Validate a weight dict meets all constraints.

    Mirrors validate_weights() in model_weight_optimizer.py (lines 222-248).

    Returns:
        tuple: (is_valid: bool, errors: list[str])
    """
    errors = []
    required = {'sg_efficiency', 'course_fit', 'golf_rat', 'mc'}

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
