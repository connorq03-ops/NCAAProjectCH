"""Optimize the KenPom calibration blend ratio.

Runs backtests at different blend ratios to find the optimal value.
Should be run AFTER Phase 2B (interleaved sim) and Phase 2C (style validation)
to measure whether the sim has improved enough to reduce the anchor.

Usage:
    python optimize_kp_blend.py --start 2025-01-01 --end 2025-03-15

Requires KENPOM_API_KEY environment variable.
"""

import argparse
import os
import sys

BLEND_RATIOS = [0.0, 0.05, 0.10, 0.15, 0.18, 0.25]


def optimize_blend(backtester, start_date, end_date, kenpom_client, cache):
    """Run backtests at different KenPom blend ratios and compare results.

    Args:
        backtester: Backtester instance
        start_date: Start date string (YYYY-MM-DD)
        end_date: End date string (YYYY-MM-DD)
        kenpom_client: KenpomClient instance
        cache: SQLiteCache instance

    Returns:
        dict: {ratio: {pick_accuracy, avg_spread_error, total_games}} for each ratio
    """
    import matchup_params as mp

    results_by_ratio = {}
    for ratio in BLEND_RATIOS:
        # Override the blend ratio in matchup_params
        original = mp.KP_BLEND_RATIO
        mp.KP_BLEND_RATIO = ratio

        try:
            metrics = backtester.backtest_date_range(start_date, end_date, kenpom_client, cache)
            results_by_ratio[ratio] = {
                'pick_accuracy': metrics.get('our_pick_accuracy'),
                'avg_spread_error': metrics.get('our_avg_spread_error'),
                'total_games': metrics.get('total_games'),
            }
        except Exception as e:
            results_by_ratio[ratio] = {'error': str(e)}
        finally:
            mp.KP_BLEND_RATIO = original

    return results_by_ratio


def find_optimal_ratio(results_by_ratio):
    """Identify the optimal blend ratio from backtest results.

    Prioritizes pick accuracy, breaks ties with lower spread error.

    Args:
        results_by_ratio: Output of optimize_blend()

    Returns:
        tuple: (optimal_ratio, summary_dict)
    """
    valid = {r: v for r, v in results_by_ratio.items()
             if 'error' not in v and v.get('pick_accuracy') is not None}

    if not valid:
        return None, {'error': 'No valid results'}

    # Sort by pick accuracy (desc), then spread error (asc)
    ranked = sorted(valid.items(),
                    key=lambda x: (-x[1]['pick_accuracy'],
                                   x[1].get('avg_spread_error', 999)))

    optimal_ratio = ranked[0][0]
    baseline_ratio = 0.18
    baseline = valid.get(baseline_ratio, {})

    return optimal_ratio, {
        'optimal_ratio': optimal_ratio,
        'optimal_accuracy': ranked[0][1]['pick_accuracy'],
        'optimal_spread_error': ranked[0][1].get('avg_spread_error'),
        'baseline_accuracy': baseline.get('pick_accuracy'),
        'baseline_spread_error': baseline.get('avg_spread_error'),
        'accuracy_change': round(
            ranked[0][1]['pick_accuracy'] - baseline.get('pick_accuracy', 0), 1
        ) if baseline.get('pick_accuracy') else None,
        'all_results': results_by_ratio,
    }


def main():
    parser = argparse.ArgumentParser(description='Optimize KenPom blend ratio via backtesting')
    parser.add_argument('--start', required=True, help='Start date (YYYY-MM-DD)')
    parser.add_argument('--end', required=True, help='End date (YYYY-MM-DD)')
    args = parser.parse_args()

    api_key = os.getenv('KENPOM_API_KEY')
    if not api_key:
        print('ERROR: KENPOM_API_KEY environment variable required')
        sys.exit(1)

    from kenpom_client import KenpomClient
    from backtester import Backtester
    # Import SQLiteCache from app without starting the server
    from app import SQLiteCache

    client = KenpomClient(api_key=api_key)
    cache = SQLiteCache(db_path='.cache.db')
    bt = Backtester()

    print(f'Running blend ratio optimization: {args.start} to {args.end}')
    print(f'Testing ratios: {BLEND_RATIOS}')
    print()

    results = optimize_blend(bt, args.start, args.end, client, cache)
    optimal, summary = find_optimal_ratio(results)

    print('=' * 60)
    print('  KenPom Blend Ratio Optimization Results')
    print('=' * 60)

    for ratio in BLEND_RATIOS:
        r = results.get(ratio, {})
        if 'error' in r:
            print(f'  Ratio {ratio:.2f}: ERROR - {r["error"]}')
        else:
            marker = ' <-- OPTIMAL' if ratio == optimal else ''
            baseline = ' (current default)' if ratio == 0.18 else ''
            print(f'  Ratio {ratio:.2f}: Pick {r["pick_accuracy"]:.1f}%  '
                  f'MAE {r.get("avg_spread_error", "N/A")}  '
                  f'Games {r.get("total_games", 0)}{baseline}{marker}')

    print()
    if summary.get('accuracy_change') is not None:
        sign = '+' if summary['accuracy_change'] > 0 else ''
        print(f'  Accuracy change vs baseline: {sign}{summary["accuracy_change"]:.1f}%')

    if optimal is not None:
        print(f'\n  Recommended ratio: {optimal:.2f}')
        if optimal < 0.05:
            print('  WARNING: Ratio < 0.05 not recommended. A small KenPom anchor')
            print('           captures opponent quality info the sim may miss.')


if __name__ == '__main__':
    main()
