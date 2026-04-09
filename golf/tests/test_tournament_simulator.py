"""
Tests for golf_tournament_simulator.py
"""

import random
import pytest
from golf.golf_tournament_simulator import (
    GolfTournamentSimulator,
    _run_golf_tournament_batch,
)
from golf.golf_course_profiles import get_course_profile


# ═══════════════════════════════════════════════════════════════
# Mock Classes
# ═══════════════════════════════════════════════════════════════

class MockClient:
    pass


class MockCache:
    def get(self, *a, **kw):
        return None

    def set(self, *a, **kw):
        pass


# ═══════════════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════════════

def _make_mock_player(name, sg_total, owgr=50, tier="unknown"):
    """Create a mock player param dict with varying SG levels."""
    return {
        "_player_name": name,
        "_player_id": name,
        "_owgr_rank": owgr,
        "_tier": tier,
        "_sg_total_raw": sg_total,
        "sg_total_adj": sg_total,
        "sg_ott": sg_total * 0.25,
        "sg_app": sg_total * 0.30,
        "sg_arg": sg_total * 0.20,
        "sg_putt": sg_total * 0.25,
        "birdie_rate_par3": max(0.05, 0.12 + sg_total * 0.02),
        "birdie_rate_par4": max(0.05, 0.18 + sg_total * 0.03),
        "birdie_rate_par5": max(0.10, 0.45 + sg_total * 0.03),
        "bogey_rate_par3": max(0.05, 0.22 - sg_total * 0.02),
        "bogey_rate_par4": max(0.05, 0.20 - sg_total * 0.02),
        "bogey_rate_par5": max(0.05, 0.12 - sg_total * 0.01),
        "double_rate": max(0.01, 0.03 - sg_total * 0.005),
        "eagle_rate_par5": max(0.005, 0.04 + sg_total * 0.005),
        "round_volatility": max(1.5, 2.8 - sg_total * 0.15),
        "streakiness": 0.5,
        "consistency_score": min(0.9, max(0.2, 0.55 + sg_total * 0.1)),
        "pressure_modifier": sg_total * 0.1,
        "major_experience": min(1.0, max(0.0, sg_total * 0.2)),
        "weather_adj": 0.0,
        "weather_resilience": 0.5,
        "fatigue_factor": max(0.1, 0.5 - sg_total * 0.08),
        "course_history_adj": 0.0,
        "course_fit_score": 0.5,
        "form_adj": 0.0,
    }


@pytest.fixture
def augusta_profile():
    """Augusta National course profile."""
    return get_course_profile("augusta_national")


@pytest.fixture
def augusta_holes(augusta_profile):
    """Augusta National hole-by-hole data."""
    return augusta_profile["holes"]


@pytest.fixture
def mock_field():
    """A mock 10-player field with varying skill levels."""
    return [
        _make_mock_player("Elite A", 2.5, 1, "elite"),
        _make_mock_player("Elite B", 2.0, 3, "elite"),
        _make_mock_player("Star A", 1.5, 8, "star"),
        _make_mock_player("Star B", 1.0, 12, "star"),
        _make_mock_player("Key A", 0.5, 25, "key"),
        _make_mock_player("Key B", 0.0, 50, "role"),
        _make_mock_player("Avg A", -0.5, 80, "role"),
        _make_mock_player("Avg B", -1.0, 120, "role"),
        _make_mock_player("Fringe A", -1.5, 160, "role"),
        _make_mock_player("Fringe B", -2.0, 200, "role"),
    ]


@pytest.fixture
def simulator(mock_field, augusta_profile, augusta_holes):
    """A GolfTournamentSimulator with mock data pre-injected."""
    sim = GolfTournamentSimulator(MockClient(), MockCache())
    sim.player_params = mock_field
    sim.course_profile = augusta_profile
    sim.holes = augusta_holes
    sim.player_data = {p["_player_name"]: p for p in mock_field}
    sim.composite_predictions = None
    return sim


# ═══════════════════════════════════════════════════════════════
# __init__ Tests
# ═══════════════════════════════════════════════════════════════

class TestInit:

    def test_stores_client_and_cache(self):
        """GolfTournamentSimulator.__init__ stores client and cache."""
        client = MockClient()
        cache = MockCache()
        sim = GolfTournamentSimulator(client, cache)
        assert sim.client is client
        assert sim.cache is cache

    def test_initial_state_is_none(self):
        """Initial state attributes are None before prefetch."""
        sim = GolfTournamentSimulator(MockClient())
        assert sim.player_data is None
        assert sim.player_params is None
        assert sim.course_profile is None
        assert sim.holes is None
        assert sim.composite_predictions is None
        assert sim.weather is None
        assert sim._matchup_cache == {}


# ═══════════════════════════════════════════════════════════════
# _run_golf_tournament_batch Tests
# ═══════════════════════════════════════════════════════════════

class TestRunGolfTournamentBatch:

    def test_returns_correct_accumulator_structure(self, mock_field, augusta_holes):
        """_run_golf_tournament_batch returns correct accumulator structure."""
        random.seed(42)
        args = (mock_field, augusta_holes, None, 3, None, 12345)
        accum, num_completed = _run_golf_tournament_batch(args)

        assert isinstance(accum, dict)
        assert num_completed == 3

        # Check that each player has the expected keys
        expected_keys = {
            "wins", "top5", "top10", "top20", "cuts_made",
            "total_finish", "total_score_to_par", "total_birdies",
            "total_bogeys", "total_rounds", "best_finish", "worst_finish",
        }
        for player_name, counts in accum.items():
            missing = expected_keys - set(counts.keys())
            assert not missing, f"{player_name} missing keys: {missing}"

    def test_all_players_present_in_accumulator(self, mock_field, augusta_holes):
        """All players from the field appear in the accumulator."""
        random.seed(42)
        args = (mock_field, augusta_holes, None, 2, None, 99)
        accum, _ = _run_golf_tournament_batch(args)

        for p in mock_field:
            assert p["_player_name"] in accum

    def test_exactly_one_winner_per_tournament(self, mock_field, augusta_holes):
        """Total wins across all players equals num_tournaments."""
        random.seed(42)
        num_t = 5
        args = (mock_field, augusta_holes, None, num_t, None, 42)
        accum, _ = _run_golf_tournament_batch(args)

        total_wins = sum(a["wins"] for a in accum.values())
        assert total_wins == num_t

    def test_counts_are_non_negative(self, mock_field, augusta_holes):
        """All accumulated counts are non-negative."""
        random.seed(42)
        args = (mock_field, augusta_holes, None, 3, None, 42)
        accum, _ = _run_golf_tournament_batch(args)

        for player, counts in accum.items():
            for key in ("wins", "top5", "top10", "top20", "cuts_made",
                        "total_finish", "total_birdies", "total_bogeys",
                        "total_rounds"):
                assert counts[key] >= 0, f"{player}.{key} is negative"


# ═══════════════════════════════════════════════════════════════
# _aggregate_results Tests
# ═══════════════════════════════════════════════════════════════

class TestAggregateResults:

    def test_computes_correct_percentages(self, simulator):
        """_aggregate_results computes correct percentages from raw counts."""
        accum = {
            "Player A": {
                "wins": 20, "top5": 40, "top10": 60, "top20": 80,
                "cuts_made": 90, "total_finish": 1000,
                "total_score_to_par": -500, "total_birdies": 360,
                "total_bogeys": 200, "total_rounds": 360,
                "best_finish": 1, "worst_finish": 30,
            },
            "Player B": {
                "wins": 10, "top5": 30, "top10": 50, "top20": 70,
                "cuts_made": 85, "total_finish": 1500,
                "total_score_to_par": -200, "total_birdies": 340,
                "total_bogeys": 220, "total_rounds": 350,
                "best_finish": 1, "worst_finish": 45,
            },
        }
        result = simulator._aggregate_results(accum, 100, 5.0)

        probs_a = result["player_probs"]["Player A"]
        assert probs_a["win_pct"] == 20.0
        assert probs_a["top5_pct"] == 40.0
        assert probs_a["top10_pct"] == 60.0
        assert probs_a["top20_pct"] == 80.0
        assert probs_a["cut_pct"] == 90.0
        assert probs_a["avg_finish"] == 10.0
        assert probs_a["avg_score"] == -5.0
        assert probs_a["best_finish"] == 1
        assert probs_a["worst_finish"] == 30

    def test_meta_fields_present(self, simulator):
        """_aggregate_results includes all expected meta fields."""
        accum = {
            "P1": {
                "wins": 5, "top5": 10, "top10": 20, "top20": 30,
                "cuts_made": 40, "total_finish": 500,
                "total_score_to_par": -100, "total_birdies": 180,
                "total_bogeys": 100, "total_rounds": 180,
                "best_finish": 1, "worst_finish": 20,
            },
        }
        result = simulator._aggregate_results(accum, 50, 3.0)
        meta = result["meta"]

        assert meta["num_tournaments"] == 50
        assert meta["num_players"] == 1
        assert "course" in meta
        assert "course_id" in meta
        assert "elapsed_seconds" in meta
        assert "num_workers" in meta
        assert "tournaments_per_second" in meta
        assert "weather_available" in meta


# ═══════════════════════════════════════════════════════════════
# Winner Board Tests
# ═══════════════════════════════════════════════════════════════

class TestWinnerBoard:

    def test_winner_board_probabilities_sum_to_approximately_100(self, simulator):
        """Winner board probabilities sum to approximately 100%."""
        random.seed(42)
        results = simulator.run(num_tournaments=50, num_workers=1)

        total_win_pct = sum(
            p["win_pct"] for p in results["player_probs"].values()
        )
        assert 85 < total_win_pct < 115, \
            f"Win probabilities sum to {total_win_pct}, expected ~100%"

    def test_better_players_have_higher_win_pct(self, simulator):
        """Better players (higher SG) have higher win_pct than weaker players."""
        random.seed(42)
        results = simulator.run(num_tournaments=200, num_workers=1)

        probs = results["player_probs"]
        elite_win = probs["Elite A"]["win_pct"]
        fringe_win = probs["Fringe B"]["win_pct"]

        assert elite_win > fringe_win, \
            f"Elite ({elite_win}%) should beat Fringe ({fringe_win}%)"

    def test_winner_board_sorted_descending(self, simulator):
        """Winner board is sorted by win_pct descending."""
        random.seed(42)
        results = simulator.run(num_tournaments=50, num_workers=1)

        board = results["winner_board"]
        for i in range(len(board) - 1):
            assert board[i]["win_pct"] >= board[i + 1]["win_pct"]


# ═══════════════════════════════════════════════════════════════
# run_matchup Tests
# ═══════════════════════════════════════════════════════════════

class TestRunMatchup:

    def test_returns_correct_structure(self, simulator):
        """run_matchup returns correct structure with mc and composite keys."""
        random.seed(42)
        result = simulator.run_matchup("Elite A", "Fringe B", num_sims=100)

        assert "mc" in result
        assert "p1_composite" in result
        assert "p2_composite" in result
        assert "p1_name" in result
        assert "p2_name" in result
        assert result["p1_name"] == "Elite A"
        assert result["p2_name"] == "Fringe B"

    def test_matchup_percentages_sum_to_100(self, simulator):
        """p1_win_pct + p2_win_pct + tie_pct sum to ~100%."""
        random.seed(42)
        result = simulator.run_matchup("Elite A", "Fringe B", num_sims=200)
        mc = result["mc"]

        total = mc["p1_win_pct"] + mc["p2_win_pct"] + mc["tie_pct"]
        assert abs(total - 100.0) < 0.1, \
            f"Matchup percentages sum to {total}, expected ~100%"

    def test_better_player_wins_more_in_matchup(self, simulator):
        """Better player (higher SG) wins more often in H2H."""
        random.seed(42)
        result = simulator.run_matchup("Elite A", "Fringe B", num_sims=500)
        mc = result["mc"]

        assert mc["p1_win_pct"] > mc["p2_win_pct"], \
            f"Elite ({mc['p1_win_pct']}%) should beat Fringe ({mc['p2_win_pct']}%)"

    def test_matchup_cached(self, simulator):
        """run_matchup result is cached in _matchup_cache."""
        random.seed(42)
        simulator.run_matchup("Elite A", "Star A", num_sims=50)
        assert ("Elite A", "Star A") in simulator._matchup_cache

    def test_raises_for_unknown_player(self, simulator):
        """run_matchup raises ValueError for unknown player."""
        with pytest.raises(ValueError, match="Player not found"):
            simulator.run_matchup("Elite A", "Nobody", num_sims=10)

    def test_raises_before_prefetch(self):
        """run_matchup raises RuntimeError before prefetch_data()."""
        sim = GolfTournamentSimulator(MockClient())
        with pytest.raises(RuntimeError, match="prefetch_data"):
            sim.run_matchup("A", "B")


# ═══════════════════════════════════════════════════════════════
# Sequential vs Parallel Tests
# ═══════════════════════════════════════════════════════════════

class TestSequentialVsParallel:

    def test_sequential_and_parallel_produce_structurally_identical_results(
        self, mock_field, augusta_profile, augusta_holes
    ):
        """Sequential and parallel runs produce structurally identical results."""
        import os
        num_cpus = os.cpu_count() or 1
        if num_cpus <= 1:
            pytest.skip("Single-core machine, cannot test parallel")

        # Sequential
        sim_seq = GolfTournamentSimulator(MockClient(), MockCache())
        sim_seq.player_params = mock_field
        sim_seq.course_profile = augusta_profile
        sim_seq.holes = augusta_holes
        sim_seq.player_data = {p["_player_name"]: p for p in mock_field}
        sim_seq.composite_predictions = None

        random.seed(42)
        seq_results = sim_seq.run(num_tournaments=30, num_workers=1)

        # Parallel
        sim_par = GolfTournamentSimulator(MockClient(), MockCache())
        sim_par.player_params = mock_field
        sim_par.course_profile = augusta_profile
        sim_par.holes = augusta_holes
        sim_par.player_data = {p["_player_name"]: p for p in mock_field}
        sim_par.composite_predictions = None

        random.seed(99)
        par_results = sim_par.run(num_tournaments=30, num_workers=2)

        # Same structure
        assert set(seq_results.keys()) == set(par_results.keys())
        assert set(seq_results["player_probs"].keys()) == \
            set(par_results["player_probs"].keys())
        assert set(seq_results["meta"].keys()) == set(par_results["meta"].keys())

        # Both should have valid win totals
        seq_win_total = sum(
            p["win_pct"] for p in seq_results["player_probs"].values())
        par_win_total = sum(
            p["win_pct"] for p in par_results["player_probs"].values())
        assert 85 < seq_win_total < 115
        assert 85 < par_win_total < 115


# ═══════════════════════════════════════════════════════════════
# Progress Callback Tests
# ═══════════════════════════════════════════════════════════════

class TestProgressCallback:

    def test_progress_callback_called_with_increasing_percentages(self, simulator):
        """Progress callback is called with increasing percentages."""
        progress_log = []

        def callback(pct, msg):
            progress_log.append((pct, msg))

        random.seed(42)
        simulator.run(num_tournaments=30, num_workers=1,
                      progress_callback=callback)

        assert len(progress_log) > 0, "Progress callback was never called"

        # Percentages should generally increase
        pcts = [entry[0] for entry in progress_log]
        for i in range(1, len(pcts)):
            assert pcts[i] >= pcts[i - 1], \
                f"Progress went backwards: {pcts[i-1]} -> {pcts[i]}"

    def test_progress_starts_at_zero_and_reaches_near_100(self, simulator):
        """Progress starts at 0 and reaches near 100%."""
        progress_log = []

        def callback(pct, msg):
            progress_log.append(pct)

        random.seed(42)
        simulator.run(num_tournaments=30, num_workers=1,
                      progress_callback=callback)

        assert progress_log[0] == 0, "Progress should start at 0"
        assert progress_log[-1] > 50, \
            f"Final progress {progress_log[-1]} should be > 50%"


# ═══════════════════════════════════════════════════════════════
# get_player_detail Tests
# ═══════════════════════════════════════════════════════════════

class TestGetPlayerDetail:

    def test_returns_none_for_unknown_players(self, simulator):
        """get_player_detail returns None for unknown players."""
        random.seed(42)
        results = simulator.run(num_tournaments=10, num_workers=1)
        detail = simulator.get_player_detail("Nobody", results)
        assert detail is None

    def test_returns_valid_detail_for_known_player(self, simulator):
        """get_player_detail returns valid detail dict for known player."""
        random.seed(42)
        results = simulator.run(num_tournaments=10, num_workers=1)
        detail = simulator.get_player_detail("Elite A", results)

        assert detail is not None
        assert detail["player"] == "Elite A"
        assert detail["owgr"] == 1
        assert detail["tier"] == "elite"
        assert "probs" in detail
        assert "matchup_history" in detail
        assert "params" in detail
        assert detail["params"]["sg_total_adj"] == 2.5

    def test_includes_matchup_history(self, simulator):
        """get_player_detail includes matchup history after run_matchup."""
        random.seed(42)
        results = simulator.run(num_tournaments=10, num_workers=1)

        # Run a matchup first
        simulator.run_matchup("Elite A", "Fringe B", num_sims=50)

        detail = simulator.get_player_detail("Elite A", results)
        assert "Fringe B" in detail["matchup_history"]


# ═══════════════════════════════════════════════════════════════
# get_matchup_cache Tests
# ═══════════════════════════════════════════════════════════════

class TestGetMatchupCache:

    def test_empty_cache_returns_empty_dict(self, simulator):
        """get_matchup_cache returns empty dict when no matchups cached."""
        assert simulator.get_matchup_cache("Elite A") == {}

    def test_returns_cached_matchups(self, simulator):
        """get_matchup_cache returns cached matchup results."""
        random.seed(42)
        simulator.run_matchup("Elite A", "Star A", num_sims=50)
        simulator.run_matchup("Elite A", "Fringe B", num_sims=50)

        cache = simulator.get_matchup_cache("Elite A")
        assert "Star A" in cache
        assert "Fringe B" in cache

    def test_flips_perspective_for_p2(self, simulator):
        """get_matchup_cache flips perspective when player is p2."""
        random.seed(42)
        simulator.run_matchup("Elite A", "Star A", num_sims=50)

        # Star A was p2 in the matchup
        cache = simulator.get_matchup_cache("Star A")
        assert "Elite A" in cache


# ═══════════════════════════════════════════════════════════════
# run() Validation Tests
# ═══════════════════════════════════════════════════════════════

class TestRunValidation:

    def test_raises_before_prefetch(self):
        """run() raises RuntimeError before prefetch_data()."""
        sim = GolfTournamentSimulator(MockClient())
        with pytest.raises(RuntimeError, match="prefetch_data"):
            sim.run(num_tournaments=10)

    def test_cut_pct_between_0_and_100(self, simulator):
        """All players have cut_pct between 0 and 100."""
        random.seed(42)
        results = simulator.run(num_tournaments=30, num_workers=1)

        for player, probs in results["player_probs"].items():
            assert 0 <= probs["cut_pct"] <= 100, \
                f"{player} cut_pct out of range: {probs['cut_pct']}"

    def test_avg_finish_in_valid_range(self, simulator, mock_field):
        """avg_finish is between 1 and num_players for all players."""
        random.seed(42)
        results = simulator.run(num_tournaments=30, num_workers=1)
        num_players = len(mock_field)

        for player, probs in results["player_probs"].items():
            assert 1 <= probs["avg_finish"] <= num_players + 5, \
                f"{player} avg_finish out of range: {probs['avg_finish']}"

    def test_all_result_keys_present(self, simulator):
        """run() result has all expected top-level keys."""
        random.seed(42)
        results = simulator.run(num_tournaments=10, num_workers=1)

        expected_keys = {
            "player_probs", "winner_board", "top10_board",
            "cut_danger", "value_picks", "composite_predictions", "meta",
        }
        missing = expected_keys - set(results.keys())
        assert not missing, f"Missing keys: {missing}"

    def test_top10_board_sorted_descending(self, simulator):
        """Top-10 board is sorted by top10_pct descending."""
        random.seed(42)
        results = simulator.run(num_tournaments=30, num_workers=1)

        board = results["top10_board"]
        for i in range(len(board) - 1):
            assert board[i]["top10_pct"] >= board[i + 1]["top10_pct"]
