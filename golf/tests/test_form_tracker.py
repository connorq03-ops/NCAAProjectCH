"""
Tests for golf_form_tracker.py
"""

import json
import os
import tempfile
import pytest
from unittest.mock import patch, MagicMock

from golf.golf_form_tracker import FormTracker


# ═══════════════════════════════════════════════════════════════
# Sample Data Fixtures
# ═══════════════════════════════════════════════════════════════

def _make_results_improving():
    """Sample results where player is improving (recent results better)."""
    return [
        {"tournament": "Event 1", "finish": "3", "score_to_par": -12, "sg_total": 2.5, "date": "2026-04-01"},
        {"tournament": "Event 2", "finish": "5", "score_to_par": -10, "sg_total": 2.0, "date": "2026-03-25"},
        {"tournament": "Event 3", "finish": "T8", "score_to_par": -8, "sg_total": 1.5, "date": "2026-03-18"},
        {"tournament": "Event 4", "finish": "T12", "score_to_par": -5, "sg_total": 1.0, "date": "2026-03-11"},
        {"tournament": "Event 5", "finish": "T20", "score_to_par": -3, "sg_total": 0.5, "date": "2026-03-04"},
        {"tournament": "Event 6", "finish": "25", "score_to_par": -1, "sg_total": 0.2, "date": "2026-02-25"},
        {"tournament": "Event 7", "finish": "T30", "score_to_par": 1, "sg_total": -0.1, "date": "2026-02-18"},
        {"tournament": "Event 8", "finish": "T35", "score_to_par": 2, "sg_total": -0.3, "date": "2026-02-11"},
    ]


def _make_results_declining():
    """Sample results where player is declining (recent results worse)."""
    return [
        {"tournament": "Event 1", "finish": "T40", "score_to_par": 4, "sg_total": -0.8, "date": "2026-04-01"},
        {"tournament": "Event 2", "finish": "T35", "score_to_par": 2, "sg_total": -0.5, "date": "2026-03-25"},
        {"tournament": "Event 3", "finish": "CUT", "score_to_par": 6, "sg_total": -1.2, "date": "2026-03-18"},
        {"tournament": "Event 4", "finish": "T25", "score_to_par": 1, "sg_total": -0.1, "date": "2026-03-11"},
        {"tournament": "Event 5", "finish": "T10", "score_to_par": -5, "sg_total": 1.0, "date": "2026-03-04"},
        {"tournament": "Event 6", "finish": "5", "score_to_par": -8, "sg_total": 1.5, "date": "2026-02-25"},
        {"tournament": "Event 7", "finish": "3", "score_to_par": -10, "sg_total": 2.0, "date": "2026-02-18"},
        {"tournament": "Event 8", "finish": "T2", "score_to_par": -12, "sg_total": 2.5, "date": "2026-02-11"},
    ]


def _make_results_stable():
    """Sample results where player is stable (flat trend)."""
    return [
        {"tournament": "Event 1", "finish": "T15", "score_to_par": -4, "sg_total": 0.8, "date": "2026-04-01"},
        {"tournament": "Event 2", "finish": "T18", "score_to_par": -3, "sg_total": 0.7, "date": "2026-03-25"},
        {"tournament": "Event 3", "finish": "T12", "score_to_par": -5, "sg_total": 0.9, "date": "2026-03-18"},
        {"tournament": "Event 4", "finish": "T16", "score_to_par": -4, "sg_total": 0.8, "date": "2026-03-11"},
        {"tournament": "Event 5", "finish": "T14", "score_to_par": -4, "sg_total": 0.85, "date": "2026-03-04"},
        {"tournament": "Event 6", "finish": "T17", "score_to_par": -3, "sg_total": 0.75, "date": "2026-02-25"},
        {"tournament": "Event 7", "finish": "T13", "score_to_par": -5, "sg_total": 0.9, "date": "2026-02-18"},
        {"tournament": "Event 8", "finish": "T15", "score_to_par": -4, "sg_total": 0.8, "date": "2026-02-11"},
    ]


def _make_results_consistent():
    """Sample results with low finish variance (high consistency)."""
    return [
        {"tournament": "Event 1", "finish": "T10", "score_to_par": -6, "sg_total": 1.2, "date": "2026-04-01"},
        {"tournament": "Event 2", "finish": "T12", "score_to_par": -5, "sg_total": 1.0, "date": "2026-03-25"},
        {"tournament": "Event 3", "finish": "T8", "score_to_par": -7, "sg_total": 1.3, "date": "2026-03-18"},
        {"tournament": "Event 4", "finish": "T11", "score_to_par": -5, "sg_total": 1.1, "date": "2026-03-11"},
        {"tournament": "Event 5", "finish": "T9", "score_to_par": -6, "sg_total": 1.2, "date": "2026-03-04"},
        {"tournament": "Event 6", "finish": "T13", "score_to_par": -4, "sg_total": 0.9, "date": "2026-02-25"},
        {"tournament": "Event 7", "finish": "T10", "score_to_par": -6, "sg_total": 1.2, "date": "2026-02-18"},
        {"tournament": "Event 8", "finish": "T11", "score_to_par": -5, "sg_total": 1.1, "date": "2026-02-11"},
    ]


def _make_results_inconsistent():
    """Sample results with high finish variance (low consistency)."""
    return [
        {"tournament": "Event 1", "finish": "1", "score_to_par": -18, "sg_total": 3.0, "date": "2026-04-01"},
        {"tournament": "Event 2", "finish": "CUT", "score_to_par": 8, "sg_total": -1.5, "date": "2026-03-25"},
        {"tournament": "Event 3", "finish": "T60", "score_to_par": 6, "sg_total": -1.0, "date": "2026-03-18"},
        {"tournament": "Event 4", "finish": "2", "score_to_par": -15, "sg_total": 2.8, "date": "2026-03-11"},
        {"tournament": "Event 5", "finish": "CUT", "score_to_par": 5, "sg_total": -0.8, "date": "2026-03-04"},
        {"tournament": "Event 6", "finish": "T55", "score_to_par": 4, "sg_total": -0.5, "date": "2026-02-25"},
        {"tournament": "Event 7", "finish": "3", "score_to_par": -12, "sg_total": 2.5, "date": "2026-02-18"},
        {"tournament": "Event 8", "finish": "T50", "score_to_par": 3, "sg_total": -0.3, "date": "2026-02-11"},
    ]


# ═══════════════════════════════════════════════════════════════
# calc_form_metrics Tests
# ═══════════════════════════════════════════════════════════════

class TestCalcFormMetrics:
    """Tests for FormTracker.calc_form_metrics."""

    def test_correct_averages_from_sample(self):
        """calc_form_metrics computes correct averages from sample results."""
        tracker = FormTracker()
        results = _make_results_improving()
        metrics = tracker.calc_form_metrics(results)

        # last_4_avg_finish: avg of 3, 5, 8, 12 = 7.0
        assert abs(metrics["last_4_avg_finish"] - 7.0) < 0.5

        # last_4_avg_sg: avg of 2.5, 2.0, 1.5, 1.0 = 1.75
        assert abs(metrics["last_4_avg_sg"] - 1.75) < 0.01

    def test_empty_results_returns_defaults(self):
        """Empty results list returns sensible defaults (not errors)."""
        tracker = FormTracker()
        metrics = tracker.calc_form_metrics([])

        assert metrics["last_4_avg_finish"] == 0.0
        assert metrics["last_8_avg_finish"] == 0.0
        assert metrics["last_4_avg_sg"] == 0.0
        assert metrics["trend"] == 0.0
        assert metrics["trend_label"] == "stable"
        assert metrics["cuts_made_last_8"] == 0
        assert metrics["top10s_last_8"] == 0
        assert metrics["wins_last_12"] == 0
        assert metrics["consistency"] == 0.5

    def test_handles_cut_and_wd_finishes(self):
        """calc_form_metrics gracefully handles CUT, WD, DQ finishes."""
        tracker = FormTracker()
        results = [
            {"tournament": "E1", "finish": "CUT", "score_to_par": 5, "sg_total": -1.0, "date": "2026-04-01"},
            {"tournament": "E2", "finish": "WD", "score_to_par": 0, "sg_total": 0.0, "date": "2026-03-25"},
            {"tournament": "E3", "finish": "T10", "score_to_par": -5, "sg_total": 1.0, "date": "2026-03-18"},
            {"tournament": "E4", "finish": "T20", "score_to_par": -2, "sg_total": 0.5, "date": "2026-03-11"},
        ]
        metrics = tracker.calc_form_metrics(results)
        # CUT and WD are excluded from finish averages; only T10 (10) and T20 (20) count
        assert abs(metrics["last_4_avg_finish"] - 15.0) < 0.5
        # Only 2 of 4 results had valid finishes => cuts_made_last_8 = 2
        assert metrics["cuts_made_last_8"] == 2

    def test_top10s_counted_correctly(self):
        """Top 10 finishes are counted correctly."""
        tracker = FormTracker()
        results = _make_results_improving()
        metrics = tracker.calc_form_metrics(results)
        # In improving results: finishes 3, 5, T8 are top-10 in the first 8 events
        assert metrics["top10s_last_8"] == 3

    def test_wins_counted_correctly(self):
        """Wins are counted correctly."""
        tracker = FormTracker()
        results = [
            {"tournament": "E1", "finish": "1", "score_to_par": -18, "sg_total": 3.0, "date": "2026-04-01"},
            {"tournament": "E2", "finish": "T5", "score_to_par": -8, "sg_total": 1.5, "date": "2026-03-25"},
            {"tournament": "E3", "finish": "1", "score_to_par": -15, "sg_total": 2.8, "date": "2026-03-18"},
        ]
        metrics = tracker.calc_form_metrics(results)
        assert metrics["wins_last_12"] == 2

    def test_consistency_higher_for_low_variance(self):
        """Consistency score is higher for players with low finish variance."""
        tracker = FormTracker()

        consistent_metrics = tracker.calc_form_metrics(_make_results_consistent())
        inconsistent_metrics = tracker.calc_form_metrics(_make_results_inconsistent())

        assert consistent_metrics["consistency"] > inconsistent_metrics["consistency"]

    def test_all_required_keys_present(self):
        """calc_form_metrics returns all required keys."""
        tracker = FormTracker()
        metrics = tracker.calc_form_metrics(_make_results_stable())

        required_keys = {
            "last_4_avg_finish", "last_8_avg_finish", "last_12_avg_finish",
            "last_4_avg_sg", "last_8_avg_sg", "last_12_avg_sg",
            "trend", "trend_label",
            "cuts_made_last_8", "top10s_last_8", "wins_last_12",
            "consistency",
        }
        assert required_keys.issubset(set(metrics.keys()))


# ═══════════════════════════════════════════════════════════════
# calc_trend Tests
# ═══════════════════════════════════════════════════════════════

class TestCalcTrend:
    """Tests for FormTracker.calc_trend."""

    def test_positive_slope_for_improving(self):
        """calc_trend returns positive slope for improving results."""
        tracker = FormTracker()
        results = _make_results_improving()
        trend = tracker.calc_trend(results)
        assert trend > 0, f"Expected positive trend for improving results, got {trend}"

    def test_negative_slope_for_declining(self):
        """calc_trend returns negative slope for declining results."""
        tracker = FormTracker()
        results = _make_results_declining()
        trend = tracker.calc_trend(results)
        assert trend < 0, f"Expected negative trend for declining results, got {trend}"

    def test_near_zero_for_stable(self):
        """calc_trend returns near-zero slope for stable results."""
        tracker = FormTracker()
        results = _make_results_stable()
        trend = tracker.calc_trend(results)
        assert abs(trend) < 0.1, f"Expected near-zero trend for stable results, got {trend}"

    def test_single_result_returns_zero(self):
        """calc_trend returns 0.0 for a single result."""
        tracker = FormTracker()
        results = [{"tournament": "E1", "finish": "5", "score_to_par": -8, "sg_total": 1.5, "date": "2026-04-01"}]
        trend = tracker.calc_trend(results)
        assert trend == 0.0

    def test_empty_results_returns_zero(self):
        """calc_trend returns 0.0 for empty results."""
        tracker = FormTracker()
        trend = tracker.calc_trend([])
        assert trend == 0.0

    def test_no_sg_data_returns_zero(self):
        """calc_trend returns 0.0 when results have no sg_total."""
        tracker = FormTracker()
        results = [
            {"tournament": "E1", "finish": "5", "score_to_par": -8, "date": "2026-04-01"},
            {"tournament": "E2", "finish": "10", "score_to_par": -4, "date": "2026-03-25"},
        ]
        trend = tracker.calc_trend(results)
        assert trend == 0.0


# ═══════════════════════════════════════════════════════════════
# get_form_label Tests
# ═══════════════════════════════════════════════════════════════

class TestGetFormLabel:
    """Tests for FormTracker.get_form_label."""

    def test_hot_for_strong_positive_trend_and_high_sg(self):
        """Returns 'hot' for strong positive trend + high recent SG."""
        tracker = FormTracker()
        label = tracker.get_form_label(trend=0.15, last_4_sg=2.0, career_sg=1.0)
        assert label == "hot"

    def test_cold_for_strong_negative_trend_and_low_sg(self):
        """Returns 'cold' for strong negative trend + low recent SG."""
        tracker = FormTracker()
        label = tracker.get_form_label(trend=-0.15, last_4_sg=-0.5, career_sg=0.5)
        assert label == "cold"

    def test_stable_for_flat_trend(self):
        """Returns 'stable' for flat trend."""
        tracker = FormTracker()
        label = tracker.get_form_label(trend=0.0, last_4_sg=1.0, career_sg=1.0)
        assert label == "stable"

    def test_improving_for_moderate_positive_trend(self):
        """Returns 'improving' for moderate positive trend."""
        tracker = FormTracker()
        label = tracker.get_form_label(trend=0.08, last_4_sg=1.0, career_sg=1.0)
        assert label == "improving"

    def test_declining_for_moderate_negative_trend(self):
        """Returns 'declining' for moderate negative trend."""
        tracker = FormTracker()
        label = tracker.get_form_label(trend=-0.08, last_4_sg=1.0, career_sg=1.0)
        assert label == "declining"

    def test_hot_requires_both_trend_and_sg(self):
        """'hot' requires BOTH strong trend AND high SG above career."""
        tracker = FormTracker()
        # Strong trend but SG not high enough above career
        label = tracker.get_form_label(trend=0.15, last_4_sg=1.2, career_sg=1.0)
        assert label == "improving"  # Not "hot" because SG only 0.2 above career (needs > 0.5)

    def test_cold_requires_both_trend_and_sg(self):
        """'cold' requires BOTH strong negative trend AND low SG below career."""
        tracker = FormTracker()
        # Strong negative trend but SG not low enough below career
        label = tracker.get_form_label(trend=-0.15, last_4_sg=0.8, career_sg=1.0)
        assert label == "declining"  # Not "cold" because SG only 0.2 below career (needs > 0.5)

    def test_boundary_stable(self):
        """Boundary: trend exactly at -0.05 is stable."""
        tracker = FormTracker()
        label = tracker.get_form_label(trend=-0.05, last_4_sg=1.0, career_sg=1.0)
        assert label == "stable"

    def test_boundary_stable_positive(self):
        """Boundary: trend exactly at 0.05 is stable."""
        tracker = FormTracker()
        label = tracker.get_form_label(trend=0.05, last_4_sg=1.0, career_sg=1.0)
        assert label == "stable"


# ═══════════════════════════════════════════════════════════════
# get_player_form / get_field_form Tests
# ═══════════════════════════════════════════════════════════════

class TestFormTrackerIntegration:
    """Integration tests for get_player_form and get_field_form."""

    def test_get_player_form_no_client(self):
        """get_player_form without client returns empty results with stable form."""
        tracker = FormTracker(client=None)

        with patch.object(tracker.cache, 'get', return_value=None), \
             patch.object(tracker.cache, 'set'):
            form = tracker.get_player_form("Scottie Scheffler")

        assert form["player_name"] == "Scottie Scheffler"
        assert form["recent_results"] == []
        assert form["form_label"] == "stable"
        assert "form_metrics" in form

    def test_get_field_form_returns_all_players(self):
        """get_field_form returns form data for all players in field."""
        tracker = FormTracker(client=None)

        field = ["Scottie Scheffler", "Rory McIlroy", "Jon Rahm"]

        with patch.object(tracker.cache, 'get', return_value=None), \
             patch.object(tracker.cache, 'set'):
            result = tracker.get_field_form(field)

        assert len(result) == 3
        for name in field:
            assert name in result
            assert "form_metrics" in result[name]
            assert "form_label" in result[name]
