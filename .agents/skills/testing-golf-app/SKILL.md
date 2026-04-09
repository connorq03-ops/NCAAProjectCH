# Testing the Golf Flask App

## Devin Secrets Needed
- `DATAGOLF_API_KEY` — repo-scoped secret for `connorq03-ops/NCAAProjectCH`. Sourced from `/run/repo_secrets/connorq03-ops/NCAAProjectCH/.env.secrets`.

## Prerequisites
1. Source the API key:
   ```bash
   source /run/repo_secrets/connorq03-ops/NCAAProjectCH/.env.secrets
   ```
2. Working directory: `/home/ubuntu/repos/NCAAProjectCH`

## Integration Test Suite (Shell)
Runs 63 assertions against the live DataGolf API covering rankings, skill ratings, prefetch pipeline, sim params, predictions, tournament simulation, course fit, and matchup odds.

```bash
cd /home/ubuntu/repos/NCAAProjectCH
python -m golf.test_real_api
```

**Pass criteria**: Output ends with `RESULTS: 63 passed, 0 failed, 0 skipped`

## Flask Server
Start the Flask app on port 5002:
```bash
DATAGOLF_API_KEY="$DATAGOLF_API_KEY" python -m golf.golf_app
```

The server logs `[golf-app] ANTHROPIC_API_KEY not set — WD features disabled` which is expected — WD (weather data?) features are optional.

## API Endpoints to Test (Browser)

| Endpoint | What to verify |
|----------|---------------|
| `http://localhost:5002/api/golf/rankings` | JSON list of 500+ players with `player_name`, `dg_skill_estimate`, `owgr_rank` |
| `http://localhost:5002/api/golf/skill-decompositions` | Players with `sg_ott`, `sg_app`, `sg_arg`, `sg_putt`, `driving_dist`, `driving_acc` (note: `driving_dist` not `driving_distance`) |
| `http://localhost:5002/api/golf/pre-tournament-preds` | Player predictions with American odds strings (e.g., "+878"), not raw floats. Uses `baseline` wrapper key. |
| `http://localhost:5002/api/golf/odds/matchups` | 100+ matchups with `p1_player_name`, `p2_player_name`, multi-bookmaker odds |
| `http://localhost:5002/golf` | Dashboard HTML loads with real player data |

## Dashboard Tabs to Test

1. **Rankings** — Real player rankings with SG:Total and OWGR. Note: SG:OTT/APP/ARG/Putt columns show 0.00 on this tab (by design — detailed splits are on Strokes Gained tab).
2. **Strokes Gained** — Visual bars with real SG split values for all players.
3. **Course Analysis** — Select a course (e.g., Augusta National). Shows course details, SG weights, and player fit scores.
4. **Simulator** — Select course and simulations count (default 1000). Takes ~40s. Shows Winner Board and Cut Danger.
5. **Matchup** — Enter two player names and click Compare.
6. **Form Tracker** — Loads 50 players. Metrics may show 0.0 if no historical data has been stored.
7. **Odds** — Loads player names from rankings.
8. **Predictions** — Shows prediction accuracy tracker.

## Known Pre-existing Issues (as of April 2026)

These issues exist in the dashboard UI and are NOT related to the API field mapping:

1. **Matchup Compare button** might error with `compute_golf_composite() missing required positional arguments`. The matchup API endpoint itself works fine — it's the dashboard's Compare button that calls `compute_golf_composite()` incorrectly.

2. **Odds tab** might show 0.0% for Implied Prob and DG Prob columns even though player names load correctly. The outright odds data might not be parsed into UI columns properly.

## Key Architecture Notes

- **Centralized field mapping**: `golf/api_field_map.py` is the single source of truth for all DataGolf API field names. If the API changes field names, only this file needs updating.
- **Wrapper keys**: Different endpoints use different wrapper keys (e.g., `baseline` for pre-tournament predictions). These are defined in `WRAPPER_KEYS` dict in `api_field_map.py`.
- **Import naming**: In `golf_app.py`, the `get_field` function from `api_field_map` is imported as `map_get_field` to avoid collision with the Flask route function `get_field()`.
- **American odds**: Pre-tournament prediction values are American odds strings (e.g., "+878"), converted to probabilities via `american_odds_to_probability()` in `api_field_map.py`.
- **Archive values**: Historical prediction archive values are percentages (0-100 scale), converted via `archive_value_to_probability()` which always divides by 100.
