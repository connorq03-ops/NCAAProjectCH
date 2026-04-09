"""
Golf Automated Backtesting Pipeline.
Replays model predictions against actual PGA Tour leaderboard results.
Computes accuracy metrics: outright pick %, top-10 hit rate, cut prediction
accuracy, finish position MAE, Brier score, ROI vs odds.

Uses the full 4-model composite pipeline (SG Efficiency + Course Fit + GolfRat + MC)
matching the golf_composite_model.py logic.

Historical mode (use_historical=True) fetches per-tournament historical DataGolf
data so the backtest only sees data available at tournament time, eliminating
lookahead bias. This mirrors backtester.py's historical mode for basketball.

Legacy mode (use_historical=False) uses current rankings for all tournaments,
which introduces lookahead bias but is faster and requires fewer API calls.

Mirrors backtester.py (root of repo) structure with golf-specific metrics:
  - backtest_predictions()           (backtester.py lines 130-158)
  - backtest_tournament()            (backtester.py _backtest_single_day lines 390-709)
  - backtest_date_range()            (backtester.py lines 183-337)
  - backtest_with_bias_comparison()  (backtester.py lines 339-370)
  - _compute_metrics()               (backtester.py lines 726-1145)
  - _validate_backtest()             (backtester.py lines 1360-1377)
"""

import json
import os
import math
from collections import defaultdict
from datetime import datetime, timedelta

from golf.datagolf_client import DataGolfClient
from golf.golf_composite_model import (
    predict_field, compute_golf_composite, calc_golf_rat,
    model_sg_efficiency, model_course_fit, model_golf_rat,
)
from golf.golf_sim_params import prefetch_all_player_data, build_player_sim_params
from golf.golf_course_profiles import get_course_profile, COURSES
from golf.golf_mc_engine import simulate_tournament
from golf.golf_weight_optimizer import compute_per_model_accuracy, compute_optimal_weights


# ─── Player tier classification (for by-tier breakdown) ─────────────────────

def _classify_tier(player_name):
    """Classify a player into a tier based on elite_players database."""
    try:
        from golf.golf_elite_players import get_player_info
        info = get_player_info(player_name)
        if info:
            return info.get('tier', 'unranked')
    except ImportError:
        pass
    return 'unranked'


def _classify_field_strength(tournament_name):
    """Classify tournament field strength from name."""
    name_lower = (tournament_name or '').lower()
    major_keywords = ['masters', 'u.s. open', 'us open', 'open championship',
                      'pga championship', 'british open']
    signature_keywords = ['players', 'genesis', 'arnold palmer', 'memorial',
                          'wgc', 'tour championship', 'sentry']
    for kw in major_keywords:
        if kw in name_lower:
            return 'major'
    for kw in signature_keywords:
        if kw in name_lower:
            return 'signature'
    return 'regular'


def _classify_course_type(course_id):
    """Classify course type from course profile."""
    profile = get_course_profile(course_id) if course_id else None
    if not profile:
        return 'parkland'
    return profile.get('style', profile.get('type', 'parkland')).lower()


class GolfBacktester:
    """Runs backtests against historical PGA Tour tournament results.

    Mirrors Backtester class from backtester.py (root of repo).
    """

    def __init__(self, predictions_file='golf_predictions.json'):
        self.predictions_file = predictions_file

    # ── Utility: name normalization and fuzzy matching ──

    def _normalize(self, name):
        """Normalize player name for matching. Mirror backtester.py lines 117-119."""
        return (name or '').lower().replace('.', '').replace("'", '').replace('-', ' ').strip()

    def _match_player(self, pred_name, result_names):
        """Fuzzy match a predicted player name against result names.

        Mirror backtester.py _match_teams() lines 121-128.

        Args:
            pred_name: Player name from prediction
            result_names: List of player names from results

        Returns:
            Best matching name from result_names, or None
        """
        np = self._normalize(pred_name)
        for rn in result_names:
            nr = self._normalize(rn)
            if np == nr:
                return rn
            # Check last-name match for common abbreviations
            if np in nr or nr in np:
                return rn
        # Try last name only
        pred_parts = np.split()
        if pred_parts:
            pred_last = pred_parts[-1]
            candidates = []
            for rn in result_names:
                nr = self._normalize(rn)
                nr_parts = nr.split()
                if nr_parts and nr_parts[-1] == pred_last:
                    candidates.append(rn)
            if len(candidates) == 1:
                return candidates[0]
        return None

    # ── Load saved predictions ──

    def _load_predictions(self):
        """Load saved predictions from file. Mirror backtester.py lines 108-115."""
        if not os.path.exists(self.predictions_file):
            return []
        try:
            with open(self.predictions_file, 'r') as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            return []

    # ── Backtest saved predictions ──

    def backtest_predictions(self):
        """Backtest all saved predictions that have results entered.

        Mirror Backtester.backtest_predictions() (backtester.py lines 130-158).
        Load saved predictions, filter to completed ones, compare against actuals.

        Returns:
            dict: Metrics dict from _compute_metrics(), or error dict
        """
        preds = self._load_predictions()
        completed = [p for p in preds if p.get('result_entered')]
        if not completed:
            return {'error': 'No completed predictions to backtest', 'results': []}

        results = []
        for p in completed:
            actual_finish = p.get('actual_finish', 0) or 0
            predicted_finish = p.get('predicted_finish', 0) or 0
            actual_made_cut = p.get('actual_made_cut', True)

            results.append({
                'tournament_id': p.get('tournament', ''),
                'tournament_name': p.get('tournament', ''),
                'course_id': p.get('course_id', ''),
                'player_name': p.get('player', ''),
                'predicted_finish': predicted_finish,
                'actual_finish': actual_finish,
                'finish_error': abs(predicted_finish - actual_finish),
                'predicted_win_pct': p.get('win_probability', 0.0) or 0.0,
                'actual_won': actual_finish == 1,
                'predicted_top5_pct': p.get('predicted_top5_pct', 0.0) or 0.0,
                'actual_top5': 1 <= actual_finish <= 5,
                'predicted_top10_pct': p.get('top10_probability', 0.0) or 0.0,
                'actual_top10': 1 <= actual_finish <= 10,
                'predicted_top20_pct': p.get('predicted_top20_pct', 0.0) or 0.0,
                'actual_top20': 1 <= actual_finish <= 20,
                'predicted_cut_pct': p.get('predicted_make_cut') if p.get('predicted_make_cut') is not None else 1.0,
                'actual_made_cut': actual_made_cut,
                'golf_rat_score': p.get('golf_rat_score', 5.0) or 5.0,
                'composite_weights': p.get('composite_weights', {}),
                'sub_model_finishes': p.get('sub_model_finishes', {}),
                'odds_win_pct': p.get('odds_win_pct'),
                'odds_value': p.get('odds_value'),
            })

        return self._compute_metrics(results)

    # ── Backtest single tournament ──

    def backtest_tournament(self, tournament_id, dg_client, cache, use_historical=False):
        """Backtest a single golf tournament.

        Golf equivalent of _backtest_single_day() (backtester.py lines 390-709).
        Instead of backtesting one day of basketball games, backtests one tournament.

        Args:
            tournament_id: DataGolf event ID
            dg_client: DataGolfClient instance
            cache: SQLiteCache instance
            use_historical: If True, use historical data to eliminate lookahead bias

        Returns:
            list: Per-player result dicts
        """
        results = []

        # 1. Fetch historical tournament results
        try:
            historical = dg_client.get_historical_rounds(event_id=tournament_id)
        except Exception as e:
            print(f"[backtest] Failed to get historical rounds for {tournament_id}: {e}")
            return results

        if not historical:
            return results

        # Extract standings from historical data
        rounds_data = historical if isinstance(historical, list) else historical.get('rounds', historical.get('results', []))
        if not rounds_data:
            return results

        # Build actual results lookup: player_name -> {finish, made_cut, score}
        actual_results = {}
        tournament_name = ''
        course_id = ''

        for entry in rounds_data:
            player_name = entry.get('player_name', entry.get('name', ''))
            if not player_name:
                continue
            fin = entry.get('fin_num', entry.get('finish', entry.get('position', 999)))
            if isinstance(fin, str):
                try:
                    fin = int(fin.replace('T', '').replace('CUT', '999').replace('WD', '998').replace('DQ', '997'))
                except ValueError:
                    fin = 999
            made_cut = entry.get('made_cut', fin <= 70)
            if isinstance(made_cut, str):
                made_cut = made_cut.lower() in ('true', '1', 'yes')
            actual_results[player_name] = {
                'finish': int(fin),
                'made_cut': bool(made_cut),
                'score': entry.get('total_to_par', entry.get('score', 0)),
            }
            if not tournament_name:
                tournament_name = entry.get('event_name', entry.get('tournament', str(tournament_id)))
            if not course_id:
                course_id = entry.get('course_id', entry.get('course', ''))

        if not actual_results:
            return results

        # 2. Build player field for model predictions
        # If historical mode, fetch pre-tournament predictions archive
        player_stats_list = []
        if use_historical:
            try:
                archive = dg_client.get_pre_tournament_pred_archive(event_id=tournament_id)
                archive_preds = archive if isinstance(archive, list) else archive.get('predictions', archive.get('players', []))
                for pred in archive_preds:
                    pname = pred.get('player_name', pred.get('name', ''))
                    if pname:
                        player_stats_list.append({
                            '_player_name': pname,
                            'sg_total': pred.get('sg_total', pred.get('dg_skill_estimate', 0.0)),
                            'sg_ott': pred.get('sg_ott', 0.0),
                            'sg_app': pred.get('sg_app', 0.0),
                            'sg_arg': pred.get('sg_arg', 0.0),
                            'sg_putt': pred.get('sg_putt', 0.0),
                            'driving_accuracy': pred.get('driving_accuracy', 60.0),
                            'scrambling_pct': pred.get('scrambling_pct', 58.0),
                            'cuts_made_pct': pred.get('cuts_made_pct', 70.0),
                            'consistency_score': pred.get('consistency_score', 0.5),
                            'fatigue_factor': pred.get('fatigue_factor', 0.5),
                            'recent_form': pred.get('recent_form', {}),
                            'course_history': pred.get('course_history'),
                        })
            except Exception as e:
                print(f"[backtest] Historical archive not available for {tournament_id}: {e}")
                # Fall back to current data
                use_historical = False

        if not player_stats_list:
            # Use current rankings (legacy mode, introduces lookahead bias)
            try:
                rankings = dg_client.get_rankings()
                rankings_list = rankings.get('rankings', []) if isinstance(rankings, dict) else rankings or []
                for entry in rankings_list:
                    pname = entry.get('player_name', '')
                    if pname and pname in actual_results:
                        player_stats_list.append({
                            '_player_name': pname,
                            'sg_total': entry.get('dg_skill_estimate', 0.0),
                            'sg_ott': entry.get('sg_ott', 0.0),
                            'sg_app': entry.get('sg_app', 0.0),
                            'sg_arg': entry.get('sg_arg', 0.0),
                            'sg_putt': entry.get('sg_putt', 0.0),
                            'driving_accuracy': entry.get('driving_accuracy', 60.0),
                            'scrambling_pct': entry.get('scrambling_pct', 58.0),
                            'cuts_made_pct': entry.get('cuts_made_pct', 70.0),
                            'consistency_score': entry.get('consistency_score', 0.5),
                            'fatigue_factor': entry.get('fatigue_factor', 0.5),
                            'recent_form': entry.get('recent_form', {}),
                            'course_history': entry.get('course_history'),
                        })
            except Exception as e:
                print(f"[backtest] Failed to get rankings: {e}")
                return results

        if not player_stats_list:
            return results

        # 3. Get course profile
        course_profile = get_course_profile(course_id) if course_id else None
        if not course_profile:
            # Try matching by tournament name
            for cid, cp in COURSES.items():
                cname = cp.get('name', '').lower()
                if cname and cname in tournament_name.lower():
                    course_profile = cp
                    course_id = cid
                    break
        if not course_profile:
            # Use a generic course profile
            course_profile = {
                'name': tournament_name,
                'par': 72,
                'yards': 7200,
                'style': 'parkland',
                'grass': 'bermuda',
                'sg_weights': {'ott': 0.25, 'app': 0.30, 'arg': 0.20, 'putt': 0.25},
            }

        # 4. Get cached model weights
        weight_overrides = None
        if cache:
            cached_weights = cache.get('golf_model_weights', {}, ttl=86400 * 365)
            if cached_weights and 'weights' in cached_weights:
                weight_overrides = cached_weights['weights']

        # 5. Determine field strength context
        context = _classify_field_strength(tournament_name)
        if context == 'regular':
            context = None

        # 6. Run composite model predictions via predict_field()
        predictions = predict_field(
            player_stats_list, course_profile,
            mc_results=None,  # Skip MC for speed in initial pass
            weight_overrides=weight_overrides,
            context=context,
        )

        # 7. Build MC sim params and run reduced MC (100 sims for speed)
        mc_results = {}
        try:
            sim_params = []
            for ps in player_stats_list:
                sp = build_player_sim_params(ps, course_profile)
                sim_params.append(sp)

            if sim_params:
                # Generate synthetic holes for MC
                from golf.golf_mc_engine import _generate_synthetic_holes
                holes = _generate_synthetic_holes(course_profile.get('par', 72))
                mc_output = simulate_tournament(sim_params, holes, num_sims=100)
                mc_results = mc_output
        except Exception as e:
            print(f"[backtest] MC simulation failed for {tournament_id}: {e}")

        # 8. Re-run predict_field with MC results if available
        if mc_results:
            # Convert MC output to format expected by predict_field
            mc_for_predict = {}
            for pname, mc_data in mc_results.items():
                mc_for_predict[pname] = {
                    'predicted_finish': mc_data.get('avg_finish', 35.0),
                    'win_prob': mc_data.get('win_pct', 0.0) / 100.0,
                    'top5_prob': mc_data.get('top5_pct', 0.0) / 100.0,
                    'top10_prob': mc_data.get('top10_pct', 0.0) / 100.0,
                    'top20_prob': mc_data.get('top20_pct', 0.0) / 100.0,
                    'make_cut_prob': mc_data.get('cut_pct', 0.0) / 100.0,
                    'iterations': 100,
                }
            predictions = predict_field(
                player_stats_list, course_profile,
                mc_results=mc_for_predict,
                weight_overrides=weight_overrides,
                context=context,
            )

        # 9. Compare predictions against actual results
        pred_map = {p['player_name']: p for p in predictions}
        result_names = list(actual_results.keys())

        for pred in predictions:
            pred_name = pred['player_name']
            # Try exact match first, then fuzzy
            actual = actual_results.get(pred_name)
            if not actual:
                matched_name = self._match_player(pred_name, result_names)
                if matched_name:
                    actual = actual_results[matched_name]

            if not actual:
                continue

            actual_finish = actual['finish']
            actual_made_cut = actual['made_cut']
            predicted_finish = pred.get('predicted_finish', 35.0)

            # Extract sub-model finishes from model_details
            sub_model_finishes = {}
            model_details = pred.get('model_details', {})
            for model_key in ['sg_efficiency', 'course_fit', 'golf_rat', 'mc']:
                md = model_details.get(model_key, {})
                sub_model_finishes[model_key] = md.get('predicted_finish', predicted_finish)

            results.append({
                'tournament_id': str(tournament_id),
                'tournament_name': tournament_name,
                'course_id': course_id,
                'player_name': pred_name,
                'predicted_finish': predicted_finish,
                'actual_finish': actual_finish,
                'finish_error': abs(predicted_finish - actual_finish),
                'predicted_win_pct': pred.get('win_prob', 0.0),
                'actual_won': actual_finish == 1,
                'predicted_top5_pct': pred.get('top5_prob', 0.0),
                'actual_top5': 1 <= actual_finish <= 5,
                'predicted_top10_pct': pred.get('top10_prob', 0.0),
                'actual_top10': 1 <= actual_finish <= 10,
                'predicted_top20_pct': pred.get('top20_prob', 0.0),
                'actual_top20': 1 <= actual_finish <= 20,
                'predicted_cut_pct': pred.get('make_cut_prob', 0.5),
                'actual_made_cut': actual_made_cut,
                'golf_rat_score': pred.get('golf_rat_score', 5.0),
                'composite_weights': pred.get('weights_used', {}),
                'sub_model_finishes': sub_model_finishes,
                'odds_win_pct': None,
                'odds_value': None,
            })

        return results

    # ── Backtest over date range ──

    def backtest_date_range(self, start_date, end_date, dg_client, cache,
                            use_historical=False, progress_cb=None):
        """Automated backtest pipeline over a date range.

        Mirror Backtester.backtest_date_range() (backtester.py lines 183-337).

        Steps:
        1. Load stored calibration overrides from cache
        2. Fetch list of tournaments in date range
        3. One-time bulk data prefetch
        4. Loop through each tournament, collect per-player results
        5. Aggregate metrics via _compute_metrics()
        6. Run dynamic weight optimization
        7. Add metadata and validate

        Args:
            start_date: 'YYYY-MM-DD' start
            end_date: 'YYYY-MM-DD' end
            dg_client: DataGolfClient instance
            cache: SQLiteCache instance
            use_historical: Whether to use historical data (eliminates lookahead bias)
            progress_cb: Optional callback(progress_pct, message) for UI updates

        Returns:
            dict: Aggregated metrics
        """
        # 1. Load stored calibration overrides
        calibration_coeffs = None
        if cache:
            calibration_coeffs = cache.get('golf_calibration', {}, ttl=86400 * 365)

        # 2. Fetch list of tournaments in date range
        try:
            events = dg_client.get_historical_events()
            event_list = events if isinstance(events, list) else events.get('events', events.get('tournaments', []))
        except Exception as e:
            print(f"[backtest] Failed to get event list: {e}")
            return {'error': str(e), 'results': []}

        # Filter events by date range
        tournaments = []
        for ev in event_list:
            ev_date = ev.get('date', ev.get('start_date', ev.get('calendar_date', '')))
            if isinstance(ev_date, str) and start_date <= ev_date <= end_date:
                tournaments.append(ev)

        if not tournaments:
            return {'error': 'No tournaments found in date range', 'results': [],
                    'total_tournaments': 0, 'total_players_evaluated': 0}

        # 3. One-time bulk data prefetch
        try:
            prefetch_all_player_data(dg_client)
        except Exception as e:
            print(f"[backtest] Prefetch failed (non-fatal): {e}")

        # 4. Loop through each tournament
        all_results = []
        num_tournaments = len(tournaments)

        for i, tournament in enumerate(tournaments):
            tid = tournament.get('event_id', tournament.get('id', tournament.get('dg_id', '')))
            tname = tournament.get('event_name', tournament.get('name', str(tid)))
            print(f"[backtest] Tournament {i + 1}/{num_tournaments}: {tname}")

            if progress_cb:
                pct = int((i / num_tournaments) * 100)
                progress_cb(pct, f'Tournament {i + 1}/{num_tournaments}: {tname}')

            try:
                tournament_results = self.backtest_tournament(tid, dg_client, cache,
                                                              use_historical=use_historical)
                all_results.extend(tournament_results)
            except Exception as e:
                print(f"[backtest] Error on tournament {tname}: {e}")
                continue

        if not all_results:
            return {'error': 'No results collected', 'results': [],
                    'total_tournaments': num_tournaments, 'total_players_evaluated': 0}

        # 5. Aggregate metrics
        metrics = self._compute_metrics(all_results, calibration_coeffs=calibration_coeffs)

        # 6. Run dynamic weight optimization
        per_model_acc = compute_per_model_accuracy(all_results)
        optimal_weights = compute_optimal_weights(per_model_acc)
        from golf.golf_weight_optimizer import BASE_WEIGHTS
        weight_change = {name: round(optimal_weights.get(name, 0) - BASE_WEIGHTS.get(name, 0), 4)
                         for name in BASE_WEIGHTS}

        metrics['per_model_accuracy'] = per_model_acc
        metrics['optimal_weights'] = optimal_weights
        metrics['weight_change'] = weight_change

        # 7. Add metadata (mirror backtester.py lines 326-335)
        metrics['meta'] = {
            'mode': 'historical' if use_historical else 'current',
            'lookahead_bias': 'eliminated' if use_historical else 'present',
            'date_range': f'{start_date} to {end_date}',
            'num_tournaments': num_tournaments,
            'num_tournaments_with_data': len(set(r['tournament_id'] for r in all_results)),
            'num_players_evaluated': len(all_results),
        }

        # 8. Validate
        warnings = self._validate_backtest(metrics)
        if warnings:
            metrics['warnings'] = warnings

        if progress_cb:
            progress_cb(100, 'Backtest complete')

        return metrics

    # ── Bias comparison ──

    def backtest_with_bias_comparison(self, start_date, end_date, dg_client, cache,
                                      progress_cb=None):
        """Run backtest twice: with and without historical data, compare results.

        Mirror Backtester.backtest_with_bias_comparison() (backtester.py lines 339-370).

        Args:
            start_date, end_date: Date range
            dg_client: DataGolfClient instance
            cache: SQLiteCache instance
            progress_cb: Optional progress callback

        Returns:
            dict with 'historical', 'current', and 'bias_analysis' sections
        """
        def hist_progress(pct, msg):
            if progress_cb:
                progress_cb(int(pct * 0.45), f'[Historical] {msg}')

        def curr_progress(pct, msg):
            if progress_cb:
                progress_cb(50 + int(pct * 0.45), f'[Current] {msg}')

        # Run with historical data (no lookahead bias)
        historical_metrics = self.backtest_date_range(
            start_date, end_date, dg_client, cache,
            use_historical=True, progress_cb=hist_progress)

        # Run with current data (has lookahead bias)
        current_metrics = self.backtest_date_range(
            start_date, end_date, dg_client, cache,
            use_historical=False, progress_cb=curr_progress)

        # Compare results
        hist_mae = historical_metrics.get('finish_mae', 0)
        curr_mae = current_metrics.get('finish_mae', 0)
        hist_top10 = historical_metrics.get('top10_hit_rate', 0)
        curr_top10 = current_metrics.get('top10_hit_rate', 0)
        hist_cut = historical_metrics.get('cut_prediction_accuracy', 0)
        curr_cut = current_metrics.get('cut_prediction_accuracy', 0)

        bias_analysis = {
            'finish_mae_difference': round(curr_mae - hist_mae, 2) if hist_mae and curr_mae else None,
            'top10_hit_rate_difference': round(curr_top10 - hist_top10, 1) if hist_top10 and curr_top10 else None,
            'cut_accuracy_difference': round(curr_cut - hist_cut, 1) if hist_cut and curr_cut else None,
            'lookahead_bias_detected': (curr_mae < hist_mae - 2) if hist_mae and curr_mae else None,
            'recommendation': (
                'Current rankings show significantly lower MAE — lookahead bias likely present. '
                'Use historical mode for realistic accuracy estimates.'
                if hist_mae and curr_mae and curr_mae < hist_mae - 2
                else 'No significant lookahead bias detected.'
            ),
        }

        if progress_cb:
            progress_cb(100, 'Bias comparison complete')

        return {
            'historical': historical_metrics,
            'current': current_metrics,
            'bias_analysis': bias_analysis,
        }

    # ── Compute metrics ──

    def _compute_metrics(self, results, calibration_coeffs=None):
        """Compute golf-specific accuracy metrics from backtest results.

        Mirror Backtester._compute_metrics() (backtester.py lines 726-1145).
        Golf metrics replace basketball's pick%, ATS%, MAE on spread.

        Args:
            results: List of per-player result dicts
            calibration_coeffs: Optional calibration coefficients

        Returns:
            dict: Comprehensive metrics
        """
        if not results:
            return {'total_tournaments': 0, 'total_players_evaluated': 0, 'results': []}

        total = len(results)

        # ── Core accuracy metrics ──
        finish_errors = [r['finish_error'] for r in results if r.get('finish_error') is not None]
        finish_mae = round(sum(finish_errors) / len(finish_errors), 2) if finish_errors else None
        finish_errors_sorted = sorted(finish_errors) if finish_errors else []
        finish_median = (finish_errors_sorted[len(finish_errors_sorted) // 2]
                         if finish_errors_sorted else None)

        # Outright pick accuracy: % of tournaments where top predicted player won
        tournaments = defaultdict(list)
        for r in results:
            tournaments[r.get('tournament_id', '')].append(r)

        outright_correct = 0
        total_tournaments = len(tournaments)
        for tid, t_results in tournaments.items():
            if not t_results:
                continue
            # Find our top predicted player (lowest predicted finish)
            best_pred = min(t_results, key=lambda r: r.get('predicted_finish', 999))
            if best_pred.get('actual_won', False):
                outright_correct += 1

        outright_accuracy = round(outright_correct / total_tournaments * 100, 1) if total_tournaments > 0 else 0

        # Top-5/10/20 hit rates
        top5_preds = [r for r in results if r.get('predicted_top5_pct', 0) >= 0.10]
        top5_hits = sum(1 for r in top5_preds if r.get('actual_top5', False))
        top5_hit_rate = round(top5_hits / len(top5_preds) * 100, 1) if top5_preds else 0

        top10_preds = [r for r in results if r.get('predicted_top10_pct', 0) >= 0.10]
        top10_hits = sum(1 for r in top10_preds if r.get('actual_top10', False))
        top10_hit_rate = round(top10_hits / len(top10_preds) * 100, 1) if top10_preds else 0

        top20_preds = [r for r in results if r.get('predicted_top20_pct', 0) >= 0.15]
        top20_hits = sum(1 for r in top20_preds if r.get('actual_top20', False))
        top20_hit_rate = round(top20_hits / len(top20_preds) * 100, 1) if top20_preds else 0

        # Cut prediction accuracy
        cut_preds = [r for r in results
                     if r.get('predicted_cut_pct') is not None and r.get('actual_made_cut') is not None]
        cut_correct = sum(1 for r in cut_preds
                          if (r['predicted_cut_pct'] >= 0.5) == r['actual_made_cut'])
        cut_accuracy = round(cut_correct / len(cut_preds) * 100, 1) if cut_preds else 0

        # ── Brier scores (probability calibration) ──
        # Brier = (1/N) * sum((predicted_prob - actual_outcome)^2)
        win_brier_scores = [(r.get('predicted_win_pct', 0) - (1 if r.get('actual_won') else 0)) ** 2
                            for r in results if r.get('predicted_win_pct') is not None]
        win_brier = round(sum(win_brier_scores) / len(win_brier_scores), 4) if win_brier_scores else None

        top10_brier_scores = [(r.get('predicted_top10_pct', 0) - (1 if r.get('actual_top10') else 0)) ** 2
                              for r in results if r.get('predicted_top10_pct') is not None]
        top10_brier = round(sum(top10_brier_scores) / len(top10_brier_scores), 4) if top10_brier_scores else None

        cut_brier_scores = [(r.get('predicted_cut_pct', 0) - (1 if r.get('actual_made_cut') else 0)) ** 2
                            for r in results if r.get('predicted_cut_pct') is not None]
        cut_brier = round(sum(cut_brier_scores) / len(cut_brier_scores), 4) if cut_brier_scores else None

        # ── ROI metrics (if odds available) ──
        outright_roi = self._compute_outright_roi(results)
        top10_roi = self._compute_top10_roi(results)
        value_bet_roi = self._compute_value_bet_roi(results)

        # ── By-tier breakdown (analogous to basketball's by-conference) ──
        by_tier = self._compute_by_tier(results)

        # ── By-course-type breakdown ──
        by_course_type = self._compute_by_course_type(results)

        # ── By-field-strength breakdown ──
        by_field_strength = self._compute_by_field_strength(results)

        # ── Per-model accuracy ──
        per_model_acc = compute_per_model_accuracy(results)
        optimal_weights = compute_optimal_weights(per_model_acc)
        from golf.golf_weight_optimizer import BASE_WEIGHTS
        weight_change = {name: round(optimal_weights.get(name, 0) - BASE_WEIGHTS.get(name, 0), 4)
                         for name in BASE_WEIGHTS}

        # ── By-tournament breakdown ──
        by_tournament = []
        for tid, t_results in sorted(tournaments.items()):
            if not t_results:
                continue
            t_errors = [r['finish_error'] for r in t_results if r.get('finish_error') is not None]
            t_top10_pred = [r for r in t_results if r.get('predicted_top10_pct', 0) >= 0.10]
            t_top10_hits = sum(1 for r in t_top10_pred if r.get('actual_top10', False))
            by_tournament.append({
                'tournament_id': tid,
                'tournament_name': t_results[0].get('tournament_name', tid),
                'players': len(t_results),
                'finish_mae': round(sum(t_errors) / len(t_errors), 2) if t_errors else None,
                'top10_hit_rate': round(t_top10_hits / len(t_top10_pred) * 100, 1) if t_top10_pred else 0,
            })

        return {
            # Core accuracy
            'outright_pick_accuracy': outright_accuracy,
            'top5_hit_rate': top5_hit_rate,
            'top10_hit_rate': top10_hit_rate,
            'top20_hit_rate': top20_hit_rate,
            'cut_prediction_accuracy': cut_accuracy,
            'finish_mae': finish_mae,
            'finish_median_error': round(finish_median, 2) if finish_median is not None else None,
            # Brier scores
            'win_brier_score': win_brier,
            'top10_brier_score': top10_brier,
            'cut_brier_score': cut_brier,
            # ROI
            'outright_roi': outright_roi,
            'top10_roi': top10_roi,
            'value_bet_roi': value_bet_roi,
            # Breakdowns
            'by_tier': by_tier,
            'by_course_type': by_course_type,
            'by_field_strength': by_field_strength,
            'by_tournament': by_tournament,
            # Model weights
            'per_model_accuracy': per_model_acc,
            'optimal_weights': optimal_weights,
            'weight_change': weight_change,
            # Totals
            'total_tournaments': total_tournaments,
            'total_players_evaluated': total,
            'results': results,
        }

    # ── Breakdown helpers ──

    def _compute_by_tier(self, results):
        """Compute metrics breakdown by player tier."""
        tier_stats = defaultdict(lambda: {'errors': [], 'top10_pred': 0, 'top10_hit': 0, 'total': 0})
        for r in results:
            tier = _classify_tier(r.get('player_name', ''))
            tier_stats[tier]['errors'].append(r.get('finish_error', 0))
            tier_stats[tier]['total'] += 1
            if r.get('predicted_top10_pct', 0) >= 0.10:
                tier_stats[tier]['top10_pred'] += 1
                if r.get('actual_top10', False):
                    tier_stats[tier]['top10_hit'] += 1

        by_tier = {}
        for tier, stats in tier_stats.items():
            errors = stats['errors']
            by_tier[tier] = {
                'players': stats['total'],
                'finish_mae': round(sum(errors) / len(errors), 2) if errors else None,
                'top10_hit_rate': round(stats['top10_hit'] / stats['top10_pred'] * 100, 1) if stats['top10_pred'] > 0 else 0,
            }
        return by_tier

    def _compute_by_course_type(self, results):
        """Compute metrics breakdown by course type."""
        type_stats = defaultdict(lambda: {'errors': [], 'top10_pred': 0, 'top10_hit': 0, 'total': 0})
        for r in results:
            ctype = _classify_course_type(r.get('course_id', ''))
            type_stats[ctype]['errors'].append(r.get('finish_error', 0))
            type_stats[ctype]['total'] += 1
            if r.get('predicted_top10_pct', 0) >= 0.10:
                type_stats[ctype]['top10_pred'] += 1
                if r.get('actual_top10', False):
                    type_stats[ctype]['top10_hit'] += 1

        by_type = {}
        for ctype, stats in type_stats.items():
            errors = stats['errors']
            by_type[ctype] = {
                'players': stats['total'],
                'finish_mae': round(sum(errors) / len(errors), 2) if errors else None,
                'top10_hit_rate': round(stats['top10_hit'] / stats['top10_pred'] * 100, 1) if stats['top10_pred'] > 0 else 0,
            }
        return by_type

    def _compute_by_field_strength(self, results):
        """Compute metrics breakdown by field strength."""
        strength_stats = defaultdict(lambda: {'errors': [], 'top10_pred': 0, 'top10_hit': 0, 'total': 0})
        for r in results:
            strength = _classify_field_strength(r.get('tournament_name', ''))
            strength_stats[strength]['errors'].append(r.get('finish_error', 0))
            strength_stats[strength]['total'] += 1
            if r.get('predicted_top10_pct', 0) >= 0.10:
                strength_stats[strength]['top10_pred'] += 1
                if r.get('actual_top10', False):
                    strength_stats[strength]['top10_hit'] += 1

        by_strength = {}
        for strength, stats in strength_stats.items():
            errors = stats['errors']
            by_strength[strength] = {
                'players': stats['total'],
                'finish_mae': round(sum(errors) / len(errors), 2) if errors else None,
                'top10_hit_rate': round(stats['top10_hit'] / stats['top10_pred'] * 100, 1) if stats['top10_pred'] > 0 else 0,
            }
        return by_strength

    # ── ROI helpers ──

    def _compute_outright_roi(self, results):
        """Compute ROI on outright winner bets using model's top pick per tournament."""
        tournaments = defaultdict(list)
        for r in results:
            if r.get('odds_win_pct') is not None:
                tournaments[r.get('tournament_id', '')].append(r)

        if not tournaments:
            return None

        total_staked = 0
        total_returned = 0
        for tid, t_results in tournaments.items():
            # Bet on player with highest model win probability
            best = max(t_results, key=lambda r: r.get('predicted_win_pct', 0))
            odds_pct = best.get('odds_win_pct', 0)
            if odds_pct and odds_pct > 0:
                total_staked += 1
                if best.get('actual_won', False):
                    total_returned += 1.0 / odds_pct  # implied decimal odds payout

        if total_staked == 0:
            return None
        return round((total_returned - total_staked) / total_staked * 100, 1)

    def _compute_top10_roi(self, results):
        """Compute ROI on top-10 finish bets."""
        bets = [r for r in results
                if r.get('odds_win_pct') is not None and r.get('predicted_top10_pct', 0) >= 0.25]
        if not bets:
            return None

        total_staked = len(bets)
        total_returned = sum(2.0 for r in bets if r.get('actual_top10', False))  # ~2x payout for top 10
        return round((total_returned - total_staked) / total_staked * 100, 1)

    def _compute_value_bet_roi(self, results):
        """Compute ROI on value bets (model_pct > odds_pct + threshold)."""
        value_threshold = 0.02  # 2% edge required
        bets = [r for r in results
                if r.get('odds_win_pct') is not None
                and r.get('predicted_win_pct', 0) > (r.get('odds_win_pct', 0) + value_threshold)]

        if not bets:
            return None

        total_staked = len(bets)
        total_returned = 0
        for r in bets:
            if r.get('actual_won', False):
                odds_pct = r.get('odds_win_pct', 0.01)
                total_returned += 1.0 / odds_pct if odds_pct > 0 else 0

        return round((total_returned - total_staked) / total_staked * 100, 1)

    # ── Validation ──

    def _validate_backtest(self, metrics):
        """Check backtest results for red flags.

        Mirror Backtester._validate_backtest() (backtester.py lines 1360-1377).

        Args:
            metrics: Computed metrics dict

        Returns:
            list: Warning strings (empty if all OK)
        """
        issues = []

        finish_mae = metrics.get('finish_mae')
        if finish_mae is not None:
            if finish_mae < 5:
                issues.append(f"Finish MAE {finish_mae} is suspiciously low. Check for data leakage.")
            if finish_mae > 80:
                issues.append(f"Finish MAE {finish_mae} is very high. Model may be miscalibrated.")

        cut_acc = metrics.get('cut_prediction_accuracy', 0)
        if cut_acc < 40:
            issues.append(f"Cut prediction accuracy {cut_acc}% is below 40%. Model may be inverted.")
        if cut_acc > 90:
            issues.append(f"Cut prediction accuracy {cut_acc}% is suspiciously high. Check for data leakage.")

        top10_rate = metrics.get('top10_hit_rate', 0)
        if top10_rate < 5:
            issues.append(f"Top-10 hit rate {top10_rate}% is very low. Model may not be predictive.")
        if top10_rate > 50:
            issues.append(f"Top-10 hit rate {top10_rate}% is suspiciously high. Check for data leakage.")

        win_brier = metrics.get('win_brier_score')
        if win_brier is not None and win_brier > 0.1:
            issues.append(f"Win Brier score {win_brier} is high. Win probability calibration may be off.")

        return issues
