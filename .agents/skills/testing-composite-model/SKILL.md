# Testing the Composite Model & Recap Tab

## Environment Setup

1. **Start Flask server:**
   ```bash
   cd /home/ubuntu/repos/NCAAProjectCH
   export KENPOM_API_KEY=dummy  # Use real key if available
   python3 app.py &
   # App runs on localhost:5001
   ```

2. **Install dependencies if needed:**
   ```bash
   pip install flask flask-cors python-dotenv requests beautifulsoup4 lxml anthropic numpy
   ```

3. **Cache Management:**
   - SQLite cache at `.cache.db` uses hashed keys (MD5), not human-readable
   - Cache has TTL; refresh timestamps before testing stale data:
     ```python
     import sqlite3, time
     conn = sqlite3.connect('.cache.db')
     conn.execute('UPDATE cache SET ts = ?', (time.time(),))
     conn.commit()
     ```
   - Frontend prediction cache (`window._gamePredCache`) is in-memory only, populated when users click "Predict" on Games tab
   - Dates with cached fanmatch + archive data (as of March 2026): 2026-03-09

## Testing the Recap Tab

### Navigating to Recap Tab with a specific date
The date input doesn't respond well to direct typing. Use JavaScript injection via address bar:
```
javascript:void(document.querySelector('input[type="date"]').value='2026-03-09')
```
Then trigger the change event:
```
javascript:void(document.querySelector('input[type="date"]').dispatchEvent(new Event('change',{bubbles:true})))
```

### Verifying Weight Adjustments (Issue #3)
Add temporary `console.log` in the fresh-prediction branch (around line 3635-3650) to log:
- `hasRichData`, `avgSOS`, `avgGames` values
- Raw vs adjusted weights
- Look for `avgGames < 15` adjustment (common with cached data that has 0 wins/losses)

### Verifying Situational Dampening (Issue #4)
Add temporary `console.log` after the dampening block (around line 3655-3670) to log:
- `recapDate.getMonth()` (should match the selected date, not today)
- `isNeutral`, `isConfTourney`, `isEarlySeason` flags
- Pre/post dampening margins
- For March dates: conference tournament games (neutral + same conf) get x0.88
- For Nov-Jan dates: early season games get x0.95

### Verifying No Double-Dampening (Issue #4)
Inject a cached prediction via JavaScript:
```
javascript:void(window._gamePredCache['TeamA|TeamB']={ourMargin:-12.5,ourPredWinner:'TeamB',ourTotal:140})
```
Then reload Recap — the `[RECAP-CACHED]` log should show the exact injected margin with no dampening applied.

## Running Validation Scripts

### Referee Data (Issue #1)
```bash
python3 -c "
import json
with open('static/referee_data.json') as f:
    data = json.load(f)
refs = data.get('referees', {})
print(f'Total referees: {len(refs)}')
climates = [r['foulClimate'] for r in refs.values()]
print(f'Mean: {sum(climates)/len(climates):.3f}')
print(f'Range: [{min(climates):.3f}, {max(climates):.3f}]')
"
```
Expect: 452 refs, mean ~0.998, range [0.75, 1.30]

### Style Correlation Validation (Issue #2)
```bash
python3 validate_style_correlations.py --skip-espn --sims 100
```
Runs MC simulations and checks internal consistency. Use `--skip-espn` when no ESPN scraping is needed.

### Backtester Docstring (Issue #5)
```bash
python3 -c "import backtester; print(backtester.__doc__)"
```
Should describe historical mode and legacy mode without stale "KNOWN LIMITATION" phrasing.

## Known Limitations

- Without a real KenPom API key (`$KENPOM_API_KEY`), can only test with cached data
- The Games tab "Predict" button requires live KenPom data, so the full Games→Recap flow can't be tested without credentials
- `use_historical=True` in the backtester addresses lookahead bias for core ratings; supplemental data still uses current-season values
- The referee data `meta.gamesAnalyzed` field may be 0 if not populated during scrape; the actual ref data is still valid
- Referee field is `totalGames` (not `gamesOfficiated` as originally documented)
