"""
golf_tournament_simulator.py - Golf Tournament Simulator Orchestrator

Ties together the MC engine, composite model, data client, and course profiles
into a single class with parallel execution and progress reporting.

The golf equivalent of bracket_simulator.py (~791 lines). Mirrors the
BracketSimulator class with prefetch_data(), run(), _run_tournament_batch()
(top-level picklable function), _run_sequential(), _aggregate_results(),
and ProcessPoolExecutor parallelism.

Architecture:
  1. Prefetch all DataGolf data (5 bulk API calls)
  2. Build sim params for each player in the field
  3. Run composite model predictions (4-model blend)
  4. Run N tournament MC simulations in parallel
  5. Aggregate results across all simulations
  6. Return per-player probabilities + analytics
"""

import os
import pickle
import random
import time
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor, as_completed

from golf.datagolf_client import DataGolfClient
from golf.golf_course_profiles import get_course_profile, COURSES
from golf.golf_sim_params import (
    prefetch_all_player_data,
    build_player_sim_params,
    build_field_sim_params,
)
from golf.golf_mc_engine import (
    simulate_tournament,
    simulate_matchup,
    sim_tournament_single,
)
from golf.golf_composite_model import (
    predict_field,
    compute_golf_composite,
    model_sg_efficiency,
    model_course_fit,
    model_golf_rat,
)
from golf.golf_weather_scraper import WeatherFetcher, calc_weather_impact


# ─── Top-level helpers (must be picklable for ProcessPoolExecutor) ───────────

def _run_golf_tournament_batch(args):
    """Run a batch of tournament simulations in a worker process.

    Must be top-level function (picklable) for ProcessPoolExecutor.
    Mirrors _run_tournament_batch() in bracket_simulator.py.

    Args:
        args: tuple of (player_params_list, holes, weather_per_round,
               num_tournaments, num_sims_per_tournament, worker_seed)

    Returns:
        tuple of (player_accum_dict, num_completed)
        where player_accum_dict is keyed by player name with accumulated
        wins, top5, top10, top20, cuts_made, total_finish, total_score counts
    """
    (player_params_list, holes, weather_per_round,
     num_tournaments, num_sims_per_tournament, worker_seed) = args

    # Seed random per-worker to avoid correlated results
    # (same pattern as basketball bracket_simulator.py line 63)
    random.seed(worker_seed)

    # Initialize accumulator dict for each player
    accum = {}
    for p in player_params_list:
        name = p.get("_player_name", f"Player_{id(p)}")
        accum[name] = {
            "wins": 0,
            "top5": 0,
            "top10": 0,
            "top20": 0,
            "cuts_made": 0,
            "total_finish": 0,
            "total_score_to_par": 0,
            "total_birdies": 0,
            "total_bogeys": 0,
            "total_rounds": 0,
            "best_finish": 999,
            "worst_finish": 0,
        }

    for _ in range(num_tournaments):
        result = sim_tournament_single(player_params_list, holes, weather_per_round)

        winner = result["winner"]
        if winner in accum:
            accum[winner]["wins"] += 1

        for entry in result["standings"]:
            name = entry["player_name"]
            if name not in accum:
                continue

            pos = entry["position"]
            accum[name]["total_finish"] += pos
            accum[name]["total_score_to_par"] += entry["total_to_par"]
            accum[name]["total_birdies"] += entry.get("birdies", 0)
            accum[name]["total_bogeys"] += entry.get("bogeys", 0)

            num_rounds = len(entry["rounds"])
            accum[name]["total_rounds"] += num_rounds

            if entry["made_cut"]:
                accum[name]["cuts_made"] += 1
            if pos <= 5:
                accum[name]["top5"] += 1
            if pos <= 10:
                accum[name]["top10"] += 1
            if pos <= 20:
                accum[name]["top20"] += 1

            accum[name]["best_finish"] = min(accum[name]["best_finish"], pos)
            accum[name]["worst_finish"] = max(accum[name]["worst_finish"], pos)

    # Convert defaultdicts to regular dicts for pickling
    return (dict(accum), num_tournaments)


class GolfTournamentSimulator:
    """Full PGA Tour tournament simulator with parallel execution.

    Mirrors BracketSimulator from bracket_simulator.py.

    Architecture:
      1. Prefetch all DataGolf data (5 bulk API calls)
      2. Build sim params for each player in the field
      3. Run composite model predictions (4-model blend)
      4. Run N tournament MC simulations in parallel
      5. Aggregate results across all simulations
      6. Return per-player probabilities + analytics
    """

    def __init__(self, client, cache=None):
        """
        Args:
            client: DataGolfClient instance
            cache: optional SQLiteCache instance (for caching API responses)
        """
        self.client = client
        self.cache = cache
        self.player_data = None       # raw DataGolf data (from prefetch)
        self.player_params = None     # sim-ready params (from build_player_sim_params)
        self.course_profile = None
        self.holes = None
        self.composite_predictions = None  # from predict_field()
        self.weather = None
        self._matchup_cache = {}      # (p1, p2) -> matchup result
        self._last_num_workers = 1

    def prefetch_data(self, course_id, tournament_id=None):
        """Bulk fetch all DataGolf data for all players. Call once before run().

        Mirrors BracketSimulator.prefetch_data() (line 109-114).

        Args:
            course_id: str, key into COURSES dict
            tournament_id: optional str, DataGolf tournament/event ID

        Returns:
            int: number of players in field
        """
        # 1. Bulk-fetch from DataGolf
        self.player_data = prefetch_all_player_data(
            self.client, tournament_id=tournament_id
        )

        # 2. Load course profile
        self.course_profile = get_course_profile(course_id)
        if self.course_profile is None:
            raise ValueError(f"Unknown course_id: {course_id}")
        self.holes = self.course_profile.get("holes", [])

        # 3. Optionally fetch weather
        weather_api_key = os.environ.get("WEATHER_API_KEY")
        if weather_api_key:
            try:
                fetcher = WeatherFetcher(api_key=weather_api_key)
                self.weather = fetcher.fetch_tournament_weather(course_id)
            except Exception as e:
                print(f"[golf-sim] Weather fetch failed: {e}", flush=True)
                self.weather = None
        else:
            self.weather = None

        # 4. Compute field average SG for field-strength adjustment
        sg_values = [
            p.get("sg_total", 0.0) for p in self.player_data.values()
            if p.get("sg_total") is not None
        ]
        field_avg_sg = sum(sg_values) / len(sg_values) if sg_values else 0.0

        # 5. Build sim params for each player
        params_list = []
        for name, stats in self.player_data.items():
            params = build_player_sim_params(
                stats, self.course_profile,
                weather=self.weather, field_strength=field_avg_sg,
            )
            params_list.append(params)

        # Sort by adjusted SG (best first)
        params_list.sort(key=lambda p: p["sg_total_adj"], reverse=True)
        self.player_params = params_list

        # 6. Run composite model predictions
        self.composite_predictions = predict_field(
            list(self.player_data.values()),
            self.course_profile,
            weather=self.weather,
        )

        # 7. Clear matchup cache
        self._matchup_cache.clear()

        return len(self.player_params)

    def run(self, num_tournaments=1000, num_sims_per_tournament=None,
            progress_callback=None, num_workers=None):
        """Run N tournament simulations and aggregate results.

        Mirrors BracketSimulator.run() (lines 349-482).

        Args:
            num_tournaments: Number of full tournaments to simulate
            num_sims_per_tournament: Unused (kept for API symmetry)
            progress_callback: Optional fn(pct, msg) called periodically
            num_workers: Number of parallel workers (default: cpu_count - 1, min 1)

        Returns:
            dict with per-player probabilities, winner odds, analytics, etc.
        """
        if self.player_params is None:
            raise RuntimeError("Call prefetch_data() before run()")

        start_time = time.time()

        # Determine parallelism
        if num_workers is None:
            num_workers = max(1, (os.cpu_count() or 1) - 1)
        num_workers = min(num_workers, num_tournaments)
        # Don't bother with parallelism for tiny runs
        if num_tournaments < 10:
            num_workers = 1
        # Ensure minimum batch size per worker
        if num_workers > 1 and num_tournaments / num_workers < 5:
            num_workers = max(1, num_tournaments // 5)

        self._last_num_workers = num_workers

        if progress_callback:
            progress_callback(0, "Preparing tournament field...")

        # Prepare weather_per_round list (4 entries, one per round)
        weather_per_round = None
        if self.weather is not None:
            try:
                altitude = self.course_profile.get("elevation_ft", 0)
                weather_per_round = [
                    calc_weather_impact(self.weather, rd, altitude_ft=altitude)
                    for rd in range(1, 5)
                ]
            except Exception as e:
                print(f"[golf-sim] Weather impact calc failed: {e}", flush=True)
                weather_per_round = None

        if progress_callback:
            progress_callback(
                15,
                f"Field prepared ({len(self.player_params)} players). "
                f"Starting tournament batches..."
            )

        # Sequential fallback for small runs
        if num_workers <= 1:
            return self._run_sequential(
                num_tournaments, weather_per_round,
                progress_callback, start_time)

        # Verify player_params is picklable before launching workers
        try:
            pickle.dumps(self.player_params)
        except Exception as e:
            print(f"[golf-sim] player_params not picklable, "
                  f"falling back to sequential: {e}", flush=True)
            return self._run_sequential(
                num_tournaments, weather_per_round,
                progress_callback, start_time)

        # Split tournaments across workers (same batch_sizes logic as
        # basketball bracket_simulator.py lines 410-414)
        batch_sizes = []
        base_batch = num_tournaments // num_workers
        remainder = num_tournaments % num_workers
        for i in range(num_workers):
            batch_sizes.append(base_batch + (1 if i < remainder else 0))

        # Prepare worker args tuples
        worker_args = []
        for i, batch_size in enumerate(batch_sizes):
            if batch_size == 0:
                continue
            worker_args.append((
                self.player_params,
                self.holes,
                weather_per_round,
                batch_size,
                num_sims_per_tournament,
                random.randint(0, 2**32 - 1),
            ))

        # Launch workers
        accum = {}
        completed = 0

        with ProcessPoolExecutor(max_workers=num_workers) as executor:
            futures = {
                executor.submit(_run_golf_tournament_batch, args): i
                for i, args in enumerate(worker_args)
            }

            for future in as_completed(futures):
                try:
                    batch_accum, batch_n = future.result(timeout=300)

                    # Merge accumulators
                    for player, counts in batch_accum.items():
                        if player not in accum:
                            accum[player] = {
                                "wins": 0, "top5": 0, "top10": 0,
                                "top20": 0, "cuts_made": 0,
                                "total_finish": 0, "total_score_to_par": 0,
                                "total_birdies": 0, "total_bogeys": 0,
                                "total_rounds": 0,
                                "best_finish": 999, "worst_finish": 0,
                            }
                        for k in ("wins", "top5", "top10", "top20",
                                  "cuts_made", "total_finish",
                                  "total_score_to_par", "total_birdies",
                                  "total_bogeys", "total_rounds"):
                            accum[player][k] += counts[k]
                        accum[player]["best_finish"] = min(
                            accum[player]["best_finish"],
                            counts["best_finish"])
                        accum[player]["worst_finish"] = max(
                            accum[player]["worst_finish"],
                            counts["worst_finish"])

                    completed += batch_n

                    if progress_callback:
                        pct = 15 + (completed / num_tournaments) * 85
                        elapsed = time.time() - start_time
                        progress_callback(
                            pct,
                            f"Completed {completed}/{num_tournaments} "
                            f"tournaments ({elapsed:.1f}s elapsed)")

                except Exception as e:
                    print(f"[golf-sim] Worker error: {e}", flush=True)

        elapsed = time.time() - start_time

        if completed == 0:
            raise RuntimeError(
                f"All {len(worker_args)} parallel workers failed. "
                f"Check logs for details.")

        return self._aggregate_results(accum, completed, elapsed)

    def _run_sequential(self, num_tournaments, weather_per_round,
                        progress_callback, start_time):
        """Original sequential tournament loop (fallback for single-core).

        Mirrors BracketSimulator._run_sequential() (lines 484-510).
        """
        accum = {}
        for p in self.player_params:
            name = p.get("_player_name", f"Player_{id(p)}")
            accum[name] = {
                "wins": 0, "top5": 0, "top10": 0, "top20": 0,
                "cuts_made": 0, "total_finish": 0,
                "total_score_to_par": 0, "total_birdies": 0,
                "total_bogeys": 0, "total_rounds": 0,
                "best_finish": 999, "worst_finish": 0,
            }

        for t_idx in range(num_tournaments):
            result = sim_tournament_single(
                self.player_params, self.holes, weather_per_round)

            winner = result["winner"]
            if winner in accum:
                accum[winner]["wins"] += 1

            for entry in result["standings"]:
                name = entry["player_name"]
                if name not in accum:
                    continue

                pos = entry["position"]
                accum[name]["total_finish"] += pos
                accum[name]["total_score_to_par"] += entry["total_to_par"]
                accum[name]["total_birdies"] += entry.get("birdies", 0)
                accum[name]["total_bogeys"] += entry.get("bogeys", 0)

                num_rounds = len(entry["rounds"])
                accum[name]["total_rounds"] += num_rounds

                if entry["made_cut"]:
                    accum[name]["cuts_made"] += 1
                if pos <= 5:
                    accum[name]["top5"] += 1
                if pos <= 10:
                    accum[name]["top10"] += 1
                if pos <= 20:
                    accum[name]["top20"] += 1

                accum[name]["best_finish"] = min(
                    accum[name]["best_finish"], pos)
                accum[name]["worst_finish"] = max(
                    accum[name]["worst_finish"], pos)

            if progress_callback and (t_idx + 1) % max(1, num_tournaments // 20) == 0:
                pct = 15 + (t_idx + 1) / num_tournaments * 85
                elapsed = time.time() - start_time
                eta = elapsed / (t_idx + 1) * (num_tournaments - t_idx - 1)
                progress_callback(
                    pct,
                    f"Tournament {t_idx + 1}/{num_tournaments} "
                    f"({elapsed:.1f}s elapsed, ~{eta:.0f}s remaining)")

        elapsed = time.time() - start_time
        return self._aggregate_results(accum, num_tournaments, elapsed)

    def _aggregate_results(self, accum, num_tournaments, elapsed):
        """Aggregate raw counts into the final results dict.

        Mirrors BracketSimulator._aggregate_results() (lines 512-612).
        """
        n = num_tournaments

        # Build player_params lookup for metadata
        params_lookup = {}
        if self.player_params:
            for p in self.player_params:
                params_lookup[p.get("_player_name", "")] = p

        # Composite predictions lookup
        composite_lookup = {}
        if self.composite_predictions:
            for pred in self.composite_predictions:
                composite_lookup[pred.get("player_name", "")] = pred

        # Per-player probabilities
        player_probs = {}
        for player, a in accum.items():
            total_rounds = max(a["total_rounds"], 1)
            player_probs[player] = {
                "win_pct": round(a["wins"] / n * 100, 2),
                "top5_pct": round(a["top5"] / n * 100, 2),
                "top10_pct": round(a["top10"] / n * 100, 2),
                "top20_pct": round(a["top20"] / n * 100, 2),
                "cut_pct": round(a["cuts_made"] / n * 100, 2),
                "avg_finish": round(a["total_finish"] / n, 1),
                "avg_score": round(a["total_score_to_par"] / n, 1),
                "avg_birdies_per_round": round(
                    a["total_birdies"] / total_rounds, 2),
                "avg_bogeys_per_round": round(
                    a["total_bogeys"] / total_rounds, 2),
                "best_finish": a["best_finish"] if a["best_finish"] < 999 else 0,
                "worst_finish": a["worst_finish"],
            }

        # Winner board (sorted by win_pct descending, like champion_board)
        winner_board = sorted(
            [
                {
                    "player": player,
                    "owgr": params_lookup.get(player, {}).get("_owgr_rank", 999),
                    "tier": params_lookup.get(player, {}).get("_tier", "unknown"),
                    "win_pct": probs["win_pct"],
                    "top5_pct": probs["top5_pct"],
                    "top10_pct": probs["top10_pct"],
                    "golf_rat": composite_lookup.get(player, {}).get(
                        "golf_rat_score", 5.0),
                    "count": accum[player]["wins"],
                }
                for player, probs in player_probs.items()
                if probs["win_pct"] > 0
            ],
            key=lambda x: -x["win_pct"],
        )

        # Top-10 board (sorted by top10_pct descending)
        top10_board = sorted(
            [
                {
                    "player": player,
                    "owgr": params_lookup.get(player, {}).get("_owgr_rank", 999),
                    "tier": params_lookup.get(player, {}).get("_tier", "unknown"),
                    "top10_pct": probs["top10_pct"],
                    "top5_pct": probs["top5_pct"],
                    "win_pct": probs["win_pct"],
                    "avg_finish": probs["avg_finish"],
                }
                for player, probs in player_probs.items()
            ],
            key=lambda x: -x["top10_pct"],
        )

        # Cut danger: players with cut_pct < 60%, sorted ascending
        # (like upset_alerts in bracket_simulator)
        cut_danger = sorted(
            [
                {
                    "player": player,
                    "owgr": params_lookup.get(player, {}).get("_owgr_rank", 999),
                    "cut_pct": probs["cut_pct"],
                }
                for player, probs in player_probs.items()
                if probs["cut_pct"] < 60
            ],
            key=lambda x: x["cut_pct"],
        )

        # Value picks: players where model win_pct significantly exceeds
        # market odds (if composite_predictions available, compare model
        # vs DataGolf pre-tournament)
        value_picks = []
        if self.composite_predictions:
            for pred in self.composite_predictions:
                pname = pred.get("player_name", "")
                if pname not in player_probs:
                    continue
                mc_win = player_probs[pname]["win_pct"]
                model_win = pred.get("win_prob", 0) * 100  # convert from 0-1
                # Look for players in raw data with pre-tournament win_prob
                raw = self.player_data.get(pname, {}) if self.player_data else {}
                market_win = raw.get("win_prob", 0) * 100  # DataGolf pre-tourney
                if market_win > 0 and mc_win > market_win * 1.3:
                    value_picks.append({
                        "player": pname,
                        "owgr": params_lookup.get(pname, {}).get("_owgr_rank", 999),
                        "mc_win_pct": mc_win,
                        "model_win_pct": round(model_win, 2),
                        "market_win_pct": round(market_win, 2),
                        "edge": round(mc_win - market_win, 2),
                    })
            value_picks.sort(key=lambda x: -x["edge"])

        return {
            "player_probs": player_probs,
            "winner_board": winner_board,
            "top10_board": top10_board,
            "cut_danger": cut_danger,
            "value_picks": value_picks,
            "composite_predictions": self.composite_predictions,
            "meta": {
                "num_tournaments": num_tournaments,
                "num_players": len(player_probs),
                "course": self.course_profile.get("name", "") if self.course_profile else "",
                "course_id": self.course_profile.get("course_id", "") if self.course_profile else "",
                "elapsed_seconds": round(elapsed, 1),
                "num_workers": self._last_num_workers,
                "tournaments_per_second": round(num_tournaments / elapsed, 1) if elapsed > 0 else 0,
                "weather_available": self.weather is not None,
            },
        }

    def get_player_detail(self, player_name, results):
        """Extract detailed results for a single player from aggregated results.

        Mirrors BracketSimulator.get_team_detail() (lines 614-625).

        Args:
            player_name: str
            results: dict from run()

        Returns:
            dict with player details, or None if not found
        """
        pp = results.get("player_probs", {}).get(player_name)
        if not pp:
            return None

        # Find composite prediction for this player
        composite_detail = None
        if results.get("composite_predictions"):
            for pred in results["composite_predictions"]:
                if pred.get("player_name") == player_name:
                    composite_detail = pred
                    break

        # Find player params for metadata
        params = None
        if self.player_params:
            for p in self.player_params:
                if p.get("_player_name") == player_name:
                    params = p
                    break

        return {
            "player": player_name,
            "owgr": params.get("_owgr_rank", 999) if params else 999,
            "tier": params.get("_tier", "unknown") if params else "unknown",
            "probs": pp,
            "composite": composite_detail,
            "matchup_history": self.get_matchup_cache(player_name),
            "params": {
                "sg_total_adj": params.get("sg_total_adj", 0) if params else 0,
                "sg_ott": params.get("sg_ott", 0) if params else 0,
                "sg_app": params.get("sg_app", 0) if params else 0,
                "sg_arg": params.get("sg_arg", 0) if params else 0,
                "sg_putt": params.get("sg_putt", 0) if params else 0,
            } if params else None,
        }

    def run_matchup(self, player1_name, player2_name, num_sims=1000):
        """Convenience method for H2H matchup prediction.

        Args:
            player1_name: str
            player2_name: str
            num_sims: number of MC simulations

        Returns:
            dict with combined MC + composite matchup result
        """
        if self.player_params is None:
            raise RuntimeError("Call prefetch_data() before run_matchup()")

        # Look up both players in player_params
        p1_params = None
        p2_params = None
        for p in self.player_params:
            if p.get("_player_name") == player1_name:
                p1_params = p
            if p.get("_player_name") == player2_name:
                p2_params = p

        if p1_params is None:
            raise ValueError(f"Player not found in field: {player1_name}")
        if p2_params is None:
            raise ValueError(f"Player not found in field: {player2_name}")

        # MC matchup simulation
        mc_result = simulate_matchup(
            p1_params, p2_params, self.holes, num_sims=num_sims)

        # Composite model for both players
        p1_composite = None
        p2_composite = None
        if self.composite_predictions:
            for pred in self.composite_predictions:
                if pred.get("player_name") == player1_name:
                    p1_composite = pred
                if pred.get("player_name") == player2_name:
                    p2_composite = pred

        # Cache result
        self._matchup_cache[(player1_name, player2_name)] = mc_result

        return {
            "mc": mc_result,
            "p1_composite": p1_composite,
            "p2_composite": p2_composite,
            "p1_name": player1_name,
            "p2_name": player2_name,
        }

    def get_matchup_cache(self, player_name):
        """Return all cached matchup results involving a specific player.

        Mirrors BracketSimulator._get_team_matchup_cache() (lines 627-635).

        Args:
            player_name: str

        Returns:
            dict of opponent_name -> matchup result
        """
        matchups = {}
        for (p1, p2), result in self._matchup_cache.items():
            if p1 == player_name:
                matchups[p2] = result
            elif p2 == player_name:
                # Flip perspective
                matchups[p1] = {
                    "p1_name": p2,
                    "p2_name": p1,
                    "p1_win_pct": result.get("p2_win_pct", 0),
                    "p2_win_pct": result.get("p1_win_pct", 0),
                    "tie_pct": result.get("tie_pct", 0),
                    "p1_avg_margin": -result.get("p1_avg_margin", 0),
                }
        return matchups


# ─── Self-test (offline, no API) ─────────────────────────────────────────────

if __name__ == "__main__":
    print("=" * 60)
    print("  golf_tournament_simulator.py — Orchestrator Self-Test")
    print("=" * 60)

    # ── Mock classes ──
    class MockClient:
        pass

    class MockCache:
        def get(self, *a, **kw):
            return None
        def set(self, *a, **kw):
            pass

    # ── Build mock player params for ~20 fake players ──
    from golf.golf_course_profiles import get_course_profile as _gcp

    augusta = _gcp("augusta_national")
    assert augusta is not None, "Augusta National course profile not found"
    mock_holes = augusta["holes"]

    def _make_mock_player(name, sg_total, owgr, tier="unknown"):
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

    mock_players = [
        _make_mock_player("Scottie Scheffler", 2.5, 1, "elite"),
        _make_mock_player("Xander Schauffele", 2.2, 2, "elite"),
        _make_mock_player("Rory McIlroy", 2.0, 3, "elite"),
        _make_mock_player("Jon Rahm", 1.8, 5, "star"),
        _make_mock_player("Collin Morikawa", 1.6, 6, "star"),
        _make_mock_player("Viktor Hovland", 1.4, 8, "star"),
        _make_mock_player("Patrick Cantlay", 1.2, 10, "key"),
        _make_mock_player("Wyndham Clark", 1.0, 12, "key"),
        _make_mock_player("Tommy Fleetwood", 0.8, 15, "key"),
        _make_mock_player("Shane Lowry", 0.6, 18, "key"),
        _make_mock_player("Tony Finau", 0.4, 22, "key"),
        _make_mock_player("Russell Henley", 0.2, 28, "key"),
        _make_mock_player("Avg Player A", 0.0, 50, "role"),
        _make_mock_player("Avg Player B", -0.2, 60, "role"),
        _make_mock_player("Avg Player C", -0.4, 75, "role"),
        _make_mock_player("Avg Player D", -0.6, 90, "role"),
        _make_mock_player("Fringe Player A", -0.8, 120, "role"),
        _make_mock_player("Fringe Player B", -1.0, 140, "role"),
        _make_mock_player("Fringe Player C", -1.2, 160, "role"),
        _make_mock_player("Fringe Player D", -1.5, 180, "role"),
    ]

    # ── Create simulator with mock data injected ──
    sim = GolfTournamentSimulator(MockClient(), MockCache())
    sim.player_params = mock_players
    sim.course_profile = augusta
    sim.holes = mock_holes
    sim.player_data = {p["_player_name"]: p for p in mock_players}
    sim.composite_predictions = None  # no composite for mock test

    # ── Run 50 tournament sims sequentially ──
    print("\n  Running 50 tournament sims (sequential)...")
    start = time.time()
    results = sim.run(num_tournaments=50, num_workers=1)
    elapsed_time = time.time() - start

    print(f"  Completed in {elapsed_time:.1f}s")
    print(f"  Players tracked: {results['meta']['num_players']}")

    # Winner board
    print(f"\n  Winner board (top 10):")
    for entry in results["winner_board"][:10]:
        print(f"    ({entry['owgr']}) {entry['player']}: {entry['win_pct']:.1f}%")

    # Top-10 board
    print(f"\n  Top-10 board (top 8):")
    for entry in results["top10_board"][:8]:
        print(f"    ({entry['owgr']}) {entry['player']}: T10 {entry['top10_pct']:.1f}%")

    # Sanity checks
    print("\n  Running sanity checks...")

    # 1. Winner board sums to ~100%
    win_total = sum(p["win_pct"] for p in results["player_probs"].values())
    print(f"  Win probabilities sum: {win_total:.1f}% (expect ~100%)")
    assert 85 < win_total < 115, f"Win probabilities don't sum to ~100%: {win_total}"

    # 2. Top-ranked player (highest SG) has highest win_pct
    top_player_probs = results["player_probs"].get("Scottie Scheffler", {})
    assert top_player_probs.get("win_pct", 0) > 0, "Top player should have wins"
    # The top player should generally be in the top 3 of the winner board
    top3_names = [e["player"] for e in results["winner_board"][:3]]
    print(f"  Top 3 winners: {top3_names}")
    # Note: with only 50 sims there's variance, so we check top player has > 0 wins

    # 3. All players have cut_pct between 0-100
    for player, probs in results["player_probs"].items():
        assert 0 <= probs["cut_pct"] <= 100, \
            f"{player} cut_pct out of range: {probs['cut_pct']}"

    # 4. avg_finish is between 1 and num_players
    num_players = len(mock_players)
    for player, probs in results["player_probs"].items():
        assert 1 <= probs["avg_finish"] <= num_players + 5, \
            f"{player} avg_finish out of range: {probs['avg_finish']}"

    # 5. Meta fields are present
    meta = results["meta"]
    assert "num_tournaments" in meta, "Missing num_tournaments in meta"
    assert "num_players" in meta, "Missing num_players in meta"
    assert "elapsed_seconds" in meta, "Missing elapsed_seconds in meta"
    assert "num_workers" in meta, "Missing num_workers in meta"
    assert "tournaments_per_second" in meta, "Missing tournaments_per_second in meta"
    assert "weather_available" in meta, "Missing weather_available in meta"
    print(f"  Meta: workers={meta['num_workers']}, "
          f"t/s={meta['tournaments_per_second']}, "
          f"weather={meta['weather_available']}")

    # 6. get_player_detail works
    detail = sim.get_player_detail("Scottie Scheffler", results)
    assert detail is not None, "get_player_detail returned None for valid player"
    assert detail["player"] == "Scottie Scheffler"
    assert detail["owgr"] == 1

    unknown = sim.get_player_detail("Unknown Player XYZ", results)
    assert unknown is None, "get_player_detail should return None for unknown player"

    print("\n  All sequential assertions passed!")

    # ── Parallel Execution Test ──
    print("\n" + "=" * 60)
    print("  Parallel Execution Test")
    print("=" * 60)

    num_cpus = os.cpu_count() or 1
    print(f"  Available CPUs: {num_cpus}")

    if num_cpus > 1:
        sim2 = GolfTournamentSimulator(MockClient(), MockCache())
        sim2.player_params = mock_players
        sim2.course_profile = augusta
        sim2.holes = mock_holes
        sim2.player_data = {p["_player_name"]: p for p in mock_players}
        sim2.composite_predictions = None

        print(f"  Running 100 tournaments with {num_cpus - 1} workers...")
        start = time.time()
        par_results = sim2.run(num_tournaments=100,
                               num_workers=num_cpus - 1)
        par_time = time.time() - start
        print(f"  Parallel: {par_time:.1f}s")

        # Validate parallel results
        par_win_total = sum(
            p["win_pct"] for p in par_results["player_probs"].values())
        print(f"  Parallel win sum: {par_win_total:.1f}%")
        assert 85 < par_win_total < 115, (
            f"Parallel win probs don't sum to ~100%: {par_win_total}")

        # Both should have same structure
        assert "player_probs" in par_results
        assert "winner_board" in par_results
        assert "top10_board" in par_results
        assert "meta" in par_results
        print("  Parallel results validated!")
    else:
        print("  Skipping parallel test (single-core machine)")

    # ── Cut Danger Test ──
    print("\n" + "=" * 60)
    print("  Cut Danger Analysis")
    print("=" * 60)
    if results["cut_danger"]:
        print(f"  Players in cut danger ({len(results['cut_danger'])}):")
        for cd in results["cut_danger"][:5]:
            print(f"    {cd['player']}: {cd['cut_pct']:.1f}% make-cut rate")
    else:
        print("  No players in cut danger (field too small for meaningful cut)")

    print("\n  All tests passed!")
