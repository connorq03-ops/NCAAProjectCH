"""
Tests for the DataGolf API client.

Uses unittest and unittest.mock to mock HTTP responses,
mirroring the testing patterns used throughout the project.
"""

import unittest
from unittest.mock import patch, MagicMock
import requests

from golf.datagolf_client import DataGolfClient


class TestDataGolfClientInit(unittest.TestCase):
    """Tests for DataGolfClient initialization."""

    @patch.dict('os.environ', {}, clear=True)
    def test_init_raises_without_api_key(self):
        """Test that __init__ raises ValueError when no API key is provided."""
        with self.assertRaises(ValueError) as ctx:
            DataGolfClient()
        self.assertIn("API key is required", str(ctx.exception))

    @patch.dict('os.environ', {'DATAGOLF_API_KEY': 'env_test_key'})
    def test_init_loads_from_env_var(self):
        """Test that __init__ loads API key from environment variable."""
        client = DataGolfClient()
        self.assertEqual(client.api_key, 'env_test_key')

    def test_init_with_direct_api_key(self):
        """Test that __init__ accepts a directly provided API key."""
        client = DataGolfClient(api_key='direct_key')
        self.assertEqual(client.api_key, 'direct_key')

    def test_init_direct_key_overrides_env(self):
        """Test that a directly provided API key takes precedence over env var."""
        with patch.dict('os.environ', {'DATAGOLF_API_KEY': 'env_key'}):
            client = DataGolfClient(api_key='direct_key')
            self.assertEqual(client.api_key, 'direct_key')

    def test_init_default_base_url(self):
        """Test that the default base URL is set correctly."""
        client = DataGolfClient(api_key='test_key')
        self.assertEqual(client.base_url, 'https://feeds.datagolf.com')

    def test_init_custom_base_url(self):
        """Test that a custom base URL can be provided."""
        client = DataGolfClient(api_key='test_key', base_url='https://custom.api.com')
        self.assertEqual(client.base_url, 'https://custom.api.com')

    def test_init_creates_session(self):
        """Test that a requests.Session is created."""
        client = DataGolfClient(api_key='test_key')
        self.assertIsInstance(client.session, requests.Session)

    def test_init_sets_content_type_header(self):
        """Test that Content-Type header is set on the session."""
        client = DataGolfClient(api_key='test_key')
        self.assertEqual(client.session.headers['Content-Type'], 'application/json')


class TestMakeRequest(unittest.TestCase):
    """Tests for the _make_request base method."""

    def setUp(self):
        self.client = DataGolfClient(api_key='test_key_123')

    @patch.object(requests.Session, 'get')
    def test_make_request_adds_key_param(self, mock_get):
        """Test that _make_request adds the API key as a query parameter."""
        mock_response = MagicMock()
        mock_response.json.return_value = {'data': 'test'}
        mock_response.raise_for_status = MagicMock()
        mock_get.return_value = mock_response

        self.client._make_request('test/endpoint')

        mock_get.assert_called_once()
        call_kwargs = mock_get.call_args
        self.assertEqual(call_kwargs[1]['params']['key'], 'test_key_123')

    @patch.object(requests.Session, 'get')
    def test_make_request_builds_correct_url(self, mock_get):
        """Test that _make_request builds the correct full URL."""
        mock_response = MagicMock()
        mock_response.json.return_value = {}
        mock_response.raise_for_status = MagicMock()
        mock_get.return_value = mock_response

        self.client._make_request('preds/get-dg-rankings')

        call_args = mock_get.call_args
        self.assertEqual(call_args[0][0], 'https://feeds.datagolf.com/preds/get-dg-rankings')

    @patch.object(requests.Session, 'get')
    def test_make_request_passes_additional_params(self, mock_get):
        """Test that _make_request passes additional params alongside the key."""
        mock_response = MagicMock()
        mock_response.json.return_value = {}
        mock_response.raise_for_status = MagicMock()
        mock_get.return_value = mock_response

        self.client._make_request('test/endpoint', {'tour': 'pga', 'file_format': 'json'})

        call_kwargs = mock_get.call_args[1]['params']
        self.assertEqual(call_kwargs['tour'], 'pga')
        self.assertEqual(call_kwargs['file_format'], 'json')
        self.assertEqual(call_kwargs['key'], 'test_key_123')

    @patch.object(requests.Session, 'get')
    def test_make_request_sets_timeout(self, mock_get):
        """Test that _make_request sets a 30-second timeout."""
        mock_response = MagicMock()
        mock_response.json.return_value = {}
        mock_response.raise_for_status = MagicMock()
        mock_get.return_value = mock_response

        self.client._make_request('test/endpoint')

        call_kwargs = mock_get.call_args[1]
        self.assertEqual(call_kwargs['timeout'], 30)

    @patch.object(requests.Session, 'get')
    def test_make_request_returns_json(self, mock_get):
        """Test that _make_request returns parsed JSON response."""
        expected = {'rankings': [{'player': 'Tiger Woods', 'rank': 1}]}
        mock_response = MagicMock()
        mock_response.json.return_value = expected
        mock_response.raise_for_status = MagicMock()
        mock_get.return_value = mock_response

        result = self.client._make_request('test/endpoint')
        self.assertEqual(result, expected)


class TestErrorHandling(unittest.TestCase):
    """Tests for HTTP error handling."""

    def setUp(self):
        self.client = DataGolfClient(api_key='test_key_123')

    @patch.object(requests.Session, 'get')
    def test_http_401_raises(self, mock_get):
        """Test that a 401 Unauthorized response raises an exception."""
        mock_response = MagicMock()
        mock_response.raise_for_status.side_effect = requests.exceptions.HTTPError(
            response=MagicMock(status_code=401)
        )
        mock_get.return_value = mock_response

        with self.assertRaises(requests.exceptions.HTTPError):
            self.client._make_request('test/endpoint')

    @patch.object(requests.Session, 'get')
    def test_http_429_raises(self, mock_get):
        """Test that a 429 Rate Limited response raises an exception."""
        mock_response = MagicMock()
        mock_response.raise_for_status.side_effect = requests.exceptions.HTTPError(
            response=MagicMock(status_code=429)
        )
        mock_get.return_value = mock_response

        with self.assertRaises(requests.exceptions.HTTPError):
            self.client._make_request('test/endpoint')

    @patch.object(requests.Session, 'get')
    def test_http_500_raises(self, mock_get):
        """Test that a 500 Server Error response raises an exception."""
        mock_response = MagicMock()
        mock_response.raise_for_status.side_effect = requests.exceptions.HTTPError(
            response=MagicMock(status_code=500)
        )
        mock_get.return_value = mock_response

        with self.assertRaises(requests.exceptions.HTTPError):
            self.client._make_request('test/endpoint')

    @patch.object(requests.Session, 'get')
    def test_connection_error_raises(self, mock_get):
        """Test that a connection error raises an exception."""
        mock_get.side_effect = requests.exceptions.ConnectionError("Connection refused")

        with self.assertRaises(requests.exceptions.ConnectionError):
            self.client._make_request('test/endpoint')

    @patch.object(requests.Session, 'get')
    def test_timeout_error_raises(self, mock_get):
        """Test that a timeout error raises an exception."""
        mock_get.side_effect = requests.exceptions.Timeout("Request timed out")

        with self.assertRaises(requests.exceptions.Timeout):
            self.client._make_request('test/endpoint')


class TestEndpointMethods(unittest.TestCase):
    """Tests for each API endpoint method."""

    def setUp(self):
        self.client = DataGolfClient(api_key='test_key_123')
        self.mock_response = MagicMock()
        self.mock_response.json.return_value = {'data': 'test'}
        self.mock_response.raise_for_status = MagicMock()

    @patch.object(requests.Session, 'get')
    def test_get_rankings_default_params(self, mock_get):
        """Test get_rankings builds correct default params."""
        mock_get.return_value = self.mock_response
        self.client.get_rankings()

        params = mock_get.call_args[1]['params']
        self.assertEqual(params['file_format'], 'json')
        self.assertIn('preds/get-dg-rankings', mock_get.call_args[0][0])

    @patch.object(requests.Session, 'get')
    def test_get_rankings_custom_format(self, mock_get):
        """Test get_rankings with custom file format."""
        mock_get.return_value = self.mock_response
        self.client.get_rankings(file_format='csv')

        params = mock_get.call_args[1]['params']
        self.assertEqual(params['file_format'], 'csv')

    @patch.object(requests.Session, 'get')
    def test_get_skill_decompositions_default_params(self, mock_get):
        """Test get_skill_decompositions builds correct default params."""
        mock_get.return_value = self.mock_response
        self.client.get_skill_decompositions()

        params = mock_get.call_args[1]['params']
        self.assertEqual(params['tour'], 'pga')
        self.assertEqual(params['file_format'], 'json')
        self.assertIn('preds/skill-decompositions', mock_get.call_args[0][0])

    @patch.object(requests.Session, 'get')
    def test_get_skill_decompositions_custom_tour(self, mock_get):
        """Test get_skill_decompositions with custom tour."""
        mock_get.return_value = self.mock_response
        self.client.get_skill_decompositions(tour='euro')

        params = mock_get.call_args[1]['params']
        self.assertEqual(params['tour'], 'euro')

    @patch.object(requests.Session, 'get')
    def test_get_pre_tournament_preds_default_params(self, mock_get):
        """Test get_pre_tournament_preds builds correct default params."""
        mock_get.return_value = self.mock_response
        self.client.get_pre_tournament_preds()

        params = mock_get.call_args[1]['params']
        self.assertEqual(params['tour'], 'pga')
        self.assertEqual(params['odds_format'], 'american')
        self.assertEqual(params['file_format'], 'json')
        self.assertNotIn('add_position', params)
        self.assertIn('preds/pre-tournament', mock_get.call_args[0][0])

    @patch.object(requests.Session, 'get')
    def test_get_pre_tournament_preds_with_add_position(self, mock_get):
        """Test get_pre_tournament_preds with add_position parameter."""
        mock_get.return_value = self.mock_response
        self.client.get_pre_tournament_preds(add_position=30)

        params = mock_get.call_args[1]['params']
        self.assertEqual(params['add_position'], 30)

    @patch.object(requests.Session, 'get')
    def test_get_pre_tournament_pred_archive_default_params(self, mock_get):
        """Test get_pre_tournament_pred_archive builds correct default params."""
        mock_get.return_value = self.mock_response
        self.client.get_pre_tournament_pred_archive()

        params = mock_get.call_args[1]['params']
        self.assertEqual(params['odds_format'], 'american')
        self.assertEqual(params['file_format'], 'json')
        self.assertNotIn('event_id', params)
        self.assertNotIn('year', params)
        self.assertIn('preds/pre-tournament-archive', mock_get.call_args[0][0])

    @patch.object(requests.Session, 'get')
    def test_get_pre_tournament_pred_archive_with_filters(self, mock_get):
        """Test get_pre_tournament_pred_archive with event_id and year."""
        mock_get.return_value = self.mock_response
        self.client.get_pre_tournament_pred_archive(event_id='401', year=2024)

        params = mock_get.call_args[1]['params']
        self.assertEqual(params['event_id'], '401')
        self.assertEqual(params['year'], 2024)

    @patch.object(requests.Session, 'get')
    def test_get_player_decompositions_default_params(self, mock_get):
        """Test get_player_decompositions builds correct default params."""
        mock_get.return_value = self.mock_response
        self.client.get_player_decompositions()

        params = mock_get.call_args[1]['params']
        self.assertEqual(params['tour'], 'pga')
        self.assertEqual(params['file_format'], 'json')
        self.assertIn('preds/player-decompositions', mock_get.call_args[0][0])

    @patch.object(requests.Session, 'get')
    def test_get_field_updates_default_params(self, mock_get):
        """Test get_field_updates builds correct default params."""
        mock_get.return_value = self.mock_response
        self.client.get_field_updates()

        params = mock_get.call_args[1]['params']
        self.assertEqual(params['tour'], 'pga')
        self.assertEqual(params['file_format'], 'json')
        self.assertIn('field-updates', mock_get.call_args[0][0])

    @patch.object(requests.Session, 'get')
    def test_get_live_model_default_params(self, mock_get):
        """Test get_live_model builds correct default params."""
        mock_get.return_value = self.mock_response
        self.client.get_live_model()

        params = mock_get.call_args[1]['params']
        self.assertEqual(params['tour'], 'pga')
        self.assertEqual(params['dead_heat'], 'no')
        self.assertEqual(params['odds_format'], 'american')
        self.assertEqual(params['file_format'], 'json')
        self.assertIn('preds/in-play', mock_get.call_args[0][0])

    @patch.object(requests.Session, 'get')
    def test_get_live_model_custom_params(self, mock_get):
        """Test get_live_model with custom parameters."""
        mock_get.return_value = self.mock_response
        self.client.get_live_model(tour='euro', dead_heat='yes', odds_format='decimal')

        params = mock_get.call_args[1]['params']
        self.assertEqual(params['tour'], 'euro')
        self.assertEqual(params['dead_heat'], 'yes')
        self.assertEqual(params['odds_format'], 'decimal')

    @patch.object(requests.Session, 'get')
    def test_get_historical_rounds_default_params(self, mock_get):
        """Test get_historical_rounds builds correct default params."""
        mock_get.return_value = self.mock_response
        self.client.get_historical_rounds()

        params = mock_get.call_args[1]['params']
        self.assertEqual(params['tour'], 'pga')
        self.assertEqual(params['file_format'], 'json')
        self.assertNotIn('event_id', params)
        self.assertNotIn('year', params)
        self.assertIn('historical-raw-data/rounds', mock_get.call_args[0][0])

    @patch.object(requests.Session, 'get')
    def test_get_historical_rounds_with_filters(self, mock_get):
        """Test get_historical_rounds with event_id and year."""
        mock_get.return_value = self.mock_response
        self.client.get_historical_rounds(event_id='100', year=2023)

        params = mock_get.call_args[1]['params']
        self.assertEqual(params['event_id'], '100')
        self.assertEqual(params['year'], 2023)

    @patch.object(requests.Session, 'get')
    def test_get_historical_events_default_params(self, mock_get):
        """Test get_historical_events builds correct default params."""
        mock_get.return_value = self.mock_response
        self.client.get_historical_events()

        params = mock_get.call_args[1]['params']
        self.assertEqual(params['tour'], 'pga')
        self.assertEqual(params['file_format'], 'json')
        self.assertIn('historical-raw-data/event-list', mock_get.call_args[0][0])

    @patch.object(requests.Session, 'get')
    def test_get_outright_odds_default_params(self, mock_get):
        """Test get_outright_odds builds correct default params."""
        mock_get.return_value = self.mock_response
        self.client.get_outright_odds()

        params = mock_get.call_args[1]['params']
        self.assertEqual(params['tour'], 'pga')
        self.assertEqual(params['market'], 'win')
        self.assertEqual(params['odds_format'], 'american')
        self.assertEqual(params['file_format'], 'json')
        self.assertIn('betting-tools/outrights', mock_get.call_args[0][0])

    @patch.object(requests.Session, 'get')
    def test_get_outright_odds_custom_market(self, mock_get):
        """Test get_outright_odds with custom market."""
        mock_get.return_value = self.mock_response
        self.client.get_outright_odds(market='top5')

        params = mock_get.call_args[1]['params']
        self.assertEqual(params['market'], 'top5')

    @patch.object(requests.Session, 'get')
    def test_get_matchup_odds_default_params(self, mock_get):
        """Test get_matchup_odds builds correct default params."""
        mock_get.return_value = self.mock_response
        self.client.get_matchup_odds()

        params = mock_get.call_args[1]['params']
        self.assertEqual(params['tour'], 'pga')
        self.assertEqual(params['odds_format'], 'american')
        self.assertEqual(params['file_format'], 'json')
        self.assertIn('betting-tools/matchups', mock_get.call_args[0][0])

    @patch.object(requests.Session, 'get')
    def test_get_general_info_default_params(self, mock_get):
        """Test get_general_info builds correct default params."""
        mock_get.return_value = self.mock_response
        self.client.get_general_info()

        params = mock_get.call_args[1]['params']
        self.assertEqual(params['tour'], 'pga')
        self.assertNotIn('file_format', params)
        self.assertIn('general/info', mock_get.call_args[0][0])

    @patch.object(requests.Session, 'get')
    def test_get_general_info_custom_tour(self, mock_get):
        """Test get_general_info with custom tour."""
        mock_get.return_value = self.mock_response
        self.client.get_general_info(tour='euro')

        params = mock_get.call_args[1]['params']
        self.assertEqual(params['tour'], 'euro')

    @patch.object(requests.Session, 'get')
    def test_all_methods_return_response(self, mock_get):
        """Test that all endpoint methods return the JSON response."""
        expected = {'players': [{'name': 'Scottie Scheffler'}]}
        self.mock_response.json.return_value = expected
        mock_get.return_value = self.mock_response

        methods = [
            self.client.get_rankings,
            self.client.get_skill_decompositions,
            self.client.get_pre_tournament_preds,
            self.client.get_pre_tournament_pred_archive,
            self.client.get_player_decompositions,
            self.client.get_field_updates,
            self.client.get_live_model,
            self.client.get_historical_rounds,
            self.client.get_historical_events,
            self.client.get_outright_odds,
            self.client.get_matchup_odds,
            self.client.get_general_info,
        ]

        for method in methods:
            result = method()
            self.assertEqual(result, expected, f"{method.__name__} did not return expected data")


if __name__ == '__main__':
    unittest.main()
