# Testing NCAAProjectCH App

## Environment Setup

1. Start Flask server:
   ```bash
   cd /home/ubuntu/repos/NCAAProjectCH
   KENPOM_API_KEY=dummy python3 app.py
   ```
   Server runs on `http://localhost:5001`.

2. The app requires `KENPOM_API_KEY` env var to start. A dummy value works for testing features that don't need live KenPom data.

3. `ANTHROPIC_API_KEY` is optional — without it, injury features are disabled (non-blocking warning).

## Devin Secrets Needed

- `KENPOM_API_KEY` — Required for full end-to-end testing with live data (ratings, fanmatch, game scores). Without it, only cached data and non-data-dependent features can be tested.

## Cache Behavior

- Cache DB: `.cache.db` (SQLite)
- **KenPom API data** (ratings, fanmatch, odds, scores): **3600s TTL (1 hour)**. This cache expires quickly, so testing data-dependent features (Recap tab game data, Backtest runs, Matchup Predictor predictions) requires either a real API key or very recently cached data.
- **ATS history** data: **7776000s TTL (90 days)**. This data persists much longer and can be used for testing the ATS Tracker tab.
- **Calibration data** (spread and total calibration): **86400 * 365 TTL (1 year)**. Very long-lived.
- Cache keys are MD5 hashes of API call parameters, so you can't easily look up data by date.

## What's Testable Without Real KenPom API Key

- **Page load**: Verify no JS syntax errors (check DevTools Console)
- **API endpoints**: `/api/total-calibration` GET/POST, `/api/calibration` GET/POST, `/api/model-weights`, etc.
- **JS functions**: `calibrateTotal()`, `calibrateSpread()`, `TOTAL_COEFFS`, `SPREAD_COEFFS` via DevTools Console
- **Tab navigation**: All tabs switch without JS crashes (Ratings, Recap, ATS Tracker, Backtest, etc.)
- **ATS Tracker tab**: Renders with existing ATS history data (90-day TTL)
- **Matchup Predictor modal**: Opens without crash (but can't run predictions without team data)
- **Backtest tab**: Form renders (but can't run backtests without game data)
- **Python backend functions**: `calibrate_total()`, `_compute_metrics()`, `_compute_total_calibration_recommendations()` via Python shell with mock data

## What Requires Real KenPom API Key

- Recap tab with actual game data and predictions
- Running backtests (needs fanmatch + ratings data)
- Matchup Predictor predictions (needs team ratings)
- ATS Tracker with fresh O/U data (needs new Recap saves that include O/U tracking)

## Testing Tips

- The `browser_console` tool may not work reliably with this app. Use DevTools (F12) and type directly in the Console tab instead.
- When testing Python backend functions, create mock results with realistic data ranges (e.g., predicted_total 125-165, actual_total 125-165, vegas_ou 130-160).
- The app's `index.html` is very large (~5000+ lines) with deeply nested template literals. JS syntax errors from template literal changes are a key risk — always verify page loads without SyntaxError after making changes.
- Conference/ratings data loads on startup; 401 errors are expected and non-blocking with a dummy API key.
- To test the total calibration API: `curl -s http://localhost:5001/api/total-calibration` (GET) and `curl -s -X POST http://localhost:5001/api/total-calibration -H 'Content-Type: application/json' -d '{"center": 138, "compression": 0.85}'` (POST).
- Bounds validation: center is clamped to [120, 160], compression to [0.70, 1.0].
