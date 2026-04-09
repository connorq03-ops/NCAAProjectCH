"""
Tests for golf_weather_scraper.py
"""

import json
import os
import tempfile
import time
import pytest
from unittest.mock import patch, MagicMock

from golf.golf_weather_scraper import (
    WeatherCache,
    calc_weather_impact,
    calc_player_weather_resilience,
    clamp,
)


class TestClamp:
    """Tests for the clamp utility."""

    def test_clamp_within_range(self):
        assert clamp(5, 0, 10) == 5

    def test_clamp_below(self):
        assert clamp(-1, 0, 10) == 0

    def test_clamp_above(self):
        assert clamp(15, 0, 10) == 10


class TestWeatherCache:
    """Tests for WeatherCache with file-based storage."""

    def test_set_and_get(self):
        """Cache stores and retrieves correctly."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cache = WeatherCache(cache_dir=tmpdir)
            data = {"temp": 72, "wind": 5}
            cache.set("test_key", data)
            result = cache.get("test_key", max_age_minutes=60)
            assert result is not None
            assert result["temp"] == 72
            assert result["wind"] == 5

    def test_get_returns_none_for_missing(self):
        """Cache returns None for missing keys."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cache = WeatherCache(cache_dir=tmpdir)
            result = cache.get("nonexistent_key", max_age_minutes=60)
            assert result is None

    def test_cache_respects_ttl(self):
        """Cache returns None for expired entries."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cache = WeatherCache(cache_dir=tmpdir)
            data = {"temp": 72}
            cache.set("ttl_test", data)
            # Set max_age to 0 so it's immediately expired
            result = cache.get("ttl_test", max_age_minutes=0)
            assert result is None

    def test_cache_returns_within_ttl(self):
        """Cache returns data that is still within TTL."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cache = WeatherCache(cache_dir=tmpdir)
            data = {"temp": 72}
            cache.set("fresh_test", data)
            result = cache.get("fresh_test", max_age_minutes=120)
            assert result is not None


class TestCalcWeatherImpact:
    """Tests for calc_weather_impact function."""

    def _make_forecast(self, wind_mph=5, rain_pct=0, temp_f=72, condition="Clear"):
        """Build a minimal WeatherAPI forecast structure."""
        return {
            "forecast": {
                "forecastday": [
                    {
                        "day": {
                            "maxwind_mph": wind_mph,
                            "daily_chance_of_rain": rain_pct,
                            "avgtemp_f": temp_f,
                            "condition": {"text": condition},
                        }
                    }
                ]
            }
        }

    def test_calm_weather_zero_adjustments(self):
        """Calm weather (5mph wind, 0% rain, 72F) returns all-zero adjustments."""
        weather = self._make_forecast(wind_mph=5, rain_pct=0, temp_f=72)
        result = calc_weather_impact(weather, round_number=1)
        assert result["wind_adj"] == 0.0
        assert result["rain_adj"] == 0.0
        assert result["temp_adj"] == 0.0
        assert result["combined_adj"] <= 0.0  # Could be negative from altitude

    def test_high_wind_positive_adj(self):
        """25mph wind returns positive wind_adj."""
        weather = self._make_forecast(wind_mph=25, rain_pct=0, temp_f=72)
        result = calc_weather_impact(weather, round_number=1)
        assert result["wind_adj"] > 0
        # Expected: (25 - 10) * 0.04 = 0.6
        assert abs(result["wind_adj"] - 0.6) < 0.01

    def test_rain_positive_adj(self):
        """80% precip chance returns positive rain_adj."""
        weather = self._make_forecast(wind_mph=5, rain_pct=80, temp_f=72)
        result = calc_weather_impact(weather, round_number=1)
        assert result["rain_adj"] > 0
        # Expected: 80/100 * 0.3 = 0.24
        assert abs(result["rain_adj"] - 0.24) < 0.01

    def test_extreme_cold_temp_adj(self):
        """Temperature of 40F returns positive temp_adj."""
        weather = self._make_forecast(wind_mph=5, rain_pct=0, temp_f=40)
        result = calc_weather_impact(weather, round_number=1)
        assert result["temp_adj"] > 0

    def test_extreme_hot_temp_adj(self):
        """Temperature of 100F returns positive temp_adj."""
        weather = self._make_forecast(wind_mph=5, rain_pct=0, temp_f=100)
        result = calc_weather_impact(weather, round_number=1)
        assert result["temp_adj"] > 0

    def test_normal_temp_zero_adj(self):
        """Temperature between 55-90F returns zero temp_adj."""
        for temp in [55, 72, 90]:
            weather = self._make_forecast(wind_mph=5, rain_pct=0, temp_f=temp)
            result = calc_weather_impact(weather, round_number=1)
            assert result["temp_adj"] == 0.0

    def test_empty_forecast_returns_zeros(self):
        """Empty forecast data returns all-zero adjustments."""
        result = calc_weather_impact({}, round_number=1)
        assert result["wind_adj"] == 0.0
        assert result["rain_adj"] == 0.0
        assert result["combined_adj"] == 0.0

    def test_altitude_adjustment(self):
        """High altitude reduces combined_adj."""
        weather = self._make_forecast(wind_mph=15, rain_pct=0, temp_f=72)
        result_low = calc_weather_impact(weather, round_number=1, altitude_ft=0)
        result_high = calc_weather_impact(weather, round_number=1, altitude_ft=5000)
        assert result_high["altitude_adj"] > result_low["altitude_adj"]
        assert result_high["combined_adj"] < result_low["combined_adj"]

    def test_has_required_keys(self):
        """Result dict contains all required keys."""
        weather = self._make_forecast()
        result = calc_weather_impact(weather, round_number=1)
        required = {
            "wind_adj", "rain_adj", "temp_adj", "altitude_adj",
            "combined_adj", "weather_resilience_weight", "description",
        }
        assert required.issubset(set(result.keys()))


class TestCalcPlayerWeatherResilience:
    """Tests for calc_player_weather_resilience function."""

    def test_high_accuracy_lower_penalty(self):
        """High-accuracy player gets lower weather penalty than low-accuracy player."""
        weather_impact = {
            "wind_adj": 0.6,
            "rain_adj": 0.24,
            "temp_adj": 0.0,
            "altitude_adj": 0.0,
            "combined_adj": 0.84,
            "weather_resilience_weight": 1.0,
            "description": "Windy and rainy",
        }

        # High accuracy player
        high_acc = {
            "driving_accuracy": 75,
            "scrambling_pct": 65,
            "consistency_score": 0.80,
            "style_tags": ["links_experience"],
        }
        # Low accuracy player
        low_acc = {
            "driving_accuracy": 50,
            "scrambling_pct": 50,
            "consistency_score": 0.50,
            "style_tags": [],
        }

        penalty_high = calc_player_weather_resilience(high_acc, weather_impact)
        penalty_low = calc_player_weather_resilience(low_acc, weather_impact)

        assert penalty_high < penalty_low, (
            f"High-accuracy penalty ({penalty_high:.3f}) should be less than "
            f"low-accuracy penalty ({penalty_low:.3f})"
        )

    def test_zero_weather_impact_zero_resilience(self):
        """Zero combined_adj means zero penalty for all players."""
        weather_impact = {
            "combined_adj": 0.0,
            "wind_adj": 0.0,
            "rain_adj": 0.0,
            "temp_adj": 0.0,
            "altitude_adj": 0.0,
            "weather_resilience_weight": 0.0,
            "description": "Calm",
        }
        player = {"driving_accuracy": 70, "scrambling_pct": 60}
        penalty = calc_player_weather_resilience(player, weather_impact)
        assert abs(penalty) < 0.001

    def test_links_experience_bonus(self):
        """Player with links_experience gets a lower penalty."""
        weather_impact = {
            "combined_adj": 0.5,
            "wind_adj": 0.4,
            "rain_adj": 0.1,
            "temp_adj": 0.0,
            "altitude_adj": 0.0,
            "weather_resilience_weight": 1.0,
            "description": "Windy",
        }
        base_stats = {
            "driving_accuracy": 65,
            "scrambling_pct": 62,
            "consistency_score": 0.75,
        }
        with_links = {**base_stats, "style_tags": ["links_experience"]}
        without_links = {**base_stats, "style_tags": []}

        penalty_with = calc_player_weather_resilience(with_links, weather_impact)
        penalty_without = calc_player_weather_resilience(without_links, weather_impact)

        assert penalty_with < penalty_without
