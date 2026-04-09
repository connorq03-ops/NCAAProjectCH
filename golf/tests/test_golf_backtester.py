"""Tests for golf_backtester.py.

Mirrors the patterns from backtester.py tests, adapted for golf-specific
tournament backtesting with ordinal/probability outcomes.
"""
import pytest
import json
import os
from unittest.mock import patch, MagicMock

from golf.golf_backtester import GolfBacktester


# ─── Fixtures ──────────────────────────────────────────────────────────────

def _make_player_result(player='Scottie Scheffler', predicted_finish=5.0,
                        actual_finish=3, predicted_win_pct=0.15,
                        actual_won=False, predicted_top10_pct=0.60,
                        actual_top10=True, predicted_cut_pct=0.95,
                        actual_made_cut=True, tournament_id='401',
                        tournament_name='The Masters', course_id='augusta_national'):
    """Create a single per-player result dict for testing."""
    return {
        'tournament_id': tournament_id,
        'tournament_name': tournament_name,
        'course_id': course_id,
        'player_name': player,
        'predicted_finish': predicted_finish,
        'actual_finish': actual_finish,
        'finish_error': abs(predicted_finish - actual_finish),
        'predicted_win_pct': predicted_win_pct,
        'actual_won': actual_won,
        'predicted_top5_pct': 0.30,
        'actual_top5': 1 <= actual_finish <= 5,
        'predicted_top10_pct': predicted_top10_pct,
        'actual_top10': actual_top10,
        'predicted_top20_pct': 0.70,
        'actual_top20': 1 <= actual_finish <= 20,
        'predicted_cut_pct': predicted_cut_pct,
        'actual_made_cut': actual_made_cut,
        'golf_rat_score': 7.5,
        'composite_weights': {'sg_efficiency': 0.25, 'course_fit': 0.25, 'golf_rat': 0.25, 'mc': 0.25},
        'sub_model_finishes': {
            'sg_efficiency': predicted_finish + 2,
            'course_fit': predicted_finish + 5,
            'golf_rat': predicted_finish - 1,
            'mc': predicted_finish + 3,
        },
        'odds_win_pct': None,
        'odds_value': None,
    }


def _make_sample_results(n=20):
    """Create a list of sample results for metrics testing."""
    results = []
    players = [
        ('Scottie Scheffler', 3, True, True),
        ('Rory McIlroy', 8, False, True),
        ('Jon Rahm', 12, False, True),
        ('Viktor Hovland', 22, False, True),
        ('Xander Schauffele', 1, True, True),
    ]
    for i in range(n):
        p = players[i % len(players)]
        name, actual, top10, made_cut = p
        tid = f'tournament_{i // 5}'
        results.append(_make_player_result(
            player=name,
            predicted_finish=actual + (i % 3) - 1,  # small variation
            actual_finish=actual,
            actual_won=(actual == 1),
            predicted_win_pct=0.20 if actual <= 3 else 0.02,
            predicted_top10_pct=0.50 if top10 else 0.10,
            actual_top10=top10,
            predicted_cut_pct=0.90 if made_cut else 0.30,
            actual_made_cut=made_cut,
            tournament_id=tid,
            tournament_name=f'Tournament {i // 5}',
        ))
    return results


# ─── GolfBacktester.__init__ ──────────────────────────────────────────────

class TestGolfBacktesterInit:

    def test_creates_instance(self):
        """GolfBacktester.__init__ creates instance correctly."""
        bt = GolfBacktester()
        assert bt.predictions_file == 'golf_predictions.json'

    def test_custom_predictions_file(self):
        """GolfBacktester.__init__ accepts custom predictions file."""
        bt = GolfBacktester(predictions_file='custom.json')
        assert bt.predictions_file == 'custom.json'


# ─── _normalize ───────────────────────────────────────────────────────────

class TestNormalize:

    def test_lowercase(self):
        bt = GolfBacktester()
        assert bt._normalize('SCOTTIE SCHEFFLER') == 'scottie scheffler'

    def test_removes_dots(self):
        bt = GolfBacktester()
        assert bt._normalize('J.T. Poston') == 'jt poston'

    def test_removes_apostrophes(self):
        bt = GolfBacktester()
        assert bt._normalize("Si Woo Kim") == 'si woo kim'
        assert bt._normalize("Seamus O'Brien") == 'seamus obrien'

    def test_strips_whitespace(self):
        bt = GolfBacktester()
        assert bt._normalize('  Tiger Woods  ') == 'tiger woods'

    def test_handles_hyphens(self):
        bt = GolfBacktester()
        assert bt._normalize('Byeong-Hun An') == 'byeong hun an'


# ─── _match_player ────────────────────────────────────────────────────────

class TestMatchPlayer:

    def test_exact_match(self):
        bt = GolfBacktester()
        names = ['Scottie Scheffler', 'Rory McIlroy', 'Jon Rahm']
        assert bt._match_player('Scottie Scheffler', names) == 'Scottie Scheffler'

    def test_case_insensitive_match(self):
        bt = GolfBacktester()
        names = ['Scottie Scheffler', 'Rory McIlroy']
        assert bt._match_player('scottie scheffler', names) == 'Scottie Scheffler'

    def test_last_name_match(self):
        bt = GolfBacktester()
        names = ['Scottie Scheffler', 'Rory McIlroy']
        result = bt._match_player('S. Scheffler', names)
        assert result == 'Scottie Scheffler'

    def test_no_match_returns_none(self):
        bt = GolfBacktester()
        names = ['Scottie Scheffler', 'Rory McIlroy']
        assert bt._match_player('Tiger Woods', names) is None


# ─── _compute_metrics ─────────────────────────────────────────────────────

class TestComputeMetrics:

    def test_computes_correct_mae(self):
        """_compute_metrics() computes correct MAE from sample results."""
        bt = GolfBacktester()
        results = [
            _make_player_result(predicted_finish=10.0, actual_finish=15,
                                tournament_id='t1', tournament_name='T1'),
            _make_player_result(predicted_finish=5.0, actual_finish=3,
                                tournament_id='t1', tournament_name='T1'),
            _make_player_result(predicted_finish=20.0, actual_finish=25,
                                tournament_id='t2', tournament_name='T2'),
        ]
        # Override finish_error to match
        for r in results:
            r['finish_error'] = abs(r['predicted_finish'] - r['actual_finish'])

        metrics = bt._compute_metrics(results)
        # MAE = (5 + 2 + 5) / 3 = 4.0
        assert metrics['finish_mae'] == 4.0

    def test_brier_score_perfect_predictions(self):
        """_compute_metrics() Brier score is 0 for perfect predictions."""
        bt = GolfBacktester()
        results = []
        for i in range(10):
            results.append(_make_player_result(
                predicted_win_pct=1.0, actual_won=True,
                predicted_top10_pct=1.0, actual_top10=True,
                predicted_cut_pct=1.0, actual_made_cut=True,
                tournament_id=f't{i}', tournament_name=f'T{i}',
            ))
        metrics = bt._compute_metrics(results)
        assert metrics['win_brier_score'] == 0.0
        assert metrics['top10_brier_score'] == 0.0
        assert metrics['cut_brier_score'] == 0.0

    def test_brier_score_worst_predictions(self):
        """_compute_metrics() Brier score is ~1.0 for completely wrong predictions."""
        bt = GolfBacktester()
        results = []
        for i in range(10):
            results.append(_make_player_result(
                predicted_win_pct=1.0, actual_won=False,
                predicted_top10_pct=1.0, actual_top10=False,
                actual_finish=50,
                predicted_cut_pct=1.0, actual_made_cut=False,
                tournament_id=f't{i}', tournament_name=f'T{i}',
            ))
        metrics = bt._compute_metrics(results)
        assert metrics['win_brier_score'] == 1.0
        assert metrics['top10_brier_score'] == 1.0
        assert metrics['cut_brier_score'] == 1.0

    def test_by_tier_breakdown_has_keys(self):
        """_compute_metrics() by-tier breakdown has correct structure."""
        bt = GolfBacktester()
        results = _make_sample_results(20)
        metrics = bt._compute_metrics(results)
        by_tier = metrics.get('by_tier', {})
        # Should have at least one tier
        assert len(by_tier) > 0
        # Each tier should have standard keys
        for tier, data in by_tier.items():
            assert 'players' in data
            assert 'finish_mae' in data
            assert 'top10_hit_rate' in data

    def test_empty_results(self):
        """_compute_metrics() handles empty results gracefully."""
        bt = GolfBacktester()
        metrics = bt._compute_metrics([])
        assert metrics['total_tournaments'] == 0
        assert metrics['total_players_evaluated'] == 0

    def test_total_counts(self):
        """_compute_metrics() tracks correct totals."""
        bt = GolfBacktester()
        results = _make_sample_results(20)
        metrics = bt._compute_metrics(results)
        assert metrics['total_players_evaluated'] == 20
        assert metrics['total_tournaments'] > 0


# ─── _validate_backtest ───────────────────────────────────────────────────

class TestValidateBacktest:

    def test_flags_suspicious_low_mae(self):
        """_validate_backtest() flags suspiciously low MAE."""
        bt = GolfBacktester()
        metrics = {'finish_mae': 0.5, 'cut_prediction_accuracy': 65,
                    'top10_hit_rate': 15, 'win_brier_score': 0.02}
        issues = bt._validate_backtest(metrics)
        assert any('low' in i.lower() for i in issues)

    def test_flags_suspicious_high_mae(self):
        """_validate_backtest() flags very high MAE."""
        bt = GolfBacktester()
        metrics = {'finish_mae': 200, 'cut_prediction_accuracy': 65,
                    'top10_hit_rate': 15, 'win_brier_score': 0.02}
        issues = bt._validate_backtest(metrics)
        assert any('high' in i.lower() for i in issues)

    def test_no_warnings_for_normal_metrics(self):
        """_validate_backtest() returns empty list for normal metrics."""
        bt = GolfBacktester()
        metrics = {'finish_mae': 15.0, 'cut_prediction_accuracy': 65,
                    'top10_hit_rate': 20, 'win_brier_score': 0.02}
        issues = bt._validate_backtest(metrics)
        assert issues == []

    def test_flags_low_cut_accuracy(self):
        """_validate_backtest() flags cut accuracy below 40%."""
        bt = GolfBacktester()
        metrics = {'finish_mae': 15, 'cut_prediction_accuracy': 30,
                    'top10_hit_rate': 15, 'win_brier_score': 0.02}
        issues = bt._validate_backtest(metrics)
        assert any('cut' in i.lower() for i in issues)

    def test_flags_high_brier(self):
        """_validate_backtest() flags high win Brier score."""
        bt = GolfBacktester()
        metrics = {'finish_mae': 15, 'cut_prediction_accuracy': 65,
                    'top10_hit_rate': 15, 'win_brier_score': 0.5}
        issues = bt._validate_backtest(metrics)
        assert any('brier' in i.lower() for i in issues)


# ─── backtest_predictions ─────────────────────────────────────────────────

class TestBacktestPredictions:

    def test_returns_error_when_no_predictions(self, tmp_path):
        """backtest_predictions() returns error dict when no completed predictions."""
        pred_file = tmp_path / 'empty_preds.json'
        pred_file.write_text('[]')
        bt = GolfBacktester(predictions_file=str(pred_file))
        result = bt.backtest_predictions()
        assert 'error' in result

    def test_returns_error_when_no_completed(self, tmp_path):
        """backtest_predictions() returns error when predictions exist but none completed."""
        pred_file = tmp_path / 'preds.json'
        pred_file.write_text(json.dumps([
            {'player': 'Test', 'result_entered': False}
        ]))
        bt = GolfBacktester(predictions_file=str(pred_file))
        result = bt.backtest_predictions()
        assert 'error' in result

    def test_returns_error_when_file_missing(self):
        """backtest_predictions() returns error when predictions file doesn't exist."""
        bt = GolfBacktester(predictions_file='/nonexistent/path.json')
        result = bt.backtest_predictions()
        assert 'error' in result


# ─── backtest_tournament (integration test with mocks) ────────────────────

class TestBacktestTournament:

    def test_produces_correct_result_structure(self):
        """backtest_tournament() produces correct result structure with mock data."""
        bt = GolfBacktester()

        # Mock DataGolf client
        mock_client = MagicMock()
        mock_client.get_historical_rounds.return_value = [
            {'player_name': 'Scottie Scheffler', 'fin_num': 1, 'made_cut': True,
             'event_name': 'The Masters', 'course_id': 'augusta_national'},
            {'player_name': 'Rory McIlroy', 'fin_num': 5, 'made_cut': True,
             'event_name': 'The Masters', 'course_id': 'augusta_national'},
            {'player_name': 'Jon Rahm', 'fin_num': 15, 'made_cut': True,
             'event_name': 'The Masters', 'course_id': 'augusta_national'},
        ]
        mock_client.get_rankings.return_value = {
            'rankings': [
                {'player_name': 'Scottie Scheffler', 'dg_skill_estimate': 2.5,
                 'sg_ott': 0.8, 'sg_app': 0.7, 'sg_arg': 0.3, 'sg_putt': 0.5},
                {'player_name': 'Rory McIlroy', 'dg_skill_estimate': 2.0,
                 'sg_ott': 1.0, 'sg_app': 0.5, 'sg_arg': 0.1, 'sg_putt': 0.3},
                {'player_name': 'Jon Rahm', 'dg_skill_estimate': 1.8,
                 'sg_ott': 0.6, 'sg_app': 0.6, 'sg_arg': 0.2, 'sg_putt': 0.4},
            ]
        }

        mock_cache = MagicMock()
        mock_cache.get.return_value = None

        # Mock predict_field and simulate_tournament to avoid full pipeline
        with patch('golf.golf_backtester.predict_field') as mock_predict, \
             patch('golf.golf_backtester.build_player_sim_params') as mock_build_sp, \
             patch('golf.golf_backtester.simulate_tournament') as mock_sim:

            mock_predict.return_value = [
                {'player_name': 'Scottie Scheffler', 'predicted_finish': 5.0,
                 'win_prob': 0.15, 'top5_prob': 0.35, 'top10_prob': 0.55,
                 'top20_prob': 0.80, 'make_cut_prob': 0.95, 'golf_rat_score': 8.0,
                 'weights_used': {'sg_efficiency': 0.25, 'course_fit': 0.25,
                                  'golf_rat': 0.25, 'mc': 0.25},
                 'model_details': {
                     'sg_efficiency': {'predicted_finish': 4.0},
                     'course_fit': {'predicted_finish': 6.0},
                     'golf_rat': {'predicted_finish': 3.0},
                     'mc': {'predicted_finish': 7.0},
                 }},
                {'player_name': 'Rory McIlroy', 'predicted_finish': 8.0,
                 'win_prob': 0.08, 'top5_prob': 0.25, 'top10_prob': 0.45,
                 'top20_prob': 0.70, 'make_cut_prob': 0.90, 'golf_rat_score': 7.0,
                 'weights_used': {'sg_efficiency': 0.25, 'course_fit': 0.25,
                                  'golf_rat': 0.25, 'mc': 0.25},
                 'model_details': {
                     'sg_efficiency': {'predicted_finish': 7.0},
                     'course_fit': {'predicted_finish': 9.0},
                     'golf_rat': {'predicted_finish': 6.0},
                     'mc': {'predicted_finish': 10.0},
                 }},
            ]
            mock_sim.return_value = {}  # No MC results
            mock_build_sp.return_value = {}

            results = bt.backtest_tournament('401', mock_client, mock_cache)

        # Verify results structure
        assert len(results) >= 1
        r = results[0]
        assert 'tournament_id' in r
        assert 'player_name' in r
        assert 'predicted_finish' in r
        assert 'actual_finish' in r
        assert 'finish_error' in r
        assert 'predicted_win_pct' in r
        assert 'actual_won' in r
        assert 'sub_model_finishes' in r
        assert 'composite_weights' in r

    def test_returns_empty_on_api_failure(self):
        """backtest_tournament() returns empty list when API fails."""
        bt = GolfBacktester()
        mock_client = MagicMock()
        mock_client.get_historical_rounds.side_effect = Exception("API error")
        mock_cache = MagicMock()

        results = bt.backtest_tournament('999', mock_client, mock_cache)
        assert results == []
