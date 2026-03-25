import os
import time
import json
import hashlib
import uuid
import sqlite3
import threading
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
from flask import Flask, jsonify, request, send_from_directory
from flask_cors import CORS
from dotenv import load_dotenv
import requests as ext_requests
from kenpom_client import KenpomClient
from injury_scraper import InjuryAnalyzer
from star_players import get_team_stars, STAR_PLAYERS
from star_scraper import build_dynamic_stars
from backtester import Backtester
from bracket_simulator import BracketSimulator
from bracket_data import (
    REGIONS, FIRST_FOUR, SEED_MAP, REGION_MAP,
    FINAL_FOUR_PAIRINGS, ROUND_NAMES,
    get_all_team_names, normalize_team_name,
)

load_dotenv()


# ── Improvement #4: Persistent SQLite Cache ──
class SQLiteCache:
    """Thread-safe persistent TTL cache backed by SQLite."""
    def __init__(self, db_path='.cache.db', default_ttl=3600):
        self.db_path = db_path
        self.default_ttl = default_ttl
        self._local = threading.local()
        self._init_db()

    def _conn(self):
        if not hasattr(self._local, 'conn') or self._local.conn is None:
            self._local.conn = sqlite3.connect(self.db_path, check_same_thread=False)
        return self._local.conn

    def _init_db(self):
        conn = sqlite3.connect(self.db_path)
        conn.execute('''CREATE TABLE IF NOT EXISTS cache (
            key TEXT PRIMARY KEY, data TEXT, ts REAL, ttl REAL)''')
        conn.execute('''CREATE TABLE IF NOT EXISTS rate_limits (
            api TEXT PRIMARY KEY, calls INTEGER DEFAULT 0,
            window_start REAL, max_calls INTEGER DEFAULT 200)''')
        conn.commit()
        conn.close()

    def _key(self, endpoint, params):
        raw = endpoint + json.dumps(params, sort_keys=True, default=str)
        return hashlib.md5(raw.encode()).hexdigest()

    def get(self, endpoint, params, ttl=None):
        k = self._key(endpoint, params)
        ttl = ttl or self.default_ttl
        try:
            row = self._conn().execute(
                'SELECT data, ts FROM cache WHERE key = ?', (k,)).fetchone()
            if row and (time.time() - row[1]) < ttl:
                return json.loads(row[0])
        except Exception:
            pass
        return None

    def set(self, endpoint, params, data):
        k = self._key(endpoint, params)
        try:
            self._conn().execute(
                'INSERT OR REPLACE INTO cache (key, data, ts, ttl) VALUES (?, ?, ?, ?)',
                (k, json.dumps(data, default=str), time.time(), self.default_ttl))
            self._conn().commit()
        except Exception:
            pass

    def check_rate_limit(self, api='kenpom', max_calls=200, window_seconds=3600):
        """Returns True if under rate limit, False if exceeded."""
        now = time.time()
        try:
            row = self._conn().execute(
                'SELECT calls, window_start FROM rate_limits WHERE api = ?', (api,)).fetchone()
            if row and (now - row[1]) < window_seconds:
                if row[0] >= max_calls:
                    return False
                self._conn().execute(
                    'UPDATE rate_limits SET calls = calls + 1 WHERE api = ?', (api,))
            else:
                self._conn().execute(
                    'INSERT OR REPLACE INTO rate_limits (api, calls, window_start, max_calls) VALUES (?, 1, ?, ?)',
                    (api, now, max_calls))
            self._conn().commit()
            return True
        except Exception:
            return True

    def stats(self):
        """Return cache statistics."""
        try:
            conn = sqlite3.connect(self.db_path)
            total = conn.execute('SELECT COUNT(*) FROM cache').fetchone()[0]
            valid = conn.execute('SELECT COUNT(*) FROM cache WHERE (? - ts) < ttl',
                                 (time.time(),)).fetchone()[0]
            rates = conn.execute('SELECT api, calls, window_start, max_calls FROM rate_limits').fetchall()
            conn.close()
            return {
                'total_entries': total, 'valid_entries': valid,
                'rate_limits': {r[0]: {'calls': r[1], 'remaining': r[3] - r[1],
                                       'resets_in': max(0, int(3600 - (time.time() - r[2])))} for r in rates}
            }
        except Exception:
            return {'total_entries': 0, 'valid_entries': 0, 'rate_limits': {}}


api_cache = SQLiteCache(db_path=os.path.join(os.path.dirname(__file__), '.cache.db'), default_ttl=3600)


# ── Improvement #3: Shared ESPN Scoreboard Fetcher ──
ESPN_SCOREBOARD_URL = "https://site.api.espn.com/apis/site/v2/sports/basketball/mens-college-basketball/scoreboard"
ESPN_CORE_BASE = "http://sports.core.api.espn.com/v2/sports/basketball/leagues/mens-college-basketball/events"


def _fetch_espn_events(espn_date):
    """Fetch all events from ESPN scoreboard for a given date (YYYYMMDD). Cached."""
    cached = api_cache.get('espn_events', {'date': espn_date}, ttl=300)  # 5-min cache for live data
    if cached is not None:
        return cached
    all_events = []
    page = 1
    while True:
        resp = ext_requests.get(ESPN_SCOREBOARD_URL,
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
    api_cache.set('espn_events', {'date': espn_date}, all_events)
    return all_events


def _parse_teams(comp):
    """Extract home/away team info from an ESPN competition."""
    teams = comp['competitors']
    home = next((t for t in teams if t['homeAway'] == 'home'), None)
    away = next((t for t in teams if t['homeAway'] == 'away'), None)
    return home, away

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


def cached_call(endpoint, params, fetch_fn, ttl=None):
    """Check cache first, then call API if miss. Rate-limits KenPom calls."""
    cached = api_cache.get(endpoint, params, ttl=ttl)
    if cached is not None:
        return cached
    if not api_cache.check_rate_limit('kenpom', max_calls=200, window_seconds=3600):
        raise Exception('KenPom API rate limit exceeded. Try again later.')
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
    try:
        events = _fetch_espn_events(date.replace('-', ''))
        games = []
        for e in events:
            comp = e['competitions'][0]
            status = comp['status']['type']['name']
            if status != 'STATUS_FINAL':
                continue
            home, away = _parse_teams(comp)
            if not home or not away:
                continue
            games.append({
                'home': home['team'].get('shortDisplayName', home['team'].get('displayName', '')),
                'home_full': home['team'].get('displayName', ''),
                'home_score': int(home.get('score', 0)),
                'away': away['team'].get('shortDisplayName', away['team'].get('displayName', '')),
                'away_full': away['team'].get('displayName', ''),
                'away_score': int(away.get('score', 0)),
                'status': status,
            })
        return jsonify(games)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/odds', methods=['GET'])
def get_odds():
    """Get DraftKings odds/spreads from ESPN for a specific date.
    Odds are cached for 7 days since ESPN strips them from completed games.
    Falls back to ESPN Core API per-event odds for completed games."""
    date = request.args.get('date')
    if not date:
        return jsonify({'error': 'date parameter required'}), 400
    # Check persistent cache first (7-day TTL keeps odds available after games finish)
    cached = api_cache.get('odds_by_date', {'date': date}, ttl=604800)
    if cached is not None:
        return jsonify(cached)
    try:
        print(f"[odds] Fetching events for {date}", flush=True)
        events = _fetch_espn_events(date.replace('-', ''))
        print(f"[odds] Got {len(events)} events", flush=True)
        games = []
        # Track events missing scoreboard odds (need core API fallback)
        needs_core = []
        for e in events:
            comp = e['competitions'][0]
            home, away = _parse_teams(comp)
            if not home or not away:
                continue
            odds_list = comp.get('odds', [])
            odds = odds_list[0] if odds_list else {}
            game = {
                'event_id': e.get('id'),
                'home': home['team'].get('shortDisplayName', ''),
                'home_full': home['team'].get('displayName', ''),
                'away': away['team'].get('shortDisplayName', ''),
                'away_full': away['team'].get('displayName', ''),
                'spread': odds.get('spread'),
                'details': odds.get('details', ''),
                'over_under': odds.get('overUnder'),
                'provider': odds.get('provider', {}).get('name', ''),
                'home_ml': odds.get('homeTeamOdds', {}).get('moneyLine'),
                'away_ml': odds.get('awayTeamOdds', {}).get('moneyLine'),
                'home_favorite': odds.get('homeTeamOdds', {}).get('favorite', False),
                'status': comp.get('status', {}).get('type', {}).get('name', ''),
                'home_score': home.get('score'),
                'away_score': away.get('score'),
                'neutral_site': comp.get('neutralSite', False),
            }
            if game['spread'] is not None:
                games.append(game)
            else:
                needs_core.append(game)

        # Fallback: fetch odds from ESPN Core API for games missing scoreboard odds
        print(f"[odds] {len(needs_core)} games need core API fallback", flush=True)
        for game in needs_core:
            eid = game.get('event_id')
            if not eid:
                continue
            try:
                url = f"{ESPN_CORE_BASE}/{eid}/competitions/{eid}/odds/100"
                print(f"[odds] Fetching core odds for {game.get('away')} @ {game.get('home')} eid={eid}", flush=True)
                r = ext_requests.get(url, timeout=6)
                if r.status_code == 200:
                    od = r.json()
                    game['spread'] = od.get('spread')
                    game['details'] = od.get('details', '')
                    game['over_under'] = od.get('overUnder')
                    game['provider'] = od.get('provider', {}).get('name', '')
                    game['home_ml'] = od.get('homeTeamOdds', {}).get('moneyLine')
                    game['away_ml'] = od.get('awayTeamOdds', {}).get('moneyLine')
                    game['home_favorite'] = od.get('homeTeamOdds', {}).get('favorite', False)
                    if game['spread'] is not None:
                        games.append(game)
            except Exception:
                pass

        # Strip event_id before returning (internal use only)
        for g in games:
            g.pop('event_id', None)
        # Cache if we got odds
        if games:
            api_cache.set('odds_by_date', {'date': date}, games)
        return jsonify(games)
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
        events = _fetch_espn_events(espn_date)

        def fetch_event_intel(event):
            eid = event['id']
            comp = event['competitions'][0]
            home, away = _parse_teams(comp)
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
                odds_url = f"{ESPN_CORE_BASE}/{eid}/competitions/{eid}/odds/100"
                or_ = ext_requests.get(odds_url, timeout=8)
                if or_.status_code == 200:
                    od = or_.json()
                    result['current_spread'] = od.get('spread')
                    result['details'] = od.get('details', '')
                    result['over_under'] = od.get('overUnder')
                    home_open = od.get('homeTeamOdds', {}).get('open', {})
                    open_spread_str = home_open.get('pointSpread', {}).get('alternateDisplayValue')
                    if open_spread_str:
                        try:
                            result['open_spread'] = float(open_spread_str)
                        except ValueError:
                            result['open_spread'] = None
                    else:
                        result['open_spread'] = None
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
                off_url = f"{ESPN_CORE_BASE}/{eid}/competitions/{eid}/officials"
                ofr = ext_requests.get(off_url, timeout=8)
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
    """Get star player data. Merges manual DB + ESPN scraped stats. Optional team filter."""
    team = request.args.get('team', '')
    if team:
        return jsonify(get_team_stars(team))
    # Check cache first (dynamic stars cached for 1 hour)
    cached = api_cache.get('dynamic_stars', {}, ttl=3600)
    if cached is not None:
        return jsonify(cached)
    # Build dynamic stars: manual STAR_PLAYERS + ESPN scraped leaders
    # Get D1 team names from KenPom for filtering
    d1_teams = None
    try:
        ratings = api_cache.get('ratings', {'year': None})
        if ratings and isinstance(ratings, list):
            d1_teams = {r.get('TeamName', '') for r in ratings if r.get('TeamName')}
    except Exception:
        pass
    try:
        by_team = build_dynamic_stars(manual_stars=STAR_PLAYERS, d1_teams=d1_teams)
    except Exception:
        # Fallback to manual-only if ESPN scrape fails
        by_team = {}
        for name, info in STAR_PLAYERS.items():
            t = info['team']
            if t not in by_team:
                by_team[t] = []
            by_team[t].append({'player': name, **info, 'source': 'manual'})
    api_cache.set('dynamic_stars', {}, by_team)
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


backtester = Backtester(predictions_file=PREDICTIONS_FILE)


# ── Calibration Persistence Endpoints ──

@app.route('/api/calibration', methods=['GET'])
def get_calibration():
    """Get current spread calibration coefficients."""
    cached = api_cache.get('spread_calibration', {}, ttl=86400 * 365)
    if cached:
        return jsonify(cached)
    # Default coefficients
    return jsonify({
        'close_coeff': 0.92,
        'moderate_coeff': 0.85,
        'log_multiplier': 3.5,
        'last_updated': None,
        'sample_size': 0,
    })


@app.route('/api/calibration', methods=['POST'])
def update_calibration():
    """Update spread calibration coefficients from backtest results."""
    body = request.get_json(force=True)
    # Validate ranges
    close = max(0.80, min(1.0, body.get('close_coeff', 0.92)))
    moderate = max(0.70, min(0.95, body.get('moderate_coeff', 0.85)))
    log_mult = max(2.0, min(5.0, body.get('log_multiplier', 3.5)))

    data = {
        'close_coeff': round(close, 3),
        'moderate_coeff': round(moderate, 3),
        'log_multiplier': round(log_mult, 2),
        'last_updated': datetime.now().isoformat(),
        'sample_size': body.get('sample_size', 0),
        'source_backtest': body.get('source_backtest', ''),
    }
    # Store with very long TTL (1 year)
    k = api_cache._key('spread_calibration', {})
    conn = sqlite3.connect(api_cache.db_path)
    conn.execute(
        'INSERT OR REPLACE INTO cache (key, data, ts, ttl) VALUES (?, ?, ?, ?)',
        (k, json.dumps(data), time.time(), 86400 * 365))
    conn.commit()
    conn.close()
    return jsonify(data)


@app.route('/api/total-calibration', methods=['GET'])
def get_total_calibration():
    """Get total calibration coefficients."""
    cached = api_cache.get('total_calibration', {}, ttl=86400 * 365)
    if cached:
        return jsonify(cached)
    return jsonify({'center': 140.0, 'compression': 0.90, 'last_updated': None})


@app.route('/api/total-calibration', methods=['POST'])
def set_total_calibration():
    """Update total calibration coefficients."""
    body = request.get_json(force=True)
    center = max(120, min(160, body.get('center', 140.0)))
    compression = max(0.70, min(1.0, body.get('compression', 0.90)))
    data = {
        'center': round(center, 1),
        'compression': round(compression, 3),
        'last_updated': datetime.now().isoformat(),
        'sample_size': body.get('sample_size', 0),
    }
    k = api_cache._key('total_calibration', {})
    conn = sqlite3.connect(api_cache.db_path)
    try:
        conn.execute(
            'INSERT OR REPLACE INTO cache (key, data, ts, ttl) VALUES (?, ?, ?, ?)',
            (k, json.dumps(data), time.time(), 86400 * 365))
        conn.commit()
    finally:
        conn.close()
    return jsonify(data)


@app.route('/api/conf-adjustments', methods=['GET'])
def get_conf_adjustments():
    """Get per-conference scaling overrides for calcConfAdj()."""
    cached = api_cache.get('conf_adjustments', {}, ttl=86400 * 365)
    return jsonify(cached or {})


@app.route('/api/conf-adjustments', methods=['POST'])
def update_conf_adjustments():
    """Update per-conference scaling overrides."""
    body = request.get_json(force=True)
    # body format: {"SEC": 0.04, "WCC": 0.08, ...}
    # Each value is the per-conference scaling factor (default 0.06)
    # Clamp each to [0.01, 0.12]
    adjustments = {}
    for conf, factor in body.items():
        adjustments[conf] = round(max(0.01, min(0.12, float(factor))), 3)

    k = api_cache._key('conf_adjustments', {})
    conn = sqlite3.connect(api_cache.db_path)
    conn.execute(
        'INSERT OR REPLACE INTO cache (key, data, ts, ttl) VALUES (?, ?, ?, ?)',
        (k, json.dumps(adjustments), time.time(), 86400 * 365))
    conn.commit()
    conn.close()
    return jsonify(adjustments)


@app.route('/api/backtest', methods=['GET'])
def run_backtest():
    """Run backtest over a date range. Params: start, end (YYYY-MM-DD), historical (optional)."""
    start = request.args.get('start')
    end = request.args.get('end')
    historical = request.args.get('historical', '').lower() == 'true'
    if not start or not end:
        return jsonify({'error': 'start and end date parameters required (YYYY-MM-DD)'}), 400
    try:
        data = backtester.backtest_date_range(start, end, client, api_cache,
                                               use_historical=historical)
        return jsonify(data)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/backtest/historical', methods=['GET'])
def run_historical_backtest():
    """Run backtest with historical KenPom ratings (no lookahead bias).
    Params: start, end (YYYY-MM-DD), compare (optional, 'true' to run both modes)."""
    start = request.args.get('start')
    end = request.args.get('end')
    compare = request.args.get('compare', '').lower() == 'true'
    if not start or not end:
        return jsonify({'error': 'start and end date parameters required'}), 400
    try:
        if compare:
            data = backtester.backtest_with_bias_comparison(start, end, client, api_cache)
        else:
            data = backtester.backtest_date_range(start, end, client, api_cache,
                                                   use_historical=True)
        return jsonify(data)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/backtest/predictions', methods=['GET'])
def backtest_predictions():
    """Backtest saved predictions that have results entered."""
    try:
        data = backtester.backtest_predictions()
        return jsonify(data)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/backtest/self-test', methods=['GET'])
def backtest_self_test():
    """Run backtester self-test to validate model implementations."""
    try:
        from composite_model import self_test as model_self_test
        model_results = model_self_test()
        bt_results = backtester.run_self_test()
        return jsonify({
            'model_tests': model_results,
            'backtester_tests': bt_results,
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/game-prediction', methods=['POST'])
def save_game_prediction():
    """Save a single game prediction. Body: {date, visitor, home, ourMargin, ourPredWinner, ourPredHome, t1Score, t2Score, t1WinProb}."""
    body = request.get_json(force=True)
    date = body.get('date')
    visitor = body.get('visitor')
    home = body.get('home')
    if not date or not visitor or not home:
        return jsonify({'error': 'date, visitor, home required'}), 400
    k = api_cache._key('game_pred', {'date': date, 'visitor': visitor, 'home': home})
    try:
        conn = sqlite3.connect(api_cache.db_path)
        conn.execute(
            'INSERT OR REPLACE INTO cache (key, data, ts, ttl) VALUES (?, ?, ?, ?)',
            (k, json.dumps(body, default=str), time.time(), 86400 * 90))
        conn.commit()
        conn.close()
    except Exception:
        pass
    return jsonify({'ok': True})


@app.route('/api/game-predictions', methods=['GET'])
def get_game_predictions():
    """Return saved game predictions, optionally filtered by date."""
    date_filter = request.args.get('date')
    conn = sqlite3.connect(api_cache.db_path)
    rows = conn.execute("SELECT key, data FROM cache WHERE key LIKE ? AND (ts + ttl) > ?",
                        ('%game_pred%', time.time())).fetchall()
    conn.close()
    results = []
    for (key, data) in rows:
        try:
            parsed = json.loads(data)
            if isinstance(parsed, dict) and parsed.get('visitor') and parsed.get('home'):
                if date_filter and parsed.get('date') != date_filter:
                    continue
                results.append(parsed)
        except (json.JSONDecodeError, TypeError):
            pass
    return jsonify(results)


@app.route('/api/ats-results', methods=['POST'])
def save_ats_results():
    """Save ATS results for a date. Body: {date, games, summary}."""
    body = request.get_json(force=True)
    date = body.get('date')
    if not date:
        return jsonify({'error': 'date required'}), 400
    # Store with a long TTL (90 days) using raw SQLite since set() uses default_ttl
    k = api_cache._key('ats_daily', {'date': date})
    try:
        conn = sqlite3.connect(api_cache.db_path)
        conn.execute(
            'INSERT OR REPLACE INTO cache (key, data, ts, ttl) VALUES (?, ?, ?, ?)',
            (k, json.dumps(body, default=str), time.time(), 86400 * 90))
        conn.commit()
        conn.close()
    except Exception:
        pass
    return jsonify({'ok': True})


@app.route('/api/ats-history', methods=['GET'])
def get_ats_history():
    """Return all saved ATS daily results."""
    conn = sqlite3.connect(api_cache.db_path)
    rows = conn.execute("SELECT data FROM cache WHERE (ts + ttl) > ?", (time.time(),)).fetchall()
    conn.close()
    results = []
    for (data,) in rows:
        try:
            parsed = json.loads(data)
            if isinstance(parsed, dict) and parsed.get('date') and parsed.get('summary'):
                results.append(parsed)
        except (json.JSONDecodeError, TypeError):
            pass
    results.sort(key=lambda x: x.get('date', ''))
    return jsonify(results)


@app.route('/api/cache-stats', methods=['GET'])
def get_cache_stats():
    """Return cache hit/miss stats and rate limit info."""
    return jsonify(api_cache.stats())


# ═══════════════════════════════════════════════════════
# Bracket Simulator Endpoints
# ═══════════════════════════════════════════════════════

_bracket_sim = BracketSimulator(client, api_cache)
_bracket_sim_state = {
    'status': 'idle',       # idle | running | complete | error
    'progress': 0,
    'message': '',
    'results': None,
    'error': None,
    'started_at': None,
    'completed_at': None,
}
_bracket_sim_lock = threading.Lock()


def _run_bracket_sim(num_tournaments, num_sims_per_game, num_workers=None):
    """Background thread target for bracket simulation."""
    global _bracket_sim_state
    try:
        with _bracket_sim_lock:
            _bracket_sim_state['status'] = 'running'
            _bracket_sim_state['progress'] = 0
            _bracket_sim_state['message'] = 'Prefetching KenPom data...'
            _bracket_sim_state['started_at'] = time.time()

        # Prefetch all team data
        num_teams = _bracket_sim.prefetch_data()

        # Determine worker count
        if num_workers is None:
            num_workers = max(1, (os.cpu_count() or 1) - 1)

        with _bracket_sim_lock:
            _bracket_sim_state['message'] = (
                f'Loaded {num_teams} teams. '
                f'Starting parallel simulation ({num_workers} workers)...')
            _bracket_sim_state['progress'] = 5

        def progress_cb(pct, msg):
            with _bracket_sim_lock:
                _bracket_sim_state['progress'] = 5 + pct * 0.95
                _bracket_sim_state['message'] = msg

        results = _bracket_sim.run(
            num_tournaments=num_tournaments,
            num_sims_per_game=num_sims_per_game,
            progress_callback=progress_cb,
            num_workers=num_workers)

        with _bracket_sim_lock:
            _bracket_sim_state['status'] = 'complete'
            _bracket_sim_state['progress'] = 100
            _bracket_sim_state['message'] = f'Complete ({num_workers} workers)'
            _bracket_sim_state['results'] = results
            _bracket_sim_state['completed_at'] = time.time()
            _bracket_sim_state['error'] = None

        # Cache results persistently (24-hour TTL)
        api_cache.set('bracket_sim_results', {}, results)

    except Exception as e:
        with _bracket_sim_lock:
            _bracket_sim_state['status'] = 'error'
            _bracket_sim_state['message'] = str(e)
            _bracket_sim_state['error'] = str(e)
            _bracket_sim_state['completed_at'] = time.time()


@app.route('/api/bracket-simulate', methods=['POST'])
def start_bracket_simulation():
    """Trigger a bracket simulation (runs in background thread).

    Body (optional): {num_tournaments: 1000, num_sims_per_game: 500}
    """
    with _bracket_sim_lock:
        if _bracket_sim_state['status'] == 'running':
            return jsonify({
                'error': 'Simulation already running',
                'progress': _bracket_sim_state['progress'],
                'message': _bracket_sim_state['message'],
            }), 409

    body = request.get_json(force=True, silent=True) or {}
    num_tournaments = min(body.get('num_tournaments', 1000), 10000)
    num_sims_per_game = min(body.get('num_sims_per_game', 500), 2000)
    num_workers = body.get('num_workers', None)  # None = auto-detect
    if num_workers is not None:
        try:
            num_workers = min(max(int(num_workers), 1), os.cpu_count() or 1)
        except (ValueError, TypeError):
            num_workers = None  # fall back to auto-detect

    with _bracket_sim_lock:
        _bracket_sim_state['status'] = 'running'
        _bracket_sim_state['progress'] = 0
        _bracket_sim_state['message'] = 'Initializing...'
        _bracket_sim_state['results'] = None
        _bracket_sim_state['error'] = None

    thread = threading.Thread(
        target=_run_bracket_sim,
        args=(num_tournaments, num_sims_per_game, num_workers),
        daemon=True)
    thread.start()

    return jsonify({
        'status': 'started',
        'num_tournaments': num_tournaments,
        'num_sims_per_game': num_sims_per_game,
        'num_workers': num_workers or max(1, (os.cpu_count() or 1) - 1),
    }), 202


@app.route('/api/bracket-simulate/status', methods=['GET'])
def bracket_simulation_status():
    """Check the status of a running bracket simulation."""
    with _bracket_sim_lock:
        elapsed = None
        if _bracket_sim_state['started_at']:
            end = _bracket_sim_state['completed_at'] or time.time()
            elapsed = round(end - _bracket_sim_state['started_at'], 1)
        return jsonify({
            'status': _bracket_sim_state['status'],
            'progress': round(_bracket_sim_state['progress'], 1),
            'message': _bracket_sim_state['message'],
            'elapsed_seconds': elapsed,
            'has_results': _bracket_sim_state['results'] is not None,
            'error': _bracket_sim_state['error'],
            'num_workers': max(1, (os.cpu_count() or 1) - 1),
        })


@app.route('/api/bracket-results', methods=['GET'])
def get_bracket_results():
    """Get cached bracket simulation results."""
    # Check in-memory first
    with _bracket_sim_lock:
        if _bracket_sim_state['results']:
            return jsonify(_bracket_sim_state['results'])

    # Fall back to persistent cache
    cached = api_cache.get('bracket_sim_results', {}, ttl=86400)
    if cached:
        return jsonify(cached)

    return jsonify({'error': 'No simulation results available. POST /api/bracket-simulate first.'}), 404


@app.route('/api/bracket', methods=['GET'])
def get_bracket_data():
    """Get raw bracket structure (regions, First Four, seeds)."""
    return jsonify({
        'regions': {name: [(t1, t2) for t1, t2 in matchups]
                    for name, matchups in REGIONS.items()},
        'first_four': FIRST_FOUR,
        'seed_map': SEED_MAP,
        'region_map': REGION_MAP,
        'final_four_pairings': FINAL_FOUR_PAIRINGS,
        'round_names': ROUND_NAMES,
        'total_teams': len(get_all_team_names()),
    })


@app.route('/api/bracket-team/<team_name>', methods=['GET'])
def get_bracket_team(team_name):
    """Get detailed bracket simulation results for a specific team."""
    normalized = normalize_team_name(team_name)

    # Check in-memory results
    with _bracket_sim_lock:
        results = _bracket_sim_state.get('results')

    if not results:
        cached = api_cache.get('bracket_sim_results', {}, ttl=86400)
        if cached:
            results = cached

    if not results:
        return jsonify({'error': 'No simulation results. POST /api/bracket-simulate first.'}), 404

    team_probs = results.get('team_probs', {})
    data = team_probs.get(normalized)
    if not data:
        return jsonify({'error': f'Team "{normalized}" not found in results'}), 404

    return jsonify({
        'team': normalized,
        'seed': data['seed'],
        'region': REGION_MAP.get(normalized),
        'rounds': data['rounds'],
        'champion_prob': data['champion_prob'],
    })


# ── Dynamic Model Weights (Part A) ──

@app.route('/api/model-weights', methods=['GET'])
def get_model_weights():
    """Get current dynamic model weights."""
    cached = api_cache.get('dynamic_model_weights', {}, ttl=86400*365)
    return jsonify(cached or {
        'weights': {'efficiency': 0.10, 'similar': 0.10, 'conrat': 0.20, 'mc': 0.60},
        'source': 'default',
        'last_updated': None,
    })


@app.route('/api/model-weights', methods=['POST'])
def update_model_weights():
    """Update dynamic model weights from optimizer output."""
    body = request.get_json(force=True)
    weights = body.get('weights', {})
    # Validate: all 4 models present, sum to ~1.0, each >= 0.05
    required = {'efficiency', 'similar', 'conrat', 'mc'}
    if not required.issubset(weights.keys()):
        return jsonify({'error': 'Missing model weights'}), 400
    total = sum(weights.values())
    if abs(total - 1.0) > 0.01:
        return jsonify({'error': f'Weights sum to {total}, not 1.0'}), 400
    if any(v < 0.05 for v in weights.values()):
        return jsonify({'error': 'All weights must be >= 0.05'}), 400

    data = {
        'weights': weights,
        'source': body.get('source', 'optimizer'),
        'per_model_stats': body.get('per_model_stats', {}),
        'last_updated': body.get('timestamp', datetime.now().isoformat()),
    }
    k = api_cache._key('dynamic_model_weights', {})
    conn = sqlite3.connect(api_cache.db_path)
    try:
        conn.execute(
            'INSERT OR REPLACE INTO cache (key, data, ts, ttl) VALUES (?, ?, ?, ?)',
            (k, json.dumps(data, default=str), time.time(), 86400 * 365))
        conn.commit()
    finally:
        conn.close()
    return jsonify(data)


# ── KenPom Blend Ratio (Part B) ──

@app.route('/api/kp-blend-ratio', methods=['GET'])
def get_kp_blend_ratio():
    """Get current KenPom blend ratio."""
    cached = api_cache.get('kp_blend_ratio', {}, ttl=86400*365)
    return jsonify(cached or {'ratio': 0.18, 'source': 'default'})


@app.route('/api/kp-blend-ratio', methods=['POST'])
def update_kp_blend_ratio():
    """Update KenPom blend ratio."""
    body = request.get_json(force=True)
    ratio = float(body.get('ratio', 0.18))
    ratio = max(0.0, min(0.30, ratio))  # clamp to [0, 0.30]
    data = {
        'ratio': ratio,
        'source': body.get('source', 'manual'),
        'last_updated': datetime.now().isoformat(),
    }
    k = api_cache._key('kp_blend_ratio', {})
    conn = sqlite3.connect(api_cache.db_path)
    try:
        conn.execute(
            'INSERT OR REPLACE INTO cache (key, data, ts, ttl) VALUES (?, ?, ?, ?)',
            (k, json.dumps(data), time.time(), 86400 * 365))
        conn.commit()
    finally:
        conn.close()
    return jsonify(data)


@app.route('/')
def index():
    """Serve the main HTML page."""
    return send_from_directory('static', 'index.html')


if __name__ == '__main__':
    app.run(debug=True, port=5001)
