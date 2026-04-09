"""
datagolf_client.py - DataGolf API Client for PGA Tour golf data.

Mirrors the architecture of kenpom_client.py (basketball).
DataGolf is the "KenPom of golf" — provides strokes gained splits,
player rankings, course fit data, tournament predictions, and odds.

API docs: https://datagolf.com/api-access
"""

import os
import requests
from dotenv import load_dotenv
from typing import Dict, Optional, Any, List


class DataGolfClient:
    """Client for interacting with the DataGolf API."""

    def __init__(self, api_key: Optional[str] = None, base_url: str = "https://feeds.datagolf.com"):
        """
        Initialize the DataGolf API client.

        Args:
            api_key: The API key for authentication. If not provided, will try to load from environment.
            base_url: The base URL for the DataGolf API.
        """
        load_dotenv()
        self.api_key = api_key or os.getenv('DATAGOLF_API_KEY')
        self.base_url = base_url
        self.session = requests.Session()

        if not self.api_key:
            raise ValueError(
                "API key is required. Provide it directly or set DATAGOLF_API_KEY environment variable."
            )

        # DataGolf uses query param auth, not Bearer token
        self.session.headers.update({
            'Content-Type': 'application/json'
        })

    def _make_request(self, endpoint: str, params: Optional[Dict] = None) -> Any:
        """
        Make a request to the DataGolf API.

        Args:
            endpoint: The API endpoint to call.
            params: Query parameters for the request.

        Returns:
            The JSON response from the API.

        Raises:
            requests.exceptions.RequestException: If the request fails.
        """
        if params is None:
            params = {}
        params['key'] = self.api_key  # DataGolf uses ?key= auth

        url = f"{self.base_url}/{endpoint}"
        try:
            response = self.session.get(url, params=params, timeout=30)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            print(f"Error making request to {url}: {e}")
            raise

    def get_rankings(self, file_format: str = 'json') -> Any:
        """
        Retrieve player rankings with DG ranking, OWGR, and SG splits.

        Args:
            file_format: Response format ('json' or 'csv').

        Returns:
            Player rankings data.
        """
        params = {'file_format': file_format}
        return self._make_request('preds/get-dg-rankings', params)

    def get_skill_decompositions(self, tour: str = 'pga', file_format: str = 'json') -> Any:
        """
        Retrieve detailed strokes gained skill breakdowns per player.

        Args:
            tour: Tour to get data for (e.g., 'pga', 'euro', 'kft').
            file_format: Response format ('json' or 'csv').

        Returns:
            Skill decomposition data.
        """
        params = {
            'tour': tour,
            'file_format': file_format
        }
        return self._make_request('preds/skill-decompositions', params)

    def get_pre_tournament_preds(self, tour: str = 'pga', add_position: Optional[int] = None,
                                  odds_format: str = 'american', file_format: str = 'json') -> Any:
        """
        Retrieve pre-tournament win/top5/top10/top20/cut probabilities.

        Args:
            tour: Tour to get data for (e.g., 'pga', 'euro', 'kft').
            add_position: Additional finishing position to return probability for.
            odds_format: Odds format ('american' or 'decimal').
            file_format: Response format ('json' or 'csv').

        Returns:
            Pre-tournament prediction data.
        """
        params = {
            'tour': tour,
            'odds_format': odds_format,
            'file_format': file_format
        }
        if add_position is not None:
            params['add_position'] = add_position
        return self._make_request('preds/pre-tournament', params)

    def get_pre_tournament_pred_archive(self, event_id: Optional[str] = None, year: Optional[int] = None,
                                         odds_format: str = 'american', file_format: str = 'json') -> Any:
        """
        Retrieve historical pre-tournament predictions.

        Args:
            event_id: Specific event ID.
            year: Season year.
            odds_format: Odds format ('american' or 'decimal').
            file_format: Response format ('json' or 'csv').

        Returns:
            Historical pre-tournament prediction data.
        """
        params = {
            'odds_format': odds_format,
            'file_format': file_format
        }
        if event_id is not None:
            params['event_id'] = event_id
        if year is not None:
            params['year'] = year
        return self._make_request('preds/pre-tournament-archive', params)

    def get_player_decompositions(self, tour: str = 'pga', file_format: str = 'json') -> Any:
        """
        Retrieve course-specific player skill decompositions (course fit data).

        Args:
            tour: Tour to get data for (e.g., 'pga', 'euro', 'kft').
            file_format: Response format ('json' or 'csv').

        Returns:
            Player decomposition / course fit data.
        """
        params = {
            'tour': tour,
            'file_format': file_format
        }
        return self._make_request('preds/player-decompositions', params)

    def get_field_updates(self, tour: str = 'pga', file_format: str = 'json') -> Any:
        """
        Retrieve current tournament field with player status.

        Args:
            tour: Tour to get data for (e.g., 'pga', 'euro', 'kft').
            file_format: Response format ('json' or 'csv').

        Returns:
            Field update data.
        """
        params = {
            'tour': tour,
            'file_format': file_format
        }
        return self._make_request('field-updates', params)

    def get_live_model(self, tour: str = 'pga', dead_heat: str = 'no',
                       odds_format: str = 'american', file_format: str = 'json') -> Any:
        """
        Retrieve live in-play tournament predictions.

        Args:
            tour: Tour to get data for (e.g., 'pga', 'euro', 'kft').
            dead_heat: Whether to apply dead heat rules ('yes' or 'no').
            odds_format: Odds format ('american' or 'decimal').
            file_format: Response format ('json' or 'csv').

        Returns:
            Live model prediction data.
        """
        params = {
            'tour': tour,
            'dead_heat': dead_heat,
            'odds_format': odds_format,
            'file_format': file_format
        }
        return self._make_request('preds/in-play', params)

    def get_historical_rounds(self, tour: str = 'pga', event_id: Optional[str] = None,
                               year: Optional[int] = None, file_format: str = 'json') -> Any:
        """
        Retrieve historical round-level scoring data.

        Args:
            tour: Tour to get data for (e.g., 'pga', 'euro', 'kft').
            event_id: Specific event ID.
            year: Season year.
            file_format: Response format ('json' or 'csv').

        Returns:
            Historical round data.
        """
        params = {
            'tour': tour,
            'file_format': file_format
        }
        if event_id is not None:
            params['event_id'] = event_id
        if year is not None:
            params['year'] = year
        return self._make_request('historical-raw-data/rounds', params)

    def get_historical_events(self, tour: str = 'pga', file_format: str = 'json') -> Any:
        """
        Retrieve list of historical events/tournaments.

        Args:
            tour: Tour to get data for (e.g., 'pga', 'euro', 'kft').
            file_format: Response format ('json' or 'csv').

        Returns:
            Historical events list.
        """
        params = {
            'tour': tour,
            'file_format': file_format
        }
        return self._make_request('historical-raw-data/event-list', params)

    def get_outright_odds(self, tour: str = 'pga', market: str = 'win',
                          odds_format: str = 'american', file_format: str = 'json') -> Any:
        """
        Retrieve outright betting odds from multiple books.

        Args:
            tour: Tour to get data for (e.g., 'pga', 'euro', 'kft').
            market: Betting market ('win', 'top5', 'top10', 'top20', 'mc').
            odds_format: Odds format ('american' or 'decimal').
            file_format: Response format ('json' or 'csv').

        Returns:
            Outright odds data.
        """
        params = {
            'tour': tour,
            'market': market,
            'odds_format': odds_format,
            'file_format': file_format
        }
        return self._make_request('betting-tools/outrights', params)

    def get_matchup_odds(self, tour: str = 'pga', odds_format: str = 'american',
                         file_format: str = 'json') -> Any:
        """
        Retrieve head-to-head matchup odds.

        Args:
            tour: Tour to get data for (e.g., 'pga', 'euro', 'kft').
            odds_format: Odds format ('american' or 'decimal').
            file_format: Response format ('json' or 'csv').

        Returns:
            Matchup odds data.
        """
        params = {
            'tour': tour,
            'odds_format': odds_format,
            'file_format': file_format
        }
        return self._make_request('betting-tools/matchups', params)

    def get_general_info(self, tour: str = 'pga') -> Any:
        """
        Retrieve general tour info and current event details.

        Args:
            tour: Tour to get data for (e.g., 'pga', 'euro', 'kft').

        Returns:
            General tour information.
        """
        params = {'tour': tour}
        return self._make_request('general/info', params)
