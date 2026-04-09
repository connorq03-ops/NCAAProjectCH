"""
golf_app.py - Flask REST API for PGA Tour Golf Analytics Dashboard.

The golf equivalent of app.py (root of repo). Mirrors the exact patterns:
SQLiteCache, cached_call, background thread simulation, predictions tracking,
model weights, calibration, and all data endpoints.

31 endpoints organized into categories:
  A. Data Endpoints (rankings, skills, player, field, pre-tournament preds)
  B. Course Endpoints (courses, course profile, course fit)
  C. Weather Endpoints
  D. Tournament Simulator (background thread + status + results)
  E. Matchup Endpoint
  F. Intelligence Endpoints (injuries/WD, form tracker)
  G. Predictions Tracking (CRUD + accuracy)
  H. Odds Endpoints
  I. Model Configuration (weights, calibration)
  J. Utility (cache stats, elite players)
  K. Frontend Serving
"""

import os
import time
import json
import hashlib
import uuid
import sqlite3
import threading
from datetime import datetime
from flask import Flask, jsonify, request, send_from_directory
from flask_cors import CORS
from dotenv import load_dotenv

from golf.datagolf_client import DataGolfClient
from golf.golf_tournament_simulator import GolfTournamentSimulator
from golf.golf_course_profiles import get_course_profile, get_all_courses, get_major_courses, COURSES
from golf.golf_elite_players import get_player_info, ELITE_PLAYERS, get_players_by_tier
from golf.golf_weather_scraper import WeatherFetcher, calc_weather_impact
from golf.golf_course_fit import calc_full_course_fit
from golf.golf_composite_model import predict_field, compute_golf_composite, calc_golf_rat
from golf.golf_form_tracker import FormTracker
from golf.golf_wd_scraper import GolfWDAnalyzer
from golf.golf_mc_engine import simulate_matchup
from golf.golf_sim_params import prefetch_all_player_data, build_player_sim_params


# ── Persistent SQLite Cache (copied from app.py lines 30-114) ──

class SQLiteCache:
    """Thread-safe persistent TTL cache backed by SQLite."""
    def __init__(self, db_path='.golf_cache.db', default_ttl=3600):
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

    def check_rate_limit(self, api='datagolf', max_calls=300, window_seconds=3600):
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


# ── Client Initialization (mirror app.py lines 155-169) ──

load_dotenv()

app = Flask(__name__, static_folder='static')
CORS(app)

DATAGOLF_API_KEY = os.getenv('DATAGOLF_API_KEY')
if not DATAGOLF_API_KEY:
    raise ValueError("DATAGOLF_API_KEY environment variable is required")
dg_client = DataGolfClient(api_key=DATAGOLF_API_KEY)

golf_cache = SQLiteCache(db_path=os.path.join(os.path.dirname(__file__), '.golf_cache.db'), default_ttl=3600)

# Optional modules (won't crash if API keys missing)
wd_analyzer = None
try:
    wd_analyzer = GolfWDAnalyzer()
    print("[golf-app] WD/injury intelligence module loaded")
except ValueError:
    print("[golf-app] ANTHROPIC_API_KEY not set — WD features disabled")

form_tracker = FormTracker(client=dg_client)


# ── Cached Call Helper (mirror app.py lines 172-181) ──

def golf_cached_call(endpoint, params, fetch_fn, ttl=None):
    """Check cache first, then call API if miss. Rate-limits DataGolf calls."""
    cached = golf_cache.get(endpoint, params, ttl=ttl)
    if cached is not None:
        return cached
    if not golf_cache.check_rate_limit('datagolf', max_calls=300, window_seconds=3600):
        raise Exception('DataGolf API rate limit exceeded. Try again later.')
    data = fetch_fn()
    golf_cache.set(endpoint, params, data)
    return data


# ═══════════════════════════════════════════════════════════════
# A. Data Endpoints
# ═══════════════════════════════════════════════════════════════

@app.route('/api/golf/rankings', methods=['GET'])
def get_rankings():
    """Get player rankings with SG splits."""
    try:
        params = {}
        data = golf_cached_call('rankings', params,
            lambda: dg_client.get_rankings())
        return jsonify(data)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/golf/skill-decompositions', methods=['GET'])
def get_skill_decompositions():
    """Get detailed SG skill breakdowns."""
    tour = request.args.get('tour', 'pga')
    try:
        params = {'tour': tour}
        data = golf_cached_call('skill_decompositions', params,
            lambda: dg_client.get_skill_decompositions(tour=tour))
        return jsonify(data)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/golf/player/<player_name>', methods=['GET'])
def get_player_profile(player_name):
    """Get detailed player profile combining rankings, elite info, form, and WD status."""
    try:
        # Rankings data
        rankings = golf_cached_call('rankings', {},
            lambda: dg_client.get_rankings())
        player_rankings = None
        if isinstance(rankings, dict):
            rankings_list = rankings.get('rankings', [])
        else:
            rankings_list = rankings or []
        for entry in rankings_list:
            if entry.get('player_name', '').lower() == player_name.lower():
                player_rankings = entry
                break

        # Elite players info
        elite_info = get_player_info(player_name)

        # Form tracker data
        form_data = form_tracker.get_player_form(player_name)

        # WD status
        wd_status = None
        if wd_analyzer:
            try:
                wd_status = wd_analyzer.analyze_player_status(player_name)
            except Exception:
                pass

        result = {
            'player_name': player_name,
            'rankings': player_rankings,
            'elite_info': elite_info,
            'form': form_data,
            'wd_status': wd_status,
        }
        return jsonify(result)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/golf/field', methods=['GET'])
def get_field():
    """Get current tournament field."""
    tour = request.args.get('tour', 'pga')
    try:
        params = {'tour': tour}
        data = golf_cached_call('field_updates', params,
            lambda: dg_client.get_field_updates(tour=tour))
        return jsonify(data)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/golf/pre-tournament-preds', methods=['GET'])
def get_pre_tournament_preds():
    """Get DataGolf's pre-tournament predictions."""
    tour = request.args.get('tour', 'pga')
    odds_format = request.args.get('odds_format', 'american')
    try:
        params = {'tour': tour, 'odds_format': odds_format}
        data = golf_cached_call('pre_tournament_preds', params,
            lambda: dg_client.get_pre_tournament_preds(tour=tour, odds_format=odds_format))
        return jsonify(data)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ═══════════════════════════════════════════════════════════════
# B. Course Endpoints
# ═══════════════════════════════════════════════════════════════

@app.route('/api/golf/courses', methods=['GET'])
def get_courses():
    """List all course profiles."""
    try:
        courses = get_all_courses()
        return jsonify(courses)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/golf/course/<course_id>', methods=['GET'])
def get_course(course_id):
    """Get single course profile."""
    course = get_course_profile(course_id)
    if not course:
        return jsonify({'error': f'Course "{course_id}" not found'}), 404
    return jsonify(course)


@app.route('/api/golf/course-fit/<course_id>', methods=['GET'])
def get_course_fit(course_id):
    """Get course fit scores for all ranked players against a course."""
    course = get_course_profile(course_id)
    if not course:
        return jsonify({'error': f'Course "{course_id}" not found'}), 404

    try:
        # Check cache first (6-hour TTL)
        cache_params = {'course_id': course_id}
        cached = golf_cache.get('course_fit', cache_params, ttl=21600)
        if cached is not None:
            return jsonify(cached)

        # Fetch rankings to get player stats
        rankings = golf_cached_call('rankings', {},
            lambda: dg_client.get_rankings())
        if isinstance(rankings, dict):
            rankings_list = rankings.get('rankings', [])
        else:
            rankings_list = rankings or []

        # Fetch skill decompositions for SG splits
        decomps = golf_cached_call('skill_decompositions', {'tour': 'pga'},
            lambda: dg_client.get_skill_decompositions())
        decomp_map = {}
        if isinstance(decomps, dict):
            decomp_list = decomps.get('decompositions', decomps.get('players', []))
        else:
            decomp_list = decomps or []
        for d in decomp_list:
            name = d.get('player_name', '')
            if name:
                decomp_map[name] = d

        # Compute course fit for each player
        fit_results = []
        for entry in rankings_list[:100]:  # Top 100 ranked players
            name = entry.get('player_name', '')
            player_stats = {
                'sg_ott': decomp_map.get(name, {}).get('sg_ott', 0.0),
                'sg_app': decomp_map.get(name, {}).get('sg_app', 0.0),
                'sg_arg': decomp_map.get(name, {}).get('sg_arg', 0.0),
                'sg_putt': decomp_map.get(name, {}).get('sg_putt', 0.0),
                'sg_total': entry.get('dg_skill_estimate', 0.0),
                'driving_distance': decomp_map.get(name, {}).get('driving_distance', 295.0),
                'driving_accuracy': decomp_map.get(name, {}).get('driving_accuracy', 60.0),
                'scrambling_pct': decomp_map.get(name, {}).get('scrambling_pct', 58.0),
            }
            fit = calc_full_course_fit(player_stats, course)
            fit_results.append({
                'player_name': name,
                'owgr_rank': entry.get('owgr_rank'),
                'dg_rank': entry.get('dg_rank'),
                'sg_total': entry.get('dg_skill_estimate', 0.0),
                **fit,
            })

        # Sort by total_fit descending
        fit_results.sort(key=lambda x: x.get('total_fit', 0), reverse=True)

        result = {
            'course_id': course_id,
            'course_name': course.get('name', course_id),
            'players': fit_results,
        }
        golf_cache.set('course_fit', cache_params, result)
        return jsonify(result)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ═══════════════════════════════════════════════════════════════
# C. Weather Endpoints
# ═══════════════════════════════════════════════════════════════

@app.route('/api/golf/weather/<course_id>', methods=['GET'])
def get_weather(course_id):
    """Get weather forecast + impact for a course."""
    course = get_course_profile(course_id)
    if not course:
        return jsonify({'error': f'Course "{course_id}" not found'}), 404

    weather_api_key = os.getenv('WEATHER_API_KEY')
    if not weather_api_key:
        return jsonify({'error': 'WEATHER_API_KEY not set', 'forecast': None, 'impacts': []}), 200

    try:
        fetcher = WeatherFetcher(api_key=weather_api_key)
        forecast = fetcher.fetch_tournament_weather(course_id)
        if not forecast:
            return jsonify({'error': 'Could not fetch weather for this course', 'forecast': None, 'impacts': []}), 200

        altitude = course.get('elevation_ft', 0)
        impacts = []
        for rd in range(1, 5):
            impact = calc_weather_impact(forecast, rd, altitude_ft=altitude)
            impact['round'] = rd
            impacts.append(impact)

        return jsonify({
            'course_id': course_id,
            'course_name': course.get('name', course_id),
            'forecast': forecast,
            'impacts': impacts,
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ═══════════════════════════════════════════════════════════════
# D. Tournament Simulator Endpoints (mirror bracket simulator)
# ═══════════════════════════════════════════════════════════════

_golf_sim = GolfTournamentSimulator(dg_client, golf_cache)
_golf_sim_state = {
    'status': 'idle',       # idle | running | complete | error
    'progress': 0,
    'message': '',
    'results': None,
    'error': None,
    'started_at': None,
    'completed_at': None,
}
_golf_sim_lock = threading.Lock()


def _run_golf_sim(course_id, tournament_id, num_tournaments, num_workers):
    """Background thread target for golf tournament simulation.
    Mirror _run_bracket_sim() from app.py lines 1070-1120."""
    global _golf_sim_state
    try:
        with _golf_sim_lock:
            _golf_sim_state['status'] = 'running'
            _golf_sim_state['progress'] = 0
            _golf_sim_state['message'] = 'Prefetching DataGolf data...'
            _golf_sim_state['started_at'] = time.time()
            _golf_sim_state['completed_at'] = None

        # Prefetch all player data
        num_players = _golf_sim.prefetch_data(course_id, tournament_id)

        # Determine worker count
        if num_workers is None:
            num_workers = max(1, (os.cpu_count() or 1) - 1)

        with _golf_sim_lock:
            _golf_sim_state['message'] = (
                f'Loaded {num_players} players. '
                f'Starting parallel simulation ({num_workers} workers)...')
            _golf_sim_state['progress'] = 5

        def progress_cb(pct, msg):
            with _golf_sim_lock:
                _golf_sim_state['progress'] = 5 + pct * 0.95
                _golf_sim_state['message'] = msg

        results = _golf_sim.run(
            num_tournaments=num_tournaments,
            progress_callback=progress_cb,
            num_workers=num_workers)

        with _golf_sim_lock:
            _golf_sim_state['status'] = 'complete'
            _golf_sim_state['progress'] = 100
            _golf_sim_state['message'] = f'Complete ({num_workers} workers)'
            _golf_sim_state['results'] = results
            _golf_sim_state['completed_at'] = time.time()
            _golf_sim_state['error'] = None

        # Cache results persistently (24-hour TTL)
        golf_cache.set('golf_sim_results', {}, results)

    except Exception as e:
        with _golf_sim_lock:
            _golf_sim_state['status'] = 'error'
            _golf_sim_state['message'] = str(e)
            _golf_sim_state['error'] = str(e)
            _golf_sim_state['completed_at'] = time.time()


@app.route('/api/golf/simulate', methods=['POST'])
def start_simulation():
    """Trigger tournament simulation (runs in background thread).
    Mirror app.py lines 1123-1165."""
    # Atomically check-and-set running status to prevent TOCTOU race
    with _golf_sim_lock:
        if _golf_sim_state['status'] == 'running':
            return jsonify({
                'error': 'Simulation already running',
                'progress': _golf_sim_state['progress'],
                'message': _golf_sim_state['message'],
            }), 409
        _golf_sim_state['status'] = 'running'
        _golf_sim_state['progress'] = 0
        _golf_sim_state['message'] = 'Initializing...'
        _golf_sim_state['results'] = None
        _golf_sim_state['error'] = None
        _golf_sim_state['started_at'] = None
        _golf_sim_state['completed_at'] = None

    body = request.get_json(force=True, silent=True) or {}
    course_id = body.get('course_id', 'augusta_national')
    tournament_id = body.get('tournament_id', None)
    num_tournaments = min(body.get('num_tournaments', 1000), 10000)
    num_workers = body.get('num_workers', None)
    if num_workers is not None:
        try:
            num_workers = min(max(int(num_workers), 1), os.cpu_count() or 1)
        except (ValueError, TypeError):
            num_workers = None

    # Validate course — reset to idle if invalid
    if get_course_profile(course_id) is None:
        with _golf_sim_lock:
            _golf_sim_state['status'] = 'idle'
            _golf_sim_state['message'] = ''
        return jsonify({'error': f'Unknown course_id: {course_id}'}), 400

    thread = threading.Thread(
        target=_run_golf_sim,
        args=(course_id, tournament_id, num_tournaments, num_workers),
        daemon=True)
    thread.start()

    return jsonify({
        'status': 'started',
        'course_id': course_id,
        'num_tournaments': num_tournaments,
        'num_workers': num_workers or max(1, (os.cpu_count() or 1) - 1),
    }), 202


@app.route('/api/golf/simulate/status', methods=['GET'])
def simulation_status():
    """Check simulation status. Mirror app.py lines 1168-1184."""
    with _golf_sim_lock:
        elapsed = None
        if _golf_sim_state['started_at']:
            end = _golf_sim_state['completed_at'] or time.time()
            elapsed = round(end - _golf_sim_state['started_at'], 1)
        return jsonify({
            'status': _golf_sim_state['status'],
            'progress': round(_golf_sim_state['progress'], 1),
            'message': _golf_sim_state['message'],
            'elapsed_seconds': elapsed,
            'has_results': _golf_sim_state['results'] is not None,
            'error': _golf_sim_state['error'],
            'num_workers': max(1, (os.cpu_count() or 1) - 1),
        })


@app.route('/api/golf/results', methods=['GET'])
def get_results():
    """Get simulation results. Mirror app.py lines 1187-1200."""
    # Check in-memory first
    with _golf_sim_lock:
        if _golf_sim_state['results']:
            return jsonify(_golf_sim_state['results'])

    # Fall back to persistent cache
    cached = golf_cache.get('golf_sim_results', {}, ttl=86400)
    if cached:
        return jsonify(cached)

    return jsonify({'error': 'No simulation results available. POST /api/golf/simulate first.'}), 404


@app.route('/api/golf/results/player/<player_name>', methods=['GET'])
def get_player_results(player_name):
    """Get detailed results for one player. Mirror app.py lines 1218-1246."""
    # Check in-memory results
    with _golf_sim_lock:
        results = _golf_sim_state.get('results')

    if not results:
        cached = golf_cache.get('golf_sim_results', {}, ttl=86400)
        if cached:
            results = cached

    if not results:
        return jsonify({'error': 'No simulation results. POST /api/golf/simulate first.'}), 404

    player_probs = results.get('player_probs', {})
    # Case-insensitive lookup
    data = player_probs.get(player_name)
    if not data:
        for name, d in player_probs.items():
            if name.lower() == player_name.lower():
                data = d
                player_name = name
                break

    if not data:
        return jsonify({'error': f'Player "{player_name}" not found in results'}), 404

    return jsonify({
        'player_name': player_name,
        **data,
    })


# ═══════════════════════════════════════════════════════════════
# E. Matchup Endpoint
# ═══════════════════════════════════════════════════════════════

@app.route('/api/golf/matchup', methods=['GET'])
def get_matchup():
    """H2H player matchup prediction."""
    p1 = request.args.get('p1')
    p2 = request.args.get('p2')
    course_id = request.args.get('course_id', 'augusta_national')
    num_sims = request.args.get('num_sims', 1000, type=int)
    num_sims = min(num_sims, 10000)

    if not p1 or not p2:
        return jsonify({'error': 'p1 and p2 parameters are required'}), 400

    course = get_course_profile(course_id)
    if not course:
        return jsonify({'error': f'Course "{course_id}" not found'}), 404

    try:
        # Fetch player data
        rankings = golf_cached_call('rankings', {},
            lambda: dg_client.get_rankings())
        decomps = golf_cached_call('skill_decompositions', {'tour': 'pga'},
            lambda: dg_client.get_skill_decompositions())

        # Build lookup maps
        rankings_map = {}
        if isinstance(rankings, dict):
            rankings_list = rankings.get('rankings', [])
        else:
            rankings_list = rankings or []
        for entry in rankings_list:
            rankings_map[entry.get('player_name', '').lower()] = entry

        decomp_map = {}
        if isinstance(decomps, dict):
            decomp_list = decomps.get('decompositions', decomps.get('players', []))
        else:
            decomp_list = decomps or []
        for d in decomp_list:
            decomp_map[d.get('player_name', '').lower()] = d

        def build_stats(name):
            r = rankings_map.get(name.lower(), {})
            d = decomp_map.get(name.lower(), {})
            return {
                '_player_name': name,
                'sg_total': r.get('dg_skill_estimate', 0.0),
                'sg_ott': d.get('sg_ott', 0.0),
                'sg_app': d.get('sg_app', 0.0),
                'sg_arg': d.get('sg_arg', 0.0),
                'sg_putt': d.get('sg_putt', 0.0),
                'driving_distance': d.get('driving_distance', 295.0),
                'driving_accuracy': d.get('driving_accuracy', 60.0),
                'scrambling_pct': d.get('scrambling_pct', 58.0),
                'owgr_rank': r.get('owgr_rank', 999),
            }

        p1_stats = build_stats(p1)
        p2_stats = build_stats(p2)

        # Build sim params
        p1_params = build_player_sim_params(p1_stats, course)
        p2_params = build_player_sim_params(p2_stats, course)

        # Run matchup simulation
        holes = course.get('holes', [])
        matchup_result = simulate_matchup(p1_params, p2_params, holes, num_sims=num_sims)

        # Composite model predictions
        p1_composite = compute_golf_composite(p1_stats, course)
        p2_composite = compute_golf_composite(p2_stats, course)

        return jsonify({
            'p1': p1,
            'p2': p2,
            'course_id': course_id,
            'num_sims': num_sims,
            'matchup': matchup_result,
            'p1_composite': p1_composite,
            'p2_composite': p2_composite,
            'p1_stats': p1_stats,
            'p2_stats': p2_stats,
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ═══════════════════════════════════════════════════════════════
# F. Intelligence Endpoints (WD/injuries + form)
# ═══════════════════════════════════════════════════════════════

@app.route('/api/golf/injuries', methods=['GET'])
def get_injuries():
    """Get current WD/injury intelligence for all tracked players.
    Mirror app.py lines 590-600."""
    if not wd_analyzer:
        return jsonify({'error': 'WD features not available (ANTHROPIC_API_KEY not set)', 'statuses': {}}), 200
    force = request.args.get('force', '').lower() == 'true'
    try:
        if force:
            # Invalidate the WD cache so analyze_tournament_field re-fetches
            cache_key = "tournament_field_PGA Tour"
            cache_path = os.path.join(
                wd_analyzer.cache.cache_dir,
                f"{wd_analyzer.cache._key(cache_key)}.json")
            if os.path.exists(cache_path):
                os.remove(cache_path)
        data = wd_analyzer.analyze_tournament_field('PGA Tour')
        return jsonify({'statuses': data})
    except Exception as e:
        return jsonify({'error': str(e), 'statuses': {}}), 500


@app.route('/api/golf/injuries/player', methods=['GET'])
def get_player_injuries():
    """Get WD status for a specific player. Mirror app.py lines 603-615."""
    if not wd_analyzer:
        return jsonify({'error': 'WD features not available', 'status': {}}), 200
    player = request.args.get('player')
    if not player:
        return jsonify({'error': 'player parameter is required'}), 400
    try:
        data = wd_analyzer.analyze_player_status(player)
        return jsonify(data)
    except Exception as e:
        return jsonify({'error': str(e), 'status': {}}), 500


@app.route('/api/golf/form', methods=['GET'])
def get_form():
    """Get form tracker data."""
    player = request.args.get('player')
    try:
        if player:
            data = form_tracker.get_player_form(player)
            return jsonify(data)
        else:
            # Return top 50 elite players form
            top_players = list(ELITE_PLAYERS.keys())[:50]
            data = form_tracker.get_field_form(top_players)
            return jsonify(data)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ═══════════════════════════════════════════════════════════════
# G. Predictions Tracking (mirror app.py prediction endpoints)
# ═══════════════════════════════════════════════════════════════

GOLF_PREDICTIONS_FILE = os.path.join(os.path.dirname(__file__), 'golf_predictions.json')


def _load_golf_predictions():
    """Load predictions from disk."""
    if os.path.exists(GOLF_PREDICTIONS_FILE):
        try:
            with open(GOLF_PREDICTIONS_FILE, 'r') as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            return []
    return []


def _save_golf_predictions(preds):
    """Persist predictions to disk."""
    with open(GOLF_PREDICTIONS_FILE, 'w') as f:
        json.dump(preds, f, indent=2)


@app.route('/api/golf/predictions', methods=['POST'])
def save_prediction():
    """Save a tournament prediction. Mirror app.py lines 660-695."""
    body = request.get_json()
    if not body:
        return jsonify({'error': 'JSON body required'}), 400

    pred = {
        'id': str(uuid.uuid4())[:8],
        'created_at': datetime.now().isoformat(),
        'tournament': body.get('tournament'),
        'course_id': body.get('course_id'),
        'player': body.get('player'),
        'predicted_finish': body.get('predicted_finish'),
        'predicted_top10': body.get('predicted_top10'),
        'predicted_winner': body.get('predicted_winner'),
        'predicted_make_cut': body.get('predicted_make_cut'),
        'win_probability': body.get('win_probability'),
        'top10_probability': body.get('top10_probability'),
        'golf_rat_score': body.get('golf_rat_score'),
        'odds_at_time': body.get('odds_at_time'),
        # Result fields (filled in later)
        'actual_finish': None,
        'actual_made_cut': None,
        'result_entered': False,
        'finish_error': None,
        'top10_correct': None,
        'winner_correct': None,
        'cut_correct': None,
    }

    preds = _load_golf_predictions()
    preds.append(pred)
    _save_golf_predictions(preds)
    return jsonify(pred), 201


@app.route('/api/golf/predictions', methods=['GET'])
def get_predictions():
    """Get predictions, optionally filtered. Mirror app.py lines 698-707."""
    preds = _load_golf_predictions()
    tournament_filter = request.args.get('tournament')
    if tournament_filter:
        preds = [p for p in preds if p.get('tournament') == tournament_filter]
    # Sort by created_at descending
    preds.sort(key=lambda p: p.get('created_at', ''), reverse=True)
    return jsonify(preds)


@app.route('/api/golf/predictions/<pred_id>/result', methods=['PUT'])
def update_prediction_result(pred_id):
    """Record actual tournament result. Mirror app.py lines 710-748."""
    body = request.get_json()
    if not body:
        return jsonify({'error': 'JSON body required'}), 400

    preds = _load_golf_predictions()
    pred = next((p for p in preds if p['id'] == pred_id), None)
    if not pred:
        return jsonify({'error': 'Prediction not found'}), 404

    actual_finish = body.get('actual_finish')
    actual_made_cut = body.get('actual_made_cut')
    if actual_finish is None:
        return jsonify({'error': 'actual_finish is required'}), 400

    pred['actual_finish'] = int(actual_finish)
    pred['actual_made_cut'] = bool(actual_made_cut) if actual_made_cut is not None else (int(actual_finish) <= 65)
    pred['result_entered'] = True

    # Compute accuracy metrics
    if pred.get('predicted_finish') is not None:
        pred['finish_error'] = abs(int(actual_finish) - pred['predicted_finish'])
    else:
        pred['finish_error'] = None

    pred['top10_correct'] = (int(actual_finish) <= 10) == bool(pred.get('predicted_top10')) if pred.get('predicted_top10') is not None else None
    pred['winner_correct'] = (int(actual_finish) == 1) == bool(pred.get('predicted_winner')) if pred.get('predicted_winner') is not None else None
    pred['cut_correct'] = pred['actual_made_cut'] == bool(pred.get('predicted_make_cut')) if pred.get('predicted_make_cut') is not None else None

    _save_golf_predictions(preds)
    return jsonify(pred)


@app.route('/api/golf/predictions/accuracy', methods=['GET'])
def get_prediction_accuracy():
    """Get overall prediction accuracy stats. Mirror app.py lines 751-782."""
    preds = _load_golf_predictions()
    completed = [p for p in preds if p.get('result_entered')]

    if not completed:
        return jsonify({
            'total_predictions': len(preds),
            'completed': 0,
            'avg_finish_error': None,
            'top10_accuracy': None,
            'cut_accuracy': None,
            'winner_accuracy': None,
        })

    finish_errors = [p.get('finish_error', 0) for p in completed if p.get('finish_error') is not None]
    top10_correct = sum(1 for p in completed if p.get('top10_correct'))
    top10_total = sum(1 for p in completed if p.get('top10_correct') is not None)
    cut_correct = sum(1 for p in completed if p.get('cut_correct'))
    cut_total = sum(1 for p in completed if p.get('cut_correct') is not None)
    winner_correct = sum(1 for p in completed if p.get('winner_correct'))
    winner_total = sum(1 for p in completed if p.get('winner_correct') is not None)

    return jsonify({
        'total_predictions': len(preds),
        'completed': len(completed),
        'pending': len(preds) - len(completed),
        'avg_finish_error': round(sum(finish_errors) / len(finish_errors), 1) if finish_errors else None,
        'median_finish_error': round(sorted(finish_errors)[len(finish_errors) // 2], 1) if finish_errors else None,
        'top10_accuracy': round(top10_correct / top10_total * 100, 1) if top10_total > 0 else None,
        'top10_record': f"{top10_correct}-{top10_total - top10_correct}" if top10_total > 0 else None,
        'cut_accuracy': round(cut_correct / cut_total * 100, 1) if cut_total > 0 else None,
        'cut_record': f"{cut_correct}-{cut_total - cut_correct}" if cut_total > 0 else None,
        'winner_accuracy': round(winner_correct / winner_total * 100, 1) if winner_total > 0 else None,
        'winner_record': f"{winner_correct}-{winner_total - winner_correct}" if winner_total > 0 else None,
    })


@app.route('/api/golf/predictions/<pred_id>', methods=['DELETE'])
def delete_prediction(pred_id):
    """Delete a prediction. Mirror app.py lines 785-791."""
    preds = _load_golf_predictions()
    preds = [p for p in preds if p['id'] != pred_id]
    _save_golf_predictions(preds)
    return jsonify({'deleted': pred_id})


# ═══════════════════════════════════════════════════════════════
# H. Odds Endpoints
# ═══════════════════════════════════════════════════════════════

@app.route('/api/golf/odds/outrights', methods=['GET'])
def get_outright_odds():
    """Get outright betting odds."""
    tour = request.args.get('tour', 'pga')
    market = request.args.get('market', 'win')
    try:
        params = {'tour': tour, 'market': market}
        data = golf_cached_call('outright_odds', params,
            lambda: dg_client.get_outright_odds(tour=tour, market=market))
        return jsonify(data)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/golf/odds/matchups', methods=['GET'])
def get_matchup_odds():
    """Get H2H matchup odds."""
    tour = request.args.get('tour', 'pga')
    try:
        params = {'tour': tour}
        data = golf_cached_call('matchup_odds', params,
            lambda: dg_client.get_matchup_odds(tour=tour))
        return jsonify(data)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ═══════════════════════════════════════════════════════════════
# I. Model Configuration
# ═══════════════════════════════════════════════════════════════

@app.route('/api/golf/model-weights', methods=['GET'])
def get_model_weights():
    """Get current golf composite model weights. Mirror app.py lines 1251-1259."""
    cached = golf_cache.get('golf_model_weights', {}, ttl=86400 * 365)
    return jsonify(cached or {
        'weights': {'sg_efficiency': 0.25, 'course_fit': 0.25, 'golf_rat': 0.25, 'mc': 0.25},
        'source': 'default',
        'last_updated': None,
    })


@app.route('/api/golf/model-weights', methods=['POST'])
def update_model_weights():
    """Update golf model weights. Mirror app.py lines 1262-1292."""
    body = request.get_json(force=True)
    weights = body.get('weights', {})
    # Validate: 4 models present, sum to ~1.0, each >= 0.05
    required = {'sg_efficiency', 'course_fit', 'golf_rat', 'mc'}
    if not required.issubset(weights.keys()):
        return jsonify({'error': 'Missing model weights. Required: sg_efficiency, course_fit, golf_rat, mc'}), 400
    try:
        weights = {k: float(v) for k, v in weights.items()}
    except (TypeError, ValueError):
        return jsonify({'error': 'All weights must be numeric'}), 400
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
    k = golf_cache._key('golf_model_weights', {})
    conn = sqlite3.connect(golf_cache.db_path)
    try:
        conn.execute(
            'INSERT OR REPLACE INTO cache (key, data, ts, ttl) VALUES (?, ?, ?, ?)',
            (k, json.dumps(data, default=str), time.time(), 86400 * 365))
        conn.commit()
    finally:
        conn.close()
    return jsonify(data)


@app.route('/api/golf/calibration', methods=['GET'])
def get_calibration():
    """Get finish position calibration coefficients. Mirror app.py lines 799-812."""
    cached = golf_cache.get('golf_calibration', {}, ttl=86400 * 365)
    if cached:
        return jsonify(cached)
    return jsonify({
        'center': 35.0,
        'compression': 0.85,
    })


@app.route('/api/golf/calibration', methods=['POST'])
def update_calibration():
    """Update calibration. Mirror app.py lines 815-840."""
    body = request.get_json(force=True)
    center = body.get('center', 35.0)
    compression = body.get('compression', 0.85)
    try:
        center = float(center)
        compression = float(compression)
    except (TypeError, ValueError):
        return jsonify({'error': 'center and compression must be numeric'}), 400

    if not (10 <= center <= 60):
        return jsonify({'error': 'center must be between 10 and 60'}), 400
    if not (0.5 <= compression <= 1.0):
        return jsonify({'error': 'compression must be between 0.5 and 1.0'}), 400

    data = {
        'center': center,
        'compression': compression,
        'last_updated': datetime.now().isoformat(),
    }
    k = golf_cache._key('golf_calibration', {})
    conn = sqlite3.connect(golf_cache.db_path)
    try:
        conn.execute(
            'INSERT OR REPLACE INTO cache (key, data, ts, ttl) VALUES (?, ?, ?, ?)',
            (k, json.dumps(data), time.time(), 86400 * 365))
        conn.commit()
    finally:
        conn.close()
    return jsonify(data)


# ═══════════════════════════════════════════════════════════════
# J. Utility
# ═══════════════════════════════════════════════════════════════

@app.route('/api/golf/cache-stats', methods=['GET'])
def get_cache_stats():
    """Return cache hit/miss stats and rate limit info."""
    return jsonify(golf_cache.stats())


@app.route('/api/golf/elite-players', methods=['GET'])
def get_elite_players():
    """Get elite player database."""
    tier = request.args.get('tier')
    try:
        if tier:
            players = get_players_by_tier(tier)
            return jsonify(players)
        return jsonify(ELITE_PLAYERS)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ═══════════════════════════════════════════════════════════════
# K. Serve Frontend
# ═══════════════════════════════════════════════════════════════

@app.route('/golf')
def golf_index():
    """Serve the golf dashboard HTML."""
    return send_from_directory('static', 'golf.html')


# ── Entry Point ──

if __name__ == '__main__':
    app.run(debug=True, port=5002)
