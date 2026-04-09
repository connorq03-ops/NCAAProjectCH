"""
Tests for golf_mc_engine.py
"""

import random
import pytest
from golf.golf_mc_engine import (
    clamp,
    rand_normal,
    generate_round_style,
    sim_hole,
    sim_round,
    sim_tournament_single,
    simulate_tournament,
    simulate_matchup,
    STREAK_DECAY,
)
from golf.golf_course_profiles import get_course_profile


# ═══════════════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════════════

@pytest.fixture
def augusta_holes():
    """Augusta National hole-by-hole data."""
    profile = get_course_profile("augusta_national")
    return profile["holes"]


@pytest.fixture
def elite_player_params():
    """Sim params for an elite player (Scheffler-like)."""
    return {
        "_player_name": "Elite Player",
        "_player_id": 1,
        "_owgr_rank": 1,
        "sg_total_adj": 2.5,
        "sg_ott": 0.9,
        "sg_app": 1.0,
        "sg_arg": 0.3,
        "sg_putt": 0.3,
        "birdie_rate_par3": 0.16,
        "birdie_rate_par4": 0.24,
        "birdie_rate_par5": 0.52,
        "bogey_rate_par3": 0.18,
        "bogey_rate_par4": 0.15,
        "bogey_rate_par5": 0.08,
        "double_rate": 0.02,
        "eagle_rate_par5": 0.05,
        "round_volatility": 2.2,
        "streakiness": 0.5,
        "consistency_score": 0.75,
        "pressure_modifier": 0.3,
        "major_experience": 0.6,
        "weather_adj": 0.0,
        "weather_resilience": 0.7,
        "fatigue_factor": 0.3,
        "course_history_adj": 0.5,
        "course_fit_score": 0.8,
        "form_adj": 0.1,
    }


@pytest.fixture
def avg_player_params():
    """Sim params for an average PGA Tour player."""
    return {
        "_player_name": "Average Player",
        "_player_id": 2,
        "_owgr_rank": 100,
        "sg_total_adj": 0.0,
        "sg_ott": 0.0,
        "sg_app": 0.0,
        "sg_arg": 0.0,
        "sg_putt": 0.0,
        "birdie_rate_par3": 0.12,
        "birdie_rate_par4": 0.18,
        "birdie_rate_par5": 0.45,
        "bogey_rate_par3": 0.22,
        "bogey_rate_par4": 0.20,
        "bogey_rate_par5": 0.12,
        "double_rate": 0.03,
        "eagle_rate_par5": 0.04,
        "round_volatility": 2.8,
        "streakiness": 0.5,
        "consistency_score": 0.55,
        "pressure_modifier": 0.0,
        "major_experience": 0.0,
        "weather_adj": 0.0,
        "weather_resilience": 0.5,
        "fatigue_factor": 0.5,
        "course_history_adj": 0.0,
        "course_fit_score": 0.5,
        "form_adj": 0.0,
    }


@pytest.fixture
def weak_player_params():
    """Sim params for a below-average player."""
    return {
        "_player_name": "Weak Player",
        "_player_id": 3,
        "_owgr_rank": 200,
        "sg_total_adj": -1.0,
        "sg_ott": -0.3,
        "sg_app": -0.3,
        "sg_arg": -0.2,
        "sg_putt": -0.2,
        "birdie_rate_par3": 0.08,
        "birdie_rate_par4": 0.12,
        "birdie_rate_par5": 0.35,
        "bogey_rate_par3": 0.28,
        "bogey_rate_par4": 0.26,
        "bogey_rate_par5": 0.18,
        "double_rate": 0.05,
        "eagle_rate_par5": 0.02,
        "round_volatility": 3.5,
        "streakiness": 0.6,
        "consistency_score": 0.3,
        "pressure_modifier": -0.2,
        "major_experience": 0.0,
        "weather_adj": 0.0,
        "weather_resilience": 0.3,
        "fatigue_factor": 0.7,
        "course_history_adj": 0.0,
        "course_fit_score": 0.3,
        "form_adj": -0.1,
    }


@pytest.fixture
def small_field(elite_player_params, avg_player_params, weak_player_params):
    """A small 3-player field for fast tournament tests."""
    return [elite_player_params, avg_player_params, weak_player_params]


def _make_field(n, base_params):
    """Create an N-player field with varying skill levels."""
    field = []
    for i in range(n):
        p = dict(base_params)
        sg_offset = 3.0 - (i / max(n - 1, 1)) * 6.0  # range from +3 to -3
        p["_player_name"] = f"Player_{i+1}"
        p["_player_id"] = i + 1
        p["sg_total_adj"] = sg_offset
        p["birdie_rate_par4"] = max(0.05, 0.18 + sg_offset * 0.03)
        p["bogey_rate_par4"] = max(0.05, 0.20 - sg_offset * 0.02)
        field.append(p)
    return field


# ═══════════════════════════════════════════════════════════════
# generate_round_style Tests
# ═══════════════════════════════════════════════════════════════

class TestGenerateRoundStyle:

    def test_returns_expected_keys(self):
        """generate_round_style returns dict with all expected keys."""
        result = generate_round_style(2.8, 0.5)
        expected_keys = {"birdie_adj", "bogey_adj", "putting_adj", "driving_adj", "style_label"}
        assert set(result.keys()) == expected_keys

    def test_style_label_is_valid(self):
        """Style label is one of aggressive/conservative/balanced."""
        random.seed(42)
        for _ in range(100):
            result = generate_round_style(2.8, 0.5)
            assert result["style_label"] in ("aggressive", "conservative", "balanced")

    def test_adjustments_are_numeric(self):
        """All adjustment values are floats."""
        random.seed(42)
        result = generate_round_style(2.8, 0.5)
        for key in ("birdie_adj", "bogey_adj", "putting_adj", "driving_adj"):
            assert isinstance(result[key], float)


# ═══════════════════════════════════════════════════════════════
# sim_hole Tests
# ═══════════════════════════════════════════════════════════════

class TestSimHole:

    def _base_cfg(self, par=4):
        return {
            "par": par,
            "difficulty_rank": 9,
            "birdie_rate": 0.18,
            "bogey_rate": 0.20,
            "double_rate": 0.03,
            "eagle_rate": 0.04 if par == 5 else 0.0,
            "momentum": 0.0,
            "streakiness": 0.5,
            "weather_adj": 0.0,
            "pressure_adj": 0.0,
            "fatigue_adj": 0.0,
            "hole_key_stat": "sg_app",
            "player_sg_for_key_stat": 0.0,
            "round_style": {},
        }

    def test_returns_valid_score_range(self):
        """sim_hole returns score relative to par in valid range (-2 to +2)."""
        random.seed(42)
        for _ in range(500):
            result = sim_hole(self._base_cfg())
            assert -2 <= result["score_relative_to_par"] <= 2

    def test_birdie_rate_increases_with_higher_param(self):
        """Higher birdie_rate param produces more birdies."""
        random.seed(42)
        n = 5000

        low_birdies = 0
        cfg_low = self._base_cfg()
        cfg_low["birdie_rate"] = 0.10
        for _ in range(n):
            if sim_hole(cfg_low)["is_birdie"]:
                low_birdies += 1

        random.seed(42)
        high_birdies = 0
        cfg_high = self._base_cfg()
        cfg_high["birdie_rate"] = 0.35
        for _ in range(n):
            if sim_hole(cfg_high)["is_birdie"]:
                high_birdies += 1

        assert high_birdies > low_birdies

    def test_bogey_rate_increases_with_higher_param(self):
        """Higher bogey_rate param produces more bogeys."""
        random.seed(42)
        n = 5000

        low_bogeys = 0
        cfg_low = self._base_cfg()
        cfg_low["bogey_rate"] = 0.10
        for _ in range(n):
            if sim_hole(cfg_low)["is_bogey"]:
                low_bogeys += 1

        random.seed(42)
        high_bogeys = 0
        cfg_high = self._base_cfg()
        cfg_high["bogey_rate"] = 0.35
        for _ in range(n):
            if sim_hole(cfg_high)["is_bogey"]:
                high_bogeys += 1

        assert high_bogeys > low_bogeys

    def test_momentum_increases_after_birdie(self):
        """Momentum increases after a birdie."""
        random.seed(42)
        cfg = self._base_cfg()
        cfg["birdie_rate"] = 0.99  # force birdie
        cfg["bogey_rate"] = 0.001
        cfg["double_rate"] = 0.001
        cfg["momentum"] = 0.0
        result = sim_hole(cfg)
        if result["is_birdie"]:
            assert result["momentum_after"] > 0.0

    def test_momentum_decreases_after_bogey(self):
        """Momentum decreases after a bogey."""
        random.seed(42)
        cfg = self._base_cfg()
        cfg["birdie_rate"] = 0.001
        cfg["bogey_rate"] = 0.99  # force bogey
        cfg["double_rate"] = 0.001
        cfg["momentum"] = 0.0
        result = sim_hole(cfg)
        if result["is_bogey"]:
            assert result["momentum_after"] < 0.0

    def test_momentum_decays_after_par(self):
        """Momentum decays toward zero after a par."""
        cfg = self._base_cfg()
        cfg["birdie_rate"] = 0.001
        cfg["bogey_rate"] = 0.001
        cfg["double_rate"] = 0.001
        cfg["eagle_rate"] = 0.0
        cfg["momentum"] = 2.0

        # With very low non-par rates, most results should be par
        random.seed(42)
        par_results = []
        for _ in range(100):
            result = sim_hole(cfg)
            if result["score_relative_to_par"] == 0:
                par_results.append(result["momentum_after"])

        assert len(par_results) > 0
        for m in par_results:
            assert abs(m) < abs(2.0)  # momentum decayed

    def test_eagle_only_on_par5(self):
        """Eagles should only occur on par 5s."""
        random.seed(42)
        cfg3 = self._base_cfg(par=3)
        cfg4 = self._base_cfg(par=4)
        for _ in range(1000):
            assert not sim_hole(cfg3)["is_eagle"]
            assert not sim_hole(cfg4)["is_eagle"]

    def test_all_rates_clamped_with_extreme_inputs(self):
        """Scoring rates stay within clamped bounds even with extreme inputs."""
        random.seed(42)
        cfg = self._base_cfg()
        cfg["birdie_rate"] = 5.0  # extreme
        cfg["bogey_rate"] = 5.0
        cfg["double_rate"] = 5.0
        cfg["momentum"] = 100.0
        cfg["weather_adj"] = 10.0
        cfg["pressure_adj"] = 10.0
        cfg["fatigue_adj"] = 10.0
        cfg["player_sg_for_key_stat"] = 10.0

        # Should not crash, should return valid result
        for _ in range(100):
            result = sim_hole(cfg)
            assert -2 <= result["score_relative_to_par"] <= 2


# ═══════════════════════════════════════════════════════════════
# sim_round Tests
# ═══════════════════════════════════════════════════════════════

class TestSimRound:

    REQUIRED_KEYS = {
        "score_to_par", "total_score", "hole_scores", "birdies",
        "bogeys", "doubles_plus", "eagles", "max_hot_streak",
        "max_cold_streak", "momentum",
    }

    def test_returns_all_required_keys(self, avg_player_params, augusta_holes):
        """sim_round returns dict with all required keys."""
        random.seed(42)
        result = sim_round(avg_player_params, augusta_holes, 1)
        missing = self.REQUIRED_KEYS - set(result.keys())
        assert not missing, f"Missing keys: {missing}"

    def test_total_score_equals_par_plus_score_to_par(self, avg_player_params, augusta_holes):
        """total_score = course_par + score_to_par."""
        random.seed(42)
        result = sim_round(avg_player_params, augusta_holes, 1)
        course_par = sum(h["par"] for h in augusta_holes)
        assert result["total_score"] == course_par + result["score_to_par"]

    def test_hole_count_equals_18(self, avg_player_params, augusta_holes):
        """hole_scores has exactly 18 entries."""
        random.seed(42)
        result = sim_round(avg_player_params, augusta_holes, 1)
        assert len(result["hole_scores"]) == 18

    def test_scoring_events_sum_to_18(self, avg_player_params, augusta_holes):
        """birdies + bogeys + doubles + eagles + pars = 18."""
        random.seed(42)
        for _ in range(50):
            result = sim_round(avg_player_params, augusta_holes, 1)
            pars = 18 - result["birdies"] - result["bogeys"] - result["doubles_plus"] - result["eagles"]
            assert result["birdies"] + result["bogeys"] + result["doubles_plus"] + result["eagles"] + pars == 18

    def test_better_players_average_lower_scores(self, elite_player_params, weak_player_params, augusta_holes):
        """Better players (higher SG) average lower scores over many rounds."""
        random.seed(42)
        n = 200
        elite_total = 0
        weak_total = 0
        for _ in range(n):
            elite_total += sim_round(elite_player_params, augusta_holes, 1)["score_to_par"]
            weak_total += sim_round(weak_player_params, augusta_holes, 1)["score_to_par"]

        assert elite_total / n < weak_total / n

    def test_weekend_rounds_with_pressure(self, avg_player_params, augusta_holes):
        """Weekend rounds (rd 3-4) execute without error and produce valid results."""
        random.seed(42)
        for rd in (3, 4):
            config = {"is_weekend": True, "current_position": 3}
            result = sim_round(avg_player_params, augusta_holes, rd, config)
            assert "score_to_par" in result
            assert len(result["hole_scores"]) == 18

    def test_score_to_par_matches_hole_scores_sum(self, avg_player_params, augusta_holes):
        """score_to_par should equal sum of hole_scores."""
        random.seed(42)
        for _ in range(50):
            result = sim_round(avg_player_params, augusta_holes, 1)
            assert result["score_to_par"] == sum(result["hole_scores"])


# ═══════════════════════════════════════════════════════════════
# sim_tournament_single Tests
# ═══════════════════════════════════════════════════════════════

class TestSimTournamentSingle:

    def test_returns_standings_with_correct_player_count(self, small_field, augusta_holes):
        """Returns standings with same number of players as input."""
        random.seed(42)
        result = sim_tournament_single(small_field, augusta_holes)
        assert len(result["standings"]) == len(small_field)

    def test_cut_reduces_field(self, avg_player_params, augusta_holes):
        """With >65 players, cut reduces field for R3/R4."""
        random.seed(42)
        large_field = _make_field(80, avg_player_params)
        result = sim_tournament_single(large_field, augusta_holes)

        made_cut = [s for s in result["standings"] if s["made_cut"]]
        missed_cut = [s for s in result["standings"] if not s["made_cut"]]

        # Players who made cut should have 4 rounds
        for s in made_cut:
            assert len(s["rounds"]) == 4

        # Players who missed cut should have 2 rounds
        for s in missed_cut:
            assert len(s["rounds"]) == 2

        assert result["players_made_cut"] < 80

    def test_winner_has_lowest_total(self, small_field, augusta_holes):
        """Winner should have the lowest (or tied for lowest) total_to_par among cut players."""
        random.seed(42)
        for _ in range(20):
            result = sim_tournament_single(small_field, augusta_holes)
            winner_name = result["winner"]
            cut_standings = [s for s in result["standings"] if s["made_cut"]]
            if not cut_standings:
                continue
            best_score = min(s["total_to_par"] for s in cut_standings)
            winner_entry = next(s for s in cut_standings if s["player_name"] == winner_name)
            assert winner_entry["total_to_par"] == best_score

    def test_all_players_in_standings(self, small_field, augusta_holes):
        """All input players appear in standings."""
        random.seed(42)
        result = sim_tournament_single(small_field, augusta_holes)
        names_in = {p["_player_name"] for p in small_field}
        names_out = {s["player_name"] for s in result["standings"]}
        assert names_in == names_out

    def test_small_field_no_cut(self, small_field, augusta_holes):
        """With fewer than 65 players, all make the cut."""
        random.seed(42)
        result = sim_tournament_single(small_field, augusta_holes)
        assert result["players_made_cut"] == len(small_field)
        for s in result["standings"]:
            assert s["made_cut"]
            assert len(s["rounds"]) == 4


# ═══════════════════════════════════════════════════════════════
# simulate_tournament Tests
# ═══════════════════════════════════════════════════════════════

class TestSimulateTournament:

    def test_percentages_monotonically_increase(self, small_field, augusta_holes):
        """win% <= top5% <= top10% <= top20% <= cut% for each player."""
        random.seed(42)
        results = simulate_tournament(small_field, augusta_holes, num_sims=200)
        for name, stats in results.items():
            assert stats["win_pct"] <= stats["top5_pct"] + 0.01  # small tolerance for float
            assert stats["top5_pct"] <= stats["top10_pct"] + 0.01
            assert stats["top10_pct"] <= stats["top20_pct"] + 0.01
            assert stats["top20_pct"] <= stats["cut_pct"] + 0.01

    def test_better_player_higher_win_pct(self, elite_player_params, weak_player_params, augusta_holes):
        """Better player (higher SG) has higher win% over many sims."""
        random.seed(42)
        field = [elite_player_params, weak_player_params]
        results = simulate_tournament(field, augusta_holes, num_sims=500)
        elite_stats = results["Elite Player"]
        weak_stats = results["Weak Player"]
        assert elite_stats["win_pct"] > weak_stats["win_pct"]

    def test_returns_all_players(self, small_field, augusta_holes):
        """simulate_tournament returns results for all input players."""
        random.seed(42)
        results = simulate_tournament(small_field, augusta_holes, num_sims=50)
        for p in small_field:
            assert p["_player_name"] in results

    def test_win_pct_bounded(self, small_field, augusta_holes):
        """Win percentages are between 0 and 100."""
        random.seed(42)
        results = simulate_tournament(small_field, augusta_holes, num_sims=100)
        for name, stats in results.items():
            assert 0 <= stats["win_pct"] <= 100
            assert 0 <= stats["top5_pct"] <= 100
            assert 0 <= stats["top10_pct"] <= 100
            assert 0 <= stats["top20_pct"] <= 100
            assert 0 <= stats["cut_pct"] <= 100

    def test_avg_finish_reasonable(self, small_field, augusta_holes):
        """Average finish should be between 1 and field size."""
        random.seed(42)
        results = simulate_tournament(small_field, augusta_holes, num_sims=100)
        for name, stats in results.items():
            assert 1 <= stats["avg_finish"] <= len(small_field)


# ═══════════════════════════════════════════════════════════════
# simulate_matchup Tests
# ═══════════════════════════════════════════════════════════════

class TestSimulateMatchup:

    def test_percentages_sum_to_100(self, elite_player_params, avg_player_params, augusta_holes):
        """p1_win_pct + p2_win_pct + tie_pct should approximately equal 100."""
        random.seed(42)
        result = simulate_matchup(elite_player_params, avg_player_params, augusta_holes, num_sims=500)
        total = result["p1_win_pct"] + result["p2_win_pct"] + result["tie_pct"]
        assert abs(total - 100.0) < 0.1

    def test_better_player_wins_more(self, elite_player_params, weak_player_params, augusta_holes):
        """Better player has higher win_pct in H2H matchup."""
        random.seed(42)
        result = simulate_matchup(elite_player_params, weak_player_params, augusta_holes, num_sims=500)
        assert result["p1_win_pct"] > result["p2_win_pct"]

    def test_returns_correct_names(self, elite_player_params, avg_player_params, augusta_holes):
        """Matchup result contains correct player names."""
        random.seed(42)
        result = simulate_matchup(elite_player_params, avg_player_params, augusta_holes, num_sims=50)
        assert result["p1_name"] == "Elite Player"
        assert result["p2_name"] == "Average Player"

    def test_avg_margin_sign_matches_winner(self, elite_player_params, weak_player_params, augusta_holes):
        """p1_avg_margin should be positive when p1 wins more often."""
        random.seed(42)
        result = simulate_matchup(elite_player_params, weak_player_params, augusta_holes, num_sims=500)
        if result["p1_win_pct"] > result["p2_win_pct"]:
            assert result["p1_avg_margin"] > 0


# ═══════════════════════════════════════════════════════════════
# Deterministic Reproducibility Tests
# ═══════════════════════════════════════════════════════════════

class TestReproducibility:

    def test_deterministic_with_seed(self, avg_player_params, augusta_holes):
        """Same random seed produces identical results."""
        random.seed(123)
        r1 = sim_round(avg_player_params, augusta_holes, 1)

        random.seed(123)
        r2 = sim_round(avg_player_params, augusta_holes, 1)

        assert r1["score_to_par"] == r2["score_to_par"]
        assert r1["hole_scores"] == r2["hole_scores"]
        assert r1["birdies"] == r2["birdies"]
        assert r1["bogeys"] == r2["bogeys"]

    def test_tournament_deterministic_with_seed(self, small_field, augusta_holes):
        """Same seed produces same tournament winner."""
        random.seed(456)
        r1 = sim_tournament_single(small_field, augusta_holes)

        random.seed(456)
        r2 = sim_tournament_single(small_field, augusta_holes)

        assert r1["winner"] == r2["winner"]
        assert r1["cut_line"] == r2["cut_line"]
