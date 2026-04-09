"""
Golf Weather Intelligence Module.
Fetches weather forecasts for golf tournament venues and calculates
simulation impact modifiers.

Mirrors the patterns from injury_scraper.py (file-based cache + external API
fetching + impact calculation).

Weather impact on scoring is analogous to how ref_foul_climate modifies the
basketball MC engine (see matchup_params.py lines 455-478).
"""

import os
import json
import hashlib
import requests
from datetime import datetime, timedelta
from typing import Optional
from dotenv import load_dotenv


# ═══════════════════════════════════════════════════════════════
# Utility
# ═══════════════════════════════════════════════════════════════

def clamp(val, lo, hi):
    """Clamp a value between lo and hi."""
    return max(lo, min(hi, val))


# ═══════════════════════════════════════════════════════════════
# Weather Cache (mirrors InjuryCache from injury_scraper.py)
# ═══════════════════════════════════════════════════════════════

class WeatherCache:
    """Simple file-based cache to minimize API calls."""

    def __init__(self, cache_dir: str = ".weather_cache"):
        self.cache_dir = cache_dir
        os.makedirs(cache_dir, exist_ok=True)

    def _key(self, identifier: str) -> str:
        return hashlib.md5(identifier.encode()).hexdigest()

    def get(self, identifier: str, max_age_minutes: int = 120) -> Optional[dict]:
        path = os.path.join(self.cache_dir, f"{self._key(identifier)}.json")
        if not os.path.exists(path):
            return None
        try:
            with open(path, 'r') as f:
                data = json.load(f)
            cached_at = datetime.fromisoformat(data.get('_cached_at', '2000-01-01'))
            if datetime.now() - cached_at > timedelta(minutes=max_age_minutes):
                return None
            return data
        except (json.JSONDecodeError, ValueError):
            return None

    def set(self, identifier: str, data: dict):
        data = {**data, '_cached_at': datetime.now().isoformat()}
        os.makedirs(self.cache_dir, exist_ok=True)
        path = os.path.join(self.cache_dir, f"{self._key(identifier)}.json")
        with open(path, 'w') as f:
            json.dump(data, f)


# ═══════════════════════════════════════════════════════════════
# Weather Fetcher
# ═══════════════════════════════════════════════════════════════

class WeatherFetcher:
    """Fetches weather forecasts for golf tournament venues."""

    def __init__(self, api_key: Optional[str] = None):
        load_dotenv()
        self.api_key = api_key or os.getenv('WEATHER_API_KEY')
        self.session = requests.Session()
        self.cache = WeatherCache()
        # Use WeatherAPI.com (free tier: 1M calls/month)
        self.base_url = "http://api.weatherapi.com/v1"

    def fetch_forecast(self, lat: float, lon: float, days: int = 5) -> dict:
        """Fetch multi-day forecast for coordinates."""
        cache_key = f"forecast_{lat}_{lon}_{days}"
        cached = self.cache.get(cache_key, max_age_minutes=120)
        if cached:
            return cached

        params = {
            'key': self.api_key,
            'q': f"{lat},{lon}",
            'days': days,
            'aqi': 'no',
        }
        resp = self.session.get(f"{self.base_url}/forecast.json", params=params, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        self.cache.set(cache_key, data)
        return data

    def fetch_tournament_weather(self, course_id: str) -> Optional[dict]:
        """Look up course lat/lon from golf_course_profiles, fetch forecast."""
        from golf.golf_course_profiles import get_course_profile
        course = get_course_profile(course_id)
        if not course or 'lat' not in course:
            return None
        return self.fetch_forecast(course['lat'], course['lon'])


# ═══════════════════════════════════════════════════════════════
# Weather Impact Calculation
# ═══════════════════════════════════════════════════════════════

def calc_weather_impact(weather_data: dict, round_number: int,
                        altitude_ft: int = 0) -> dict:
    """Convert raw weather forecast into simulation modifiers.

    Analogous to how ref_foul_climate modifies the basketball MC engine
    (see matchup_params.py lines 455-478).

    Args:
        weather_data: Raw forecast from WeatherAPI
        round_number: 1-4 (to pick the correct forecast day)
        altitude_ft: Course elevation in feet (for altitude adjustment)

    Returns:
        dict with keys: wind_adj, rain_adj, temp_adj, altitude_adj,
                        combined_adj, weather_resilience_weight, description
    """
    # Extract forecast for the target round day
    forecast_days = weather_data.get('forecast', {}).get('forecastday', [])
    day_idx = max(0, min(round_number - 1, len(forecast_days) - 1))

    if not forecast_days:
        return {
            "wind_adj": 0.0,
            "rain_adj": 0.0,
            "temp_adj": 0.0,
            "altitude_adj": 0.0,
            "combined_adj": 0.0,
            "weather_resilience_weight": 0.0,
            "description": "No forecast data available",
        }

    day = forecast_days[day_idx]
    day_data = day.get('day', {})

    wind_mph = day_data.get('maxwind_mph', 0)
    precip_chance = day_data.get('daily_chance_of_rain', 0)
    temp_f = day_data.get('avgtemp_f', 72)
    condition = day_data.get('condition', {}).get('text', 'Unknown')

    # Wind adjustment: wind under 10mph has no effect,
    # each mph above adds ~0.04 strokes to field scoring avg
    wind_adj = clamp((wind_mph - 10) * 0.04, 0, 0.8)

    # Rain adjustment: rain increases scoring difficulty
    rain_adj = clamp(precip_chance / 100 * 0.3, 0, 0.3)

    # Temperature adjustment: 0 if 55-90F, penalty for extremes
    if 55 <= temp_f <= 90:
        temp_adj = 0.0
    else:
        temp_adj = clamp(abs(temp_f - 72.5) * 0.005 - 0.05, 0, 0.2)

    # Altitude adjustment: ball flies farther at altitude (slightly easier)
    altitude_adj = clamp((altitude_ft - 1000) * 0.0001, 0, 0.15)

    # Combined adjustment
    combined_adj = wind_adj + rain_adj + temp_adj - altitude_adj

    # Weather resilience weight: how much to weight player's weather resilience
    weather_resilience_weight = clamp(combined_adj * 2, 0, 1.0)

    # Build description
    parts = []
    if wind_mph > 10:
        parts.append(f"Wind {wind_mph:.0f}mph")
    if precip_chance > 20:
        parts.append(f"Rain {precip_chance}%")
    if temp_f < 55 or temp_f > 90:
        parts.append(f"Temp {temp_f:.0f}F")
    description = f"{condition}" + (f" ({', '.join(parts)})" if parts else "")

    return {
        "wind_adj": round(wind_adj, 4),
        "rain_adj": round(rain_adj, 4),
        "temp_adj": round(temp_adj, 4),
        "altitude_adj": round(altitude_adj, 4),
        "combined_adj": round(combined_adj, 4),
        "weather_resilience_weight": round(weather_resilience_weight, 4),
        "description": description,
    }


def calc_player_weather_resilience(player_stats: dict,
                                   weather_impact: dict) -> float:
    """Calculate how much a player's scoring is affected by weather.

    Players with high accuracy, links experience, high scrambling,
    and low scoring volatility are more weather-resilient.

    Args:
        player_stats: dict with keys like 'driving_accuracy', 'scrambling_pct',
                      'consistency_score', 'style_tags'
        weather_impact: dict from calc_weather_impact()

    Returns:
        float: weather-adjusted scoring penalty (lower = less penalty)
    """
    accuracy_factor = clamp(
        (player_stats.get('driving_accuracy', 60) - 60) * 0.005, -0.1, 0.1
    )
    scrambling_factor = clamp(
        (player_stats.get('scrambling_pct', 58) - 58) * 0.004, -0.08, 0.08
    )
    volatility_factor = clamp(
        (1.0 - player_stats.get('consistency_score', 0.65)) * 0.2, -0.1, 0.1
    )
    links_bonus = (
        0.05 if 'links_experience' in player_stats.get('style_tags', []) else 0.0
    )

    resilience = accuracy_factor + scrambling_factor - volatility_factor + links_bonus

    # Resilient players absorb up to 50% of weather penalty
    return weather_impact['combined_adj'] * (1.0 - clamp(resilience, 0, 0.5))
