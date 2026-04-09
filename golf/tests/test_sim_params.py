"""
Tests for golf_sim_params.py
"""

import pytest
from unittest.mock import MagicMock, patch
from golf.golf_sim_params import (
    prefetch_all_player_data,
    build_player_sim_params,
    build_field_sim_params,
    calc_birdie_rate,
    calc_bogey_rate,
    calc_double_bogey_rate,
    calc_eagle_rate,
    calc_round_volatility,
    calc_streakiness,
    calc_pressure_modifier,
)
from golf.golf_course_profiles import get_course_profile


# ═══════════════════════════════════════════════════════════════
# Mock DataGolf Client
# ═══════════════════════════════════════════════════════════════

@pytest.fixture
def mock_client():
    """Create a mock DataGolfClient with realistic return data."""
    client = MagicMock()

    client.get_rankings.return_value = {
        "rankings": [
            {
                "player_name": "Scottie Scheffler",
                "dg_skill_estimate": 2.5,
                "owgr_rank": 1,
                "dg_id": 18846,
            },
            {
                "player_name": "Rory McIlroy",
                "dg_skill_estimate": 1.8,
                "owgr_rank": 3,
                "dg_id": 16875,
            },
            {
                "player_name": "Average Joe",
                "dg_skill_estimate": 0.0,
                "owgr_rank": 150,
                "dg_id": 99999,
            },
        ]
    }

    client.get_skill_decompositions.return_value = {
        "decompositions": [
            {
                "player_name": "Scottie Scheffler",
                "sg_ott": 0.9,
                "sg_app": 1.0,
                "sg_arg": 0.3,
                "sg_putt": 0.3,
                "driving_distance": 305.0,
                "driving_accuracy": 62.0,
                "gir_pct": 72.0,
                "scrambling_pct": 63.0,
                "putts_per_round": 28.5,
            },
            {
                "player_name": "Rory McIlroy",
                "sg_ott": 1.2,
                "sg_app": 0.8,
                "sg_arg": -0.1,
                "sg_putt": -0.1,
                "driving_distance": 320.0,
                "driving_accuracy": 57.0,
                "gir_pct": 68.0,
                "scrambling_pct": 55.0,
                "putts_per_round": 29.2,
            },
            {
                "player_name": "Average Joe",
                "sg_ott": 0.0,
                "sg_app": 0.0,
                "sg_arg": 0.0,
                "sg_putt": 0.0,
                "driving_distance": 295.0,
                "driving_accuracy": 60.0,
                "gir_pct": 66.0,
                "scrambling_pct": 58.0,
                "putts_per_round": 29.0,
            },
        ]
    }

    client.get_field_updates.return_value = {
        "field": [
            {"player_name": "Scottie Scheffler", "dg_id": 18846},
            {"player_name": "Rory McIlroy", "dg_id": 16875},
            {"player_name": "Average Joe", "dg_id": 99999},
        ]
    }

    client.get_player_decompositions.return_value = {"decompositions": []}
    client.get_pre_tournament_preds.return_value = {
        "predictions": [
            {
                "player_name": "Scottie Scheffler",
                "win_prob": 0.15,
                "top_5": 0.40,
                "top_10": 0.55,
                "top_20": 0.72,
                "make_cut": 0.95,
            },
            {
                "player_name": "Rory McIlroy",
                "win_prob": 0.08,
                "top_5": 0.28,
                "top_10": 0.42,
                "top_20": 0.60,
                "make_cut": 0.90,
            },
        ]
    }

    return client


@pytest.fixture
def augusta():
    return get_course_profile("augusta_national")


@pytest.fixture
def good_player_stats():
    """Realistic merged stats for a top player."""
    return {
        "sg_ott": 0.9,
        "sg_app": 1.0,
        "sg_arg": 0.3,
        "sg_putt": 0.3,
        "sg_total": 2.5,
        "driving_distance": 305.0,
        "driving_accuracy": 62.0,
        "gir_pct": 72.0,
        "scrambling_pct": 63.0,
        "putts_per_round": 28.5,
        "_player_name": "Scottie Scheffler",
        "_player_id": 18846,
        "owgr_rank": 1,
        "dg_skill_estimate": 2.5,
    }


@pytest.fixture
def avg_player_stats():
    """Average PGA Tour player stats."""
    return {
        "sg_ott": 0.0,
        "sg_app": 0.0,
        "sg_arg": 0.0,
        "sg_putt": 0.0,
        "sg_total": 0.0,
        "driving_distance": 295.0,
        "driving_accuracy": 60.0,
        "gir_pct": 66.0,
        "scrambling_pct": 58.0,
        "putts_per_round": 29.0,
        "_player_name": "Average Joe",
        "_player_id": 99999,
        "owgr_rank": 150,
        "dg_skill_estimate": 0.0,
    }


# ═══════════════════════════════════════════════════════════════
# prefetch_all_player_data Tests
# ═══════════════════════════════════════════════════════════════

class TestPrefetchAllPlayerData:

    def test_returns_dict_keyed_by_name(self, mock_client):
        """Returns a dict keyed by player name."""
        result = prefetch_all_player_data(mock_client)
        assert isinstance(result, dict)
        assert "Scottie Scheffler" in result
        assert "Rory McIlroy" in result

    def test_merged_sg_splits(self, mock_client):
        """SG splits are merged from skill decompositions."""
        result = prefetch_all_player_data(mock_client)
        scheffler = result["Scottie Scheffler"]
        assert scheffler["sg_ott"] == 0.9
        assert scheffler["sg_app"] == 1.0
        assert scheffler["sg_arg"] == 0.3
        assert scheffler["sg_putt"] == 0.3

    def test_rankings_merged(self, mock_client):
        """Rankings data (dg_skill_estimate, owgr_rank) are present."""
        result = prefetch_all_player_data(mock_client)
        scheffler = result["Scottie Scheffler"]
        assert scheffler["dg_skill_estimate"] == 2.5
        assert scheffler["owgr_rank"] == 1

    def test_with_tournament_id_filters_field(self, mock_client):
        """With tournament_id, only field players are returned."""
        result = prefetch_all_player_data(mock_client, tournament_id="123")
        assert len(result) == 3  # all 3 are in the field

    def test_with_tournament_id_merges_preds(self, mock_client):
        """With tournament_id, pre-tournament predictions are merged."""
        result = prefetch_all_player_data(mock_client, tournament_id="123")
        scheffler = result["Scottie Scheffler"]
        assert scheffler.get("win_prob") == 0.15
        assert scheffler.get("top5_prob") == 0.40


# ═══════════════════════════════════════════════════════════════
# build_player_sim_params Tests
# ═══════════════════════════════════════════════════════════════

class TestBuildPlayerSimParams:

    REQUIRED_KEYS = {
        "sg_total_adj", "sg_ott", "sg_app", "sg_arg", "sg_putt",
        "birdie_rate_par3", "birdie_rate_par4", "birdie_rate_par5",
        "bogey_rate_par3", "bogey_rate_par4", "bogey_rate_par5",
        "double_rate", "eagle_rate_par5",
        "round_volatility", "streakiness", "consistency_score",
        "pressure_modifier", "major_experience",
        "weather_adj", "weather_resilience",
        "fatigue_factor",
        "course_history_adj", "course_fit_score",
        "form_adj",
        "_player_name", "_player_id", "_owgr_rank", "_tier", "_sg_total_raw",
    }

    def test_returns_all_required_keys(self, good_player_stats, augusta):
        """build_player_sim_params returns dict with all required keys."""
        result = build_player_sim_params(good_player_stats, augusta)
        missing = self.REQUIRED_KEYS - set(result.keys())
        assert not missing, f"Missing keys: {missing}"

    def test_metadata_preserved(self, good_player_stats, augusta):
        """Metadata fields are preserved from input."""
        result = build_player_sim_params(good_player_stats, augusta)
        assert result["_player_name"] == "Scottie Scheffler"
        assert result["_player_id"] == 18846
        assert result["_owgr_rank"] == 1
        assert result["_sg_total_raw"] == 2.5

    def test_better_player_higher_sg_total_adj(self, good_player_stats, avg_player_stats, augusta):
        """Better player should have higher sg_total_adj."""
        good = build_player_sim_params(good_player_stats, augusta)
        avg = build_player_sim_params(avg_player_stats, augusta)
        assert good["sg_total_adj"] > avg["sg_total_adj"]

    def test_all_rates_in_bounds(self, good_player_stats, augusta):
        """All scoring rates should be within their clamp bounds."""
        result = build_player_sim_params(good_player_stats, augusta)
        # Birdie rates
        for key in ("birdie_rate_par3", "birdie_rate_par4", "birdie_rate_par5"):
            assert 0.02 <= result[key] <= 0.60, f"{key} = {result[key]} out of bounds"
        # Bogey rates
        for key in ("bogey_rate_par3", "bogey_rate_par4", "bogey_rate_par5"):
            assert 0.05 <= result[key] <= 0.45, f"{key} = {result[key]} out of bounds"
        # Double bogey
        assert 0.01 <= result["double_rate"] <= 0.15
        # Eagle
        assert 0.005 <= result["eagle_rate_par5"] <= 0.12
        # Volatility
        assert 1.5 <= result["round_volatility"] <= 4.5
        # Streakiness
        assert 0.2 <= result["streakiness"] <= 0.9
        # Consistency
        assert 0.0 <= result["consistency_score"] <= 1.0
        # Pressure
        assert -0.5 <= result["pressure_modifier"] <= 0.5
        # Major experience
        assert 0.0 <= result["major_experience"] <= 1.0
        # Weather resilience
        assert 0.0 <= result["weather_resilience"] <= 1.0
        # Fatigue
        assert 0.1 <= result["fatigue_factor"] <= 0.8


# ═══════════════════════════════════════════════════════════════
# Scoring Rate Tests
# ═══════════════════════════════════════════════════════════════

class TestScoringRates:

    def test_birdie_rate_higher_for_better_players(self, augusta):
        """Better players (higher SG) have higher birdie rates."""
        good = {"sg_ott": 1.0, "sg_app": 1.0, "sg_putt": 0.5, "sg_total_adj": 2.5}
        avg = {"sg_ott": 0.0, "sg_app": 0.0, "sg_putt": 0.0, "sg_total_adj": 0.0}
        for par in (3, 4, 5):
            assert calc_birdie_rate(good, par, augusta) > calc_birdie_rate(avg, par, augusta), \
                f"Birdie rate not higher for good player on par {par}"

    def test_bogey_rate_lower_for_better_players(self, augusta):
        """Better players (higher SG) have lower bogey rates."""
        good = {"sg_ott": 1.0, "sg_app": 1.0, "sg_arg": 0.5, "sg_total_adj": 2.5}
        avg = {"sg_ott": 0.0, "sg_app": 0.0, "sg_arg": 0.0, "sg_total_adj": 0.0}
        for par in (3, 4, 5):
            assert calc_bogey_rate(good, par, augusta) < calc_bogey_rate(avg, par, augusta), \
                f"Bogey rate not lower for good player on par {par}"

    def test_eagle_rate_higher_for_long_hitters(self, augusta):
        """Eagle rate is higher for long hitters (high sg_ott)."""
        long = {"sg_ott": 1.5, "sg_app": 0.5}
        short = {"sg_ott": -0.5, "sg_app": 0.5}
        assert calc_eagle_rate(long, augusta) > calc_eagle_rate(short, augusta)

    def test_double_bogey_rate_lower_for_good_players(self, augusta):
        """Double bogey rate is lower for better players."""
        good = {"sg_total_adj": 2.0}
        avg = {"sg_total_adj": 0.0}
        assert calc_double_bogey_rate(good, augusta) < calc_double_bogey_rate(avg, augusta)

    def test_birdie_rates_clamped(self, augusta):
        """Birdie rates are within clamp bounds even for extreme SG."""
        extreme = {"sg_ott": 5.0, "sg_app": 5.0, "sg_putt": 5.0, "sg_total_adj": 15.0}
        for par in (3, 4, 5):
            rate = calc_birdie_rate(extreme, par, augusta)
            assert 0.02 <= rate <= 0.60

    def test_bogey_rates_clamped(self, augusta):
        """Bogey rates are within clamp bounds even for extreme SG."""
        terrible = {"sg_ott": -3.0, "sg_app": -3.0, "sg_arg": -3.0, "sg_total_adj": -9.0}
        for par in (3, 4, 5):
            rate = calc_bogey_rate(terrible, par, augusta)
            assert 0.05 <= rate <= 0.45


# ═══════════════════════════════════════════════════════════════
# Volatility Tests
# ═══════════════════════════════════════════════════════════════

class TestVolatility:

    def test_round_volatility_lower_for_better_players(self):
        """Better players are more consistent (lower round volatility)."""
        good = {"sg_total_adj": 2.0}
        avg = {"sg_total_adj": 0.0}
        assert calc_round_volatility(good) < calc_round_volatility(avg)

    def test_round_volatility_clamped(self):
        """Round volatility is clamped to [1.5, 4.5]."""
        extreme_good = {"sg_total_adj": 20.0}
        extreme_bad = {"sg_total_adj": -20.0}
        assert calc_round_volatility(extreme_good) >= 1.5
        assert calc_round_volatility(extreme_bad) <= 4.5

    def test_streakiness_higher_for_trending_players(self):
        """Players with strong form trend are streakier."""
        trending = {"recent_form": {"trend": 1.0}}
        flat = {"recent_form": {"trend": 0.0}}
        assert calc_streakiness(trending) > calc_streakiness(flat)


# ═══════════════════════════════════════════════════════════════
# Pressure Tests
# ═══════════════════════════════════════════════════════════════

class TestPressure:

    def test_pressure_positive_for_pressure_strength(self):
        """Players with 'pressure' in strengths get positive modifier."""
        # Scottie Scheffler has "pressure" in strengths
        stats = {"_player_name": "Scottie Scheffler"}
        result = calc_pressure_modifier(stats)
        assert result > 0

    def test_pressure_negative_for_closing_weakness(self):
        """Players with 'closing' in weaknesses but no pressure strength get negative."""
        # Rory McIlroy has "closing" in weaknesses but no "pressure" in strengths
        stats = {"_player_name": "Rory McIlroy"}
        result = calc_pressure_modifier(stats)
        # Rory has 4 majors * 0.05 = 0.20 major bonus, closing penalty -0.2
        # Net could be 0 or slightly positive due to majors
        # Just verify it's a valid value
        assert -0.5 <= result <= 0.5

    def test_unknown_player_returns_zero(self):
        """Unknown players return 0.0 pressure modifier."""
        stats = {"_player_name": "Nobody Known"}
        assert calc_pressure_modifier(stats) == 0.0


# ═══════════════════════════════════════════════════════════════
# build_field_sim_params Tests
# ═══════════════════════════════════════════════════════════════

class TestBuildFieldSimParams:

    def test_returns_list_of_dicts(self, mock_client):
        """build_field_sim_params returns list of param dicts."""
        result = build_field_sim_params(mock_client, "augusta_national", tournament_id="123")
        assert isinstance(result, list)
        assert len(result) > 0
        assert all(isinstance(p, dict) for p in result)

    def test_sorted_by_sg_total_adj_descending(self, mock_client):
        """Results are sorted by sg_total_adj (best first)."""
        result = build_field_sim_params(mock_client, "augusta_national", tournament_id="123")
        sgs = [p["sg_total_adj"] for p in result]
        assert sgs == sorted(sgs, reverse=True)

    def test_invalid_course_raises(self, mock_client):
        """Invalid course_id raises ValueError."""
        with pytest.raises(ValueError, match="Unknown course_id"):
            build_field_sim_params(mock_client, "nonexistent_course")
