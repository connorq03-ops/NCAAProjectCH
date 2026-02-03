import os
import requests
from dotenv import load_dotenv
from typing import Dict, Optional, Any

class KenpomClient:
    """
    A client for interacting with the Kenpom college basketball API.
    """
    
    def __init__(self, api_key: Optional[str] = None, base_url: str = "https://kenpom.com"):
        """
        Initialize the Kenpom API client.
        
        Args:
            api_key: The API key for authentication. If not provided, will try to load from environment.
            base_url: The base URL for the Kenpom API.
        """
        load_dotenv()
        self.api_key = api_key or os.getenv('KENPOM_API_KEY')
        self.base_url = base_url
        self.session = requests.Session()
        
        if not self.api_key:
            raise ValueError("API key is required. Please provide it directly or set KENPOM_API_KEY environment variable.")
        
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
        if params is None:
            params = {}
        params['endpoint'] = endpoint
        
        url = f"{self.base_url}/api.php"
        
        try:
            response = self.session.get(url, params=params)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            print(f"Error making request to {url}: {e}")
            raise
    
    def get_ratings(self, year: Optional[int] = None, team_id: Optional[int] = None, 
                    conference: Optional[str] = None) -> Dict[str, Any]:
        """
        Retrieve team ratings, strength of schedule, tempo, and possession length data.
        
        Args:
            year: Season year (e.g., 2025)
            team_id: Specific team ID
            conference: Conference short code (e.g., 'B12', 'ACC')
            
        Returns:
            Dictionary containing ratings data.
        """
        params = {}
        if year:
            params['y'] = year
        if team_id:
            params['team_id'] = team_id
        if conference:
            params['c'] = conference
            
        return self._make_request('ratings', params)
    
    def get_archive(self, date: Optional[str] = None, year: Optional[int] = None,
                    preseason: bool = False, team_id: Optional[int] = None,
                    conference: Optional[str] = None) -> Dict[str, Any]:
        """
        Retrieve historical team ratings data from specific dates.
        
        Args:
            date: Date in YYYY-MM-DD format
            year: Season year (required with preseason=True)
            preseason: If True, get preseason ratings
            team_id: Specific team ID
            conference: Conference short code
            
        Returns:
            Dictionary containing archive data.
        """
        params = {}
        if date:
            params['d'] = date
        if year:
            params['y'] = year
        if preseason:
            params['preseason'] = 'true'
        if team_id:
            params['team_id'] = team_id
        if conference:
            params['c'] = conference
            
        return self._make_request('archive', params)
    
    def get_four_factors(self, year: Optional[int] = None, team_id: Optional[int] = None,
                         conference: Optional[str] = None, conf_only: bool = False) -> Dict[str, Any]:
        """
        Retrieve the Four Factors statistics for both offense and defense.
        
        Args:
            year: Season year
            team_id: Specific team ID
            conference: Conference short code
            conf_only: If True, only conference games
            
        Returns:
            Dictionary containing four factors data.
        """
        params = {}
        if year:
            params['y'] = year
        if team_id:
            params['team_id'] = team_id
        if conference:
            params['c'] = conference
        if conf_only:
            params['conf_only'] = 'true'
            
        return self._make_request('four-factors', params)
    
    def get_point_distribution(self, year: Optional[int] = None, team_id: Optional[int] = None,
                               conference: Optional[str] = None, conf_only: bool = False) -> Dict[str, Any]:
        """
        Retrieve the percentage of points scored from FT, 2PT, and 3PT.
        
        Args:
            year: Season year
            team_id: Specific team ID
            conference: Conference short code
            conf_only: If True, only conference games
            
        Returns:
            Dictionary containing point distribution data.
        """
        params = {}
        if year:
            params['y'] = year
        if team_id:
            params['team_id'] = team_id
        if conference:
            params['c'] = conference
        if conf_only:
            params['conf_only'] = 'true'
            
        return self._make_request('pointdist', params)
    
    def get_height(self, year: Optional[int] = None, team_id: Optional[int] = None,
                   conference: Optional[str] = None) -> Dict[str, Any]:
        """
        Retrieve team height statistics including average height, effective height, experience, etc.
        
        Args:
            year: Season year
            team_id: Specific team ID
            conference: Conference short code
            
        Returns:
            Dictionary containing height data.
        """
        params = {}
        if year:
            params['y'] = year
        if team_id:
            params['team_id'] = team_id
        if conference:
            params['c'] = conference
            
        return self._make_request('height', params)
    
    def get_misc_stats(self, year: Optional[int] = None, team_id: Optional[int] = None,
                       conference: Optional[str] = None, conf_only: bool = False) -> Dict[str, Any]:
        """
        Retrieve miscellaneous statistics including shooting percentages, blocks, steals, assists.
        
        Args:
            year: Season year
            team_id: Specific team ID
            conference: Conference short code
            conf_only: If True, only conference games
            
        Returns:
            Dictionary containing misc stats data.
        """
        params = {}
        if year:
            params['y'] = year
        if team_id:
            params['team_id'] = team_id
        if conference:
            params['c'] = conference
        if conf_only:
            params['conf_only'] = 'true'
            
        return self._make_request('misc-stats', params)
    
    def get_fanmatch(self, date: str) -> Dict[str, Any]:
        """
        Retrieve game predictions for a specific date.
        
        Args:
            date: Date in YYYY-MM-DD format
            
        Returns:
            Dictionary containing fanmatch/game predictions data.
        """
        params = {'d': date}
        return self._make_request('fanmatch', params)
    
    def get_conference_ratings(self, year: Optional[int] = None, 
                               conference: Optional[str] = None) -> Dict[str, Any]:
        """
        Retrieve conference ratings for a given season.
        
        Args:
            year: Season year
            conference: Conference short code
            
        Returns:
            Dictionary containing conference ratings data.
        """
        params = {}
        if year:
            params['y'] = year
        if conference:
            params['c'] = conference
            
        return self._make_request('conf-ratings', params)
    
    def get_teams(self, year: Optional[int] = None, conference: Optional[str] = None) -> Dict[str, Any]:
        """
        Retrieve the list of teams for a given season.
        
        Args:
            year: Season year
            conference: Conference short code
            
        Returns:
            Dictionary containing teams data.
        """
        params = {}
        if year:
            params['y'] = year
        if conference:
            params['c'] = conference
            
        return self._make_request('teams', params)
    
    def get_conferences(self, year: Optional[int] = None) -> Dict[str, Any]:
        """
        Retrieve the list of conferences for a given season.
        
        Args:
            year: Season year
            
        Returns:
            Dictionary containing conferences data.
        """
        params = {}
        if year:
            params['y'] = year
            
        return self._make_request('conferences', params)
