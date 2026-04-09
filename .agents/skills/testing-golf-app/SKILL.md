# Testing the Golf App

## Starting the Golf App

```bash
# Requires DATAGOLF_API_KEY (use "dummy" for local-data-only testing)
DAGOLF_API_KEY=dummy python3 -m golf.golf_app
# Runs on port 5002, open http://localhost:5002/golf
```

## What Works Without a Real API Key

With `DATAGOLF_API_KEY=dummy`, these endpoints return real local data:
- `GET /api/golf/courses` — 16 courses from golf_course_profiles.py
- `GET /api/golf/course/<course_id>` — e.g. `augusta_national`, `pinehurst_no2`
- `GET /api/golf/elite-players` — 44 players across 5 tiers (elite/star/key/solid/rising)
- `GET /api/golf/model-weights` — Default weights (4 x 0.25)
- `GET /api/golf/calibration` — Default center=35.0, compression=0.85
- `GET /api/golf/cache-stats` — Cache statistics
- `GET /api/golf/simulate/status` — Returns "idle"
- `POST/GET/PUT/DELETE /api/golf/predictions` — Full CRUD flow
- `GET /golf` — Frontend HTML dashboard

These require a real DATAGOLF_API_KEY:
- Rankings, Strokes Gained, Field, Pre-tournament predictions
- Odds (outrights and matchups)
- Matchup predictor (needs player data from API)
- Tournament Simulator full run (needs field data)
- Form Tracker (needs tournament history)

## Optional API Keys
- `ANTHROPIC_API_KEY` — Enables WD/injury intelligence features. Without it, injury endpoints return graceful error messages.
- `WEATHER_API_KEY` — Enables weather forecast and impact calculations in Course Analysis tab.

## Key UI Testing Flows

### Course Analysis (works without API key)
1. Click "Course Analysis" tab
2. Select a course from dropdown (e.g. Augusta National Golf Club)
3. Verify: Par, Yardage, Location, SG Weights, Style Tags all display correctly

### Predictions CRUD (works without API key)
1. Create prediction via API:
   ```bash
   curl -X POST -H 'Content-Type: application/json' \
     -d '{"tournament":"The Masters","player":"Scottie Scheffler","predicted_finish":3,"predicted_top10":true}' \
     http://localhost:5002/api/golf/predictions
   ```
2. Click "Predictions" tab — verify prediction appears in table
3. Enter result via API:
   ```bash
   curl -X PUT -H 'Content-Type: application/json' \
     -d '{"actual_finish":5,"actual_made_cut":true}' \
     http://localhost:5002/api/golf/predictions/<pred_id>/result
   ```
4. Refresh — verify accuracy dashboard updates (finish error, top10/cut/winner accuracy)

### Settings (works without API key)
1. Click "Settings" tab
2. Verify: 4 model weight sliders at 0.25, sum=1.00
3. Verify: Calibration inputs (center=35, compression=0.85)
4. Verify: Cache statistics display
5. Verify: Elite Players Database table (44 players with tiers, impact, strengths)

## API Smoke Test Script

```bash
curl -s http://localhost:5002/api/golf/courses | python3 -c "import sys,json; d=json.load(sys.stdin); print(f'Courses: {len(d)}')"
curl -s http://localhost:5002/api/golf/elite-players | python3 -c "import sys,json; d=json.load(sys.stdin); print(f'Players: {len(d)}')"
curl -s http://localhost:5002/api/golf/model-weights | python3 -c "import sys,json; d=json.load(sys.stdin); print(f'Sum: {sum(d[\"weights\"].values())}')"
curl -s -o /dev/null -w '%{http_code}' http://localhost:5002/api/golf/course/nonexistent  # expect 404
curl -s -o /dev/null -w '%{http_code}' -X POST -H 'Content-Type: application/json' -d '{"weights":{"a":0.5,"b":0.5,"c":0.5,"d":0.5}}' http://localhost:5002/api/golf/model-weights  # expect 400
```

## Files
- `golf/golf_app.py` — Flask REST API (31 endpoints, port 5002)
- `golf/static/golf.html` — Frontend SPA (9 tabs)
- `golf/static/golf-mc-worker.js` — Web Worker for client-side MC simulation
- `golf/tests/test_golf_app.py` — Flask test client tests (run with `python -m pytest golf/tests/ -v`)
