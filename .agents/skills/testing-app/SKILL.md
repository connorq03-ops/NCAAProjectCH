# Testing NCAAProjectCH App

## Environment Setup

1. Start Flask server:
   ```bash
   cd /home/ubuntu/repos/NCAAProjectCH
   KENPOM_API_KEY=<your_key> python3 app.py
   ```
   Server runs on `http://localhost:5001`.

2. The app requires `KENPOM_API_KEY` env var to start. A dummy value (`dummy`) works for testing features that don't need live KenPom data, but a real key is needed for full end-to-end testing.

3. `ANTHROPIC_API_KEY` is optional — without it, injury features are disabled (non-blocking warning).

## Devin Secrets Needed

- `KENPOM_API_KEY` — Required for full end-to-end testing with live data (ratings, fanmatch, game scores, odds). Without it, only cached data and non-data-dependent features can be tested.

## Cache Behavior

- Cache DB: `.cache.db` (SQLite)
- **KenPom API data** (ratings, fanmatch, odds, scores): **3600s TTL (1 hour)**. Expires quickly.
- **ATS history** data: **7776000s TTL (90 days)**. Persists much longer.
- **Calibration data** (spread and total calibration): **86400 * 365 TTL (1 year)**. Very long-lived.
- Cache keys are MD5 hashes of API call parameters.

## Testing Approach

### With Real KenPom API Key (Full Testing)

1. **Recap tab**: Navigate to a recent date with completed games (e.g., a Saturday during March Madness). Verify:
   - O/U summary cards render (All Bets O/U %, HC O/U %, Total Error)
   - Game table shows Total column (pred/vegas) and O/U column (hit/miss icons)
   - ATS summary cards still work alongside O/U cards
   - Data auto-saves to ATS history

2. **ATS Tracker tab**: After viewing Recap for a date with O/U data:
   - O/U summary cards appear (All Bets O/U, HC O/U, O/U P/L, O/U HC P/L)
   - O/U Hit Rate by Edge Bucket section populates
   - Daily breakdown shows Day O/U% and Cum O/U% for dates with O/U data
   - Game log shows Total and O/U columns

3. **Matchup Predictor**: Select two real teams and run prediction:
   - "Predicted Total" card appears in Key Stats Grid
   - Shows numeric total (e.g., 144 for Duke vs Michigan)

4. **Backtest tab**: Note that the Python backtester fetches game data from fanmatch/scores endpoints, which may NOT include Vegas O/U lines. O/U lines come from the ESPN odds endpoint used by the frontend Recap tab. So backtest O/U metrics may show `None` for vegas_ou even with a real API key. The O/U comparison logic works best through the Recap tab flow.

5. **API endpoints**: Test via curl:
   - `curl http://localhost:5001/api/total-calibration` (GET defaults)
   - `curl -X POST http://localhost:5001/api/total-calibration -H 'Content-Type: application/json' -d '{"center": 138, "compression": 0.85}'` (POST custom values)
   - Bounds validation: center clamped to [120, 160], compression to [0.70, 1.0]

### Without Real API Key (Limited Testing)

- Page load: Verify no JS syntax errors
- API endpoints: /api/total-calibration GET/POST
- JS functions: `calibrateTotal()` via DevTools Console
- Tab navigation: All tabs switch without crashes
- ATS Tracker: Renders with existing history (O/U columns show `-` for pre-O/U data)
- Python backend: `calibrate_total()`, `_compute_metrics()` with mock data

## Testing Tips

- The `browser_console` tool may not work reliably. Use DevTools (F12) Console tab directly.
- HTML date inputs can be tricky to fill. If typing dates doesn't work, try:
  - Using the calendar picker dropdown
  - Setting values via DevTools Console: `document.querySelector('input[type=date]').value = '2026-03-22'`
  - Note: element IDs may differ from expected — inspect the DOM first
- The app's `index.html` is very large (~5000+ lines) with deeply nested template literals. JS syntax errors from template literal changes are a key risk.
- Conference/ratings data loads on startup; 401/404 errors are expected and non-blocking with a dummy API key.
- The Recap tab auto-saves ATS results (including O/U data) when it loads game data. This means visiting the Recap tab for a date populates the ATS Tracker with that date's data.
- For the ATS Tracker to show O/U data, you need to first visit the Recap tab for dates that have O/U lines available. Pre-existing ATS history from before O/U feature was added will show `-` for O/U columns.
- The backtester runs server-side but is triggered client-side. There is no direct POST /api/backtest endpoint — the backtest runs through a different mechanism.
- "Backtest Saved Picks" requires saved predictions (from the Picks tab), not just ATS history.
