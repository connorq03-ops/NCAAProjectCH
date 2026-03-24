# Testing the NCAA Basketball Prediction App

## Local Setup

1. Install dependencies:
   ```bash
   pip install flask requests
   ```

2. The app requires `KENPOM_API_KEY` to start. For testing without real KenPom data:
   ```bash
   echo 'KENPOM_API_KEY=dummy_key_for_testing' > .env
   source .env
   ```
   This allows the Flask server to start. KenPom API calls will return 401, but all custom API endpoints and UI structure will work.

3. Start the Flask server:
   ```bash
   python app.py
   ```
   Server runs on `http://localhost:5001`.

## What Can Be Tested Without Real KenPom Key

- **API endpoints**: All custom endpoints (`/api/model-weights`, `/api/kp-blend-ratio`, `/api/calibration`, `/api/conf-adjustments`, etc.) work fully.
- **Frontend UI structure**: The main page loads, tabs work, matchup predictor modal opens with all UI elements.
- **Console integration**: Dynamic weights and KP blend ratio loading can be verified via browser console logs (`[weights]`, `[kp-blend]` prefixed messages).
- **Validation logic**: POST endpoints enforce constraints (weight sum = 1.0, each >= 0.05, ratio clamped to [0, 0.30]).

## What Requires Real KenPom Key

- **Team data loading**: The ratings table, team search, and team selection in matchup predictor all require real KenPom data.
- **Full matchup predictions**: Selecting two teams and running a prediction requires real team stats.
- **Backtester**: Running backtests requires both KenPom data and ESPN score data.
- **Bracket simulator**: Requires real team data and matchup params.

## Key UI Paths

- **Matchup Predictor**: Click the orange "Matchup Predictor" button in the header bar. Modal opens with Team 1/Team 2 search fields, location selector (@ Team 1 / Neutral / @ Team 2), NCAA Tournament Mode toggle, and Predict button.
- **NCAA Tournament Toggle**: Checkbox inside the matchup predictor modal, labeled "NCAA Tournament Mode (applies 15% margin dampening)". Unchecked by default.
- **Tabs**: Ratings, Four Factors, Height/Exp, Scoring, Shooting, Conferences, Games, Recap, Archive, Picks, Backtest, ATS Tracker.

## API Testing via curl

```bash
# Get current dynamic weights
curl http://localhost:5001/api/model-weights

# Set custom weights
curl -X POST http://localhost:5001/api/model-weights \
  -H 'Content-Type: application/json' \
  -d '{"weights": {"efficiency": 0.15, "similar": 0.12, "conrat": 0.18, "mc": 0.55}, "source": "optimizer"}'

# Get KP blend ratio
curl http://localhost:5001/api/kp-blend-ratio

# Set custom ratio (clamped to [0, 0.30])
curl -X POST http://localhost:5001/api/kp-blend-ratio \
  -H 'Content-Type: application/json' \
  -d '{"ratio": 0.12, "source": "optimizer"}'
```

## Verifying Frontend Integration

After POSTing custom weights/ratio with a non-"default" source:
1. Reload the page
2. Open browser console (F12)
3. Look for:
   - `[weights] Loaded dynamic weights: {conrat: 0.15, efficiency: 0.2, mc: 0.5, similar: 0.15}`
   - `[kp-blend] Loaded KP blend ratio: 0.12`
4. Verify JS globals: type `DYNAMIC_WEIGHTS` and `KP_BLEND_RATIO` in the console

## Data Storage

- SQLite cache at `.cache.db` stores API responses, predictions, calibration, weights, and blend ratio.
- Injury cache at `.injury_cache/` stores scraped injury data.
