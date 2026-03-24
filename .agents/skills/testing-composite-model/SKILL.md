# Testing the Composite Model & Backtester Pipeline

## Overview
The composite model pipeline (`composite_model.py`) and backtester (`backtester.py`) have built-in self-tests that validate all model functions with synthetic data. These do NOT require a KenPom API key.

## Devin Secrets Needed
- `KENPOM_API_KEY` — Required to start Flask app and run real backtests. NOT required for self-tests.
- `ANTHROPIC_API_KEY` — Optional, only for injury analysis features.

## Quick Self-Test (No API Key Needed)

### Standalone composite_model.py self-test (14 checks)
```bash
cd /home/ubuntu/repos/NCAAProjectCH
python3 composite_model.py
```
Expected: All 14 lines print `[PASS]`, final line says "All tests PASSED."

### Standalone Backtester self-test (7 checks)
```bash
python3 -c "
from backtester import Backtester
bt = Backtester()
results = bt.run_self_test()
for k, v in results.items():
    print(f'  {k}: {v}')
all_pass = all(v == 'PASS' for v in results.values())
print('ALL PASS' if all_pass else 'SOME FAILED')
"
```
Expected: All 7 tests show PASS.

## Flask Self-Test Endpoint

To test the `/api/backtest/self-test` endpoint via HTTP:

1. Start Flask with a dummy API key (self-test doesn't call KenPom):
```bash
KENPOM_API_KEY=dummy python3 app.py &
sleep 3
```

2. Hit the self-test endpoint:
```bash
curl -s http://localhost:5001/api/backtest/self-test | python3 -m json.tool
```

3. Expected: HTTP 200, JSON with `model_tests` (14 PASS) and `backtester_tests` (7 PASS).

## Calibration API Endpoints

The calibration feedback loop uses 4 endpoints. All work with `KENPOM_API_KEY=dummy`.

### Spread Calibration (GET/POST /api/calibration)
```bash
# GET defaults (when no calibration stored)
curl -s http://localhost:5001/api/calibration | python3 -m json.tool
# Expected: {close_coeff: 0.92, moderate_coeff: 0.85, log_multiplier: 3.5, last_updated: null}

# POST custom values
curl -s -X POST http://localhost:5001/api/calibration \
  -H 'Content-Type: application/json' \
  -d '{"close_coeff": 0.88, "moderate_coeff": 0.82, "log_multiplier": 4.0, "sample_size": 150}' | python3 -m json.tool

# GET should return stored values (NOT defaults)
curl -s http://localhost:5001/api/calibration | python3 -m json.tool
```

Bounds clamping: `close_coeff` [0.80-1.0], `moderate_coeff` [0.70-0.95], `log_multiplier` [2.0-5.0]

### Conference Adjustments (GET/POST /api/conf-adjustments)
```bash
# GET defaults (empty when no overrides stored)
curl -s http://localhost:5001/api/conf-adjustments | python3 -m json.tool
# Expected: {}

# POST per-conference overrides
curl -s -X POST http://localhost:5001/api/conf-adjustments \
  -H 'Content-Type: application/json' \
  -d '{"SEC": 0.04, "WCC": 0.08, "B12": 0.05}' | python3 -m json.tool

# GET should return stored values
curl -s http://localhost:5001/api/conf-adjustments | python3 -m json.tool
```

Bounds clamping: all values clamped to [0.01, 0.12]

### Frontend Loading Verification
After POSTing custom calibration values, open browser to `http://localhost:5001` and check the console for:
- `[calibration] Loaded:` — should show custom values, not defaults
- `[conf-adj] Loaded overrides:` — should show posted conference overrides

You can also verify via browser console:
```js
JSON.stringify(SPREAD_COEFFS)       // e.g. {"close":0.88,"moderate":0.82,"logMult":4.2}
JSON.stringify(CONF_SCALE_OVERRIDES) // e.g. {"SEC":0.04,"WCC":0.09}
```

### Cache Key Consistency Check
The `_key` method in `SQLiteCache` (app.py) uses `md5(endpoint + json.dumps(params))`. To verify POST and GET use the same key:
```bash
python3 -c "
import hashlib, json, sqlite3
def _key(endpoint, params):
    raw = endpoint + json.dumps(params, sort_keys=True, default=str)
    return hashlib.md5(raw.encode()).hexdigest()
cal_key = _key('spread_calibration', {})
conf_key = _key('conf_adjustments', {})
conn = sqlite3.connect('.cache.db')
keys = [r[0] for r in conn.execute('SELECT key FROM cache').fetchall()]
print(f'spread_calibration match: {cal_key in keys}')
print(f'conf_adjustments match: {conf_key in keys}')
conn.close()
"
```

## Real Backtest (Requires KenPom API Key)

To run a real backtest against ESPN scores:
```bash
curl 'http://localhost:5001/api/backtest?start=2025-03-01&end=2025-03-05'
```
Expected pick accuracy: ~65-72%, spread error: ~8-12 pts.

## Known Limitations
- The backtester uses current-day KenPom ratings, not historical snapshots (lookahead bias).
- Momentum enrichment is hardcoded to 0 (requires expensive per-date archive fetches).
- MC simulation uses 200 sims per game for speed.
- Port 5001 may be in use from previous runs — use `pkill -f 'python3 app.py'` to clear it.
- Without a real `KENPOM_API_KEY`, the main ratings page shows a 401 error but all calibration/self-test endpoints still work.
- The `.cache.db` file should be deleted (`rm .cache.db`) when testing with a clean state.

## Dependencies
```bash
pip install -r requirements.txt
```
Key packages: flask, flask-cors, requests, python-dotenv, beautifulsoup4, lxml, anthropic
