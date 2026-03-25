"""
Automated Backtesting Pipeline
Replays model predictions against actual ESPN scores for historical dates.
Computes accuracy metrics: pick %, ATS %, MAE, by conference, by spread bucket.

Uses the full 4-model composite pipeline (KenPom + Similar Opponents + ConRat + MC)
matching the frontend logic in static/index.html, with a built-in validation loop.

Historical mode (use_historical=True) fetches per-date KenPom archive
ratings so the backtest only sees data available at game time -- eliminating
lookahead bias. Supplemental data (four factors, misc stats, height, point
distribution) still uses current-season values since the archive endpoint does
not provide them. Weekly batching (Monday of each week) keeps API usage low.

Legacy mode (use_historical=False) uses current-day ratings for all dates,
which introduces lookahead bias but is faster and requires fewer API calls.
"""

import json
import os
from collections import defaultdict
from datetime import datetime, timedelta
import requests

from composite_model import (
    model_efficiency, model_similar_opponents, model_con_rat,
    compute_composite, calc_style_clash, calc_experience_adj,
    calc_conf_adj, calibrate_spread, get_hca, calc_momentum,
)
from matchup_params import (
    prefetch_all_team_data, prefetch_historical_team_data, build_matchup_params,
)
from mc_engine import simulate_game
from model_weight_optimizer import compute_per_model_ats, compute_optimal_weights


ESPN_SCOREBOARD_URL = "https://site.api.espn.com/apis/site/v2/sports/basketball/mens-college-basketball/scoreboard"


def _kenpom_season_year(date_str):
    """Convert a YYYY-MM-DD date to the KenPom season-ending year.

    KenPom indexes the 2024-25 season as year=2025. The season starts in
    November, so any date in Nov-Dec belongs to the *next* calendar year's
    season (e.g. 2024-11-15 → 2025).  Jan-Apr dates already have the
    correct calendar year (e.g. 2025-03-01 → 2025).
    """
    year = int(date_str[:4])
    month = int(date_str[5:7])
    if month >= 10:          # Oct-Dec → next year's season
        return year + 1
    return year


def _fetch_espn_scores(date_str):
    """Fetch final scores from ESPN for a YYYY-MM-DD date."""
    espn_date = date_str.replace('-', '')
    all_events = []
    page = 1
    while True:
        resp = requests.get(ESPN_SCOREBOARD_URL,
                            params={'dates': espn_date, 'limit': 200, 'groups': '50', 'page': page},
                            timeout=15)
        resp.raise_for_status()
        events = resp.json().get('events', [])
        if not events:
            break
        all_events.extend(events)
        if len(events) < 100:
            break
        page += 1

    games = []
    for e in all_events:
        comp = e['competitions'][0]
        status = comp['status']['type']['name']
        if status != 'STATUS_FINAL':
            continue
        teams = comp['competitors']
        home = next((t for t in teams if t['homeAway'] == 'home'), None)
        away = next((t for t in teams if t['homeAway'] == 'away'), None)
        if not home or not away:
            continue

        odds_list = comp.get('odds', [])
        odds = odds_list[0] if odds_list else {}

        games.append({
            'home': home['team'].get('displayName', ''),
            'home_short': home['team'].get('shortDisplayName', ''),
            'away': away['team'].get('displayName', ''),
            'away_short': away['team'].get('shortDisplayName', ''),
            'home_score': int(home.get('score', 0)),
            'away_score': int(away.get('score', 0)),
            'spread': odds.get('spread'),
            'over_under': odds.get('overUnder'),
            'neutral_site': comp.get('neutralSite', False),
        })
    return games


class Backtester:
    """Runs backtests against saved predictions or generates new ones from the model."""

    def __init__(self, predictions_file='predictions.json'):
        self.predictions_file = predictions_file

    def _load_predictions(self):
        if os.path.exists(self.predictions_file):
            try:
                with open(self.predictions_file, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except (json.JSONDecodeError, IOError):
                return []
        return []

    def _normalize(self, name):
        """Normalize team name for matching."""
        return name.lower().replace('.', '').replace("'", '').replace('st ', 'st. ').strip()

    def _match_teams(self, pred_team, score_teams):
        """Find matching team in score data."""
        pn = self._normalize(pred_team)
        for st in score_teams:
            sn = self._normalize(st)
            if pn == sn or pn in sn or sn in pn:
                return st
        return None

    def backtest_predictions(self):
        """Backtest all saved predictions that have results entered."""
        preds = self._load_predictions()
        completed = [p for p in preds if p.get('result_entered')]
        if not completed:
            return {'error': 'No completed predictions to backtest', 'results': []}

        results = []
        for p in completed:
            actual_margin = (p.get('actual_t1_score', 0) or 0) - (p.get('actual_t2_score', 0) or 0)
            pred_margin = (p.get('predicted_t1_score', 0) or 0) - (p.get('predicted_t2_score', 0) or 0)

            results.append({
                'date': p.get('game_date'),
                'team1': p.get('team1'),
                'team2': p.get('team2'),
                'predicted_winner': p.get('predicted_winner'),
                'actual_winner': p.get('actual_winner'),
                'pick_correct': p.get('pick_correct', False),
                'predicted_spread': pred_margin,
                'actual_spread': actual_margin,
                'spread_error': abs(actual_margin - pred_margin),
                'predicted_total': (p.get('predicted_t1_score', 0) or 0) + (p.get('predicted_t2_score', 0) or 0),
                'actual_total': (p.get('actual_t1_score', 0) or 0) + (p.get('actual_t2_score', 0) or 0),
                'confidence': p.get('confidence'),
                'model_agreement': p.get('model_agreement'),
            })

        return self._compute_metrics(results)

    def _get_archive_for_date(self, date_str, kenpom_client, cache):
        """Get archive data for a date, using weekly batching to reduce API calls.

        Fetches archive for the Monday of the week containing date_str.
        Ratings don't change dramatically within a week, so this is a good tradeoff.

        Returns:
            tuple: (list of team archive dicts or None, was_api_call: bool)
        """
        dt = datetime.strptime(date_str, '%Y-%m-%d')
        monday = dt - timedelta(days=dt.weekday())
        monday_str = monday.strftime('%Y-%m-%d')

        cache_key = 'archive_historical'
        cache_params = {'date': monday_str}
        cached = cache.get(cache_key, cache_params, ttl=86400 * 365)
        if cached is not None:
            return cached, False

        data = kenpom_client.get_archive(date=monday_str)
        cache.set(cache_key, cache_params, data)
        return data, True

    def backtest_date_range(self, start_date, end_date, kenpom_client, cache,
                            use_historical=False):
        """
        Backtest a range of dates using KenPom fanmatch data + ESPN scores.
        This is the full automated pipeline that doesn't require pre-saved predictions.
        Uses the full 4-model composite pipeline.

        Args:
            start_date: YYYY-MM-DD start date
            end_date: YYYY-MM-DD end date
            kenpom_client: KenpomClient instance
            cache: SQLiteCache instance
            use_historical: If True, use per-date archive ratings to
                eliminate lookahead bias and enable momentum enrichment.
                If False (default), use current-day ratings (legacy behavior).
                The UI checkbox sends historical=true explicitly when checked.
        """
        # -- Load stored calibration overrides --
        stored_cal = cache.get('spread_calibration', {}, ttl=86400 * 365) or {}
        calibration_coeffs = None
        if stored_cal.get('close_coeff') is not None:
            calibration_coeffs = {
                'close': stored_cal['close_coeff'],
                'moderate': stored_cal.get('moderate_coeff', 0.85),
                'logMult': stored_cal.get('log_multiplier', 3.5),
            }
        conf_overrides = cache.get('conf_adjustments', {}, ttl=86400 * 365) or {}
        if not conf_overrides:
            conf_overrides = None

        # -- One-time bulk data prefetch (always needed for supplemental data) --
        year = _kenpom_season_year(start_date)
        try:
            current_team_data = prefetch_all_team_data(kenpom_client, cache, year=year)
        except Exception as e:
            print(f"[backtest] Failed to prefetch team data: {e}")
            current_team_data = {}

        # Fetch conference ratings once
        conf_map = {}
        try:
            conf_ratings = cache.get('conf-ratings', {'year': year})
            if conf_ratings is None:
                conf_ratings = kenpom_client.get_conference_ratings(year=year)
                cache.set('conf-ratings', {'year': year}, conf_ratings)
            if isinstance(conf_ratings, list):
                for c in conf_ratings:
                    conf_short = c.get('ConfShort')
                    if conf_short and conf_short not in conf_map:
                        conf_map[conf_short] = c.get('Rating', 0)
        except Exception as e:
            print(f"[backtest] Failed to fetch conference ratings: {e}")

        results = []
        archive_calls = 0
        current = datetime.strptime(start_date, '%Y-%m-%d')
        end = datetime.strptime(end_date, '%Y-%m-%d')

        mode_label = 'historical' if use_historical else 'current'
        print(f"[backtest] Running in {mode_label} mode from {start_date} to {end_date}")

        while current <= end:
            date_str = current.strftime('%Y-%m-%d')

            # Build team_data for this day
            if use_historical:
                try:
                    archive_data, was_api_call = self._get_archive_for_date(date_str, kenpom_client, cache)
                    if was_api_call:
                        archive_calls += 1
                    if archive_data and isinstance(archive_data, list) and len(archive_data) > 0:
                        # Pass pre-fetched archive_data to avoid a redundant API call
                        historical_team_data = prefetch_historical_team_data(
                            kenpom_client, cache, date_str,
                            supplemental_data=current_team_data,
                            archive_data=archive_data,
                        )
                        if historical_team_data:
                            day_team_data = historical_team_data
                        else:
                            print(f"[backtest] No archive data for {date_str}, falling back to current ratings")
                            day_team_data = current_team_data
                    else:
                        print(f"[backtest] No archive data for {date_str}, falling back to current ratings")
                        day_team_data = current_team_data
                except Exception as e:
                    print(f"[backtest] Archive fetch failed for {date_str}: {e}, using current ratings")
                    day_team_data = current_team_data
            else:
                day_team_data = current_team_data

            if archive_calls > 150:
                print(f"[backtest] WARNING: {archive_calls} archive API calls used. Rate limit is 200/hour.")

            try:
                day_results = self._backtest_single_day(
                    date_str, kenpom_client, cache,
                    team_data=day_team_data, conf_map=conf_map,
                    calibration_coeffs=calibration_coeffs,
                    conf_overrides=conf_overrides,
                    use_historical=use_historical,
                )
                results.extend(day_results)
            except Exception as e:
                print(f"[backtest] Skipping {date_str}: {e}")
            current += timedelta(days=1)

        if not results:
            return {'error': 'No games found in date range', 'results': []}

        metrics = self._compute_metrics(results,
                                         calibration_coeffs=calibration_coeffs,
                                         conf_overrides=conf_overrides)

        # Dynamic weight optimization (Part A)
        per_model_ats = compute_per_model_ats(results)
        optimal_weights = compute_optimal_weights(per_model_ats)
        base_weights = {'efficiency': 0.10, 'similar': 0.10, 'conrat': 0.20, 'mc': 0.60}
        metrics['per_model_ats'] = per_model_ats
        metrics['optimal_weights'] = optimal_weights
        metrics['weight_change'] = {
            name: round(optimal_weights[name] - base, 4)
            for name, base in base_weights.items()
        }

        # Validation step
        validation_issues = self._validate_backtest(metrics)
        if validation_issues:
            metrics['validation_issues'] = validation_issues

        # Metadata (Step 10)
        metrics['meta'] = {
            'mode': mode_label,
            'lookahead_bias': 'eliminated' if use_historical else 'present (current-day ratings)',
            'momentum_enabled': use_historical,
            'supplemental_data': 'current-season (ff, ms, ht, pd not available historically)',
            'archive_calls': archive_calls,
            'date_range': f'{start_date} to {end_date}',
            'weekly_batching': use_historical,
        }

        return metrics

    def backtest_with_bias_comparison(self, start_date, end_date, kenpom_client, cache):
        """Run backtest twice: with and without historical data, and compare.

        Returns:
            dict with both sets of metrics plus a 'bias_analysis' section showing
            how much lookahead bias inflates accuracy.
        """
        historical_metrics = self.backtest_date_range(
            start_date, end_date, kenpom_client, cache, use_historical=True)

        current_metrics = self.backtest_date_range(
            start_date, end_date, kenpom_client, cache, use_historical=False)

        bias_analysis = {
            'historical_pick_accuracy': historical_metrics.get('our_pick_accuracy'),
            'current_pick_accuracy': current_metrics.get('our_pick_accuracy'),
            'lookahead_bias_pct': round(
                (current_metrics.get('our_pick_accuracy', 0) -
                 historical_metrics.get('our_pick_accuracy', 0)), 1),
            'historical_avg_error': historical_metrics.get('our_avg_spread_error'),
            'current_avg_error': current_metrics.get('our_avg_spread_error'),
            'error_improvement_from_lookahead': round(
                (historical_metrics.get('our_avg_spread_error', 0) -
                 current_metrics.get('our_avg_spread_error', 0)), 1),
            'momentum_enabled': True,
        }

        return {
            'historical': historical_metrics,
            'current': current_metrics,
            'bias_analysis': bias_analysis,
        }

    # -- Edge case: fuzzy team name matching --

    def _fuzzy_find_team(self, team_name, team_data):
        """Try to find a team in team_data with fuzzy matching."""
        if team_name in team_data:
            return team_name
        # Try without dots
        norm = team_name.replace('.', '').strip()
        for key in team_data:
            if key.replace('.', '').strip() == norm:
                return key
        # Try substring matching
        lower_name = team_name.lower()
        for key in team_data:
            if lower_name in key.lower() or key.lower() in lower_name:
                return key
        return None

    def _backtest_single_day(self, date_str, kenpom_client, cache,
                             team_data=None, conf_map=None,
                             calibration_coeffs=None, conf_overrides=None,
                             use_historical=False):
        """Backtest a single day: fetch fanmatch + scores, compare predictions."""
        if team_data is None:
            team_data = {}
        if conf_map is None:
            conf_map = {}

        # Get fanmatch data (KenPom's game list for the day)
        try:
            fanmatch = kenpom_client.get_fanmatch(date=date_str)
            if not fanmatch or not isinstance(fanmatch, list):
                return []
        except Exception:
            return []

        # Get actual scores
        try:
            scores = _fetch_espn_scores(date_str)
            if not scores:
                return []
        except Exception:
            return []

        # Build ratings_map from team_data (which may be historical or current)
        ratings_map = {}
        for team_name, td in team_data.items():
            r = td.get('ratings', {})
            if r:
                ratings_map[team_name] = r

        # Fallback: if no ratings in team_data, fetch current ratings directly
        if not ratings_map:
            try:
                ratings_year = _kenpom_season_year(date_str)
                ratings = cache.get('ratings_backtest', {'year': ratings_year})
                if ratings is None:
                    ratings = kenpom_client.get_ratings(year=ratings_year)
                    cache.set('ratings_backtest', {'year': ratings_year}, ratings)
                if ratings and isinstance(ratings, list):
                    ratings_map = {r.get('TeamName', ''): r for r in ratings}
            except Exception:
                return []

        if not ratings_map:
            return []

        # Pre-fetch momentum archive (28 days prior) if historical mode is enabled
        momentum_archive_map = {}
        if use_historical:
            month = int(date_str[5:7])
            year_for_season = int(date_str[:4]) if month >= 10 else int(date_str[:4]) - 1
            momentum_date = (datetime.strptime(date_str, '%Y-%m-%d') - timedelta(days=28)).strftime('%Y-%m-%d')
            # Skip momentum if 28 days ago is before season start (November)
            if momentum_date >= f'{year_for_season}-11-01':
                try:
                    arch_28d, _ = self._get_archive_for_date(momentum_date, kenpom_client, cache)
                    if arch_28d and isinstance(arch_28d, list):
                        momentum_archive_map = {t.get('TeamName', ''): t for t in arch_28d}
                except Exception:
                    pass

        results = []
        for g in fanmatch:
            home = g.get('Home', '')
            visitor = g.get('Visitor', '')
            if not home or not visitor:
                continue

            # Find matching score
            score = self._find_score(home, visitor, scores)
            if not score:
                continue

            # KenPom prediction
            home_pred = g.get('HomePred', 0) or 0
            vis_pred = g.get('VisitorPred', 0) or 0
            kp_margin = home_pred - vis_pred  # home-relative
            kp_winner = home if kp_margin >= 0 else visitor

            # Get ratings data for both teams (fuzzy match into ratings_map)
            vis_key = self._fuzzy_find_team(visitor, team_data) or self._fuzzy_find_team(visitor, ratings_map)
            home_key = self._fuzzy_find_team(home, team_data) or self._fuzzy_find_team(home, ratings_map)
            dV = ratings_map.get(vis_key or visitor)
            dH = ratings_map.get(home_key or home)
            if not dH or not dV:
                continue

            # -- Simple prediction (kept for comparison) --
            is_neutral = score.get('neutral_site', False)
            simple_hca = 3.5 if not is_neutral else 0
            simple_margin = ((dH.get('AdjEM', 0) - dV.get('AdjEM', 0)) + simple_hca) * 0.85
            simple_winner = home if simple_margin >= 0 else visitor

            # -- Full composite prediction --
            # Conference-tier HCA
            hca2 = get_hca(dH.get('ConfShort', '')) if not is_neutral else 0
            hca1 = 0  # visitor never gets HCA

            # Build enrichment data from prefetched team_data
            vis_td = team_data.get(vis_key, {}) if vis_key else {}
            home_td = team_data.get(home_key, {}) if home_key else {}

            ff1_data = vis_td.get('ff', {})
            ff2_data = home_td.get('ff', {})
            ht1_data = vis_td.get('ht', {})
            ht2_data = home_td.get('ht', {})
            ms1_data = vis_td.get('ms', {})
            ms2_data = home_td.get('ms', {})

            # Merge supplemental data for ConRat
            dV_enriched = dict(dV)
            dH_enriched = dict(dH)
            dV_enriched['_ff'] = ff1_data
            dV_enriched['_ms'] = ms1_data
            dV_enriched['_ht'] = ht1_data
            dH_enriched['_ff'] = ff2_data
            dH_enriched['_ms'] = ms2_data
            dH_enriched['_ht'] = ht2_data

            # Enrichment factors
            style_clash = calc_style_clash(ff1_data, ff2_data)
            experience = calc_experience_adj(ht1_data, ht2_data)
            conf_strength = calc_conf_adj(dV_enriched, dH_enriched, conf_map,
                                          conf_overrides=conf_overrides)

            # Momentum: compute from 28-day archive if in historical mode
            if use_historical and momentum_archive_map:
                arch1 = momentum_archive_map.get(vis_key or visitor)
                arch2 = momentum_archive_map.get(home_key or home)
                current_vis = team_data.get(vis_key, {}).get('ratings', {}) if vis_key else {}
                current_home = team_data.get(home_key, {}).get('ratings', {}) if home_key else {}
                momentum = calc_momentum(arch1, arch2, current_vis, current_home)
            else:
                momentum = {'adj': 0}

            extra = {
                'style_clash': style_clash,
                'experience': experience,
                'momentum': momentum,
                'conf_strength': conf_strength,
                'injury_adj': 0,  # No historical injury data
            }

            # Run 4 models
            eff = model_efficiency(dV_enriched, dH_enriched, hca1, hca2, extra)
            sim = model_similar_opponents(dV_enriched, dH_enriched, hca1, hca2, extra)
            cr = model_con_rat(dV_enriched, dH_enriched, hca1, hca2, extra)

            # MC simulation (reduced sims for backtest speed)
            try:
                mc_params = build_matchup_params(
                    vis_key or visitor, home_key or home, team_data,
                    hca1=hca1, hca2=hca2,
                )
                mc_result = simulate_game(mc_params, num_sims=200)
                mc = {
                    't1_score': mc_result['t1_score'],
                    't2_score': mc_result['t2_score'],
                    'margin': mc_result['margin'],
                    't1_win_prob': mc_result['t1_win_prob'],
                    'tempo': mc_params.get('game_tempo_ctr', 67.5),
                }
            except Exception:
                # Fallback: use efficiency model margin for MC
                mc = dict(eff)

            # Composite
            composite = compute_composite(eff, sim, cr, mc, dV_enriched, dH_enriched,
                                          calibration_coeffs=calibration_coeffs)
            our_margin = composite['margin']  # visitor-relative (positive = visitor favored)
            our_winner = visitor if our_margin >= 0 else home

            # Actual result
            actual_margin = score['home_score'] - score['away_score']
            actual_winner = home if actual_margin >= 0 else visitor

            # Signed error for calibration: positive = overpredicts magnitude, negative = underpredicts
            # Use direction-independent metric so home-favored and away-favored don't cancel
            # when bucketed by absolute spread
            home_pred_margin = -our_margin
            signed_error = round(abs(home_pred_margin) - abs(actual_margin), 1)

            # Sub-model signed errors (direction-independent: abs(pred) - abs(actual))
            sub_model_signed_errors = {}
            for name, margin in {'efficiency': eff['margin'], 'similar': sim['margin'],
                                  'conrat': cr['margin'], 'mc': mc['margin']}.items():
                sub_model_signed_errors[name] = round(abs(margin) - abs(actual_margin), 1)

            # Build result dict
            pred_result = {
                'date': date_str,
                'team1': visitor,
                'team2': home,
                'predicted_winner': our_winner,
                'kp_predicted_winner': kp_winner,
                'actual_winner': actual_winner,
                'pick_correct': our_winner == actual_winner,
                'kp_pick_correct': kp_winner == actual_winner,
                'predicted_spread': our_margin,
                'kp_spread': kp_margin,
                'actual_spread': actual_margin,
                'spread_error': abs(actual_margin - (-our_margin)),
                'kp_spread_error': abs(actual_margin - kp_margin),
                'vegas_spread': score.get('spread'),
                'predicted_total': composite.get('t1_score', 0) + composite.get('t2_score', 0),
                'actual_total': score['home_score'] + score['away_score'],
                # New composite fields
                'predicted_t1_score': composite.get('t1_score', 0),
                'predicted_t2_score': composite.get('t2_score', 0),
                'simple_spread': simple_margin,
                'simple_winner': simple_winner,
                'simple_correct': simple_winner == actual_winner,
                'simple_spread_error': abs(actual_margin - simple_margin),
                'composite_weights': composite.get('weights', {}),
                'model_agreement': composite.get('model_agreement', 0),
                'confidence': composite.get('confidence', 'Unknown'),
                'home_conf': dH_enriched.get('ConfShort', 'Unknown'),
                'away_conf': dV_enriched.get('ConfShort', 'Unknown'),
                'cross_conf': dH_enriched.get('ConfShort', '') != dV_enriched.get('ConfShort', ''),
                'signed_error': signed_error,
                'predicted_abs_spread': round(abs(our_margin), 1),
                'sub_model_margins': {
                    'efficiency': eff['margin'],
                    'similar': sim['margin'],
                    'conrat': cr['margin'],
                    'mc': mc['margin'],
                },
                'sub_model_signed_errors': sub_model_signed_errors,
                'sub_model_totals': {
                    'efficiency': eff['t1_score'] + eff['t2_score'],
                    'similar': sim['t1_score'] + sim['t2_score'],
                    'conrat': cr['t1_score'] + cr['t2_score'],
                    'mc': mc['t1_score'] + mc['t2_score'],
                },
            }

            # O/U (Over/Under) tracking
            vegas_ou = score.get('over_under')
            pred_result['vegas_ou'] = vegas_ou

            if vegas_ou is not None and vegas_ou != 0 and pred_result['actual_total'] > 0:
                try:
                    vegas_ou_f = float(vegas_ou)
                except (TypeError, ValueError):
                    vegas_ou_f = None

                if vegas_ou_f is not None and vegas_ou_f > 0:
                    pred_result['ou_edge'] = round(abs(pred_result['predicted_total'] - vegas_ou_f), 1)
                    pred_result['ou_bet_side'] = 'over' if pred_result['predicted_total'] > vegas_ou_f else 'under'
                    actual_vs_ou = pred_result['actual_total'] - vegas_ou_f
                    if actual_vs_ou == 0:
                        pred_result['ou_result'] = 'push'
                    elif pred_result['ou_bet_side'] == 'over':
                        pred_result['ou_result'] = 'hit' if actual_vs_ou > 0 else 'miss'
                    else:
                        pred_result['ou_result'] = 'hit' if actual_vs_ou < 0 else 'miss'

                    # Signed total error: positive = we predicted higher total than actual
                    pred_result['total_signed_error'] = round(pred_result['predicted_total'] - pred_result['actual_total'], 1)

                    # Flag possible overtime games (total > 170 is unusual for regulation)
                    pred_result['is_overtime'] = pred_result['actual_total'] > 170
                else:
                    pred_result['ou_edge'] = None
                    pred_result['ou_bet_side'] = None
                    pred_result['ou_result'] = None
                    pred_result['total_signed_error'] = round(pred_result['predicted_total'] - pred_result['actual_total'], 1) if pred_result['actual_total'] > 0 else None
                    pred_result['is_overtime'] = False
            else:
                pred_result['ou_edge'] = None
                pred_result['ou_bet_side'] = None
                pred_result['ou_result'] = None
                pred_result['total_signed_error'] = round(pred_result['predicted_total'] - pred_result['actual_total'], 1) if pred_result['actual_total'] > 0 else None
                pred_result['is_overtime'] = pred_result.get('actual_total', 0) > 170

            # Situational context tagging
            is_neutral = score.get('neutral_site', False)
            vis_conf = dV_enriched.get('ConfShort', '')
            home_conf_tag = dH_enriched.get('ConfShort', '')
            month = int(date_str[5:7])
            is_conf_tournament = is_neutral and vis_conf == home_conf_tag and month == 3
            is_ncaa_tournament = is_neutral and vis_conf != home_conf_tag and month in (3, 4)
            # Season phase: count days from Nov 1
            year_for_season = int(date_str[:4]) if month >= 10 else int(date_str[:4]) - 1
            try:
                day_of_season = (datetime.strptime(date_str, '%Y-%m-%d') -
                                 datetime(year_for_season, 11, 1)).days
            except ValueError:
                day_of_season = 90
            is_early_season = day_of_season < 45  # Before mid-December
            is_late_season = 2 <= month <= 4  # February through April

            pred_result['context'] = {
                'neutral_site': is_neutral,
                'conf_tournament': is_conf_tournament,
                'ncaa_tournament': is_ncaa_tournament,
                'early_season': is_early_season,
                'late_season': is_late_season,
                'same_conference': vis_conf == home_conf_tag,
                'cross_conference': vis_conf != home_conf_tag,
                'month': month,
            }

            # Sanity-check this prediction
            warnings = self._validate_prediction(pred_result)
            if warnings:
                pred_result['warnings'] = warnings

            results.append(pred_result)

        return results

    def _find_score(self, home, visitor, scores):
        """Match a fanmatch game to ESPN score data."""
        nh = self._normalize(home)
        nv = self._normalize(visitor)
        for sc in scores:
            sh = self._normalize(sc['home'])
            shs = self._normalize(sc['home_short'])
            sa = self._normalize(sc['away'])
            sas = self._normalize(sc['away_short'])
            if ((nh in sh or sh in nh or nh in shs or shs in nh) and
                    (nv in sa or sa in nv or nv in sas or sas in nv)):
                return sc
        return None


    def _compute_metrics(self, results, calibration_coeffs=None, conf_overrides=None):
        """Compute accuracy metrics from backtest results, including composite vs simple comparison."""
        if not results:
            return {'total_games': 0, 'results': []}

        total = len(results)
        picks_correct = sum(1 for r in results if r.get('pick_correct'))
        spread_errors = [r['spread_error'] for r in results if r.get('spread_error') is not None]
        kp_picks = sum(1 for r in results if r.get('kp_pick_correct'))
        kp_errors = [r.get('kp_spread_error', 0) for r in results if r.get('kp_spread_error') is not None]

        # Sort for median
        spread_errors_sorted = sorted(spread_errors) if spread_errors else []

        # -- Simple-vs-composite comparison (Step 3a) --
        simple_picks = sum(1 for r in results if r.get('simple_correct'))
        simple_errors = [r.get('simple_spread_error', 0) for r in results if r.get('simple_spread_error') is not None]

        # -- Per-sub-model accuracy tracking (Step 3b) --
        sub_model_correct = {'efficiency': 0, 'similar': 0, 'conrat': 0, 'mc': 0}
        sub_model_errors = {'efficiency': [], 'similar': [], 'conrat': [], 'mc': []}
        for r in results:
            subs = r.get('sub_model_margins', {})
            for model_name, model_margin in subs.items():
                model_winner = r['team1'] if model_margin >= 0 else r['team2']
                if model_winner == r.get('actual_winner'):
                    sub_model_correct[model_name] += 1
                actual_margin_home = r.get('actual_spread', 0)
                model_margin_home = -model_margin  # convert visitor-relative to home-relative
                sub_model_errors[model_name].append(abs(actual_margin_home - model_margin_home))

        # -- By-conference breakdown (Step 3c) --
        conf_stats = {}
        for r in results:
            home_conf = r.get('home_conf', 'Unknown')
            if home_conf not in conf_stats:
                conf_stats[home_conf] = {'games': 0, 'correct': 0, 'errors': []}
            conf_stats[home_conf]['games'] += 1
            if r.get('pick_correct'):
                conf_stats[home_conf]['correct'] += 1
            if r.get('spread_error') is not None:
                conf_stats[home_conf]['errors'].append(r['spread_error'])

        conf_breakdown = {}
        for cname, cdata in conf_stats.items():
            if cdata['games'] > 0:
                conf_breakdown[cname] = {
                    'games': cdata['games'],
                    'pick_pct': round(cdata['correct'] / cdata['games'] * 100, 1),
                    'avg_error': round(sum(cdata['errors']) / len(cdata['errors']), 1) if cdata['errors'] else None,
                }

        # By spread bucket
        buckets = {'0-3': [], '3-7': [], '7-12': [], '12-20': [], '20+': []}
        for r in results:
            margin = abs(r.get('predicted_spread', 0))
            if margin <= 3:
                buckets['0-3'].append(r)
            elif margin <= 7:
                buckets['3-7'].append(r)
            elif margin <= 12:
                buckets['7-12'].append(r)
            elif margin <= 20:
                buckets['12-20'].append(r)
            else:
                buckets['20+'].append(r)

        bucket_stats = {}
        for bname, bgames in buckets.items():
            if bgames:
                bc = sum(1 for g in bgames if g.get('pick_correct'))
                be = [g['spread_error'] for g in bgames if g.get('spread_error') is not None]
                bucket_stats[bname] = {
                    'games': len(bgames),
                    'pick_pct': round(bc / len(bgames) * 100, 1),
                    'avg_error': round(sum(be) / len(be), 1) if be else None,
                }

        # Signed error by spread bucket (aligned with calibrateSpread breakpoints)
        signed_buckets = {'0-3': [], '3-7': [], '7-14': [], '14-20': [], '20+': []}
        for r in results:
            margin = abs(r.get('predicted_spread', 0))
            se = r.get('signed_error')
            if se is None:
                continue
            if margin <= 3:
                signed_buckets['0-3'].append(se)
            elif margin <= 7:
                signed_buckets['3-7'].append(se)
            elif margin <= 14:
                signed_buckets['7-14'].append(se)
            elif margin <= 20:
                signed_buckets['14-20'].append(se)
            else:
                signed_buckets['20+'].append(se)

        signed_bucket_stats = {}
        for bname, errors in signed_buckets.items():
            if errors:
                mean_se = sum(errors) / len(errors)
                signed_bucket_stats[bname] = {
                    'games': len(errors),
                    'mean_signed_error': round(mean_se, 2),
                    'direction': 'overpredicts' if mean_se > 0.5 else 'underpredicts' if mean_se < -0.5 else 'accurate',
                    'std_dev': round((sum((e - mean_se)**2 for e in errors) / len(errors))**0.5, 2),
                }

        # Conference-level analysis (both home and away conferences tracked)
        conf_stats_full = {}
        for r in results:
            home_conf = r.get('home_conf', 'Unknown')
            away_conf = r.get('away_conf', 'Unknown')

            for conf, role in [(home_conf, 'home'), (away_conf, 'away')]:
                if not conf:
                    continue
                if conf not in conf_stats_full:
                    conf_stats_full[conf] = {
                        'games': 0, 'correct': 0,
                        'errors': [], 'signed_errors': [],
                        'ats_hits': 0, 'ats_misses': 0,
                        'as_home': 0, 'as_away': 0,
                    }
                conf_stats_full[conf]['games'] += 1
                conf_stats_full[conf][f'as_{role}'] += 1
                if r.get('pick_correct'):
                    conf_stats_full[conf]['correct'] += 1
                if r.get('spread_error') is not None:
                    conf_stats_full[conf]['errors'].append(r['spread_error'])
                if r.get('signed_error') is not None:
                    conf_stats_full[conf]['signed_errors'].append(r['signed_error'])
                # ATS tracking (only if vegas_spread available)
                if r.get('vegas_spread') is not None and r.get('signed_error') is not None:
                    vegas = r['vegas_spread']
                    our_margin_val = -r.get('predicted_spread', 0)  # convert visitor-relative to home-relative
                    actual = r.get('actual_spread', 0)
                    if abs(our_margin_val - vegas) > 0.5:
                        our_side_covered = (our_margin_val > vegas and actual > vegas) or \
                                           (our_margin_val < vegas and actual < vegas)
                        if our_side_covered:
                            conf_stats_full[conf]['ats_hits'] += 1
                        else:
                            conf_stats_full[conf]['ats_misses'] += 1

        by_conference_full = {}
        for conf, stats in sorted(conf_stats_full.items(), key=lambda x: -x[1]['games']):
            if stats['games'] < 5:
                continue
            ats_total = stats['ats_hits'] + stats['ats_misses']
            mse = (sum(stats['signed_errors']) / len(stats['signed_errors'])) if stats['signed_errors'] else None
            by_conference_full[conf] = {
                'games': stats['games'],
                'pick_pct': round(stats['correct'] / stats['games'] * 100, 1),
                'avg_error': round(sum(stats['errors']) / len(stats['errors']), 1) if stats['errors'] else None,
                'mean_signed_error': round(mse, 2) if mse is not None else None,
                'bias_direction': 'overpredicts' if mse is not None and mse > 1.0 else
                                  'underpredicts' if mse is not None and mse < -1.0 else 'neutral',
                'ats_pct': round(stats['ats_hits'] / ats_total * 100, 1) if ats_total > 0 else None,
                'ats_record': f"{stats['ats_hits']}-{stats['ats_misses']}" if ats_total > 0 else None,
                'as_home': stats['as_home'],
                'as_away': stats['as_away'],
            }

        # Cross-conference vs intra-conference analysis
        cross_conf_games = [r for r in results if r.get('cross_conf')]
        intra_conf_games = [r for r in results if not r.get('cross_conf')]

        cross_conf_stats = {}
        if cross_conf_games:
            cc_correct = sum(1 for r in cross_conf_games if r.get('pick_correct'))
            cc_errors = [r['spread_error'] for r in cross_conf_games if r.get('spread_error') is not None]
            cc_signed = [r['signed_error'] for r in cross_conf_games if r.get('signed_error') is not None]
            cross_conf_stats = {
                'games': len(cross_conf_games),
                'pick_pct': round(cc_correct / len(cross_conf_games) * 100, 1),
                'avg_error': round(sum(cc_errors) / len(cc_errors), 1) if cc_errors else None,
                'mean_signed_error': round(sum(cc_signed) / len(cc_signed), 2) if cc_signed else None,
            }

        intra_conf_stats = {}
        if intra_conf_games:
            ic_correct = sum(1 for r in intra_conf_games if r.get('pick_correct'))
            ic_errors = [r['spread_error'] for r in intra_conf_games if r.get('spread_error') is not None]
            ic_signed = [r['signed_error'] for r in intra_conf_games if r.get('signed_error') is not None]
            intra_conf_stats = {
                'games': len(intra_conf_games),
                'pick_pct': round(ic_correct / len(intra_conf_games) * 100, 1),
                'avg_error': round(sum(ic_errors) / len(ic_errors), 1) if ic_errors else None,
                'mean_signed_error': round(sum(ic_signed) / len(ic_signed), 2) if ic_signed else None,
            }

        # By date
        date_stats = {}
        for r in results:
            d = r.get('date', 'unknown')
            if d not in date_stats:
                date_stats[d] = {'games': 0, 'correct': 0, 'errors': []}
            date_stats[d]['games'] += 1
            if r.get('pick_correct'):
                date_stats[d]['correct'] += 1
            if r.get('spread_error') is not None:
                date_stats[d]['errors'].append(r['spread_error'])

        daily = []
        for d, ds in sorted(date_stats.items()):
            daily.append({
                'date': d,
                'games': ds['games'],
                'pick_pct': round(ds['correct'] / ds['games'] * 100, 1) if ds['games'] else 0,
                'avg_error': round(sum(ds['errors']) / len(ds['errors']), 1) if ds['errors'] else None,
            })

        # Build sub-model accuracy dict
        sub_model_accuracy = {}
        for name, correct in sub_model_correct.items():
            errs = sub_model_errors[name]
            sub_model_accuracy[name] = {
                'pick_pct': round(correct / total * 100, 1) if total else 0,
                'avg_error': round(sum(errs) / len(errs), 1) if errs else None,
            }

        # By-context breakdown (Part C: Situational Spot Adjustments)
        context_stats = defaultdict(lambda: {'games': 0, 'correct': 0, 'errors': [], 'signed_errors': []})
        for r in results:
            ctx = r.get('context', {})
            for tag, active in ctx.items():
                if active and isinstance(active, bool):
                    context_stats[tag]['games'] += 1
                    if r.get('pick_correct'):
                        context_stats[tag]['correct'] += 1
                    if r.get('spread_error') is not None:
                        context_stats[tag]['errors'].append(r['spread_error'])
                    se = r.get('signed_error')
                    if se is not None:
                        context_stats[tag]['signed_errors'].append(se)

        by_context = {}
        for tag, stats in context_stats.items():
            if stats['games'] >= 5:
                mse = sum(stats['signed_errors']) / len(stats['signed_errors']) if stats['signed_errors'] else 0
                by_context[tag] = {
                    'games': stats['games'],
                    'pick_pct': round(stats['correct'] / stats['games'] * 100, 1),
                    'avg_error': round(sum(stats['errors']) / len(stats['errors']), 1) if stats['errors'] else None,
                    'mean_signed_error': round(mse, 2),
                    'bias': 'overpredicts' if mse > 1.5 else 'underpredicts' if mse < -1.5 else 'neutral',
                }

        # ── O/U (Over/Under) metrics ──
        ou_results = [r for r in results if r.get('ou_result') is not None]
        ou_hits = sum(1 for r in ou_results if r['ou_result'] == 'hit')
        ou_misses = sum(1 for r in ou_results if r['ou_result'] == 'miss')
        ou_pushes = sum(1 for r in ou_results if r['ou_result'] == 'push')
        ou_total = ou_hits + ou_misses
        ou_pct = round(ou_hits / ou_total * 100, 1) if ou_total > 0 else None

        # High-conviction O/U (edge >= 3 pts)
        ou_hc = [r for r in ou_results if (r.get('ou_edge') or 0) >= 3]
        ou_hc_hits = sum(1 for r in ou_hc if r['ou_result'] == 'hit')
        ou_hc_misses = sum(1 for r in ou_hc if r['ou_result'] == 'miss')
        ou_hc_total = ou_hc_hits + ou_hc_misses
        ou_hc_pct = round(ou_hc_hits / ou_hc_total * 100, 1) if ou_hc_total > 0 else None

        # Total prediction error
        total_errors = [abs(r['predicted_total'] - r['actual_total']) for r in results
                        if r.get('predicted_total') and r.get('actual_total')]
        total_signed_errors = [r['total_signed_error'] for r in results if r.get('total_signed_error') is not None]
        avg_total_error = round(sum(total_errors) / len(total_errors), 1) if total_errors else None
        mean_total_signed_error = round(sum(total_signed_errors) / len(total_signed_errors), 2) if total_signed_errors else None

        # O/U by edge bucket
        ou_edge_buckets = {'0-2': [], '2-4': [], '4-6': [], '6+': []}
        for r in ou_results:
            edge = r.get('ou_edge') or 0
            if edge < 2:
                ou_edge_buckets['0-2'].append(r)
            elif edge < 4:
                ou_edge_buckets['2-4'].append(r)
            elif edge < 6:
                ou_edge_buckets['4-6'].append(r)
            else:
                ou_edge_buckets['6+'].append(r)

        ou_edge_stats = {}
        for bname, games in ou_edge_buckets.items():
            if games:
                h = sum(1 for g in games if g['ou_result'] == 'hit')
                m = sum(1 for g in games if g['ou_result'] == 'miss')
                t = h + m
                ou_edge_stats[bname] = {
                    'games': t,
                    'hit_pct': round(h / t * 100, 1) if t > 0 else None,
                    'record': f"{h}-{m}",
                }

        # Sub-model total accuracy (which sub-model predicts totals best)
        sub_model_total_errors = {'efficiency': [], 'similar': [], 'conrat': [], 'mc': []}
        for r in results:
            subs = r.get('sub_model_totals', {})
            actual_t = r.get('actual_total', 0)
            if actual_t > 0:
                for model_name, model_total in subs.items():
                    if model_total > 0:
                        sub_model_total_errors[model_name].append(abs(model_total - actual_t))

        sub_model_total_accuracy = {}
        for name, errs in sub_model_total_errors.items():
            if errs:
                sub_model_total_accuracy[name] = {
                    'avg_total_error': round(sum(errs) / len(errs), 1),
                    'games': len(errs),
                }

        # Total bias direction
        total_bias = 'neutral'
        if mean_total_signed_error is not None:
            if mean_total_signed_error > 2:
                total_bias = 'overpredicts'
            elif mean_total_signed_error < -2:
                total_bias = 'underpredicts'

        return {
            'total_games': total,
            'our_pick_accuracy': round(picks_correct / total * 100, 1) if total else 0,
            'our_pick_record': f"{picks_correct}-{total - picks_correct}",
            'our_avg_spread_error': round(sum(spread_errors) / len(spread_errors), 1) if spread_errors else None,
            'our_median_spread_error': round(spread_errors_sorted[len(spread_errors_sorted) // 2], 1) if spread_errors_sorted else None,
            'kp_pick_accuracy': round(kp_picks / total * 100, 1) if total and any(r.get('kp_pick_correct') is not None for r in results) else None,
            'kp_pick_record': f"{kp_picks}-{total - kp_picks}" if any(r.get('kp_pick_correct') is not None for r in results) else None,
            'kp_avg_spread_error': round(sum(kp_errors) / len(kp_errors), 1) if kp_errors else None,
            # Simple model comparison
            'simple_pick_accuracy': round(simple_picks / total * 100, 1) if total else 0,
            'simple_avg_spread_error': round(sum(simple_errors) / len(simple_errors), 1) if simple_errors else None,
            'improvement_pick_pct': round((picks_correct - simple_picks) / total * 100, 1) if total else 0,
            'improvement_spread_error': round(
                (sum(simple_errors) / len(simple_errors)) - (sum(spread_errors) / len(spread_errors)), 1
            ) if spread_errors and simple_errors else None,
            # Sub-model accuracy
            'sub_model_accuracy': sub_model_accuracy,
            # By-conference breakdown (legacy simple version)
            'by_conference': conf_breakdown,
            # Enhanced conference breakdown with signed errors and ATS
            'by_conference_full': by_conference_full,
            'conference_adjustment_recommendations': self._compute_conf_recommendations(by_conference_full, conf_overrides=conf_overrides),
            # Cross-conf vs intra-conf comparison
            'cross_conf_stats': cross_conf_stats,
            'intra_conf_stats': intra_conf_stats,
            'by_spread_bucket': bucket_stats,
            # Signed error by bucket (for calibration feedback loop)
            'signed_error_by_bucket': signed_bucket_stats,
            'calibration_recommendations': self._compute_calibration_recommendations(signed_bucket_stats, calibration_coeffs=calibration_coeffs),
            'by_date': daily,
            'by_context': by_context,
            # O/U metrics
            'ou_pct': ou_pct,
            'ou_record': f"{ou_hits}-{ou_misses}" if ou_total > 0 else None,
            'ou_pushes': ou_pushes,
            'ou_hc_pct': ou_hc_pct,
            'ou_hc_record': f"{ou_hc_hits}-{ou_hc_misses}" if ou_hc_total > 0 else None,
            'avg_total_error': avg_total_error,
            'mean_total_signed_error': mean_total_signed_error,
            'total_bias': total_bias,
            'ou_by_edge': ou_edge_stats,
            'sub_model_total_accuracy': sub_model_total_accuracy,
            'total_calibration_recommendations': self._compute_total_calibration_recommendations(results),
            'results': results,
        }

    # -- Calibration Recommendation Methods --

    def _compute_calibration_recommendations(self, signed_bucket_stats, calibration_coeffs=None):
        """Compute recommended calibrateSpread() coefficient adjustments.

        Uses the actual coefficients that were applied during the backtest run
        (from calibration_coeffs) rather than hardcoded defaults, so that
        iterative apply-then-rerun converges instead of oscillating.
        """
        cc = calibration_coeffs or {}
        recommendations = {}

        # Bucket 0-7
        close_errors = []
        for bname in ['0-3', '3-7']:
            if bname in signed_bucket_stats:
                s = signed_bucket_stats[bname]
                close_errors.extend([s['mean_signed_error']] * s['games'])
        if close_errors:
            avg_close = sum(close_errors) / len(close_errors)
            # Each 1pt of mean signed error -> adjust coefficient by ~0.02
            current = cc.get('close', 0.92)
            adjustment = -avg_close * 0.02  # negative error -> increase coeff
            new_coeff = max(0.80, min(1.0, current + adjustment))
            sample = sum(signed_bucket_stats.get(b, {}).get('games', 0) for b in ['0-3', '3-7'])
            recommendations['close_games_0_7'] = {
                'current': current,
                'recommended': round(new_coeff, 3),
                'mean_signed_error': round(avg_close, 2),
                'sample_size': sample,
                'confidence': 'high' if sample >= 50 else 'low',
            }

        # Bucket 7-14
        if '7-14' in signed_bucket_stats:
            s = signed_bucket_stats['7-14']
            current = cc.get('moderate', 0.85)
            adjustment = -s['mean_signed_error'] * 0.02
            new_coeff = max(0.70, min(0.95, current + adjustment))
            recommendations['moderate_spreads_7_14'] = {
                'current': current,
                'recommended': round(new_coeff, 3),
                'mean_signed_error': s['mean_signed_error'],
                'sample_size': s['games'],
                'confidence': 'high' if s['games'] >= 30 else 'low',
            }

        # Bucket 14+: log compression
        blowout_errors = []
        for bname in ['14-20', '20+']:
            if bname in signed_bucket_stats:
                s = signed_bucket_stats[bname]
                blowout_errors.extend([s['mean_signed_error']] * s['games'])
        if blowout_errors:
            avg_blowout = sum(blowout_errors) / len(blowout_errors)
            current_mult = cc.get('logMult', 3.5)
            # Each 1pt of mean signed error -> adjust log multiplier by ~0.15
            adjustment = -avg_blowout * 0.15
            new_mult = max(2.0, min(5.0, current_mult + adjustment))
            sample = sum(signed_bucket_stats.get(b, {}).get('games', 0) for b in ['14-20', '20+'])
            recommendations['blowouts_14_plus'] = {
                'current_log_mult': current_mult,
                'recommended_log_mult': round(new_mult, 2),
                'mean_signed_error': round(avg_blowout, 2),
                'sample_size': sample,
                'confidence': 'high' if sample >= 20 else 'low',
            }

        return recommendations

    def _compute_total_calibration_recommendations(self, results, total_coeffs=None):
        """Compute recommended total calibration adjustments."""
        tc = total_coeffs or {}
        current_center = tc.get('center', 140.0)
        current_compression = tc.get('compression', 0.90)

        total_signed_errors = [r['total_signed_error'] for r in results
                               if r.get('total_signed_error') is not None]
        if len(total_signed_errors) < 20:
            return {}

        mean_error = sum(total_signed_errors) / len(total_signed_errors)

        # If we systematically overpredict totals, increase compression (pull more toward center)
        # Each 1pt of mean error -> adjust compression by 0.01
        new_compression = max(0.70, min(1.0, current_compression - mean_error * 0.01))

        # If the center is wrong, adjust it
        # Mean error > 0 means we predict too high -> lower center slightly
        new_center = max(120, min(160, current_center - mean_error * 0.3))

        return {
            'current_center': current_center,
            'recommended_center': round(new_center, 1),
            'current_compression': current_compression,
            'recommended_compression': round(new_compression, 3),
            'mean_total_signed_error': round(mean_error, 2),
            'sample_size': len(total_signed_errors),
            'confidence': 'high' if len(total_signed_errors) >= 100 else 'low',
        }

    def _compute_conf_recommendations(self, by_conference, conf_overrides=None):
        """Identify conferences where the model is systematically biased.

        If we consistently overpredict SEC games (mean signed error > +2),
        it means SEC teams underperform our model's expectations.
        This could mean:
        - calcConfAdj() overvalues SEC conference strength
        - KenPom's SOS adjustment already captures this, and our conf adj double-counts
        - That conference's physical style causes more variance
        """
        recommendations = []
        for conf, stats in by_conference.items():
            mse = stats.get('mean_signed_error')
            if mse is None or stats['games'] < 10:
                continue
            if abs(mse) > 1.5:  # significant bias
                # Current calcConfAdj uses flat 0.06 scaling
                # If we overpredict by 2 pts, reduce that conference's effective rating
                recommendations.append({
                    'conference': conf,
                    'games': stats['games'],
                    'mean_signed_error': mse,
                    'direction': stats['bias_direction'],
                    'pick_pct': stats['pick_pct'],
                    'ats_pct': stats.get('ats_pct'),
                    'suggested_action': f"Reduce {conf} conf adj by {abs(mse) * 0.03:.2f}" if mse > 0
                                        else f"Increase {conf} conf adj by {abs(mse) * 0.03:.2f}",
                    'current_scale': (conf_overrides or {}).get(conf, 0.06),
                    'suggested_scale': round(max(0.01, min(0.12, (conf_overrides or {}).get(conf, 0.06) - mse * 0.03)), 3),
                    'confidence': 'high' if stats['games'] >= 30 else 'medium' if stats['games'] >= 15 else 'low',
                })

        # Sort by absolute bias magnitude
        recommendations.sort(key=lambda x: abs(x['mean_signed_error']), reverse=True)
        return recommendations

    # -- Validation Methods --

    def _validate_prediction(self, result):
        """Sanity-check a single prediction. Returns list of warnings."""
        warnings = []
        margin = abs(result.get('predicted_spread', 0))
        if margin > 35:
            warnings.append(f"Extreme margin: {margin:.1f}")
        t1_score = result.get('predicted_t1_score', 0)
        t2_score = result.get('predicted_t2_score', 0)
        if t1_score and (t1_score < 40 or t1_score > 110):
            warnings.append(f"Unrealistic T1 score: {t1_score:.1f}")
        if t2_score and (t2_score < 40 or t2_score > 110):
            warnings.append(f"Unrealistic T2 score: {t2_score:.1f}")
        subs = result.get('sub_model_margins', {})
        if subs:
            margins = list(subs.values())
            spread = max(margins) - min(margins)
            if spread > 20:
                warnings.append(f"Sub-model disagreement: {spread:.1f} pt spread")
        return warnings

    def _validate_backtest(self, metrics):
        """Check backtest results for red flags."""
        issues = []
        pick_pct = metrics.get('our_pick_accuracy', 0)
        if pick_pct < 45:
            issues.append(f"Pick accuracy {pick_pct}% is below random (50%). Model may be inverted.")
        if pick_pct > 85:
            issues.append(f"Pick accuracy {pick_pct}% is suspiciously high. Check for data leakage.")
        avg_err = metrics.get('our_avg_spread_error')
        if avg_err and avg_err > 15:
            issues.append(f"Avg spread error {avg_err} is very high. Model may be miscalibrated.")
        if avg_err and avg_err < 5:
            issues.append(f"Avg spread error {avg_err} is suspiciously low. Check for data leakage.")
        # Check that composite beats simple model
        improvement = metrics.get('improvement_pick_pct', 0)
        if improvement < -3:
            issues.append(f"Composite is {abs(improvement):.1f}% WORSE than simple model. Investigate.")
        return issues

    def run_self_test(self):
        """Run self-test with synthetic team data. Returns dict of test results."""
        test_results = {}

        # Duke-like elite team
        duke = {
            'AdjEM': 28, 'AdjOE': 120, 'AdjDE': 92, 'AdjTempo': 70,
            'Luck': 0.02, 'SOS': 8, 'Wins': 28, 'Losses': 3,
            'RankAdjEM': 3, 'RankAdjOE': 5, 'RankAdjDE': 10,
            'ConfShort': 'ACC', 'TeamName': 'TestElite', 'Pythag': 0.92,
            '_ff': {'eFG_Pct': 55, 'TO_Pct': 15, 'OR_Pct': 32, 'FT_Rate': 38,
                    'DeFG_Pct': 45, 'DTO_Pct': 21, 'DOR_Pct': 24, 'DFT_Rate': 28},
            '_ms': {'FG2Pct': 54, 'FG3Pct': 37, 'F3GRate': 35, 'FTPct': 76},
            '_ht': {'Exp': 2.5, 'Continuity': 0.55, 'Bench': 32},
        }
        # Siena-like weak team
        siena = {
            'AdjEM': -5, 'AdjOE': 98, 'AdjDE': 103, 'AdjTempo': 66,
            'Luck': -0.01, 'SOS': -8, 'Wins': 12, 'Losses': 18,
            'RankAdjEM': 220, 'RankAdjOE': 200, 'RankAdjDE': 180,
            'ConfShort': 'MAAC', 'TeamName': 'TestWeak', 'Pythag': 0.40,
            '_ff': {'eFG_Pct': 48, 'TO_Pct': 20, 'OR_Pct': 26, 'FT_Rate': 28,
                    'DeFG_Pct': 52, 'DTO_Pct': 16, 'DOR_Pct': 30, 'DFT_Rate': 35},
            '_ms': {'FG2Pct': 48, 'FG3Pct': 32, 'F3GRate': 38, 'FTPct': 68},
            '_ht': {'Exp': 1.8, 'Continuity': 0.40, 'Bench': 28},
        }

        # Run all 4 models
        extra = {
            'style_clash': calc_style_clash(duke['_ff'], siena['_ff']),
            'experience': calc_experience_adj(duke['_ht'], siena['_ht']),
            'momentum': {'adj': 0},
            'conf_strength': {'adj': 0},
            'injury_adj': 0,
        }
        eff = model_efficiency(duke, siena, 0, 0, extra)
        sim = model_similar_opponents(duke, siena, 0, 0, extra)
        cr = model_con_rat(duke, siena, 0, 0, extra)
        mc = dict(eff)  # Use eff as MC stand-in for self-test

        comp = compute_composite(eff, sim, cr, mc, duke, siena)

        # Test 1: Duke wins >90% of the time
        ok = comp['t1_win_prob'] > 0.90
        test_results['elite_vs_weak_win_prob'] = (
            'PASS' if ok else f'FAIL (win_prob={comp["t1_win_prob"]:.2f})'
        )

        # Test 2: margin is 10-25 pts
        ok = 10 < comp['margin'] < 25
        test_results['elite_vs_weak_margin'] = (
            'PASS' if ok else f'FAIL (margin={comp["margin"]:.1f})'
        )

        # Test 3: scores are 60-90 range
        ok = (60 < comp['t1_score'] < 90) and (60 < comp['t2_score'] < 90)
        test_results['scores_in_range'] = (
            'PASS' if ok
            else f'FAIL (t1={comp["t1_score"]:.1f}, t2={comp["t2_score"]:.1f})'
        )

        # Test 4: Two similar teams -> win prob 45-55%
        similar_a = dict(duke)
        similar_b = dict(duke)
        similar_b['TeamName'] = 'TestSimilar'
        eff2 = model_efficiency(similar_a, similar_b, 0, 0)
        sim2 = model_similar_opponents(similar_a, similar_b, 0, 0)
        cr2 = model_con_rat(similar_a, similar_b, 0, 0)
        mc2 = dict(eff2)
        comp2 = compute_composite(eff2, sim2, cr2, mc2, similar_a, similar_b)
        ok = 0.45 < comp2['t1_win_prob'] < 0.55
        test_results['similar_teams_close'] = (
            'PASS' if ok else f'FAIL (win_prob={comp2["t1_win_prob"]:.2f})'
        )

        # Test 5: similar teams margin < 5 pts
        ok = abs(comp2['margin']) < 5
        test_results['similar_teams_margin'] = (
            'PASS' if ok else f'FAIL (margin={comp2["margin"]:.1f})'
        )

        # Test 6: calibrate_spread(20) < 20
        ok = calibrate_spread(20) < 20
        test_results['calibrate_spread_compression'] = (
            'PASS' if ok else f'FAIL (calibrate_spread(20)={calibrate_spread(20):.1f})'
        )

        # Test 7: calibrate_spread(0) == 0
        ok = calibrate_spread(0) == 0
        test_results['calibrate_spread_zero'] = (
            'PASS' if ok else f'FAIL (calibrate_spread(0)={calibrate_spread(0):.3f})'
        )

        return test_results
