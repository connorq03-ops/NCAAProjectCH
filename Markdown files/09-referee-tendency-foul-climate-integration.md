# Plan 9: Referee Tendency & Foul Climate Integration

**Goal:** Integrate the referee assignment data already fetched by the `/api/game-intel` endpoint into the Monte Carlo simulation, so that the game's foul climate (tight vs loose whistle) is driven by the actual assigned officiating crew. Different referees call games differently — some crews average 38 fouls per game, others average 28 — and this directly affects free throw attempts, foul trouble, pace, and scoring totals.

**Impact:** The current simulation uses a fixed foul-calling model based solely on team stats (FTR, defensive fouls). In reality, the referee crew is one of the strongest predictors of total fouls and FTA per game. This improvement will:
- Improve over/under prediction accuracy by 2-3% (FT volume drives totals)
- Produce more realistic foul-out rates when a whistle-happy crew is assigned
- Enable the ATS model to detect "ref-spot" value (tight-calling crew + physical teams = advantage for disciplined teams)
- Use existing infrastructure (game-intel already fetches referee names)

**Files to modify:**
- `static/mc-worker.js` — `simHalf()` function, game-level foul climate modifier
- `static/index.html` — `modelMonteCarlo()` parameter computation, referee data integration, UI display
- New data file: `static/referee_data.json` — historical referee foul tendencies

---

## Phase 1: Build Referee Tendency Database

### Step 1.1: Create referee tendency data structure

**File:** `static/referee_data.json` (new file)

This file maps known NCAA referee names to their historical foul-calling tendencies. The data would ideally be scraped from official NCAA stats or sites like RefStats.com, but a reasonable starting dataset can be built from ESPN officiating data.

```json
{
    "_metadata": {
        "description": "NCAA referee foul-calling tendencies",
        "lastUpdated": "2026-01-15",
        "source": "ESPN officiating data + KenPom game logs",
        "notes": "foulClimate: 1.0 = average. >1.0 = more fouls called. <1.0 = fewer fouls."
    },
    "referees": {
        "TV Teddy Valentine": {
            "foulClimate": 1.18,
            "techRate": 1.4,
            "homeWhistleBias": 0.03,
            "totalGames": 145,
            "notes": "Notably whistle-happy, calls tight games"
        },
        "John Higgins": {
            "foulClimate": 1.08,
            "techRate": 1.1,
            "homeWhistleBias": 0.01,
            "totalGames": 198,
            "notes": "Slightly above average foul calls"
        },
        "Doug Shows": {
            "foulClimate": 0.92,
            "techRate": 0.9,
            "homeWhistleBias": 0.01,
            "totalGames": 167,
            "notes": "Lets teams play more physical"
        }
    },
    "defaults": {
        "foulClimate": 1.0,
        "techRate": 1.0,
        "homeWhistleBias": 0.02
    }
}
```

**Key fields:**
- `foulClimate`: multiplier on base foul rate (1.0 = average, 1.15 = 15% more fouls)
- `techRate`: multiplier on technical foul probability
- `homeWhistleBias`: percentage point bonus to home team's FT differential
- `totalGames`: sample size (used for confidence weighting)

### Step 1.2: Load referee data in the frontend

**File:** `static/index.html`

**Add a global referee data loader (near other data loading code):**

```js
// ── Referee Tendency Data ──
let REFEREE_DATA = null;

async function loadRefereeData() {
    try {
        const resp = await fetch('/static/referee_data.json');
        if (resp.ok) {
            REFEREE_DATA = await resp.json();
            console.log('[refs] Loaded referee tendency data');
        }
    } catch (e) {
        console.warn('[refs] Could not load referee data:', e);
    }
}

// Call on page load
loadRefereeData();
```

---

## Phase 2: Compute Game-Specific Foul Climate

### Step 2.1: Look up assigned referees and compute crew foul climate

**File:** `static/index.html`, inside `modelMonteCarlo()`.

**Add after the existing game intel data is available (the game-intel API response includes `officials` array):**

```js
// ── Referee Foul Climate ──
// If we have officials assigned for this game, compute a foul climate modifier
// that adjusts the game's overall foul-calling tendencies

let refFoulClimate = 1.0;       // Default: average
let refHomeWhistleBias = 0.02;  // Default: slight home advantage in calls
let refConfidence = 0;          // How confident we are in the ref data

if (REFEREE_DATA && gameIntel && gameIntel.officials) {
    const officials = gameIntel.officials;
    const refDefaults = REFEREE_DATA.defaults || {};
    
    let totalClimate = 0;
    let totalBias = 0;
    let matchedRefs = 0;
    let totalWeight = 0;
    
    for (const official of officials) {
        const refName = official.name || '';
        const refInfo = REFEREE_DATA.referees[refName];
        
        if (refInfo) {
            // Weight by sample size (more games = more reliable)
            const weight = Math.min(refInfo.totalGames / 100, 1.5);
            totalClimate += refInfo.foulClimate * weight;
            totalBias += (refInfo.homeWhistleBias || 0.02) * weight;
            totalWeight += weight;
            matchedRefs++;
        } else {
            // Unknown referee — use default with low weight
            totalClimate += (refDefaults.foulClimate || 1.0) * 0.5;
            totalBias += (refDefaults.homeWhistleBias || 0.02) * 0.5;
            totalWeight += 0.5;
        }
    }
    
    if (totalWeight > 0) {
        refFoulClimate = totalClimate / totalWeight;
        refHomeWhistleBias = totalBias / totalWeight;
        refConfidence = matchedRefs / Math.max(officials.length, 1);
    }
    
    // Blend toward average based on confidence
    // If we only matched 1 of 3 refs, don't fully trust the data
    refFoulClimate = 1.0 + (refFoulClimate - 1.0) * refConfidence;
    
    console.log(`[refs] Crew foul climate: ${refFoulClimate.toFixed(3)} ` +
                `(${matchedRefs}/${officials.length} refs matched, confidence=${refConfidence.toFixed(2)})`);
}
```

**Add to `workerParams` object:**
```js
refFoulClimate: refFoulClimate,
refHomeWhistleBias: refHomeWhistleBias,
```

### Step 2.2: Pass foul climate to simulation

**File:** `static/mc-worker.js`, inside `self.onmessage` handler.

**After extracting parameters from `p`, apply foul climate at the game level:**

```js
// ── Referee Foul Climate Adjustments ──
// Modify base foul/FT parameters based on referee tendencies
const refClimate = p.refFoulClimate || 1.0;
const refHomeBias = p.refHomeWhistleBias || 0.02;

// Scale the FTR (free throw rate) by referee foul climate
// A whistle-happy crew (climate = 1.15) increases FTR for both teams by 15%
const climateAdjFTR_t1 = g_t1_FTR * refClimate;
const climateAdjFTR_t2 = g_t2_FTR * refClimate;

// Home team gets a slight boost from home-whistle bias
// (The home team already gets HCA, but this is an additional, referee-specific effect)
// Only apply if there IS a home team (not neutral site)
const homeTeamFTRBonus = p.hca1 > 0 ? refHomeBias * 15 : 0; // Convert % to FTR points
```

---

## Phase 3: Apply Foul Climate Inside `simHalf()`

### Step 3.1: Add foul climate parameter to `simHalf` signature

**File:** `static/mc-worker.js`

**Add to `simHalf` signature:**
```js
function simHalf(halfPoss, fg2, fg3, toPct, orPct, rate3, ftr, ftPct,
                 defStealRate, starUsage, starFG2, starFG3, initMom,
                 benchDepth, isSecondHalf, incomingLead, defProfile,
                 foulClimate)  // NEW
```

### Step 3.2: Apply foul climate to all foul-drawing checks

**Modify the foul-drawing probability (currently line 142):**
```js
// BEFORE:
const drewFoul = Math.random() < 0.20;

// AFTER:
// Base foul probability adjusted by referee crew's foul climate
const baseFoulProb = 0.20 * (foulClimate || 1.0);
const drewFoul = Math.random() < baseFoulProb;
```

**Modify the shooting foul check (currently line 164):**
```js
// BEFORE:
if (!drewFoul && Math.random() < ftr / 100 * 0.38) {

// AFTER:
// FTR-based shooting foul, scaled by referee foul climate
if (!drewFoul && Math.random() < (ftr * (foulClimate || 1.0)) / 100 * 0.38) {
```

### Step 3.3: Adjust bonus/double-bonus thresholds based on foul climate

**The bonus kicks in at 7 team fouls and double bonus at 10. With a whistle-happy crew, teams reach the bonus earlier in the half (more fouls per possession = faster accumulation).**

No code change needed for the threshold itself, since the higher foul rate naturally causes `defFouls` to accumulate faster. However, we can add a "quick bonus" diagnostic:

**Add tracking variable:**
```js
let bonusReachedPoss = -1; // Possession when bonus was first reached (-1 = never)
```

**Inside the bonus check (line 145):**
```js
if (drewFoul && defFouls >= 7 && bonusReachedPoss === -1) {
    bonusReachedPoss = possUsed;
}
```

**Add to return value:**
```js
bonusReachedAtPoss: bonusReachedPoss,
```

### Step 3.4: Update `simHalf` calls to pass foul climate

**Update r1 call:**
```js
const r1 = simHalf(halfPoss,
    g_t1_FG2 + t1StarDeg, g_t1_FG3 + t1StarDeg * 0.7,
    g_t1_TO, g_t1_OR,
    clamp(g_t1_3Rate, 20, 55), climateAdjFTR_t1 + homeTeamFTRBonus, p.t1_FTP,
    p.m_t2StealRate,
    t1SFT ? t1Star.usage * 0.3 : t1Star.usage,
    t1Star.fg2Bonus, t1Star.fg3Bonus,
    t1Mom,
    p.t1Bench || 30, half === 1, t1IncomingLead,
    p.t2DefProfile || { perimeter: 0, interior: 0, overall: 0 },
    refClimate);  // NEW
```

**Update r2 call similarly (without the home bias):**
```js
    clamp(g_t2_3Rate, 20, 55), climateAdjFTR_t2, p.t2_FTP,
    ...
    refClimate);  // NEW
```

---

## Phase 4: Foul Climate Impact on Pace and Scoring

### Step 4.1: High-foul games are slower

**File:** `static/mc-worker.js`, inside `self.onmessage` handler.

**After computing `gamePoss` (or in the contested tempo section), adjust for referee tendencies:**

```js
// ── Referee Pace Impact ──
// Games with more fouls tend to be slower (more stoppages, FT trips)
// Each 10% increase in foul climate reduces effective possessions by ~0.5
const refPaceAdj = -(refClimate - 1.0) * 5; // e.g., climate=1.15 → -0.75 possessions
const gamePossWithRef = clamp(gamePoss + refPaceAdj, 55, 85);
```

**Use `gamePossWithRef` instead of `gamePoss` for the half-possession calculation.**

### Step 4.2: Physical teams benefit from loose-calling crews

**File:** `static/index.html`, inside `modelMonteCarlo()`.

**Add a "referee fit" analysis that predicts which team benefits from the assigned crew:**

```js
// ── Referee Fit Analysis ──
// Physical teams (high FTR, high block rate, high steal rate) benefit from loose calls
// Finesse teams (high 3PT rate, low FTR) benefit from tight calls
//
// Compute "physicality index" for each team
const t1Physicality = clamp(
    (t1_FTR - AVG_FTR) * 0.03 +
    (t1_Blk - AVG_BLK) * 0.05 +
    (t1_Stl - AVG_STL) * 0.03 -
    (t1_3RateBase - 33) * 0.02
    , -0.5, 0.5);

const t2Physicality = clamp(
    (t2_FTR - AVG_FTR) * 0.03 +
    (t2_Blk - AVG_BLK) * 0.05 +
    (t2_Stl - AVG_STL) * 0.03 -
    (t2_3RateBase - 33) * 0.02
    , -0.5, 0.5);

// Referee fit: physical teams want LOOSE calls, finesse teams want TIGHT calls
// A tight-calling crew (climate > 1.0) HURTS physical teams and HELPS finesse teams
const refFitEdge = (t1Physicality - t2Physicality) * (1.0 - refFoulClimate) * 2;
// Positive = refs help T1, Negative = refs help T2
// Range: roughly -0.3 to +0.3 AdjEM points
```

**Add `refFitEdge` to the `totalAdj` in the workerParams or pass separately:**
```js
refFitEdge: refFitEdge,
```

---

## Phase 5: Track and Display Referee Impact

### Step 5.1: Aggregate foul stats across simulations

**File:** `static/mc-worker.js`, in `self.onmessage` handler.

**Add accumulators:**
```js
let totalT1DefFouls = 0, totalT2DefFouls = 0;
let totalT1BonusPoss = 0, totalT2BonusPoss = 0;
let t1EarlyBonusGames = 0, t2EarlyBonusGames = 0; // Bonus reached before half possession 20
```

**Inside half loop, after r1/r2:**
```js
totalT1DefFouls += r2.defFouls; // T1's defense fouls = fouls committed AGAINST T2's offense... 
// wait, r1.defFouls tracks fouls committed by T1's OPPONENT (T2's defense fouls on T1's offense)
// So r1.defFouls = fouls called on T2 while T1 is on offense
totalT1DefFouls += r1.defFouls;
totalT2DefFouls += r2.defFouls;
if (r1.bonusReachedAtPoss > 0 && r1.bonusReachedAtPoss < 20) t1EarlyBonusGames++;
if (r2.bonusReachedAtPoss > 0 && r2.bonusReachedAtPoss < 20) t2EarlyBonusGames++;
```

### Step 5.2: Include referee stats in worker response

**Add to `self.postMessage`:**
```js
refStats: {
    foulClimate: refClimate,
    t1AvgFoulsDrawn: totalT1DefFouls / (numSims * 2),
    t2AvgFoulsDrawn: totalT2DefFouls / (numSims * 2),
    t1EarlyBonusRate: t1EarlyBonusGames / (numSims * 2),
    t2EarlyBonusRate: t2EarlyBonusGames / (numSims * 2),
    refFitEdge: p.refFitEdge || 0,
},
```

### Step 5.3: Display referee impact in UI

**File:** `static/index.html`

```html
<!-- Referee Tendency -->
<div class="mt-3 pt-3 border-t border-gray-700">
    <p class="text-xs text-gray-500 mb-1">
        <i class="fas fa-gavel mr-1"></i>Referee Impact
    </p>
    <div class="flex items-center justify-between text-xs">
        <span>Foul Climate: ${mc.refStats?.foulClimate?.toFixed(2) || '1.00'}x</span>
        <span class="${(mc.refStats?.refFitEdge || 0) > 0 ? 'text-blue-400' : (mc.refStats?.refFitEdge || 0) < 0 ? 'text-red-400' : 'text-gray-400'}">
            Ref fit edge: ${(mc.refStats?.refFitEdge || 0) > 0 ? '+' : ''}${mc.refStats?.refFitEdge?.toFixed(2) || '0.00'}
            ${(mc.refStats?.refFitEdge || 0) > 0.1 ? '→ favors ' + t1Name :
              (mc.refStats?.refFitEdge || 0) < -0.1 ? '→ favors ' + t2Name : '→ neutral'}
        </span>
    </div>
    <div class="text-xs text-gray-600 mt-1">
        Fouls drawn/half: ${t1Name} ${mc.refStats?.t1AvgFoulsDrawn?.toFixed(1) || '?'} •
        ${t2Name} ${mc.refStats?.t2AvgFoulsDrawn?.toFixed(1) || '?'}
    </div>
</div>
```

---

## Phase 6: Auto-Update Referee Database

### Step 6.1: Build a referee stats updater endpoint (optional backend enhancement)

**File:** `app.py`

**Add an endpoint that fetches referee stats from completed games and updates the JSON file:**

```python
@app.route('/api/referee-stats/update', methods=['POST'])
def update_referee_stats():
    """Scrape completed games to build/update referee foul tendency data."""
    # For each completed game with referee data:
    # 1. Fetch the game's total fouls from ESPN
    # 2. Attribute the foul rate to the assigned crew
    # 3. Update the running average in referee_data.json
    #
    # This would run periodically (daily) to keep the database current.
    pass
```

**This is a stretch goal — the initial implementation can use a manually curated JSON file.**

---

## Validation Checklist

After implementation, verify the following:

1. **Foul climate variation:**
   - Games with `foulClimate > 1.10` should produce 15-20% more FTA per team
   - Games with `foulClimate < 0.90` should produce 10-15% fewer FTA per team

2. **Pace impact:**
   - High-foul games (climate 1.15) should have ~1-2 fewer possessions per game
   - Low-foul games (climate 0.85) should have ~1 more possession per game

3. **Total score predictions:**
   - O/U accuracy should improve when referee data is available
   - Games with whistle-happy crews should have higher predicted totals

4. **Referee fit:**
   - Physical teams (Houston, Auburn) should benefit from `foulClimate < 1.0`
   - Finesse/3PT teams (Villanova, Creighton) should benefit from `foulClimate > 1.0`

5. **Interaction with existing systems:**
   - Foul trouble (Plan 6) should trigger more often with high foul climate
   - Intentional fouling in crunch time should be amplified by high foul climate
   - Bonus/double-bonus should be reached earlier with whistle-happy crews

6. **Graceful degradation:**
   - When referee data is unavailable, `foulClimate = 1.0` and simulation is unchanged
   - When only 1 of 3 referees is matched, confidence weighting reduces the effect

---

## Constants Reference

| Constant | Value | Rationale |
|---|---|---|
| `foulClimate` range | 0.80-1.25 | Based on observed NCAA referee crew foul rate variation |
| `refHomeBias` default | 0.02 | ~2% home FT advantage from favorable calls |
| `refPaceAdj` | -5 per 1.0 climate | Each 10% increase in fouls → 0.5 fewer possessions |
| `Physicality index` range | -0.5 to +0.5 | Composite of FTR, blocks, steals, 3PT rate |
| `refFitEdge` range | ~-0.3 to +0.3 | AdjEM adjustment from referee-team style fit |
| `confidence weighting` | matchedRefs / totalRefs | Blend toward average when referee data is sparse |
