"""
Automated Backtesting Pipeline
Replays model predictions against actual ESPN scores for historical dates.
Computes accuracy metrics: pick %, ATS %, MAE, by conference, by spread bucket.

Uses the full 4-model composite pipeline (KenPom + Similar Opponents + ConRat + MC)
matching the frontend logic in static/index.html, with a built-in validation loop.

KNOWN LIMITATION: The KenPom `ratings` endpoint returns current-day ratings,
not historical snapshots. For true historical backtesting you would need the
`archive` endpoint for each date. Using current ratings introduces lookahead
bias -- results will be slightly optimistic.
"""

import json
import os
from datetime import datetime, timedelta
import requests

from composite_model import (
    model_efficiency, model_similar_opponents, model_con_rat,
    compute_composite, calc_style_clash, calc_experience_adj,
    calc_conf_adj, calibrate_spread, get_hca,
)
from matchup_params import prefetch_all_team_data, build_matchup_params
from mc_engine import simulate_game


ESPN_SCOREBOARD_URL = "https://site.api.espn.com/apis/site/v2/sports/basketball/mens-college-basketball/scoreboard"


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

    def backtest_date_range(self, start_date, end_date, kenpom_client, cache):
        """
        Backtest a range of dates using KenPom fanmatch data + ESPN scores.
        This is the full automated pipeline that doesn't require pre-saved predictions.
        Uses the full 4-model composite pipeline.
        """
        # -- One-time bulk data prefetch --
        year = int(start_date[:4])
        try:
            team_data = prefetch_all_team_data(kenpom_client, cache, year=year)
        except Exception as e:
            print(f"[backtest] Failed to prefetch team data: {e}")
            team_data = {}

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
        current = datetime.strptime(start_date, '%Y-%m-%d')
        end = datetime.strptime(end_date, '%Y-%m-%d')

        while current <= end:
            date_str = current.strftime('%Y-%m-%d')
            try:
                day_results = self._backtest_single_day(
                    date_str, kenpom_client, cache,
                    team_data=team_data, conf_map=conf_map,
                )
                results.extend(day_results)
            except Exception as e:
                print(f"[backtest] Skipping {date_str}: {e}")
            current += timedelta(days=1)

        if not results:
            return {'error': 'No games found in date range', 'results': []}

        metrics = self._compute_metrics(results)

        # Validation step
        validation_issues = self._validate_backtest(metrics)
        if validation_issues:
            metrics['validation_issues'] = validation_issues

        return metrics

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
                             team_data=None, conf_map=None):
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

        # Get ratings for predictions
        try:
            ratings = cache.get('ratings_backtest', {'year': date_str[:4]})
            if ratings is None:
                ratings = kenpom_client.get_ratings(year=int(date_str[:4]))
                cache.set('ratings_backtest', {'year': date_str[:4]}, ratings)
        except Exception:
            return []

        if not ratings or not isinstance(ratings, list):
            return []

        ratings_map = {r.get('TeamName', ''): r for r in ratings}

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

            # Get ratings data for both teams
            dH = ratings_map.get(home)
            dV = ratings_map.get(visitor)
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
            vis_key = self._fuzzy_find_team(visitor, team_data)
            home_key = self._fuzzy_find_team(home, team_data)
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
            conf_strength = calc_conf_adj(dV_enriched, dH_enriched, conf_map)
            # Momentum: skip for now (requires archive fetch per date, expensive)
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
            composite = compute_composite(eff, sim, cr, mc, dV_enriched, dH_enriched)
            our_margin = composite['margin']  # visitor-relative (positive = visitor favored)
            our_winner = visitor if our_margin >= 0 else home

            # Actual result
            actual_margin = score['home_score'] - score['away_score']
            actual_winner = home if actual_margin >= 0 else visitor

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
                'sub_model_margins': {
                    'efficiency': eff['margin'],
                    'similar': sim['margin'],
                    'conrat': cr['margin'],
                    'mc': mc['margin'],
                },
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


    def _compute_metrics(self, results):
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
            # Use team2 (home) conference from enriched data or fallback
            home_conf = r.get('composite_weights', {}).get('_home_conf', 'Unknown')
            # Try to extract from sub_model data if not present
            if home_conf == 'Unknown':
                home_conf = 'Unknown'
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
            # By-conference breakdown
            'by_conference': conf_breakdown,
            'by_spread_bucket': bucket_stats,
            'by_date': daily,
            'results': results,
        }

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
