"""
Automated Backtesting Pipeline
Replays model predictions against actual ESPN scores for historical dates.
Computes accuracy metrics: pick %, ATS %, MAE, by conference, by spread bucket.
"""

import json
import os
from datetime import datetime, timedelta
import requests


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
        """
        results = []
        current = datetime.strptime(start_date, '%Y-%m-%d')
        end = datetime.strptime(end_date, '%Y-%m-%d')

        while current <= end:
            date_str = current.strftime('%Y-%m-%d')
            try:
                day_results = self._backtest_single_day(date_str, kenpom_client, cache)
                results.extend(day_results)
            except Exception as e:
                print(f"[backtest] Skipping {date_str}: {e}")
            current += timedelta(days=1)

        if not results:
            return {'error': 'No games found in date range', 'results': []}

        return self._compute_metrics(results)

    def _backtest_single_day(self, date_str, kenpom_client, cache):
        """Backtest a single day: fetch fanmatch + scores, compare predictions."""
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

            # Our simple prediction using ratings (efficiency model only for backtest speed)
            dH = ratings_map.get(home)
            dV = ratings_map.get(visitor)
            if not dH or not dV:
                continue

            hca = 3.5 if not score.get('neutral_site') else 0
            our_margin = ((dH.get('AdjEM', 0) - dV.get('AdjEM', 0)) + hca) * 0.85
            our_winner = home if our_margin >= 0 else visitor

            # Actual result
            actual_margin = score['home_score'] - score['away_score']
            actual_winner = home if actual_margin >= 0 else visitor

            results.append({
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
                'spread_error': abs(actual_margin - our_margin),
                'kp_spread_error': abs(actual_margin - kp_margin),
                'vegas_spread': score.get('spread'),
                'predicted_total': (dH.get('AdjOE', 100) + dV.get('AdjOE', 100)) * (dH.get('AdjTempo', 67) + dV.get('AdjTempo', 67)) / 2 / 100,
                'actual_total': score['home_score'] + score['away_score'],
            })

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
        """Compute accuracy metrics from backtest results."""
        if not results:
            return {'total_games': 0, 'results': []}

        total = len(results)
        picks_correct = sum(1 for r in results if r.get('pick_correct'))
        spread_errors = [r['spread_error'] for r in results if r.get('spread_error') is not None]
        kp_picks = sum(1 for r in results if r.get('kp_pick_correct'))
        kp_errors = [r.get('kp_spread_error', 0) for r in results if r.get('kp_spread_error') is not None]

        # Sort for median
        spread_errors_sorted = sorted(spread_errors) if spread_errors else []

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

        return {
            'total_games': total,
            'our_pick_accuracy': round(picks_correct / total * 100, 1) if total else 0,
            'our_pick_record': f"{picks_correct}-{total - picks_correct}",
            'our_avg_spread_error': round(sum(spread_errors) / len(spread_errors), 1) if spread_errors else None,
            'our_median_spread_error': round(spread_errors_sorted[len(spread_errors_sorted) // 2], 1) if spread_errors_sorted else None,
            'kp_pick_accuracy': round(kp_picks / total * 100, 1) if total and any(r.get('kp_pick_correct') is not None for r in results) else None,
            'kp_pick_record': f"{kp_picks}-{total - kp_picks}" if any(r.get('kp_pick_correct') is not None for r in results) else None,
            'kp_avg_spread_error': round(sum(kp_errors) / len(kp_errors), 1) if kp_errors else None,
            'by_spread_bucket': bucket_stats,
            'by_date': daily,
            'results': results,
        }
