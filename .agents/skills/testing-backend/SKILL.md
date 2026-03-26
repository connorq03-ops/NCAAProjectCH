# Testing NCAAProjectCH Backend

## Environment Requirements

### Devin Secrets Needed
- `KENPOM_API_KEY` — Required to start the Flask app (`python3 app.py`). Without it, app.py raises `ValueError` at startup (line ~159). If unavailable, test backend logic directly via Python imports.
- `ANTHROPIC_API_KEY` — Optional. Only needed for injury intelligence features. App starts without it.

## Running the Full App
```bash
pip install -r requirements.txt
export KENPOM_API_KEY=<key>
python3 app.py  # Starts on port 5001
```
Open http://localhost:5001 in a browser.

## Testing Backend Logic Without API Keys

When `KENPOM_API_KEY` is unavailable, you can still test core computation modules directly:

### MC Engine (`mc_engine.py`)
```python
from mc_engine import simulate_game
params = {
    "m_t1_fg2": 48.0, "m_t2_fg2": 48.0,
    "m_t1_fg3": 34.0, "m_t2_fg3": 34.0,
    "m_t1_to": 17.0, "m_t2_to": 17.0,
    "m_t1_or": 28.0, "m_t2_or": 28.0,
    "t1_3rate": 35, "t2_3rate": 35,
    "m_t1_ftr": 30, "m_t2_ftr": 30,
    "t1_ftp": 72, "t2_ftp": 72,
    "m_t1_steal_rate": 9, "m_t2_steal_rate": 9,
    "game_tempo_ctr": 67.5,
    "t1_preferred_tempo": 67.5, "t2_preferred_tempo": 67.5,
    "t1_tempo_control": 0.5, "t2_tempo_control": 0.5,
    "t1_favored": True,
    "kp_blend_ratio": 0.18,
    "kp_t1_exp_oe": 100, "kp_t2_exp_oe": 100,
    "stars1": None, "stars2": None,
    "t1_bench": 30, "t2_bench": 30,
    "t1_vol_mod": 1.0, "t2_vol_mod": 1.0,
    "t1_style_bias": 0, "t2_style_bias": 0,
    "t1_def_profile": {"perimeter": 0, "interior": 0, "overall": 0},
    "t2_def_profile": {"perimeter": 0, "interior": 0, "overall": 0},
    "t1_streakiness": 1.0, "t2_streakiness": 1.0,
    "total_adj": 0, "hca1": 0, "hca2": 0,
    "coach_edge": 0, "ft_clutch_edge": 0,
    "ref_foul_climate": 1.0,
}
result = simulate_game(params, num_sims=500)
# Check keys: t1_win_prob, t1_score, t2_score, margin, ot_rate, avg_ot_points, ref_stats
```

### Backtester (`backtester.py`)
The `Backtester._compute_metrics()` method can be tested with synthetic result dicts:
```python
from backtester import Backtester
bt = Backtester()
synthetic_result = {
    'team1': 'TeamA', 'team2': 'TeamB', 'actual_winner': 'TeamA',
    'pick_correct': True, 'spread_error': 3.0, 'predicted_spread': 5.0,
    'actual_spread': 2.0, 'home_conf': 'ACC',
    'kp_pick_correct': True, 'kp_spread_error': 4.0,
    'simple_correct': True, 'simple_spread_error': 5.0, 'signed_error': 1.0,
    'sub_model_margins': {'efficiency': 5, 'similar': 4, 'conrat': 3, 'mc': 6},
    'sub_model_totals': {'efficiency': 140, 'similar': 142, 'conrat': 138, 'mc': 145},
    'predicted_total': 145, 'actual_total': 140,
    'ou_result': 'hit', 'ou_edge': 3, 'ou_bet_side': 'over',
    'is_overtime': False, 'vegas_ou': 140, 'total_signed_error': 5,
    'context': {},
}
metrics = bt._compute_metrics([synthetic_result])
```

### Self-Test Endpoint
The backtester has a built-in self-test (`run_self_test()`) that uses synthetic team data and doesn't need API access. However, running it through the Flask endpoint (`/api/backtest/self-test`) requires the app to be started.

## Testing the Frontend (Backtest Results)

The backtest UI is in the "Backtest" tab of `static/index.html`.
- Navigate to the Backtest tab
- Set start/end dates (e.g., a recent week of games)
- Click "Run Backtest" — calls `GET /api/backtest?start=X&end=Y`
- Results render via `renderBacktestResults(data)` which displays O/U performance cards, edge buckets, calibration recommendations, etc.
- The "Regulation Only" OT-excluded metrics appear as a subsection within the O/U Performance card (cyan-colored to distinguish from all-games violet)

## Key Heuristics to Be Aware Of
- `is_overtime` flag uses `actual_total > 170` as an approximate heuristic. This might misclassify some very high-scoring regulation games or miss low-scoring OT games.
- ESPN score data doesn't currently expose an OT indicator field. If it ever does, prefer that over the 170-point threshold.
