import os
import requests
from dotenv import load_dotenv
from typing import Dict, List, Optional, Any

class BasketballAPIClient:
    """
    A client for interacting with the basketball API.
    """
    
    def __init__(self, api_key: Optional[str] = None, base_url: str = "https://api.balldontlie.io/v1"):
        """
        Initialize the Basketball API client.
        
        Args:
            api_key: The API key for authentication. If not provided, will try to load from environment.
            base_url: The base URL for the basketball API.
        """
        load_dotenv()
        self.api_key = api_key or os.getenv('BASKETBALL_API_KEY')
        self.base_url = base_url
        self.session = requests.Session()
        
        if not self.api_key:
            raise ValueError("API key is required. Please provide it directly or set BASKETBALL_API_KEY environment variable.")
        
        self.session.headers.update({
            'Authorization': f'Bearer {self.api_key}',
            'Content-Type': 'application/json'
        })
    
    def _make_request(self, endpoint: str, params: Optional[Dict] = None) -> Dict[str, Any]:
        """
        Make a request to the API.
        
        Args:
            endpoint: The API endpoint to call.
            params: Query parameters for the request.
            
        Returns:
            The JSON response from the API.
            
        Raises:
            requests.exceptions.RequestException: If the request fails.
        """
        url = f"{self.base_url}/{endpoint}"
        
        try:
            response = self.session.get(url, params=params)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            print(f"Error making request to {url}: {e}")
            raise
    
    def get_players(self, page: int = 0, per_page: int = 30, search: Optional[str] = None) -> Dict[str, Any]:
        """
        Get a list of players.
        
        Args:
            page: Page number for pagination.
            per_page: Number of results per page.
            search: Search term to filter players.
            
        Returns:
            Dictionary containing player data.
        """
        params = {
            'page': page,
            'per_page': per_page
        }
        
        if search:
            params['search'] = search
            
        return self._make_request('players', params)
    
    def get_player(self, player_id: int) -> Dict[str, Any]:
        """
        Get a specific player by ID.
        
        Args:
            player_id: The ID of the player.
            
        Returns:
            Dictionary containing player data.
        """
        return self._make_request(f'players/{player_id}')
    
    def get_teams(self, page: int = 0, per_page: int = 30) -> Dict[str, Any]:
        """
        Get a list of teams.
        
        Args:
            page: Page number for pagination.
            per_page: Number of results per page.
            
        Returns:
            Dictionary containing team data.
        """
        params = {
            'page': page,
            'per_page': per_page
        }
        
        return self._make_request('teams', params)
    
    def get_team(self, team_id: int) -> Dict[str, Any]:
        """
        Get a specific team by ID.
        
        Args:
            team_id: The ID of the team.
            
        Returns:
            Dictionary containing team data.
        """
        return self._make_request(f'teams/{team_id}')
    
    def get_games(self, page: int = 0, per_page: int = 30, seasons: Optional[List[int]] = None,
                  team_ids: Optional[List[int]] = None, player_ids: Optional[List[int]] = None,
                  start_date: Optional[str] = None, end_date: Optional[str] = None) -> Dict[str, Any]:
        """
        Get a list of games.
        
        Args:
            page: Page number for pagination.
            per_page: Number of results per page.
            seasons: List of seasons to filter by.
            team_ids: List of team IDs to filter by.
            player_ids: List of player IDs to filter by.
            start_date: Start date in YYYY-MM-DD format.
            end_date: End date in YYYY-MM-DD format.
            
        Returns:
            Dictionary containing game data.
        """
        params = {
            'page': page,
            'per_page': per_page
        }
        
        if seasons:
            params['seasons[]'] = seasons
        if team_ids:
            params['team_ids[]'] = team_ids
        if player_ids:
            params['player_ids[]'] = player_ids
        if start_date:
            params['start_date'] = start_date
        if end_date:
            params['end_date'] = end_date
            
        return self._make_request('games', params)
    
    def get_game(self, game_id: int) -> Dict[str, Any]:
        """
        Get a specific game by ID.
        
        Args:
            game_id: The ID of the game.
            
        Returns:
            Dictionary containing game data.
        """
        return self._make_request(f'games/{game_id}')
    
    def get_stats(self, page: int = 0, per_page: int = 30, seasons: Optional[List[int]] = None,
                  team_ids: Optional[List[int]] = None, player_ids: Optional[List[int]] = None,
                  game_ids: Optional[List[int]] = None, start_date: Optional[str] = None,
                  end_date: Optional[str] = None) -> Dict[str, Any]:
        """
        Get player statistics.
        
        Args:
            page: Page number for pagination.
            per_page: Number of results per page.
            seasons: List of seasons to filter by.
            team_ids: List of team IDs to filter by.
            player_ids: List of player IDs to filter by.
            game_ids: List of game IDs to filter by.
            start_date: Start date in YYYY-MM-DD format.
            end_date: End date in YYYY-MM-DD format.
            
        Returns:
            Dictionary containing player statistics.
        """
        params = {
            'page': page,
            'per_page': per_page
        }
        
        if seasons:
            params['seasons[]'] = seasons
        if team_ids:
            params['team_ids[]'] = team_ids
        if player_ids:
            params['player_ids[]'] = player_ids
        if game_ids:
            params['game_ids[]'] = game_ids
        if start_date:
            params['start_date'] = start_date
        if end_date:
            params['end_date'] = end_date
            
        return self._make_request('stats', params)
    
    def get_averages(self, seasons: Optional[List[int]] = None, team_ids: Optional[List[int]] = None,
                     player_ids: Optional[List[int]] = None) -> Dict[str, Any]:
        """
        Get player season averages.
        
        Args:
            seasons: List of seasons to filter by.
            team_ids: List of team IDs to filter by.
            player_ids: List of player IDs to filter by.
            
        Returns:
            Dictionary containing player averages.
        """
        params = {}
        
        if seasons:
            params['seasons[]'] = seasons
        if team_ids:
            params['team_ids[]'] = team_ids
        if player_ids:
            params['player_ids[]'] = player_ids
            
        return self._make_request('averages', params)
