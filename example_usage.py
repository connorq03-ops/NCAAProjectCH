#!/usr/bin/env python3
"""
Example usage of the Basketball API client.
"""

import requests
from basketball_api_client import BasketballAPIClient

def main():
    # Initialize the client with your API key
    # You can either pass the key directly or set it in the environment
    api_key = "c778f2c8af554d059b4c9413fc7ea44a7d45da806a19985421c4dd311816ef6e"
    client = BasketballAPIClient(api_key=api_key)
    
    print("Basketball API Client Example")
    print("=" * 40)
    
    try:
        # Example 1: Get all teams
        print("\n1. Getting all teams...")
        teams = client.get_teams(per_page=10)
        print(f"Found {len(teams.get('data', []))} teams:")
        for team in teams.get('data', []):
            print(f"  - {team['full_name']} ({team['abbreviation']})")
        
        # Example 2: Search for a specific player
        print("\n2. Searching for LeBron James...")
        players = client.get_players(search="LeBron James")
        if players.get('data'):
            player = players['data'][0]
            print(f"Found: {player['first_name']} {player['last_name']}")
            print(f"  Team: {player['team']['full_name']}")
            print(f"  Position: {player['position']}")
        
        # Example 3: Get games from a specific date range
        print("\n3. Getting recent games...")
        games = client.get_games(start_date="2024-01-01", end_date="2024-01-05")
        game_data = games.get('data', [])
        print(f"Found {len(game_data)} games:")
        for game in game_data[:5]:  # Limit to first 5 games
            home_team = game['home_team']['full_name']
            away_team = game['visitor_team']['full_name']
            print(f"  - {away_team} @ {home_team} on {game['date']}")
        
        # Example 4: Get player statistics
        print("\n4. Getting player statistics...")
        stats = client.get_stats(seasons=[2023])
        stats_data = stats.get('data', [])
        print(f"Found {len(stats_data)} stat entries:")
        for stat in stats_data[:5]:  # Limit to first 5 entries
            player = stat['player']
            print(f"  - {player['first_name']} {player['last_name']}: {stat['pts']} PPG")
        
        # Example 5: Get season averages
        print("\n5. Getting season averages...")
        averages = client.get_averages(seasons=[2023])
        averages_data = averages.get('data', [])
        print(f"Found {len(averages_data)} player averages:")
        for avg in averages_data[:5]:  # Limit to first 5 entries
            player = avg['player']
            print(f"  - {player['first_name']} {player['last_name']}: {avg['pts']} PPG, {avg['ast']} APG")
            
    except requests.exceptions.RequestException as e:
        print(f"API request error: {e}")
    except KeyError as e:
        print(f"Data format error: {e}")
    except Exception as e:
        print(f"Unexpected error: {e}")

if __name__ == "__main__":
    main()
