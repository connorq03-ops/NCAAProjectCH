"""Tests for golf_weight_optimizer.py.

Mirrors the patterns that would test model_weight_optimizer.py (basketball),
adapted for golf-specific models and metrics.
"""
import pytest
from datetime import datetime

from golf.golf_weight_optimizer import (
    BASE_WEIGHTS, MAX_WEIGHT_DEVIATION, MIN_MODEL_WEIGHT,
    compute_per_model_accuracy, compute_optimal_weights,
    should_reset_weights, should_rollback, validate_weights,
)


# ─── Fixtures ──────────────────────────────────────────────────────────────

def _make_result(sg_eff_finish=20, cf_finish=25, gr_finish=15, mc_finish=30,
                 actual_finish=18, actual_top10=False):
    """Create a single backtest result dict for testing."""
    return {
        'sub_model_finishes': {
            'sg_efficiency': sg_eff_finish,
            'course_fit': cf_finish,
            'golf_rat': gr_finish,
            'mc': mc_finish,
        },
        'actual_finish': actual_finish,
        'actual_top10': actual_top10,
    }


def _make_results(n=200, sg_mae=10, cf_mae=20, gr_mae=5, mc_mae=15):
    """Create N results where each model has a predictable MAE.

    For simplicity, actual_finish is always 20, and each model's predicted
    finish is offset by the specified MAE from actual.
    """
    results = []
    for i in range(n):
        actual = 20
        results.append(_make_result(
            sg_eff_finish=actual + sg_mae,
            cf_finish=actual + cf_mae,
            gr_finish=actual + gr_mae,
            mc_finish=actual + mc_mae,
            actual_finish=actual,
            actual_top10=actual <= 10,
        ))
    return results


# ─── compute_per_model_accuracy ────────────────────────────────────────────

class TestComputePerModelAccuracy:

    def test_correct_mae_from_sample_data(self):
        """compute_per_model_accuracy() returns correct MAE from sample data."""
        results = _make_results(n=50, sg_mae=10, cf_mae=20, gr_mae=5, mc_mae=15)
        acc = compute_per_model_accuracy(results)

        assert 'sg_efficiency' in acc
        assert acc['sg_efficiency']['mae'] == 10.0
        assert acc['course_fit']['mae'] == 20.0
        assert acc['golf_rat']['mae'] == 5.0
        assert acc['mc']['mae'] == 15.0

    def test_empty_results(self):
        """compute_per_model_accuracy() returns empty dict for empty results."""
        acc = compute_per_model_accuracy([])
        assert acc == {}

    def test_player_count(self):
        """compute_per_model_accuracy() tracks correct player count."""
        results = _make_results(n=75)
        acc = compute_per_model_accuracy(results)
        for model in BASE_WEIGHTS:
            assert acc[model]['players'] == 75

    def test_window_truncation(self):
        """compute_per_model_accuracy() respects window parameter."""
        results = _make_results(n=500)
        acc = compute_per_model_accuracy(results, window=100)
        for model in BASE_WEIGHTS:
            assert acc[model]['players'] == 100

    def test_top10_hit_rate(self):
        """compute_per_model_accuracy() computes top-10 hit rate."""
        results = []
        for i in range(20):
            # Model predicts finish=5 (top 10), player actually finishes 3 (top 10)
            results.append(_make_result(
                sg_eff_finish=5, cf_finish=5, gr_finish=5, mc_finish=5,
                actual_finish=3, actual_top10=True))
        for i in range(20):
            # Model predicts finish=5 (top 10), player actually finishes 30 (NOT top 10)
            results.append(_make_result(
                sg_eff_finish=5, cf_finish=5, gr_finish=5, mc_finish=5,
                actual_finish=30, actual_top10=False))

        acc = compute_per_model_accuracy(results)
        # 20 out of 40 predicted top-10 players actually finished top 10 = 50%
        assert acc['sg_efficiency']['top10_hit_rate'] == 50.0


# ─── compute_optimal_weights ──────────────────────────────────────────────

class TestComputeOptimalWeights:

    def test_base_weights_when_no_data(self):
        """compute_optimal_weights() returns base weights when no data."""
        result = compute_optimal_weights({})
        assert result == BASE_WEIGHTS

    def test_base_weights_when_none(self):
        """compute_optimal_weights() returns base weights when None."""
        result = compute_optimal_weights(None)
        assert result == BASE_WEIGHTS

    def test_higher_weight_for_lower_mae(self):
        """compute_optimal_weights() gives higher weight to model with lower MAE."""
        per_model = {
            'sg_efficiency': {'mae': 5.0, 'top10_hit_rate': 30.0, 'players': 200},
            'course_fit': {'mae': 20.0, 'top10_hit_rate': 10.0, 'players': 200},
            'golf_rat': {'mae': 10.0, 'top10_hit_rate': 20.0, 'players': 200},
            'mc': {'mae': 15.0, 'top10_hit_rate': 15.0, 'players': 200},
        }
        result = compute_optimal_weights(per_model)

        # sg_efficiency has lowest MAE, should get highest weight
        assert result['sg_efficiency'] > result['course_fit']
        # golf_rat has second lowest MAE
        assert result['golf_rat'] > result['mc']

    def test_respects_max_weight_deviation(self):
        """compute_optimal_weights() respects MAX_WEIGHT_DEVIATION constraint."""
        per_model = {
            'sg_efficiency': {'mae': 1.0, 'top10_hit_rate': 80.0, 'players': 500},
            'course_fit': {'mae': 100.0, 'top10_hit_rate': 1.0, 'players': 500},
            'golf_rat': {'mae': 100.0, 'top10_hit_rate': 1.0, 'players': 500},
            'mc': {'mae': 100.0, 'top10_hit_rate': 1.0, 'players': 500},
        }
        result = compute_optimal_weights(per_model)

        for name in BASE_WEIGHTS:
            deviation = abs(result[name] - BASE_WEIGHTS[name])
            # Allow small tolerance for normalization
            assert deviation <= MAX_WEIGHT_DEVIATION + 0.02, \
                f"{name} deviation {deviation:.4f} exceeds {MAX_WEIGHT_DEVIATION}"

    def test_respects_min_model_weight(self):
        """compute_optimal_weights() respects MIN_MODEL_WEIGHT constraint."""
        per_model = {
            'sg_efficiency': {'mae': 1.0, 'top10_hit_rate': 80.0, 'players': 500},
            'course_fit': {'mae': 200.0, 'top10_hit_rate': 0.5, 'players': 500},
            'golf_rat': {'mae': 200.0, 'top10_hit_rate': 0.5, 'players': 500},
            'mc': {'mae': 200.0, 'top10_hit_rate': 0.5, 'players': 500},
        }
        result = compute_optimal_weights(per_model)

        for name in BASE_WEIGHTS:
            assert result[name] >= MIN_MODEL_WEIGHT, \
                f"{name} weight {result[name]:.4f} below minimum {MIN_MODEL_WEIGHT}"

    def test_weights_sum_to_one(self):
        """compute_optimal_weights() weights sum to 1.0."""
        per_model = {
            'sg_efficiency': {'mae': 8.0, 'top10_hit_rate': 25.0, 'players': 300},
            'course_fit': {'mae': 12.0, 'top10_hit_rate': 18.0, 'players': 300},
            'golf_rat': {'mae': 6.0, 'top10_hit_rate': 30.0, 'players': 300},
            'mc': {'mae': 10.0, 'top10_hit_rate': 22.0, 'players': 300},
        }
        result = compute_optimal_weights(per_model)
        total = sum(result.values())
        assert abs(total - 1.0) < 0.001, f"Weights sum to {total}, not 1.0"

    def test_returns_base_when_insufficient_data(self):
        """compute_optimal_weights() returns base weights when players < min_players."""
        per_model = {
            'sg_efficiency': {'mae': 8.0, 'top10_hit_rate': 25.0, 'players': 10},
            'course_fit': {'mae': 12.0, 'top10_hit_rate': 18.0, 'players': 10},
            'golf_rat': {'mae': 6.0, 'top10_hit_rate': 30.0, 'players': 10},
            'mc': {'mae': 10.0, 'top10_hit_rate': 22.0, 'players': 10},
        }
        result = compute_optimal_weights(per_model, min_players=100)
        assert result == BASE_WEIGHTS


# ─── should_reset_weights ─────────────────────────────────────────────────

class TestShouldResetWeights:

    def test_returns_true_before_season_start(self):
        """should_reset_weights() returns True when last_updated is before season start."""
        # PGA Tour season starts Jan 1. A date from last year should trigger reset.
        result = should_reset_weights('2023-06-15T12:00:00')
        assert result is True

    def test_returns_false_current_season(self):
        """should_reset_weights() returns False when last_updated is current season."""
        now = datetime.utcnow()
        recent = now.isoformat()
        result = should_reset_weights(recent)
        assert result is False

    def test_returns_false_for_none(self):
        """should_reset_weights() returns False for None input."""
        assert should_reset_weights(None) is False

    def test_returns_false_for_empty_string(self):
        """should_reset_weights() returns False for empty string."""
        assert should_reset_weights('') is False

    def test_returns_false_for_invalid(self):
        """should_reset_weights() returns False for invalid date string."""
        assert should_reset_weights('not-a-date') is False


# ─── should_rollback ──────────────────────────────────────────────────────

class TestShouldRollback:

    def test_returns_true_when_dynamic_worse(self):
        """should_rollback() returns True when dynamic weights produce higher MAE."""
        per_model = {
            'sg_efficiency': {'mae': 10.0, 'players': 200},
            'course_fit': {'mae': 10.0, 'players': 200},
            'golf_rat': {'mae': 10.0, 'players': 200},
            'mc': {'mae': 10.0, 'players': 200},
        }
        # Weights that put more emphasis on all models equally — same as base
        # But let's make dynamic weights emphasize a high-MAE model
        per_model_skewed = {
            'sg_efficiency': {'mae': 5.0, 'players': 200},
            'course_fit': {'mae': 30.0, 'players': 200},
            'golf_rat': {'mae': 5.0, 'players': 200},
            'mc': {'mae': 5.0, 'players': 200},
        }
        # Dynamic weights put too much on course_fit (high MAE)
        dynamic_weights = {'sg_efficiency': 0.10, 'course_fit': 0.60, 'golf_rat': 0.15, 'mc': 0.15}
        result = should_rollback(per_model_skewed, dynamic_weights)
        assert result['should_rollback'] is True

    def test_returns_false_insufficient_data(self):
        """should_rollback() returns False with insufficient data."""
        per_model = {
            'sg_efficiency': {'mae': 10.0, 'players': 20},
            'course_fit': {'mae': 10.0, 'players': 20},
            'golf_rat': {'mae': 10.0, 'players': 20},
            'mc': {'mae': 10.0, 'players': 20},
        }
        dynamic_weights = {'sg_efficiency': 0.30, 'course_fit': 0.20, 'golf_rat': 0.30, 'mc': 0.20}
        result = should_rollback(per_model, dynamic_weights)
        assert result['should_rollback'] is False
        assert result['reason'] == 'insufficient_data'

    def test_returns_false_when_already_base(self):
        """should_rollback() returns False when already using base weights."""
        result = should_rollback({}, dict(BASE_WEIGHTS))
        assert result['should_rollback'] is False
        assert result['reason'] == 'already_base'


# ─── validate_weights ─────────────────────────────────────────────────────

class TestValidateWeights:

    def test_rejects_weights_not_summing_to_one(self):
        """validate_weights() rejects weights that don't sum to 1.0."""
        weights = {'sg_efficiency': 0.5, 'course_fit': 0.5, 'golf_rat': 0.5, 'mc': 0.5}
        is_valid, errors = validate_weights(weights)
        assert is_valid is False
        assert any('sum' in e.lower() for e in errors)

    def test_rejects_below_min_weight(self):
        """validate_weights() rejects weights below MIN_MODEL_WEIGHT."""
        weights = {'sg_efficiency': 0.01, 'course_fit': 0.33, 'golf_rat': 0.33, 'mc': 0.33}
        is_valid, errors = validate_weights(weights)
        assert is_valid is False
        assert any('below minimum' in e for e in errors)

    def test_accepts_valid_weights(self):
        """validate_weights() accepts valid weight dicts."""
        is_valid, errors = validate_weights(dict(BASE_WEIGHTS))
        assert is_valid is True
        assert errors == []

    def test_rejects_missing_keys(self):
        """validate_weights() rejects weights with missing model keys."""
        weights = {'sg_efficiency': 0.5, 'course_fit': 0.5}
        is_valid, errors = validate_weights(weights)
        assert is_valid is False
        assert any('Missing' in e for e in errors)

    def test_accepts_slightly_off_weights(self):
        """validate_weights() accepts weights that sum to ~1.0 (within tolerance)."""
        weights = {'sg_efficiency': 0.2501, 'course_fit': 0.2499, 'golf_rat': 0.2500, 'mc': 0.2500}
        is_valid, errors = validate_weights(weights)
        assert is_valid is True
