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

## Dependencies
```bash
pip install -r requirements.txt
```
Key packages: flask, flask-cors, requests, python-dotenv
