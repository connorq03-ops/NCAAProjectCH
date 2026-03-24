"""
bracket_simulator.py - NCAA Tournament Bracket Simulator

Orchestrates full tournament simulations:
  First Four → R64 → R32 → Sweet 16 → Elite 8 → Final Four → Championship

Uses:
  - bracket_data.py for bracket structure
  - matchup_params.py for KenPom → sim parameter conversion
  - mc_engine.py for possession-level Monte Carlo simulation

Architecture:
  1. Prefetch all KenPom data (5 bulk API calls)
  2. For each tournament sim:
     a. Resolve First Four (4 play-in games)
     b. Simulate each region (R64 → E8)
     c. Simulate Final Four and Championship
  3. Aggregate results across N tournaments
  4. Win probabilities cached per unique matchup for efficiency
"""

import os
import pickle
import random
import time
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor, as_completed

from bracket_data import (
    FIRST_FOUR, FINAL_FOUR_PAIRINGS, REGIONS,
    SEED_MAP,
    get_bracket_with_first_four_resolved,
)
from matchup_params import (
    prefetch_all_team_data, build_matchup_params,
    flip_matchup_params, matchup_cache_key,
)
from mc_engine import simulate_game


# ─── Top-level helpers (must be picklable for ProcessPoolExecutor) ───────────

def _run_single_sim(params, num_sims):
    """Top-level function for ProcessPoolExecutor (must be picklable)."""
    return simulate_game(params, num_sims=num_sims)


def _run_tournament_batch(args):
    """Run a batch of tournament simulations in a worker process.

    Args:
        args: tuple of (team_data, precomputed_win_probs, precomputed_params,
               num_tournaments, num_sims_per_game, worker_seed)

    Returns:
        tuple of (round_counts_dict, champion_counts_dict, num_completed,
                  new_win_probs_dict)
    """
    (team_data, win_prob_cache, params_cache,
     num_tournaments, num_sims_per_game, worker_seed) = args

    # Seed random per-worker to avoid correlated results
    random.seed(worker_seed)

    # Create a lightweight simulator with pre-warmed caches
    sim = BracketSimulator.__new__(BracketSimulator)
    sim.team_data = team_data
    sim._win_prob_cache = dict(win_prob_cache)
    sim._params_cache = dict(params_cache)
    sim.year = 2026

    round_counts = defaultdict(lambda: defaultdict(int))
    champion_counts = defaultdict(int)

    for _ in range(num_tournaments):
        result = sim._sim_one_tournament(num_sims_per_game)
        for round_name, teams in result.items():
            for team in teams:
                round_counts[team][round_name] += 1
        for team in result.get("Champion", set()):
            champion_counts[team] += 1

    # Convert defaultdicts to regular dicts for pickling
    return (
        {team: dict(counts) for team, counts in round_counts.items()},
        dict(champion_counts),
        num_tournaments,
        {k: v for k, v in sim._win_prob_cache.items() if k not in win_prob_cache},
    )


class BracketSimulator:
    """Full NCAA tournament bracket simulator."""

    def __init__(self, client, cache, year=2026):
        """
        Args:
            client: KenpomClient instance
            cache: SQLiteCache instance
            year: Season year (default 2026)
        """
        self.client = client
        self.cache = cache
        self.year = year
        self.team_data = None
        self._win_prob_cache = {}  # (team1, team2) → float
        self._params_cache = {}   # matchup_cache_key → params dict

    def prefetch_data(self):
        """Bulk fetch all KenPom data for all teams. Call once before run()."""
        self.team_data = prefetch_all_team_data(self.client, self.cache, self.year)
        self._win_prob_cache.clear()
        self._params_cache.clear()
        return len(self.team_data)

    def _get_matchup_params(self, team1, team2):
        """Get or compute matchup params for team1 vs team2 (cached)."""
        key = matchup_cache_key(team1, team2)
        if key not in self._params_cache:
            # Always build with alphabetically-first team as t1
            sorted_teams = sorted([team1, team2])
            params = build_matchup_params(
                sorted_teams[0], sorted_teams[1],
                self.team_data, hca1=0, hca2=0)
            self._params_cache[key] = params
        params = self._params_cache[key]
        # If team1 is the alphabetically-second team, flip perspective
        sorted_teams = sorted([team1, team2])
        if team1 != sorted_teams[0]:
            return flip_matchup_params(params)
        return params

    def _adaptive_num_sims(self, team1, team2, base_num_sims):
        """Compute adaptive sim count based on expected closeness.

        Blowout matchups (large seed gap) need fewer sims to converge.
        Close matchups need more sims for accurate win probabilities.
        """
        s1 = SEED_MAP.get(team1, 8)
        s2 = SEED_MAP.get(team2, 8)
        seed_gap = abs(s1 - s2)

        if seed_gap >= 12:      # 1v16, 2v15 — very lopsided
            return max(150, base_num_sims // 3)
        elif seed_gap >= 8:     # 1v12, 3v14 — still lopsided
            return max(200, base_num_sims // 2)
        elif seed_gap >= 4:     # 4v8, 5v9 — moderate gap
            return max(300, int(base_num_sims * 0.7))
        else:                   # Close matchups — full precision
            return base_num_sims

    def _get_win_prob(self, team1, team2, num_sims=500):
        """Get win probability for team1 beating team2 (cached).

        First call runs MC simulation; subsequent calls return cached result.
        Uses adaptive sim count based on seed gap.
        """
        # Check cache both ways
        if (team1, team2) in self._win_prob_cache:
            return self._win_prob_cache[(team1, team2)]
        if (team2, team1) in self._win_prob_cache:
            return 1.0 - self._win_prob_cache[(team2, team1)]

        # Use adaptive sim count
        adaptive_sims = self._adaptive_num_sims(team1, team2, num_sims)

        # Compute via MC simulation
        params = self._get_matchup_params(team1, team2)
        result = simulate_game(params, num_sims=adaptive_sims)
        prob = result["t1_win_prob"]

        self._win_prob_cache[(team1, team2)] = prob
        return prob

    def _sim_game(self, team1, team2, num_sims=500):
        """Simulate a single game, return winner using cached win probability."""
        prob = self._get_win_prob(team1, team2, num_sims)
        return team1 if random.random() < prob else team2

    def _sim_region(self, matchups, num_sims):
        """Simulate one region from Round of 64 through Elite 8.

        Args:
            matchups: list of 8 (team1, team2) tuples in bracket order
            num_sims: MC sims per game for win probability

        Returns:
            dict with:
              r64_winners: list of 8 R64 winners
              r32_winners: list of 4 R32 winners
              s16_winners: list of 2 S16 winners
              e8_winner: region champion
              games: list of (team1, team2, winner) for all games
        """
        games = []

        # Round of 64: 8 matchups → 8 winners
        r64_winners = []
        for t1, t2 in matchups:
            winner = self._sim_game(t1, t2, num_sims)
            r64_winners.append(winner)
            games.append((t1, t2, winner))

        # Round of 32: adjacent pairs → 4 winners
        r32_winners = []
        for i in range(0, 8, 2):
            winner = self._sim_game(r64_winners[i], r64_winners[i + 1], num_sims)
            r32_winners.append(winner)
            games.append((r64_winners[i], r64_winners[i + 1], winner))

        # Sweet 16: adjacent pairs → 2 winners
        s16_winners = []
        for i in range(0, 4, 2):
            winner = self._sim_game(r32_winners[i], r32_winners[i + 1], num_sims)
            s16_winners.append(winner)
            games.append((r32_winners[i], r32_winners[i + 1], winner))

        # Elite 8: final pair → region champion
        e8_winner = self._sim_game(s16_winners[0], s16_winners[1], num_sims)
        games.append((s16_winners[0], s16_winners[1], e8_winner))

        return {
            "r64_winners": r64_winners,
            "r32_winners": r32_winners,
            "s16_winners": s16_winners,
            "e8_winner": e8_winner,
            "games": games,
        }

    def _sim_one_tournament(self, num_sims):
        """Simulate one complete tournament. Returns per-team advancement dict."""
        # Track which teams reach each round
        advancement = defaultdict(set)  # round_name → set of teams

        # First Four
        ff_winners = {}
        for i, ff in enumerate(FIRST_FOUR):
            winner = self._sim_game(ff["team1"], ff["team2"], num_sims)
            ff_winners[i] = winner
            advancement["First Four"].add(winner)

        # Resolve bracket with First Four winners
        bracket = get_bracket_with_first_four_resolved(ff_winners)

        # All 64 teams in main bracket make Round of 64
        for matchups in bracket.values():
            for t1, t2 in matchups:
                advancement["Round of 64"].add(t1)
                advancement["Round of 64"].add(t2)

        # Simulate each region
        region_champions = {}
        for region_name, matchups in bracket.items():
            result = self._sim_region(matchups, num_sims)

            for w in result["r64_winners"]:
                advancement["Round of 32"].add(w)
            for w in result["r32_winners"]:
                advancement["Sweet 16"].add(w)
            for w in result["s16_winners"]:
                advancement["Elite 8"].add(w)
            advancement["Final Four"].add(result["e8_winner"])
            region_champions[region_name] = result["e8_winner"]

        # Final Four
        f4_winners = []
        for r1_name, r2_name in FINAL_FOUR_PAIRINGS:
            t1 = region_champions[r1_name]
            t2 = region_champions[r2_name]
            winner = self._sim_game(t1, t2, num_sims)
            f4_winners.append(winner)
            advancement["Championship"].add(winner)

        # Championship
        champion = self._sim_game(f4_winners[0], f4_winners[1], num_sims)
        advancement["Champion"].add(champion)

        return dict(advancement)

    def _precompute_r64_win_probs(self, num_sims):
        """Pre-compute win probabilities for all 32 R64 matchups in parallel.

        Also pre-computes First Four matchups (4 games).
        These are fixed matchups that occur in every tournament simulation.
        """
        # Collect all fixed matchups
        fixed_matchups = []

        # First Four
        for ff in FIRST_FOUR:
            fixed_matchups.append((ff["team1"], ff["team2"]))

        # R64 (skip None slots — those are First Four placeholders)
        for region_name, matchups in REGIONS.items():
            for t1, t2 in matchups:
                if t1 and t2:
                    fixed_matchups.append((t1, t2))

        # Build params for all matchups
        matchup_params_list = []
        for t1, t2 in fixed_matchups:
            key = matchup_cache_key(t1, t2)
            if key not in self._params_cache:
                sorted_teams = sorted([t1, t2])
                params = build_matchup_params(
                    sorted_teams[0], sorted_teams[1],
                    self.team_data, hca1=0, hca2=0)
                self._params_cache[key] = params
            matchup_params_list.append(
                (t1, t2, self._params_cache[matchup_cache_key(t1, t2)], num_sims))

        # Run simulations in parallel
        num_workers = min(os.cpu_count() or 1, len(matchup_params_list), 8)

        if num_workers <= 1:
            # Single-core fallback: sequential
            for t1, t2, params, ns in matchup_params_list:
                sorted_teams = sorted([t1, t2])
                if t1 != sorted_teams[0]:
                    result = simulate_game(flip_matchup_params(params), num_sims=ns)
                else:
                    result = simulate_game(params, num_sims=ns)
                self._win_prob_cache[(t1, t2)] = result["t1_win_prob"]
            return

        with ProcessPoolExecutor(max_workers=num_workers) as executor:
            futures = {}
            for t1, t2, params, ns in matchup_params_list:
                sorted_teams = sorted([t1, t2])
                if t1 != sorted_teams[0]:
                    sim_params = flip_matchup_params(params)
                else:
                    sim_params = params
                future = executor.submit(_run_single_sim, sim_params, ns)
                futures[future] = (t1, t2)

            for future in as_completed(futures):
                t1, t2 = futures[future]
                try:
                    result = future.result(timeout=300)
                    self._win_prob_cache[(t1, t2)] = result["t1_win_prob"]
                except Exception as e:
                    # Fallback: use seed-based estimate
                    s1 = SEED_MAP.get(t1, 8)
                    s2 = SEED_MAP.get(t2, 8)
                    gap = s2 - s1
                    self._win_prob_cache[(t1, t2)] = max(0.05, min(0.95, 0.5 + gap * 0.03))

    def run(self, num_tournaments=1000, num_sims_per_game=500,
            progress_callback=None, num_workers=None):
        """Run N tournament simulations and aggregate results.

        Args:
            num_tournaments: Number of full tournaments to simulate
            num_sims_per_game: MC sims per individual game (for win prob)
            progress_callback: Optional fn(pct, msg) called periodically
            num_workers: Number of parallel workers (default: cpu_count - 1, min 1)

        Returns:
            dict with per-team probabilities, champion odds, upset analysis, etc.
        """
        if self.team_data is None:
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

        # Phase 1: Pre-compute R64 win probabilities in parallel
        if progress_callback:
            progress_callback(0, "Pre-computing Round of 64 matchups...")
        self._precompute_r64_win_probs(num_sims_per_game)

        precompute_elapsed = time.time() - start_time
        self._precompute_time = round(precompute_elapsed, 2)
        if progress_callback:
            progress_callback(
                15,
                f"R64 pre-computed in {precompute_elapsed:.1f}s. "
                f"Starting tournament batches...")

        # Phase 2: Run tournament batches
        if num_workers <= 1:
            return self._run_sequential(
                num_tournaments, num_sims_per_game,
                progress_callback, start_time)

        # Verify team_data is picklable before launching workers
        try:
            pickle.dumps(self.team_data)
        except Exception as e:
            print(f"[bracket-sim] team_data not picklable, "
                  f"falling back to sequential: {e}", flush=True)
            return self._run_sequential(
                num_tournaments, num_sims_per_game,
                progress_callback, start_time)

        # Split tournaments across workers
        batch_sizes = []
        base_batch = num_tournaments // num_workers
        remainder = num_tournaments % num_workers
        for i in range(num_workers):
            batch_sizes.append(base_batch + (1 if i < remainder else 0))

        # Prepare worker args
        worker_args = []
        for i, batch_size in enumerate(batch_sizes):
            if batch_size == 0:
                continue
            worker_args.append((
                self.team_data,
                dict(self._win_prob_cache),
                dict(self._params_cache),
                batch_size,
                num_sims_per_game,
                random.randint(0, 2**32 - 1),
            ))

        # Launch workers
        round_counts = defaultdict(lambda: defaultdict(int))
        champion_counts = defaultdict(int)
        completed = 0

        with ProcessPoolExecutor(max_workers=num_workers) as executor:
            futures = {
                executor.submit(_run_tournament_batch, args): i
                for i, args in enumerate(worker_args)
            }

            for future in as_completed(futures):
                try:
                    batch_rounds, batch_champs, batch_n, new_probs = (
                        future.result(timeout=300))

                    # Merge round counts
                    for team, counts in batch_rounds.items():
                        for round_name, count in counts.items():
                            round_counts[team][round_name] += count

                    # Merge champion counts
                    for team, count in batch_champs.items():
                        champion_counts[team] += count

                    # Merge any new win probabilities back into main cache
                    for k, v in new_probs.items():
                        if k not in self._win_prob_cache:
                            self._win_prob_cache[k] = v

                    completed += batch_n

                    if progress_callback:
                        pct = 15 + (completed / num_tournaments) * 85
                        elapsed = time.time() - start_time
                        progress_callback(
                            pct,
                            f"Completed {completed}/{num_tournaments} "
                            f"tournaments ({elapsed:.1f}s elapsed)")

                except Exception as e:
                    print(f"[bracket-sim] Worker error: {e}", flush=True)

        elapsed = time.time() - start_time

        if completed == 0:
            raise RuntimeError(
                f"All {len(worker_args)} parallel workers failed. "
                f"Check logs for details.")

        return self._aggregate_results(
            round_counts, champion_counts, completed, elapsed,
            num_sims_per_game)

    def _run_sequential(self, num_tournaments, num_sims_per_game,
                        progress_callback, start_time):
        """Original sequential tournament loop (fallback for single-core)."""
        round_counts = defaultdict(lambda: defaultdict(int))
        champion_counts = defaultdict(int)

        for t_idx in range(num_tournaments):
            result = self._sim_one_tournament(num_sims_per_game)
            for round_name, teams in result.items():
                for team in teams:
                    round_counts[team][round_name] += 1
            for team in result.get("Champion", set()):
                champion_counts[team] += 1

            if progress_callback and (t_idx + 1) % max(1, num_tournaments // 20) == 0:
                pct = 15 + (t_idx + 1) / num_tournaments * 85
                elapsed = time.time() - start_time
                eta = elapsed / (t_idx + 1) * (num_tournaments - t_idx - 1)
                progress_callback(
                    pct,
                    f"Tournament {t_idx + 1}/{num_tournaments} "
                    f"({elapsed:.1f}s elapsed, ~{eta:.0f}s remaining)")

        elapsed = time.time() - start_time
        return self._aggregate_results(
            round_counts, champion_counts, num_tournaments, elapsed,
            num_sims_per_game)

    def _aggregate_results(self, round_counts, champion_counts,
                           num_tournaments, elapsed, num_sims_per_game):
        """Aggregate raw counts into the final results dict."""
        n = num_tournaments
        rounds_ordered = [
            "Round of 64", "Round of 32", "Sweet 16",
            "Elite 8", "Final Four", "Championship", "Champion"
        ]

        # Per-team probabilities
        team_probs = {}
        for team, counts in round_counts.items():
            seed = SEED_MAP.get(team, 0)
            team_probs[team] = {
                "seed": seed,
                "rounds": {
                    r: round(counts.get(r, 0) / n * 100, 2)
                    for r in rounds_ordered
                },
                "champion_prob": round(champion_counts.get(team, 0) / n * 100, 3),
            }

        # Champion leaderboard (sorted by probability)
        champion_board = sorted(
            [{"team": t, "seed": SEED_MAP.get(t, 0),
              "prob": round(c / n * 100, 2), "count": c}
             for t, c in champion_counts.items() if c > 0],
            key=lambda x: -x["prob"])

        # Final Four probabilities (sorted)
        f4_probs = sorted(
            [{"team": t, "seed": SEED_MAP.get(t, 0),
              "prob": round(counts.get("Final Four", 0) / n * 100, 2)}
             for t, counts in round_counts.items()
             if counts.get("Final Four", 0) > 0],
            key=lambda x: -x["prob"])

        # Seed analysis
        seed_champion_counts = defaultdict(int)
        seed_f4_counts = defaultdict(int)
        for team, counts in round_counts.items():
            seed = SEED_MAP.get(team, 0)
            seed_champion_counts[seed] += champion_counts.get(team, 0)
            seed_f4_counts[seed] += counts.get("Final Four", 0)

        seed_analysis = {}
        for seed in sorted(set(SEED_MAP.values())):
            seed_analysis[seed] = {
                "champion_prob": round(seed_champion_counts.get(seed, 0) / n * 100, 2),
                "f4_prob": round(seed_f4_counts.get(seed, 0) / n * 100, 2),
            }

        # Upset alerts: lower seeds (higher number) with high advancement rates
        upset_alerts = []
        for team, data in team_probs.items():
            seed = data["seed"]
            if seed >= 7 and data["rounds"].get("Sweet 16", 0) > 15:
                upset_alerts.append({
                    "team": team, "seed": seed,
                    "s16_prob": data["rounds"]["Sweet 16"],
                    "e8_prob": data["rounds"].get("Elite 8", 0),
                })
        upset_alerts.sort(key=lambda x: (-x["seed"], -x["s16_prob"]))

        # Cinderella watch: seeds 11+ making deep runs
        cinderellas = []
        for team, data in team_probs.items():
            seed = data["seed"]
            if seed >= 11 and data["rounds"].get("Round of 32", 0) > 20:
                cinderellas.append({
                    "team": team, "seed": seed,
                    "r32_prob": data["rounds"]["Round of 32"],
                    "s16_prob": data["rounds"].get("Sweet 16", 0),
                })
        cinderellas.sort(key=lambda x: (-x["seed"], -x["s16_prob"]))

        # Cache stats
        cache_stats = {
            "matchup_params_cached": len(self._params_cache),
            "win_probs_cached": len(self._win_prob_cache),
        }

        return {
            "team_probs": team_probs,
            "champion_board": champion_board,
            "final_four_probs": f4_probs,
            "seed_analysis": seed_analysis,
            "upset_alerts": upset_alerts,
            "cinderellas": cinderellas,
            "meta": {
                "num_tournaments": num_tournaments,
                "num_sims_per_game": num_sims_per_game,
                "elapsed_seconds": round(elapsed, 1),
                "total_teams": len(team_probs),
                "cache": cache_stats,
                "num_workers": getattr(self, '_last_num_workers', 1),
                "precompute_time": getattr(self, '_precompute_time', 0),
                "tournaments_per_second": round(num_tournaments / elapsed, 1) if elapsed > 0 else 0,
                "adaptive_sims": True,
            },
        }

    def get_team_detail(self, team_name, results):
        """Extract detailed results for a single team from aggregated results."""
        tp = results.get("team_probs", {}).get(team_name)
        if not tp:
            return None
        return {
            "team": team_name,
            "seed": tp["seed"],
            "rounds": tp["rounds"],
            "champion_prob": tp["champion_prob"],
            "matchup_history": self._get_team_matchup_cache(team_name),
        }

    def _get_team_matchup_cache(self, team_name):
        """Return cached win probabilities involving a specific team."""
        matchups = {}
        for (t1, t2), prob in self._win_prob_cache.items():
            if t1 == team_name:
                matchups[t2] = round(prob * 100, 1)
            elif t2 == team_name:
                matchups[t1] = round((1 - prob) * 100, 1)
        return matchups


# ─── Self-test (offline, no API) ─────────────────────────────────────────────

if __name__ == "__main__":
    print("=" * 60)
    print("  bracket_simulator.py — Orchestrator Self-Test")
    print("=" * 60)

    # Test bracket progression logic with mock data
    print("\nTesting bracket progression logic (mock)...")

    # Create a mock simulator with fake win probs
    class MockClient:
        pass

    class MockCache:
        def get(self, *a, **kw):
            return None
        def set(self, *a, **kw):
            pass

    sim = BracketSimulator(MockClient(), MockCache())

    # Instead of real data, inject mock team_data and override _get_win_prob
    # to always favor the higher seed
    from bracket_data import get_all_team_names
    mock_teams = get_all_team_names()
    sim.team_data = {t: {} for t in mock_teams}

    def mock_win_prob(t1, t2, num_sims=500):
        """Higher seed (lower number) wins with probability based on seed gap."""
        s1 = SEED_MAP.get(t1, 8)
        s2 = SEED_MAP.get(t2, 8)
        if s1 == s2:
            return 0.5
        # Larger gap → higher probability for better seed
        gap = s2 - s1  # positive if t1 is better seed
        prob = 0.5 + gap * 0.03  # +3% per seed difference
        return max(0.05, min(0.95, prob))

    sim._get_win_prob = mock_win_prob

    print("  Running 50 tournament sims (seed-based mock, sequential)...")
    start = time.time()
    results = sim.run(num_tournaments=50, num_sims_per_game=100, num_workers=1)
    elapsed = time.time() - start

    print(f"  Completed in {elapsed:.1f}s")
    print(f"  Teams tracked: {results['meta']['total_teams']}")
    print(f"  Champion board (top 10):")
    for entry in results["champion_board"][:10]:
        print(f"    ({entry['seed']}) {entry['team']}: {entry['prob']:.1f}%")

    print(f"\n  Final Four probabilities (top 8):")
    for entry in results["final_four_probs"][:8]:
        print(f"    ({entry['seed']}) {entry['team']}: {entry['prob']:.1f}%")

    print(f"\n  Seed analysis:")
    for seed in [1, 2, 3, 4, 5, 6, 7, 8]:
        sa = results["seed_analysis"].get(seed, {})
        print(f"    Seed {seed}: Champion {sa.get('champion_prob', 0):.1f}%, F4 {sa.get('f4_prob', 0):.1f}%")

    if results["upset_alerts"]:
        print(f"\n  Upset alerts:")
        for ua in results["upset_alerts"][:5]:
            print(f"    ({ua['seed']}) {ua['team']}: S16 {ua['s16_prob']:.1f}%")
    else:
        print("\n  No upset alerts (expected with seed-based mock)")

    # Sanity checks
    champ_total = sum(e["prob"] for e in results["champion_board"])
    print(f"\n  Champion probabilities sum: {champ_total:.1f}% (expect ~100%)")
    assert 95 < champ_total < 105, f"Champion probabilities don't sum to ~100%: {champ_total}"

    # Check that 1-seeds dominate in seed-based mock
    top_seeds = [e for e in results["champion_board"] if e["seed"] <= 2]
    top_seed_prob = sum(e["prob"] for e in top_seeds)
    print(f"  1/2-seed champion share: {top_seed_prob:.1f}% (expect >50% in seed-based mock)")

    # Check meta includes new parallelism stats
    meta = results["meta"]
    assert "num_workers" in meta, "Missing num_workers in meta"
    assert "precompute_time" in meta, "Missing precompute_time in meta"
    assert "tournaments_per_second" in meta, "Missing tournaments_per_second in meta"
    assert "adaptive_sims" in meta, "Missing adaptive_sims in meta"
    print(f"  Meta stats: workers={meta['num_workers']}, "
          f"precompute={meta['precompute_time']}s, "
          f"t/s={meta['tournaments_per_second']}")

    print("\n  All assertions passed!")

    # ── Parallel Execution Test ──
    print("\n" + "=" * 60)
    print("  Parallel Execution Test")
    print("=" * 60)

    num_cpus = os.cpu_count() or 1
    print(f"  Available CPUs: {num_cpus}")

    sim2 = BracketSimulator(MockClient(), MockCache())
    sim2.team_data = {t: {} for t in mock_teams}
    sim2._get_win_prob = mock_win_prob

    print("  Running 100 tournaments sequentially...")
    start = time.time()
    seq_results = sim2.run(num_tournaments=100, num_sims_per_game=100,
                           num_workers=1)
    seq_time = time.time() - start
    print(f"  Sequential: {seq_time:.1f}s")

    if num_cpus > 1:
        sim3 = BracketSimulator(MockClient(), MockCache())
        sim3.team_data = {t: {} for t in mock_teams}
        sim3._get_win_prob = mock_win_prob

        print(f"  Running 100 tournaments with {num_cpus - 1} workers...")
        start = time.time()
        par_results = sim3.run(num_tournaments=100, num_sims_per_game=100,
                               num_workers=num_cpus - 1)
        par_time = time.time() - start
        print(f"  Parallel: {par_time:.1f}s")

        # Both should produce valid results
        seq_champ = sum(e["prob"] for e in seq_results["champion_board"])
        par_champ = sum(e["prob"] for e in par_results["champion_board"])
        print(f"  Sequential champion sum: {seq_champ:.1f}%")
        print(f"  Parallel champion sum: {par_champ:.1f}%")
        assert 95 < par_champ < 105, (
            f"Parallel champion probs don't sum to ~100%: {par_champ}")
        print("  Parallel results validated!")
    else:
        print("  Skipping parallel test (single-core machine)")

    # ── Adaptive Sims Test ──
    print("\n" + "=" * 60)
    print("  Adaptive Sims Test")
    print("=" * 60)
    sim4 = BracketSimulator(MockClient(), MockCache())
    sim4.team_data = {t: {} for t in mock_teams}
    base = 500
    # 1v16 (gap=15) → should get ~166
    n1 = sim4._adaptive_num_sims("Duke", "Siena", base)
    print(f"  1v16 (Duke vs Siena, gap=15): {n1} sims (expect ~{base//3})")
    assert n1 == max(150, base // 3)
    # 8v9 (gap=1) → full sims
    n2 = sim4._adaptive_num_sims("Ohio St.", "TCU", base)
    print(f"  8v9 (Ohio St. vs TCU, gap=1): {n2} sims (expect {base})")
    assert n2 == base
    # 4v13 (gap=9) → half sims
    n3 = sim4._adaptive_num_sims("Kansas", "Cal Baptist", base)
    print(f"  4v13 (Kansas vs Cal Baptist, gap=9): {n3} sims (expect ~{base//2})")
    assert n3 == max(200, base // 2)
    print("  Adaptive sims validated!")

    print("\n  All tests passed!")
