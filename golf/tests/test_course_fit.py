"""
Tests for golf_course_fit.py
"""

import pytest
from golf.golf_course_fit import (
    calc_full_course_fit,
    calc_form_regression,
    calc_field_strength_adj,
    clamp,
)
from golf.golf_course_profiles import get_course_profile, AVG_DRIVING_DIST, AVG_DRIVING_ACC


# ═══════════════════════════════════════════════════════════════
# Helper Fixtures
# ═══════════════════════════════════════════════════════════════

@pytest.fixture
def augusta():
    """Augusta National course profile."""
    return get_course_profile("augusta_national")


@pytest.fixture
def royal_troon():
    """Royal Troon (links, heavy rough, windy)."""
    return get_course_profile("royal_troon")


@pytest.fixture
def good_player():
    """Above-average player stats."""
    return {
        "sg_ott": 1.0,
        "sg_app": 0.8,
        "sg_arg": 0.3,
        "sg_putt": 0.4,
        "sg_total": 2.5,
        "driving_distance": 305.0,
        "driving_accuracy": 65.0,
        "gir_pct": 70.0,
        "scrambling_pct": 63.0,
        "putts_per_round": 28.5,
        "_player_name": "Scottie Scheffler",
    }


@pytest.fixture
def avg_player():
    """Average PGA Tour player stats."""
    return {
        "sg_ott": 0.0,
        "sg_app": 0.0,
        "sg_arg": 0.0,
        "sg_putt": 0.0,
        "sg_total": 0.0,
        "driving_distance": AVG_DRIVING_DIST,
        "driving_accuracy": AVG_DRIVING_ACC,
        "gir_pct": 66.0,
        "scrambling_pct": 58.0,
        "putts_per_round": 29.0,
        "_player_name": "Average Player",
    }


# ═══════════════════════════════════════════════════════════════
# calc_full_course_fit Tests
# ═══════════════════════════════════════════════════════════════

class TestCalcFullCourseFit:

    def test_returns_dict_with_all_keys(self, good_player, augusta):
        """calc_full_course_fit returns dict with all expected keys."""
        result = calc_full_course_fit(good_player, augusta)
        expected_keys = {
            "base_fit", "history_adj", "length_adj", "accuracy_adj",
            "green_adj", "scramble_adj", "weather_adj", "dg_fit_adj",
            "total_fit",
        }
        assert set(result.keys()) == expected_keys

    def test_course_history_positive_for_good_history(self, good_player, augusta):
        """Course history adjustment is positive for players with good history
        (negative avg_finish_vs_field)."""
        good_player["course_history"] = {"avg_finish_vs_field": -5.0}
        result = calc_full_course_fit(good_player, augusta)
        assert result["history_adj"] > 0

    def test_course_history_negative_for_bad_history(self, good_player, augusta):
        """Course history adjustment is negative for players with bad history
        (positive avg_finish_vs_field)."""
        good_player["course_history"] = {"avg_finish_vs_field": 5.0}
        result = calc_full_course_fit(good_player, augusta)
        assert result["history_adj"] < 0

    def test_course_history_clamped(self, good_player, augusta):
        """Course history adjustment is clamped to [-0.5, 0.5]."""
        good_player["course_history"] = {"avg_finish_vs_field": -100.0}
        result = calc_full_course_fit(good_player, augusta)
        assert result["history_adj"] <= 0.5

        good_player["course_history"] = {"avg_finish_vs_field": 100.0}
        result = calc_full_course_fit(good_player, augusta)
        assert result["history_adj"] >= -0.5

    def test_length_adj_positive_for_long_hitters_on_long_courses(self, augusta):
        """Length adjustment is positive for long hitters on long courses (>7400 yds)."""
        # Augusta is 7545 yards
        long_hitter = {
            "sg_ott": 1.0, "sg_app": 0.5, "sg_arg": 0.0, "sg_putt": 0.0,
            "driving_distance": 315.0,  # well above avg 295
            "driving_accuracy": 60.0, "scrambling_pct": 58.0,
        }
        result = calc_full_course_fit(long_hitter, augusta)
        assert result["length_adj"] > 0

    def test_length_adj_negative_for_long_hitters_on_short_courses(self):
        """Long hitters slightly penalized on short courses (<7000 yds)."""
        # Create a short course scenario
        short_course = get_course_profile("harbour_town")
        if short_course is None:
            # Use any course < 7000 yds; fallback to manual check
            for cid in ("harbour_town", "riviera", "tpc_scottsdale"):
                short_course = get_course_profile(cid)
                if short_course and short_course.get("yardage", 8000) < 7000:
                    break
        if short_course is None or short_course.get("yardage", 8000) >= 7000:
            pytest.skip("No short course (<7000 yds) available in profiles")

        long_hitter = {
            "sg_ott": 1.0, "sg_app": 0.5, "sg_arg": 0.0, "sg_putt": 0.0,
            "driving_distance": 315.0,
            "driving_accuracy": 60.0, "scrambling_pct": 58.0,
        }
        result = calc_full_course_fit(long_hitter, short_course)
        assert result["length_adj"] < 0

    def test_accuracy_adj_positive_for_accurate_on_narrow(self):
        """Accuracy adjustment is positive for accurate players on narrow courses."""
        # Find a narrow fairway course
        narrow_course = None
        from golf.golf_course_profiles import COURSES
        for cid, cp in COURSES.items():
            if cp.get("fairway_width") == "narrow":
                narrow_course = cp
                break
        if narrow_course is None:
            pytest.skip("No narrow fairway course in profiles")

        accurate_player = {
            "sg_ott": 0.0, "sg_app": 0.0, "sg_arg": 0.0, "sg_putt": 0.0,
            "driving_distance": 295.0,
            "driving_accuracy": 72.0,  # well above avg 60
            "scrambling_pct": 58.0,
        }
        result = calc_full_course_fit(accurate_player, narrow_course)
        assert result["accuracy_adj"] > 0

    def test_weather_adj_reduces_penalty_for_resilient(self, good_player, royal_troon):
        """Weather adjustment reduces penalty for resilient players."""
        weather = {
            "wind_adj": 0.4,
            "rain_adj": 0.1,
            "temp_adj": 0.0,
            "altitude_adj": 0.0,
            "combined_adj": 0.5,
            "weather_resilience_weight": 0.8,
            "description": "Windy (20mph)",
        }
        # Resilient player (high accuracy, scrambling, links background)
        good_player["consistency_score"] = 0.8
        good_player["style_tags"] = ["links_experience"]
        result_resilient = calc_full_course_fit(good_player, royal_troon, weather)

        # Non-resilient player
        weak_player = {
            "sg_ott": 0.0, "sg_app": 0.0, "sg_arg": 0.0, "sg_putt": 0.0,
            "driving_distance": 295.0,
            "driving_accuracy": 55.0,
            "scrambling_pct": 50.0,
            "consistency_score": 0.3,
            "style_tags": [],
        }
        result_weak = calc_full_course_fit(weak_player, royal_troon, weather)

        # Resilient player should have less negative (or more positive) weather_adj
        assert result_resilient["weather_adj"] > result_weak["weather_adj"]

    def test_total_fit_clamped(self, avg_player, augusta):
        """Total fit is clamped to [-3.0, 3.0]."""
        result = calc_full_course_fit(avg_player, augusta)
        assert -3.0 <= result["total_fit"] <= 3.0

    def test_higher_total_fit_for_aligned_player(self, augusta):
        """A player aligned with the course gets a higher total_fit."""
        # Augusta: sg_ott=0.30, sg_app=0.30 are highest
        aligned = {
            "sg_ott": 1.5, "sg_app": 1.5, "sg_arg": 0.2, "sg_putt": 0.2,
            "driving_distance": 310.0, "driving_accuracy": 62.0,
            "scrambling_pct": 58.0,
        }
        misaligned = {
            "sg_ott": 0.2, "sg_app": 0.2, "sg_arg": 1.5, "sg_putt": 1.5,
            "driving_distance": 280.0, "driving_accuracy": 62.0,
            "scrambling_pct": 58.0,
        }
        fit_a = calc_full_course_fit(aligned, augusta)
        fit_m = calc_full_course_fit(misaligned, augusta)
        assert fit_a["total_fit"] > fit_m["total_fit"]

    def test_no_weather_means_zero_weather_adj(self, good_player, augusta):
        """When no weather dict is provided, weather_adj should be 0."""
        result = calc_full_course_fit(good_player, augusta, weather=None)
        assert result["weather_adj"] == 0.0


# ═══════════════════════════════════════════════════════════════
# calc_form_regression Tests
# ═══════════════════════════════════════════════════════════════

class TestFormRegression:

    def test_no_recent_form_returns_career(self):
        """Without recent_form, returns career sg_total."""
        stats = {"sg_total": 1.5}
        assert calc_form_regression(stats) == 1.5

    def test_hot_streak_regressed_toward_career(self):
        """Hot streak is regressed toward career mean."""
        stats = {
            "sg_total": 1.0,
            "recent_form": {"last_4": 3.0, "last_8": 2.0, "last_12": 1.5, "trend": 0.5},
        }
        result = calc_form_regression(stats)
        # Should be between career (1.0) and recent (3.0)
        assert 1.0 < result < 3.0
        # Specifically: 1.0 + (3.0 - 1.0) * 0.60 = 2.2
        assert abs(result - 2.2) < 0.001

    def test_cold_streak_regressed_toward_career(self):
        """Cold streak is regressed toward career mean."""
        stats = {
            "sg_total": 1.0,
            "recent_form": {"last_4": -1.0, "last_8": 0.0, "last_12": 0.5, "trend": -0.5},
        }
        result = calc_form_regression(stats)
        # Should be between recent (-1.0) and career (1.0)
        assert -1.0 < result < 1.0
        # Specifically: 1.0 + (-1.0 - 1.0) * 0.60 = -0.2
        assert abs(result - (-0.2)) < 0.001

    def test_form_at_career_returns_career(self):
        """When recent form equals career, returns career."""
        stats = {
            "sg_total": 1.0,
            "recent_form": {"last_4": 1.0, "last_8": 1.0, "last_12": 1.0, "trend": 0},
        }
        result = calc_form_regression(stats)
        assert abs(result - 1.0) < 0.001


# ═══════════════════════════════════════════════════════════════
# calc_field_strength_adj Tests
# ═══════════════════════════════════════════════════════════════

class TestFieldStrengthAdj:

    def test_average_field_returns_one(self):
        """A field with 0.0 avg SG returns multiplier of 1.0."""
        result = calc_field_strength_adj({}, field_avg_sg=0.0)
        assert abs(result - 1.0) < 0.001

    def test_strong_field_returns_above_one(self):
        """A strong field (positive avg SG) returns multiplier > 1.0."""
        result = calc_field_strength_adj({}, field_avg_sg=0.3)
        assert result > 1.0

    def test_weak_field_returns_below_one(self):
        """A weak field (negative avg SG) returns multiplier < 1.0."""
        result = calc_field_strength_adj({}, field_avg_sg=-0.3)
        assert result < 1.0

    def test_clamped_to_range(self):
        """Multiplier is clamped to (0.92, 1.08)."""
        very_strong = calc_field_strength_adj({}, field_avg_sg=5.0)
        assert very_strong <= 1.08

        very_weak = calc_field_strength_adj({}, field_avg_sg=-5.0)
        assert very_weak >= 0.92
