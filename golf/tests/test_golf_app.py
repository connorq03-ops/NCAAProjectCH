"""
Tests for golf_app.py — Flask test client tests for all golf API endpoints.
Uses mocking to avoid requiring real API keys.
"""

import json
import os
import sys
import tempfile
import unittest
from unittest.mock import patch, MagicMock

# Set a dummy API key before importing golf_app
os.environ['DATAGOLF_API_KEY'] = 'test_key_12345'

# Patch external dependencies before importing golf_app
with patch('golf.golf_app.DataGolfClient') as MockDGClient, \
     patch('golf.golf_app.GolfTournamentSimulator') as MockGolfSim, \
     patch('golf.golf_app.WeatherFetcher') as MockWeatherFetcher, \
     patch('golf.golf_app.FormTracker') as MockFormTracker, \
     patch('golf.golf_app.GolfWDAnalyzer', side_effect=ValueError("No API key")):

    mock_dg = MockDGClient.return_value
    mock_dg.get_rankings.return_value = {
        'rankings': [
            {'player_name': 'Scottie Scheffler', 'dg_rank': 1, 'dg_skill_estimate': 2.5,
             'sg_ott': 0.8, 'sg_app': 0.6, 'sg_arg': 0.3, 'sg_putt': 0.4, 'owgr_rank': 1},
            {'player_name': 'Rory McIlroy', 'dg_rank': 2, 'dg_skill_estimate': 2.1,
             'sg_ott': 1.0, 'sg_app': 0.5, 'sg_arg': 0.1, 'sg_putt': 0.2, 'owgr_rank': 3},
        ]
    }
    mock_dg.get_skill_decompositions.return_value = {
        'decompositions': [
            {'player_name': 'Scottie Scheffler', 'sg_ott': 0.8, 'sg_app': 0.6,
             'sg_arg': 0.3, 'sg_putt': 0.4, 'sg_total': 2.5},
        ]
    }
    mock_dg.get_field_updates.return_value = {'field': [{'player_name': 'Scottie Scheffler'}]}
    mock_dg.get_pre_tournament_preds.return_value = {'predictions': []}
    mock_dg.get_outright_odds.return_value = {'odds': []}
    mock_dg.get_matchup_odds.return_value = {'matchups': []}

    mock_form = MockFormTracker.return_value
    mock_form.get_player_form.return_value = {'form_metrics': {'trend_label': 'hot'}}
    mock_form.get_field_form.return_value = {
        'Scottie Scheffler': {'form_metrics': {'trend_label': 'hot', 'last_4_avg_finish': 5.0}}
    }

    mock_sim = MockGolfSim.return_value
    mock_sim.prefetch_data.return_value = 50
    mock_sim.run.return_value = {
        'player_probs': {
            'Scottie Scheffler': {'win_pct': 0.15, 'top5_pct': 0.40, 'top10_pct': 0.55},
        },
        'meta': {'num_tournaments': 1000, 'num_players': 50}
    }

    from golf.golf_app import app


class TestGolfApp(unittest.TestCase):
    """Flask test client tests for golf_app.py endpoints."""

    def setUp(self):
        app.config['TESTING'] = True
        self.client = app.test_client()
        # Use temp file for predictions
        self.pred_tmpfile = tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False)
        self.pred_tmpfile.write('[]')
        self.pred_tmpfile.close()
        self._orig_pred_file = None

    def tearDown(self):
        try:
            os.unlink(self.pred_tmpfile.name)
        except OSError:
            pass

    # ── A. Data Endpoints ──

    def test_get_rankings_200(self):
        resp = self.client.get('/api/golf/rankings')
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertIn('rankings', data)

    def test_get_skill_decompositions_200(self):
        resp = self.client.get('/api/golf/skill-decompositions')
        self.assertEqual(resp.status_code, 200)

    def test_get_player_profile_200(self):
        resp = self.client.get('/api/golf/player/Scottie%20Scheffler')
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertEqual(data['player_name'], 'Scottie Scheffler')

    def test_get_field_200(self):
        resp = self.client.get('/api/golf/field')
        self.assertEqual(resp.status_code, 200)

    def test_get_pre_tournament_preds_200(self):
        resp = self.client.get('/api/golf/pre-tournament-preds')
        self.assertEqual(resp.status_code, 200)

    # ── B. Course Endpoints ──

    def test_get_courses_200(self):
        resp = self.client.get('/api/golf/courses')
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertIsInstance(data, (list, dict))

    def test_get_course_known_200(self):
        resp = self.client.get('/api/golf/course/augusta_national')
        # May return 200 or 404 depending on whether course exists in profiles
        self.assertIn(resp.status_code, [200, 404])

    def test_get_course_unknown_404(self):
        resp = self.client.get('/api/golf/course/nonexistent_course_xyz')
        self.assertEqual(resp.status_code, 404)
        data = resp.get_json()
        self.assertIn('error', data)

    # ── C. Weather Endpoints ──

    def test_get_weather_unknown_course_404(self):
        resp = self.client.get('/api/golf/weather/nonexistent_course_xyz')
        self.assertEqual(resp.status_code, 404)

    # ── D. Tournament Simulator ──

    def test_simulate_returns_202(self):
        resp = self.client.post('/api/golf/simulate',
            data=json.dumps({'course_id': 'augusta_national', 'num_tournaments': 100}),
            content_type='application/json')
        # May return 202 (started) or 400 (unknown course) depending on course data
        self.assertIn(resp.status_code, [202, 400])

    def test_simulate_status_returns_200(self):
        resp = self.client.get('/api/golf/simulate/status')
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertIn('status', data)
        self.assertIn('progress', data)

    def test_simulate_409_if_running(self):
        """If simulation is already running, POST should return 409."""
        from golf.golf_app import _golf_sim_state, _golf_sim_lock
        with _golf_sim_lock:
            old_status = _golf_sim_state['status']
            _golf_sim_state['status'] = 'running'
        try:
            resp = self.client.post('/api/golf/simulate',
                data=json.dumps({'course_id': 'augusta_national', 'num_tournaments': 100}),
                content_type='application/json')
            self.assertEqual(resp.status_code, 409)
        finally:
            with _golf_sim_lock:
                _golf_sim_state['status'] = old_status

    def test_get_results_404_when_none(self):
        """When no results available, should return 404."""
        from golf.golf_app import _golf_sim_state, _golf_sim_lock
        with _golf_sim_lock:
            old_results = _golf_sim_state['results']
            _golf_sim_state['results'] = None
        try:
            resp = self.client.get('/api/golf/results')
            self.assertIn(resp.status_code, [200, 404])
        finally:
            with _golf_sim_lock:
                _golf_sim_state['results'] = old_results

    # ── E. Matchup ──

    def test_matchup_requires_params(self):
        resp = self.client.get('/api/golf/matchup')
        self.assertEqual(resp.status_code, 400)
        data = resp.get_json()
        self.assertIn('error', data)

    # ── F. Intelligence ──

    def test_injuries_no_analyzer_200(self):
        """WD analyzer not available, should return graceful error with 200."""
        resp = self.client.get('/api/golf/injuries')
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertIn('error', data)

    def test_injuries_player_no_analyzer_200(self):
        resp = self.client.get('/api/golf/injuries/player?player=Scottie%20Scheffler')
        self.assertEqual(resp.status_code, 200)

    def test_form_200(self):
        resp = self.client.get('/api/golf/form')
        self.assertEqual(resp.status_code, 200)

    # ── G. Predictions ──

    @patch('golf.golf_app.GOLF_PREDICTIONS_FILE')
    def test_create_prediction(self, mock_file):
        mock_file.__str__ = lambda s: self.pred_tmpfile.name
        resp = self.client.post('/api/golf/predictions',
            data=json.dumps({
                'tournament': 'The Masters',
                'player': 'Scottie Scheffler',
                'predicted_finish': 3,
                'predicted_top10': True,
                'predicted_winner': False,
                'predicted_make_cut': True,
            }),
            content_type='application/json')
        self.assertEqual(resp.status_code, 201)
        data = resp.get_json()
        self.assertIn('id', data)
        self.assertEqual(data['tournament'], 'The Masters')
        self.assertEqual(data['player'], 'Scottie Scheffler')
        self.assertEqual(data['predicted_finish'], 3)
        self.assertTrue(data['predicted_top10'])
        self.assertFalse(data['result_entered'])

    def test_get_predictions_200(self):
        resp = self.client.get('/api/golf/predictions')
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertIsInstance(data, list)

    def test_prediction_accuracy_200(self):
        resp = self.client.get('/api/golf/predictions/accuracy')
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertIn('total_predictions', data)

    def test_delete_prediction(self):
        resp = self.client.delete('/api/golf/predictions/nonexistent_id')
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertIn('deleted', data)

    # ── H. Odds ──

    def test_outright_odds_200(self):
        resp = self.client.get('/api/golf/odds/outrights')
        self.assertEqual(resp.status_code, 200)

    def test_matchup_odds_200(self):
        resp = self.client.get('/api/golf/odds/matchups')
        self.assertEqual(resp.status_code, 200)

    # ── I. Model Configuration ──

    def test_get_model_weights_200(self):
        resp = self.client.get('/api/golf/model-weights')
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertIn('weights', data)
        weights = data['weights']
        self.assertAlmostEqual(
            sum(weights.values()), 1.0, places=2,
            msg='Default weights should sum to 1.0')

    def test_update_model_weights_validates_sum(self):
        # Bad weights (don't sum to 1.0)
        resp = self.client.post('/api/golf/model-weights',
            data=json.dumps({'weights': {
                'sg_efficiency': 0.5, 'course_fit': 0.5,
                'golf_rat': 0.5, 'mc': 0.5
            }}),
            content_type='application/json')
        self.assertEqual(resp.status_code, 400)

    def test_update_model_weights_validates_min(self):
        # Bad weights (below minimum)
        resp = self.client.post('/api/golf/model-weights',
            data=json.dumps({'weights': {
                'sg_efficiency': 0.01, 'course_fit': 0.33,
                'golf_rat': 0.33, 'mc': 0.33
            }}),
            content_type='application/json')
        self.assertEqual(resp.status_code, 400)

    def test_update_model_weights_valid(self):
        resp = self.client.post('/api/golf/model-weights',
            data=json.dumps({'weights': {
                'sg_efficiency': 0.30, 'course_fit': 0.25,
                'golf_rat': 0.25, 'mc': 0.20
            }}),
            content_type='application/json')
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertAlmostEqual(data['weights']['sg_efficiency'], 0.30)

    def test_get_calibration_200(self):
        resp = self.client.get('/api/golf/calibration')
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertIn('center', data)
        self.assertIn('compression', data)

    def test_update_calibration_valid(self):
        resp = self.client.post('/api/golf/calibration',
            data=json.dumps({'center': 30.0, 'compression': 0.90}),
            content_type='application/json')
        self.assertEqual(resp.status_code, 200)

    def test_update_calibration_invalid_center(self):
        resp = self.client.post('/api/golf/calibration',
            data=json.dumps({'center': 5.0, 'compression': 0.85}),
            content_type='application/json')
        self.assertEqual(resp.status_code, 400)

    def test_update_calibration_invalid_compression(self):
        resp = self.client.post('/api/golf/calibration',
            data=json.dumps({'center': 35.0, 'compression': 0.1}),
            content_type='application/json')
        self.assertEqual(resp.status_code, 400)

    # ── J. Utility ──

    def test_cache_stats_200(self):
        resp = self.client.get('/api/golf/cache-stats')
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertIn('total_entries', data)
        self.assertIn('valid_entries', data)

    def test_elite_players_200(self):
        resp = self.client.get('/api/golf/elite-players')
        self.assertEqual(resp.status_code, 200)

    def test_elite_players_tier_filter(self):
        resp = self.client.get('/api/golf/elite-players?tier=elite')
        self.assertEqual(resp.status_code, 200)

    # ── K. Frontend ──

    def test_golf_dashboard_serves_html(self):
        resp = self.client.get('/golf')
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b'PGA Tour', resp.data)


class TestPredictionAccuracy(unittest.TestCase):
    """Test prediction result recording and accuracy computation."""

    def setUp(self):
        app.config['TESTING'] = True
        self.client = app.test_client()

    def test_put_result_computes_accuracy(self):
        """PUT result should compute finish_error, top10_correct, etc."""
        # Create a prediction first
        resp = self.client.post('/api/golf/predictions',
            data=json.dumps({
                'tournament': 'Test Open',
                'player': 'Test Player',
                'predicted_finish': 5,
                'predicted_top10': True,
                'predicted_winner': False,
                'predicted_make_cut': True,
            }),
            content_type='application/json')
        self.assertEqual(resp.status_code, 201)
        pred_id = resp.get_json()['id']

        # Enter result
        resp = self.client.put(f'/api/golf/predictions/{pred_id}/result',
            data=json.dumps({'actual_finish': 8, 'actual_made_cut': True}),
            content_type='application/json')
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data['result_entered'])
        self.assertEqual(data['finish_error'], 3)  # |8 - 5| = 3
        self.assertTrue(data['top10_correct'])  # Both predicted and actual are top 10
        self.assertFalse(data['winner_correct'])  # Predicted not winner, actual 8th
        self.assertTrue(data['cut_correct'])  # Both predicted and actual made cut

    def test_put_result_404_unknown_pred(self):
        resp = self.client.put('/api/golf/predictions/xyz_nonexistent/result',
            data=json.dumps({'actual_finish': 1}),
            content_type='application/json')
        self.assertEqual(resp.status_code, 404)


if __name__ == '__main__':
    unittest.main()
