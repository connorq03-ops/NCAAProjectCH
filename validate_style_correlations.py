#!/usr/bin/env python3
"""
validate_style_correlations.py - Validate MC simulation style correlations against real NCAA data.

Compares the correlation matrix of 6 key stats (FG2%, FG3%, TO%, OR%, 3PT rate, FTR)
between real ESPN box-score data and Monte Carlo simulations using generate_game_style().

Usage:
    python validate_style_correlations.py [--sims 5000] [--start 2025-12-01] [--end 2026-03-01]
                                          [--cache-dir .referee_cache]
"""

import argparse
import json
import os
import sys
import time
from datetime import datetime, timedelta
from collections import defaultdict

import numpy as np

try:
    import requests
except ImportError:
    print("Error: 'requests' library required. Install with: pip install requests")
    sys.exit(1)

from mc_engine import generate_game_style

# ESPN endpoints
SCOREBOARD_URL = (
    "https://site.api.espn.com/apis/site/v2/sports/basketball/"
    "mens-college-basketball/scoreboard"
)
SUMMARY_URL = (
    "https://site.api.espn.com/apis/site/v2/sports/basketball/"
    "mens-college-basketball/summary"
)

STAT_NAMES = ["FG2%", "FG3%", "TO%", "OR%", "3PT_rate", "FTR"]


# ---- Caching ----------------------------------------------------------------

def _cache_path(cache_dir, prefix, key):
    os.makedirs(cache_dir, exist_ok=True)
    return os.path.join(cache_dir, f"{prefix}_{key}.json")


def _load_cache(cache_dir, prefix, key):
    path = _cache_path(cache_dir, prefix, key)
    if os.path.exists(path):
        with open(path, "r") as f:
            return json.load(f)
    return None


def _save_cache(cache_dir, prefix, key, data):
    path = _cache_path(cache_dir, prefix, key)
    with open(path, "w") as f:
        json.dump(data, f)


# ---- ESPN data fetching -----------------------------------------------------

def fetch_scoreboard(date_str, cache_dir):
    cached = _load_cache(cache_dir, "scoreboard", date_str)
    if cached is not None:
        return cached
    resp = requests.get(SCOREBOARD_URL, params={"dates": date_str, "limit": 200}, timeout=15)
    if resp.status_code != 200:
        return {"events": []}
    data = resp.json()
    _save_cache(cache_dir, "scoreboard", date_str, data)
    return data


def fetch_summary(eid, cache_dir):
    cached = _load_cache(cache_dir, "summary", eid)
    if cached is not None:
        return cached
    try:
        resp = requests.get(SUMMARY_URL, params={"event": eid}, timeout=15)
        if resp.status_code == 200:
            data = resp.json()
            _save_cache(cache_dir, "summary", eid, data)
            return data
    except Exception:
        pass
    return None


def is_completed_game(event):
    status = event.get("status", {}).get("type", {})
    return status.get("completed", False)


def extract_team_stats(summary):
    """Extract per-team box-score stats needed for correlation analysis.

    Returns list of dicts with keys: fg2pct, fg3pct, to_pct, or_pct, rate3, ftr
    (one dict per team in the game, so typically 2).
    """
    if not summary:
        return []
    box = summary.get("boxscore", {})
    teams = box.get("teams", [])
    results = []
    for team_data in teams:
        stats_groups = team_data.get("statistics", [])
        labels = []
        totals = []
        for sg in stats_groups:
            if sg.get("labels") and sg.get("totals"):
                labels = sg["labels"]
                totals = sg["totals"]
                break
        if not labels or not totals:
            continue

        def _get(label):
            for i, l in enumerate(labels):
                if l.upper() == label.upper() and i < len(totals):
                    try:
                        return float(totals[i])
                    except (ValueError, TypeError):
                        return None
            return None

        # Parse what we can from the box score
        fgm = _get("FGM")
        fga = _get("FGA")
        tpm = _get("3PM") or _get("3FGM")
        tpa = _get("3PA") or _get("3FGA")
        fta = _get("FTA")
        ftm = _get("FTM")
        to = _get("TO")
        oreb = _get("OREB") or _get("OR")
        dreb = _get("DREB") or _get("DR")
        reb = _get("REB")

        if fga is None or fga == 0 or fgm is None:
            continue
        if tpa is None:
            tpa = 0
        if tpm is None:
            tpm = 0
        if fta is None:
            fta = 0
        if to is None:
            to = 0
        if oreb is None:
            oreb = 0

        fg2a = fga - tpa
        fg2m = fgm - tpm
        fg2pct = (fg2m / fg2a * 100) if fg2a > 0 else 0
        fg3pct = (tpm / tpa * 100) if tpa > 0 else 0

        # Possessions estimate (simple): FGA - OREB + TO + 0.475 * FTA
        poss = fga - oreb + to + 0.475 * fta
        if poss <= 0:
            poss = fga  # fallback

        to_pct = (to / poss * 100) if poss > 0 else 0
        or_pct = (oreb / (oreb + (dreb if dreb else 0)) * 100) if (oreb + (dreb if dreb else 0)) > 0 else 0
        rate3 = (tpa / fga * 100) if fga > 0 else 0
        ftr = (fta / fga * 100) if fga > 0 else 0

        results.append({
            "fg2pct": fg2pct,
            "fg3pct": fg3pct,
            "to_pct": to_pct,
            "or_pct": or_pct,
            "rate3": rate3,
            "ftr": ftr,
        })
    return results


# ---- Real data collection ----------------------------------------------------

def collect_real_stats(start_date, end_date, cache_dir, max_games=500, rate_limit=0.3):
    """Scrape ESPN for real team-game stat lines."""
    all_stats = []
    current = start_date
    total_days = (end_date - start_date).days + 1
    day_num = 0

    while current <= end_date and len(all_stats) < max_games * 2:
        day_num += 1
        date_str = current.strftime("%Y%m%d")
        print(f"  [{day_num}/{total_days}] {current.strftime('%Y-%m-%d')}...", end="", flush=True)

        scoreboard = fetch_scoreboard(date_str, cache_dir)
        events = scoreboard.get("events", [])
        completed = [e for e in events if is_completed_game(e)]

        day_count = 0
        for event in completed:
            if len(all_stats) >= max_games * 2:
                break
            eid = event.get("id", "")
            if not eid:
                continue
            summary = fetch_summary(eid, cache_dir)
            team_stats = extract_team_stats(summary)
            all_stats.extend(team_stats)
            day_count += len(team_stats)
            time.sleep(rate_limit)

        print(f" {day_count} team-lines")
        current += timedelta(days=1)

    print(f"\n  Total real team stat lines: {len(all_stats)}")
    return all_stats


# ---- Simulated data ----------------------------------------------------------

def collect_simulated_stats(n_sims=5000):
    """Run MC simulations and extract the same 6 stats."""
    all_stats = []
    for _ in range(n_sims):
        style = generate_game_style(base_volatility=1.0, style_bias=0)
        # Map adjustments to approximate percentage-space stats
        # Base values are typical NCAA averages
        fg2pct = 48.0 + style["fg2_adj"]
        fg3pct = 33.0 + style["fg3_adj"]
        to_pct = 18.0 + style["to_adj"]
        or_pct = 28.0 + style["or_adj"]
        rate3 = 35.0 + style["rate3_adj"]
        ftr = 30.0 + style["ftr_adj"]

        # Clamp to reasonable ranges
        fg2pct = max(20, min(75, fg2pct))
        fg3pct = max(10, min(60, fg3pct))
        to_pct = max(5, min(40, to_pct))
        or_pct = max(10, min(50, or_pct))
        rate3 = max(10, min(55, rate3))
        ftr = max(10, min(60, ftr))

        all_stats.append({
            "fg2pct": fg2pct,
            "fg3pct": fg3pct,
            "to_pct": to_pct,
            "or_pct": or_pct,
            "rate3": rate3,
            "ftr": ftr,
        })
    return all_stats


# ---- Analysis ----------------------------------------------------------------

def stats_to_matrix(stats_list):
    """Convert list of stat dicts to numpy array (N x 6)."""
    keys = ["fg2pct", "fg3pct", "to_pct", "or_pct", "rate3", "ftr"]
    return np.array([[s[k] for k in keys] for s in stats_list])


def compute_correlation_matrix(matrix):
    """Compute Pearson correlation matrix."""
    return np.corrcoef(matrix, rowvar=False)


def compare_matrices(real_corr, sim_corr, tolerance=0.15):
    """Compare two correlation matrices and report mismatches."""
    issues = []
    n = len(STAT_NAMES)
    for i in range(n):
        for j in range(i + 1, n):
            real_val = real_corr[i, j]
            sim_val = sim_corr[i, j]
            diff = abs(real_val - sim_val)

            # Check magnitude
            if diff > tolerance:
                issues.append({
                    "type": "magnitude",
                    "stats": (STAT_NAMES[i], STAT_NAMES[j]),
                    "real": real_val,
                    "sim": sim_val,
                    "diff": diff,
                })

            # Check sign mismatch (only if both are non-trivial)
            if abs(real_val) > 0.05 and abs(sim_val) > 0.05:
                if (real_val > 0) != (sim_val > 0):
                    issues.append({
                        "type": "sign",
                        "stats": (STAT_NAMES[i], STAT_NAMES[j]),
                        "real": real_val,
                        "sim": sim_val,
                    })
    return issues


def check_impossible_stat_lines(stats_list):
    """Flag impossible or highly unlikely stat combinations."""
    flags = []
    for i, s in enumerate(stats_list):
        if s["rate3"] > 50 and s["or_pct"] > 40:
            flags.append(f"Line {i}: 3PT rate {s['rate3']:.1f}% AND OR% {s['or_pct']:.1f}% (both extreme)")
        if s["fg2pct"] > 70 and s["to_pct"] > 30:
            flags.append(f"Line {i}: FG2% {s['fg2pct']:.1f}% AND TO% {s['to_pct']:.1f}% (contradictory)")
        if s["fg3pct"] > 50 and s["ftr"] > 50:
            flags.append(f"Line {i}: FG3% {s['fg3pct']:.1f}% AND FTR {s['ftr']:.1f}% (contradictory)")
    return flags


def print_correlation_matrix(corr, label):
    """Pretty-print a correlation matrix."""
    print(f"\n  {label}:")
    header = "          " + "  ".join(f"{n:>8s}" for n in STAT_NAMES)
    print(header)
    for i, name in enumerate(STAT_NAMES):
        row = f"  {name:>8s}"
        for j in range(len(STAT_NAMES)):
            row += f"  {corr[i, j]:>8.3f}"
        print(row)


def main():
    parser = argparse.ArgumentParser(description="Validate MC style correlation structure")
    parser.add_argument("--sims", type=int, default=5000, help="Number of MC simulations")
    parser.add_argument("--start", default="2026-01-01", help="Start date for real data (YYYY-MM-DD)")
    parser.add_argument("--end", default="2026-02-15", help="End date for real data (YYYY-MM-DD)")
    parser.add_argument("--cache-dir", default=".referee_cache", help="Cache directory for ESPN data")
    parser.add_argument("--max-games", type=int, default=500, help="Max real games to fetch")
    parser.add_argument("--tolerance", type=float, default=0.15,
                        help="Max allowed correlation difference")
    parser.add_argument("--skip-espn", action="store_true",
                        help="Skip ESPN scraping, only run simulated analysis")
    args = parser.parse_args()

    print("=" * 65)
    print("  MC Style Correlation Validation")
    print("=" * 65)

    # --- Simulated data ---
    print(f"\n[1/3] Running {args.sims} MC simulations...")
    sim_stats = collect_simulated_stats(args.sims)
    sim_matrix = stats_to_matrix(sim_stats)
    sim_corr = compute_correlation_matrix(sim_matrix)
    print_correlation_matrix(sim_corr, "Simulated Correlation Matrix")

    # Check for impossible stat lines in simulations
    sim_flags = check_impossible_stat_lines(sim_stats)
    if sim_flags:
        print(f"\n  WARNING: {len(sim_flags)} impossible stat lines in simulations:")
        for f in sim_flags[:10]:
            print(f"    - {f}")
    else:
        print("\n  No impossible stat lines detected in simulations.")

    if args.skip_espn:
        print("\n  Skipping ESPN data collection (--skip-espn).")
        print("\n  Simulated stats summary:")
        for i, name in enumerate(STAT_NAMES):
            vals = sim_matrix[:, i]
            print(f"    {name}: mean={np.mean(vals):.1f}, std={np.std(vals):.1f}, "
                  f"min={np.min(vals):.1f}, max={np.max(vals):.1f}")
        print("\nDone (simulation-only mode).")
        return

    # --- Real data ---
    start_date = datetime.strptime(args.start, "%Y-%m-%d")
    end_date = datetime.strptime(args.end, "%Y-%m-%d")
    print(f"\n[2/3] Collecting real ESPN box-score data ({args.start} to {args.end})...")
    real_stats = collect_real_stats(start_date, end_date, args.cache_dir,
                                   max_games=args.max_games, rate_limit=0.3)

    if len(real_stats) < 50:
        print(f"\n  ERROR: Only {len(real_stats)} real stat lines collected. Need at least 50.")
        print("  Try a wider date range or check ESPN availability.")
        sys.exit(1)

    real_matrix = stats_to_matrix(real_stats)
    real_corr = compute_correlation_matrix(real_matrix)
    print_correlation_matrix(real_corr, "Real NCAA Correlation Matrix")

    # --- Comparison ---
    print(f"\n[3/3] Comparing correlation matrices (tolerance = {args.tolerance})...")
    issues = compare_matrices(real_corr, sim_corr, tolerance=args.tolerance)

    if not issues:
        print("\n  ALL CORRELATIONS MATCH within tolerance. No coefficient changes needed.")
    else:
        print(f"\n  FOUND {len(issues)} MISMATCHES:")
        for issue in issues:
            if issue["type"] == "magnitude":
                print(f"    {issue['stats'][0]} vs {issue['stats'][1]}: "
                      f"real={issue['real']:.3f}, sim={issue['sim']:.3f}, "
                      f"diff={issue['diff']:.3f} (>{args.tolerance})")
            elif issue["type"] == "sign":
                print(f"    SIGN MISMATCH: {issue['stats'][0]} vs {issue['stats'][1]}: "
                      f"real={issue['real']:.3f}, sim={issue['sim']:.3f}")

    # Summary statistics
    print("\n  Real data stats summary:")
    for i, name in enumerate(STAT_NAMES):
        vals = real_matrix[:, i]
        print(f"    {name}: mean={np.mean(vals):.1f}, std={np.std(vals):.1f}, "
              f"min={np.min(vals):.1f}, max={np.max(vals):.1f}")

    print("\n  Simulated stats summary:")
    for i, name in enumerate(STAT_NAMES):
        vals = sim_matrix[:, i]
        print(f"    {name}: mean={np.mean(vals):.1f}, std={np.std(vals):.1f}, "
              f"min={np.min(vals):.1f}, max={np.max(vals):.1f}")

    # Report coefficient reference for any adjustments needed
    if issues:
        print("\n  Current generate_game_style() coefficients (mc_engine.py & mc-worker.js):")
        print("    fg2Adj:   interiorAxis * 1.8  - disciplineAxis * 0.5")
        print("    fg3Adj:  -interiorAxis * 1.2  - disciplineAxis * 0.4")
        print("    toAdj:    interiorAxis * 0.3  + disciplineAxis * 1.5")
        print("    orAdj:    interiorAxis * 1.0  + disciplineAxis * 0.6")
        print("    rate3Adj:-interiorAxis * 2.5")
        print("    ftrAdj:   interiorAxis * 1.5")
        print("\n  Adjust these coefficients if sign or magnitude mismatches persist.")

    sys.exit(1 if issues else 0)


if __name__ == "__main__":
    main()
