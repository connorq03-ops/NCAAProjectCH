import os
import time
import json
import hashlib
import uuid
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
from flask import Flask, jsonify, request, send_from_directory
from flask_cors import CORS
from dotenv import load_dotenv
from kenpom_client import KenpomClient
from injury_scraper import InjuryAnalyzer
from star_players import get_team_stars, STAR_PLAYERS

load_dotenv()


class APICache:
    """Simple in-memory TTL cache for KenPom API responses."""
    def __init__(self, ttl_seconds=3600):
        self.ttl = ttl_seconds
        self._store = {}

    def _key(self, endpoint, params):
        raw = endpoint + json.dumps(params, sort_keys=True, default=str)
        return hashlib.md5(raw.encode()).hexdigest()

    def get(self, endpoint, params):
        k = self._key(endpoint, params)
        entry = self._store.get(k)
        if entry and (time.time() - entry['ts']) < self.ttl:
            return entry['data']
        return None

    def set(self, endpoint, params, data):
        k = self._key(endpoint, params)
        self._store[k] = {'data': data, 'ts': time.time()}


api_cache = APICache(ttl_seconds=3600)  # 1-hour cache

app = Flask(__name__, static_folder='static')
CORS(app)

API_KEY = os.getenv('KENPOM_API_KEY')
if not API_KEY:
    raise ValueError("KENPOM_API_KEY environment variable is required")
client = KenpomClient(api_key=API_KEY)

# Injury analyzer (optional — won't crash if ANTHROPIC_API_KEY missing)
injury_analyzer = None
try:
    injury_analyzer = InjuryAnalyzer()
    print("[app] Injury intelligence module loaded successfully")
except ValueError:
    print("[app] ANTHROPIC_API_KEY not set — injury features disabled")


def cached_call(endpoint, params, fetch_fn):
    """Check cache first, then call KenPom API if miss."""
    cached = api_cache.get(endpoint, params)
    if cached is not None:
        return cached
    data = fetch_fn()
    api_cache.set(endpoint, params, data)
    return data


@app.route('/api/ratings', methods=['GET'])
def get_ratings():
    """Get team ratings."""
    year = request.args.get('year', type=int)
    team_id = request.args.get('team_id', type=int)
    conference = request.args.get('conference')
    
    try:
        params = {'year': year, 'team_id': team_id, 'conference': conference}
        data = cached_call('ratings', params,
            lambda: client.get_ratings(year=year, team_id=team_id, conference=conference))
        return jsonify(data)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/archive', methods=['GET'])
def get_archive():
    """Get historical ratings data."""
    date = request.args.get('date')
    year = request.args.get('year', type=int)
    preseason = request.args.get('preseason', '').lower() == 'true'
    team_id = request.args.get('team_id', type=int)
    conference = request.args.get('conference')
    
    try:
        params = {'date': date, 'year': year, 'preseason': preseason, 'team_id': team_id, 'conference': conference}
        data = cached_call('archive', params,
            lambda: client.get_archive(date=date, year=year, preseason=preseason,
                                       team_id=team_id, conference=conference))
        return jsonify(data)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/four-factors', methods=['GET'])
def get_four_factors():
    """Get four factors statistics."""
    year = request.args.get('year', type=int)
    team_id = request.args.get('team_id', type=int)
    conference = request.args.get('conference')
    conf_only = request.args.get('conf_only', '').lower() == 'true'
    
    try:
        params = {'year': year, 'team_id': team_id, 'conference': conference, 'conf_only': conf_only}
        data = cached_call('four-factors', params,
            lambda: client.get_four_factors(year=year, team_id=team_id,
                                            conference=conference, conf_only=conf_only))
        return jsonify(data)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/pointdist', methods=['GET'])
def get_point_distribution():
    """Get point distribution statistics."""
    year = request.args.get('year', type=int)
    team_id = request.args.get('team_id', type=int)
    conference = request.args.get('conference')
    conf_only = request.args.get('conf_only', '').lower() == 'true'
    
    try:
        params = {'year': year, 'team_id': team_id, 'conference': conference, 'conf_only': conf_only}
        data = cached_call('pointdist', params,
            lambda: client.get_point_distribution(year=year, team_id=team_id,
                                                  conference=conference, conf_only=conf_only))
        return jsonify(data)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/height', methods=['GET'])
def get_height():
    """Get team height statistics."""
    year = request.args.get('year', type=int)
    team_id = request.args.get('team_id', type=int)
    conference = request.args.get('conference')
    
    try:
        params = {'year': year, 'team_id': team_id, 'conference': conference}
        data = cached_call('height', params,
            lambda: client.get_height(year=year, team_id=team_id, conference=conference))
        return jsonify(data)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/misc-stats', methods=['GET'])
def get_misc_stats():
    """Get miscellaneous statistics."""
    year = request.args.get('year', type=int)
    team_id = request.args.get('team_id', type=int)
    conference = request.args.get('conference')
    conf_only = request.args.get('conf_only', '').lower() == 'true'
    
    try:
        params = {'year': year, 'team_id': team_id, 'conference': conference, 'conf_only': conf_only}
        data = cached_call('misc-stats', params,
            lambda: client.get_misc_stats(year=year, team_id=team_id,
                                          conference=conference, conf_only=conf_only))
        return jsonify(data)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/fanmatch', methods=['GET'])
def get_fanmatch():
    """Get game predictions for a specific date."""
    date = request.args.get('date')
    
    if not date:
        return jsonify({'error': 'Date parameter is required'}), 400
    
    try:
        params = {'date': date}
        data = cached_call('fanmatch', params,
            lambda: client.get_fanmatch(date=date))
        return jsonify(data)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/scores', methods=['GET'])
def get_scores():
    """Get actual game scores from ESPN for a specific date."""
    date = request.args.get('date')
    if not date:
        return jsonify({'error': 'date parameter required'}), 400

    espn_date = date.replace('-', '')

    def fetch_espn():
        import requests as req
        url = "https://site.api.espn.com/apis/site/v2/sports/basketball/mens-college-basketball/scoreboard"
        all_events = []
        page = 1
        while True:
            resp = req.get(url, params={'dates': espn_date, 'limit': 200, 'groups': '50', 'page': page}, timeout=15)
            resp.raise_for_status()
            data = resp.json()
            events = data.get('events', [])
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
            home_team = next((t for t in teams if t['homeAway'] == 'home'), None)
            away_team = next((t for t in teams if t['homeAway'] == 'away'), None)
            if not home_team or not away_team:
                continue
            games.append({
                'home': home_team['team'].get('shortDisplayName', home_team['team'].get('displayName', '')),
                'home_full': home_team['team'].get('displayName', ''),
                'home_score': int(home_team.get('score', 0)),
                'away': away_team['team'].get('shortDisplayName', away_team['team'].get('displayName', '')),
                'away_full': away_team['team'].get('displayName', ''),
                'away_score': int(away_team.get('score', 0)),
                'status': status,
            })
        return games

    try:
        data = cached_call('scores', {'date': espn_date}, fetch_espn)
        return jsonify(data)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/odds', methods=['GET'])
def get_odds():
    """Get DraftKings odds/spreads from ESPN for a specific date."""
    date = request.args.get('date')
    if not date:
        return jsonify({'error': 'date parameter required'}), 400

    espn_date = date.replace('-', '')

    def fetch_odds():
        import requests as req
        url = "https://site.api.espn.com/apis/site/v2/sports/basketball/mens-college-basketball/scoreboard"
        all_events = []
        page = 1
        while True:
            resp = req.get(url, params={'dates': espn_date, 'limit': 200, 'groups': '50', 'page': page}, timeout=15)
            resp.raise_for_status()
            data = resp.json()
            events = data.get('events', [])
            if not events:
                break
            all_events.extend(events)
            if len(events) < 100:
                break
            page += 1

        games = []
        for e in all_events:
            comp = e['competitions'][0]
            teams = comp['competitors']
            home_team = next((t for t in teams if t['homeAway'] == 'home'), None)
            away_team = next((t for t in teams if t['homeAway'] == 'away'), None)
            if not home_team or not away_team:
                continue

            odds_list = comp.get('odds', [])
            odds = odds_list[0] if odds_list else {}

            game = {
                'home': home_team['team'].get('shortDisplayName', ''),
                'home_full': home_team['team'].get('displayName', ''),
                'away': away_team['team'].get('shortDisplayName', ''),
                'away_full': away_team['team'].get('displayName', ''),
                'spread': odds.get('spread'),
                'details': odds.get('details', ''),
                'over_under': odds.get('overUnder'),
                'provider': odds.get('provider', {}).get('name', ''),
                'home_ml': odds.get('homeTeamOdds', {}).get('moneyLine'),
                'away_ml': odds.get('awayTeamOdds', {}).get('moneyLine'),
                'home_favorite': odds.get('homeTeamOdds', {}).get('favorite', False),
                'status': comp.get('status', {}).get('type', {}).get('name', ''),
                'home_score': home_team.get('score'),
                'away_score': away_team.get('score'),
                'neutral_site': comp.get('neutralSite', False),
            }
            # Only include games that have odds data
            if game['spread'] is not None:
                games.append(game)
        return games

    try:
        data = cached_call('odds', {'date': espn_date}, fetch_odds)
        return jsonify(data)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/game-intel', methods=['GET'])
def get_game_intel():
    """Get enriched game intelligence: line movement + referee assignments."""
    date = request.args.get('date')
    if not date:
        return jsonify({'error': 'date parameter required'}), 400

    espn_date = date.replace('-', '')

    def fetch_intel():
        import requests as req

        # Step 1: Get all events from scoreboard to collect event IDs
        url = "https://site.api.espn.com/apis/site/v2/sports/basketball/mens-college-basketball/scoreboard"
        resp = req.get(url, params={'dates': espn_date, 'limit': 200, 'groups': '50'}, timeout=15)
        resp.raise_for_status()
        events = resp.json().get('events', [])

        # Step 2: For each event, fetch line movement + officials from core API
        core_base = "http://sports.core.api.espn.com/v2/sports/basketball/leagues/mens-college-basketball/events"

        def fetch_event_intel(event):
            eid = event['id']
            comp = event['competitions'][0]
            teams = comp['competitors']
            home = next((t for t in teams if t['homeAway'] == 'home'), None)
            away = next((t for t in teams if t['homeAway'] == 'away'), None)
            if not home or not away:
                return None

            result = {
                'event_id': eid,
                'home': home['team'].get('shortDisplayName', ''),
                'home_full': home['team'].get('displayName', ''),
                'away': away['team'].get('shortDisplayName', ''),
                'away_full': away['team'].get('displayName', ''),
            }

            # Fetch opening line from core API odds
            try:
                odds_url = f"{core_base}/{eid}/competitions/{eid}/odds/100"
                or_ = req.get(odds_url, timeout=8)
                if or_.status_code == 200:
                    od = or_.json()
                    result['current_spread'] = od.get('spread')
                    result['details'] = od.get('details', '')
                    result['over_under'] = od.get('overUnder')
                    # Opening line
                    home_open = od.get('homeTeamOdds', {}).get('open', {})
                    away_open = od.get('awayTeamOdds', {}).get('open', {})
                    open_spread_str = home_open.get('pointSpread', {}).get('alternateDisplayValue')
                    if open_spread_str:
                        try:
                            result['open_spread'] = float(open_spread_str)
                        except ValueError:
                            result['open_spread'] = None
                    else:
                        result['open_spread'] = None
                    # Line movement
                    if result['open_spread'] is not None and result['current_spread'] is not None:
                        result['line_movement'] = round(result['current_spread'] - result['open_spread'], 1)
                    else:
                        result['line_movement'] = None
            except Exception:
                result['current_spread'] = None
                result['open_spread'] = None
                result['line_movement'] = None

            # Fetch officials
            try:
                off_url = f"{core_base}/{eid}/competitions/{eid}/officials"
                ofr = req.get(off_url, timeout=8)
                if ofr.status_code == 200:
                    officials = ofr.json().get('items', [])
                    result['officials'] = [
                        {'name': o.get('fullName', ''), 'position': o.get('position', {}).get('displayName', '')}
                        for o in officials
                    ]
                else:
                    result['officials'] = []
            except Exception:
                result['officials'] = []

            return result

        # Run core API calls concurrently (max 10 at a time)
        results = []
        with ThreadPoolExecutor(max_workers=10) as executor:
            futures = {executor.submit(fetch_event_intel, ev): ev for ev in events}
            for future in as_completed(futures):
                try:
                    r = future.result()
                    if r and (r.get('current_spread') is not None or r.get('officials')):
                        results.append(r)
                except Exception:
                    pass

        return results

    try:
        data = cached_call('game-intel', {'date': espn_date}, fetch_intel)
        return jsonify(data)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/conf-ratings', methods=['GET'])
def get_conference_ratings():
    """Get conference ratings."""
    year = request.args.get('year', type=int)
    conference = request.args.get('conference')
    
    try:
        params = {'year': year, 'conference': conference}
        data = cached_call('conf-ratings', params,
            lambda: client.get_conference_ratings(year=year, conference=conference))
        return jsonify(data)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/teams', methods=['GET'])
def get_teams():
    """Get list of teams."""
    year = request.args.get('year', type=int)
    conference = request.args.get('conference')
    
    try:
        params = {'year': year, 'conference': conference}
        data = cached_call('teams', params,
            lambda: client.get_teams(year=year, conference=conference))
        return jsonify(data)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/conferences', methods=['GET'])
def get_conferences():
    """Get list of conferences."""
    year = request.args.get('year', type=int)
    
    try:
        params = {'year': year}
        data = cached_call('conferences', params,
            lambda: client.get_conferences(year=year))
        return jsonify(data)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/stars', methods=['GET'])
def get_stars():
    """Get star player data. Optional team filter."""
    team = request.args.get('team', '')
    if team:
        return jsonify(get_team_stars(team))
    # Return all stars grouped by team
    by_team = {}
    for name, info in STAR_PLAYERS.items():
        t = info['team']
        if t not in by_team:
            by_team[t] = []
        by_team[t].append({'player': name, **info})
    return jsonify(by_team)


@app.route('/api/injuries', methods=['GET'])
def get_injuries():
    """Get all current NCAA basketball injuries."""
    if not injury_analyzer:
        return jsonify({'error': 'Injury features not available (ANTHROPIC_API_KEY not set)', 'injuries': []}), 200
    force = request.args.get('force', '').lower() == 'true'
    try:
        data = injury_analyzer.get_all_injuries(force_refresh=force)
        return jsonify(data)
    except Exception as e:
        return jsonify({'error': str(e), 'injuries': []}), 500


@app.route('/api/injuries/team', methods=['GET'])
def get_team_injuries():
    """Get injuries for a specific team."""
    if not injury_analyzer:
        return jsonify({'error': 'Injury features not available', 'injuries': []}), 200
    team = request.args.get('team')
    if not team:
        return jsonify({'error': 'team parameter is required'}), 400
    try:
        data = injury_analyzer.get_team_injuries(team)
        return jsonify(data)
    except Exception as e:
        return jsonify({'error': str(e), 'injuries': []}), 500


@app.route('/api/injuries/matchup', methods=['GET'])
def get_matchup_injuries():
    """Get injury impact analysis for a specific matchup."""
    if not injury_analyzer:
        return jsonify({
            'error': 'Injury features not available',
            'team1_injuries': [], 'team2_injuries': [],
            'team1_impact': {'adj_em_penalty': 0, 'severity': 'none', 'summary': 'N/A'},
            'team2_impact': {'adj_em_penalty': 0, 'severity': 'none', 'summary': 'N/A'},
            'net_injury_edge': 0
        }), 200
    team1 = request.args.get('team1')
    team2 = request.args.get('team2')
    if not team1 or not team2:
        return jsonify({'error': 'team1 and team2 parameters are required'}), 400
    try:
        data = injury_analyzer.get_matchup_injuries(team1, team2)
        return jsonify(data)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


PREDICTIONS_FILE = os.path.join(os.path.dirname(__file__), 'predictions.json')


def _load_predictions():
    """Load predictions from disk."""
    if os.path.exists(PREDICTIONS_FILE):
        try:
            with open(PREDICTIONS_FILE, 'r') as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            return []
    return []


def _save_predictions(preds):
    """Persist predictions to disk."""
    with open(PREDICTIONS_FILE, 'w') as f:
        json.dump(preds, f, indent=2)


@app.route('/api/predictions', methods=['POST'])
def save_prediction():
    """Save a prediction from the matchup predictor."""
    body = request.get_json()
    if not body:
        return jsonify({'error': 'JSON body required'}), 400

    pred = {
        'id': str(uuid.uuid4())[:8],
        'created_at': datetime.now().isoformat(),
        'game_date': body.get('game_date', datetime.now().strftime('%Y-%m-%d')),
        'team1': body.get('team1'),
        'team2': body.get('team2'),
        'location': body.get('location', 'neutral'),
        'predicted_winner': body.get('predicted_winner'),
        'predicted_spread': body.get('predicted_spread'),
        'predicted_t1_score': body.get('predicted_t1_score'),
        'predicted_t2_score': body.get('predicted_t2_score'),
        'win_probability': body.get('win_probability'),
        'model_agreement': body.get('model_agreement'),
        'confidence': body.get('confidence'),
        'raw_spread': body.get('raw_spread'),
        # Result fields (filled in later)
        'actual_t1_score': None,
        'actual_t2_score': None,
        'actual_winner': None,
        'result_entered': False,
        'spread_error': None,
        'pick_correct': None,
        'ats_correct': None,
    }

    preds = _load_predictions()
    preds.append(pred)
    _save_predictions(preds)
    return jsonify(pred), 201


@app.route('/api/predictions', methods=['GET'])
def get_predictions():
    """Get predictions, optionally filtered by date."""
    preds = _load_predictions()
    date_filter = request.args.get('date')
    if date_filter:
        preds = [p for p in preds if p.get('game_date') == date_filter]
    # Sort by created_at descending
    preds.sort(key=lambda p: p.get('created_at', ''), reverse=True)
    return jsonify(preds)


@app.route('/api/predictions/<pred_id>/result', methods=['PUT'])
def update_prediction_result(pred_id):
    """Record actual game result for a prediction."""
    body = request.get_json()
    if not body:
        return jsonify({'error': 'JSON body required'}), 400

    preds = _load_predictions()
    pred = next((p for p in preds if p['id'] == pred_id), None)
    if not pred:
        return jsonify({'error': 'Prediction not found'}), 404

    t1_score = body.get('actual_t1_score')
    t2_score = body.get('actual_t2_score')
    if t1_score is None or t2_score is None:
        return jsonify({'error': 'actual_t1_score and actual_t2_score required'}), 400

    pred['actual_t1_score'] = int(t1_score)
    pred['actual_t2_score'] = int(t2_score)
    pred['actual_winner'] = pred['team1'] if int(t1_score) > int(t2_score) else pred['team2']
    pred['result_entered'] = True

    # Compute accuracy metrics
    pred['pick_correct'] = pred['actual_winner'] == pred['predicted_winner']
    actual_margin = int(t1_score) - int(t2_score)
    predicted_margin = pred.get('predicted_t1_score', 0) - pred.get('predicted_t2_score', 0)
    pred['spread_error'] = round(abs(actual_margin - predicted_margin), 1)

    # ATS: did the favorite cover the spread?
    pred_spread = pred.get('predicted_spread', 0)
    if pred['predicted_winner'] == pred['team1']:
        # team1 was favorite, spread is negative for them
        ats_result = actual_margin + pred_spread  # actual vs line
        pred['ats_correct'] = actual_margin > pred_spread
    else:
        pred['ats_correct'] = actual_margin < -pred_spread

    _save_predictions(preds)
    return jsonify(pred)


@app.route('/api/predictions/accuracy', methods=['GET'])
def get_prediction_accuracy():
    """Get overall prediction accuracy stats."""
    preds = _load_predictions()
    completed = [p for p in preds if p.get('result_entered')]

    if not completed:
        return jsonify({
            'total_predictions': len(preds),
            'completed': 0,
            'pick_accuracy': None,
            'ats_accuracy': None,
            'avg_spread_error': None,
            'median_spread_error': None,
        })

    picks_correct = sum(1 for p in completed if p.get('pick_correct'))
    ats_correct = sum(1 for p in completed if p.get('ats_correct'))
    spread_errors = [p.get('spread_error', 0) for p in completed if p.get('spread_error') is not None]
    spread_errors.sort()

    return jsonify({
        'total_predictions': len(preds),
        'completed': len(completed),
        'pending': len(preds) - len(completed),
        'pick_accuracy': round(picks_correct / len(completed) * 100, 1),
        'pick_record': f"{picks_correct}-{len(completed) - picks_correct}",
        'ats_accuracy': round(ats_correct / len(completed) * 100, 1),
        'ats_record': f"{ats_correct}-{len(completed) - ats_correct}",
        'avg_spread_error': round(sum(spread_errors) / len(spread_errors), 1) if spread_errors else None,
        'median_spread_error': round(spread_errors[len(spread_errors) // 2], 1) if spread_errors else None,
    })


@app.route('/api/predictions/<pred_id>', methods=['DELETE'])
def delete_prediction(pred_id):
    """Delete a prediction."""
    preds = _load_predictions()
    preds = [p for p in preds if p['id'] != pred_id]
    _save_predictions(preds)
    return jsonify({'deleted': pred_id})


@app.route('/')
def index():
    """Serve the main HTML page."""
    return send_from_directory('static', 'index.html')


if __name__ == '__main__':
    app.run(debug=True, port=5001)
