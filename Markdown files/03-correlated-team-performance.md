# Plan 3: Correlated Team Performance (Game Style Factors)

**Goal:** Replace the current independent random draws for FG2%, FG3%, TO%, and OR% with a correlated "game style" system where a team's shot profile shifts coherently within each game — e.g., a drive-heavy game increases FG2%, FTR, and OR% together while reducing 3PT rate.

**Impact:** Produces more realistic box score distributions. Currently the sim can generate impossible stat lines (high 3PT rate + high OR%) because each stat is drawn independently. Correlated performance also affects spread distributions — teams with volatile styles will show wider outcome ranges.

**Files to modify:**
- `static/mc-worker.js` — game-level random draws and `simHalf()` inputs
- `static/index.html` — `modelMonteCarlo()` parameter computation (lines ~1177-1328)

---

## Phase 1: Define Game Style Archetypes

### Step 1.1: Create a style factor generator function

**File:** `static/mc-worker.js`

**Add a new function after `clamp()` (line 11), before `calcStarImpact()`:**

```js
/**
 * Generate correlated game-style factors for a single team in a single game.
 * Returns an object with correlated adjustments to each stat category.
 *
 * The key insight: basketball stats are NOT independent.
 * - "Drive-heavy" games: higher FG2%, higher FTR, higher OR%, lower 3PT rate
 * - "Perimeter" games: higher 3PT rate, higher 3PT%, lower OR%, lower FTR
 * - "Sloppy" games: higher TO%, lower FG% across the board, higher OR% (more misses)
 * - "Disciplined" games: lower TO%, slightly higher FG%, lower OR% (fewer misses to rebound)
 *
 * Two independent axes:
 *   1. Interior vs Perimeter (shot selection)
 *   2. Disciplined vs Sloppy (ball control / pace)
 *
 * @param {number} baseVolatility - team's volatility modifier (from bench depth)
 * @returns {Object} correlated adjustments: { fg2Adj, fg3Adj, toAdj, orAdj, rateAdj3, ftrAdj }
 */
function generateGameStyle(baseVolatility) {
    // Axis 1: Interior ←→ Perimeter
    // Positive = interior/drive-heavy, Negative = perimeter/3PT-heavy
    const interiorAxis = randNormal(0, 1.0) * baseVolatility;

    // Axis 2: Disciplined ←→ Sloppy
    // Positive = sloppy (more TOs, more chaos), Negative = disciplined
    const disciplineAxis = randNormal(0, 1.0) * baseVolatility;

    // Axis 3: Small independent residual for each stat (uncorrelated noise)
    const residualSD = 0.8;

    return {
        // FG2% adjustment:
        //   Interior games → better 2PT% (driving, post-ups)
        //   Disciplined games → slightly better FG% (better shot selection)
        fg2Adj: interiorAxis * 1.8 - disciplineAxis * 0.5 + randNormal(0, residualSD),

        // FG3% adjustment:
        //   Perimeter games → better 3PT% (in rhythm, open looks from spacing)
        //   Sloppy games → slightly worse 3PT% (rushed, out of rhythm)
        fg3Adj: -interiorAxis * 1.2 - disciplineAxis * 0.4 + randNormal(0, residualSD),

        // TO% adjustment:
        //   Interior games → slightly more TOs (driving into traffic)
        //   Sloppy games → more TOs
        toAdj: interiorAxis * 0.3 + disciplineAxis * 1.5 + randNormal(0, residualSD * 0.5),

        // OR% adjustment:
        //   Interior games → more OR% (bodies near the basket, more putbacks)
        //   Sloppy games → more OR% (more missed shots = more rebound opportunities)
        //   Perimeter games → fewer OR% (shooters don't crash boards)
        orAdj: interiorAxis * 1.0 + disciplineAxis * 0.6 + randNormal(0, residualSD),

        // 3PT rate adjustment:
        //   Interior games → fewer 3s (driving instead)
        //   Perimeter games → more 3s
        rate3Adj: -interiorAxis * 2.5 + randNormal(0, residualSD * 0.5),

        // FTR adjustment:
        //   Interior games → more FTs (driving, contact)
        //   Perimeter games → fewer FTs (jump shots don't draw fouls)
        ftrAdj: interiorAxis * 1.5 + randNormal(0, residualSD * 0.3),

        // Diagnostic: which style dominated this game
        styleLabel: interiorAxis > 0.5 ? 'interior' : interiorAxis < -0.5 ? 'perimeter' : 'balanced',
        disciplineLabel: disciplineAxis > 0.5 ? 'sloppy' : disciplineAxis < -0.5 ? 'disciplined' : 'neutral',
    };
}
```

---

## Phase 2: Replace Independent Draws with Correlated Style

### Step 2.1: Replace the existing per-game random swing generation

**File:** `static/mc-worker.js`, inside `self.onmessage` handler.

**Current code (lines 144-159):**
```js
const SHOT_SWING_SD = 3.2;

for (let i = 0; i < p.numSims; i++) {
    const gameFactor = randNormal(0, 1.2);
    const gamePoss = clamp(randNormal(p.gameTempoCtr, 3.0), 55, 85);
    const t1Swing = randNormal(0, SHOT_SWING_SD) * p.t1VolMod;
    const t2Swing = randNormal(0, SHOT_SWING_SD) * p.t2VolMod;

    const g_t1_FG2 = clamp(p.m_t1_FG2 + t1Swing * 0.7 + gameFactor * 0.25, 28, 68);
    const g_t2_FG2 = clamp(p.m_t2_FG2 + t2Swing * 0.7 + gameFactor * 0.25, 28, 68);
    const g_t1_FG3 = clamp(p.m_t1_FG3 + t1Swing * 1.0 + gameFactor * 0.15, 18, 48);
    const g_t2_FG3 = clamp(p.m_t2_FG3 + t2Swing * 1.0 + gameFactor * 0.15, 18, 48);
    const g_t1_TO  = clamp(p.m_t1_TO + randNormal(0, 1.8), 6, 30);
    const g_t2_TO  = clamp(p.m_t2_TO + randNormal(0, 1.8), 6, 30);
    const g_t1_OR  = clamp(p.m_t1_OR + p.t1HgtORBonus + randNormal(0, 2.5), 12, 45);
    const g_t2_OR  = clamp(p.m_t2_OR - p.t1HgtORBonus + randNormal(0, 2.5), 12, 45);
```

**Replace with:**
```js
const SHOT_SWING_SD = 3.2;

for (let i = 0; i < p.numSims; i++) {
    // ── Shared game environment factor ──
    // Both teams play in the same arena/conditions — correlated slightly
    const gameFactor = randNormal(0, 1.2);
    const gamePoss = clamp(randNormal(p.gameTempoCtr, 3.0), 55, 85);

    // ── Correlated game-style factors per team ──
    const t1Style = generateGameStyle(p.t1VolMod);
    const t2Style = generateGameStyle(p.t2VolMod);

    // Apply correlated style adjustments instead of independent swings
    const g_t1_FG2 = clamp(p.m_t1_FG2 + t1Style.fg2Adj + gameFactor * 0.25, 28, 68);
    const g_t2_FG2 = clamp(p.m_t2_FG2 + t2Style.fg2Adj + gameFactor * 0.25, 28, 68);
    const g_t1_FG3 = clamp(p.m_t1_FG3 + t1Style.fg3Adj + gameFactor * 0.15, 18, 48);
    const g_t2_FG3 = clamp(p.m_t2_FG3 + t2Style.fg3Adj + gameFactor * 0.15, 18, 48);
    const g_t1_TO  = clamp(p.m_t1_TO + t1Style.toAdj, 6, 30);
    const g_t2_TO  = clamp(p.m_t2_TO + t2Style.toAdj, 6, 30);
    const g_t1_OR  = clamp(p.m_t1_OR + p.t1HgtORBonus + t1Style.orAdj, 12, 45);
    const g_t2_OR  = clamp(p.m_t2_OR - p.t1HgtORBonus + t2Style.orAdj, 12, 45);
```

### Step 2.2: Apply 3PT rate and FTR style adjustments

**The `t1_3Rate` and `t2_3Rate` are currently fixed per game. Add style adjustments.**

**Inside the simulation loop (after the `g_t2_OR` line), add:**
```js
    // Apply style-correlated 3PT rate adjustment
    const g_t1_3Rate = clamp(p.t1_3Rate + t1Style.rate3Adj, 20, 55);
    const g_t2_3Rate = clamp(p.t2_3Rate + t2Style.rate3Adj, 20, 55);

    // Apply style-correlated FTR adjustment
    const g_t1_FTR = clamp(p.m_t1_FTR + t1Style.ftrAdj, 15, 50);
    const g_t2_FTR = clamp(p.m_t2_FTR + t2Style.ftrAdj, 15, 50);
```

### Step 2.3: Update `simHalf` calls to use per-game adjusted rates

**Currently (lines 192-208), `p.t1_3Rate` and `p.m_t1_FTR` are used directly. Replace with per-game values:**

**Team 1 call:**
```js
const r1 = simHalf(halfPoss,
    g_t1_FG2 + t1_FG2Adj + t1StarDeg, g_t1_FG3 + t1StarDeg * 0.7,
    g_t1_TO + t1_TOAdj, g_t1_OR,
    clamp(g_t1_3Rate + t1_3Adj, 20, 55), g_t1_FTR, p.t1_FTP,   // g_t1_3Rate and g_t1_FTR instead of p.t1_3Rate and p.m_t1_FTR
    p.m_t2StealRate,
    t1SFT ? t1Star.usage * 0.3 : t1Star.usage,
    t1Star.fg2Bonus, t1Star.fg3Bonus,
    t1Mom);
```

**Team 2 call:**
```js
const r2 = simHalf(halfPoss,
    g_t2_FG2 + t2_FG2Adj + t2StarDeg, g_t2_FG3 + t2StarDeg * 0.7,
    g_t2_TO + t2_TOAdj, g_t2_OR,
    clamp(g_t2_3Rate + t2_3Adj, 20, 55), g_t2_FTR, p.t2_FTP,   // g_t2_3Rate and g_t2_FTR instead of p.t2_3Rate and p.m_t2_FTR
    p.m_t1StealRate,
    t2SFT ? t2Star.usage * 0.3 : t2Star.usage,
    t2Star.fg2Bonus, t2Star.fg3Bonus,
    t2Mom);
```

---

## Phase 3: Track Style Distribution Diagnostics

### Step 3.1: Add style tracking accumulators

**File:** `static/mc-worker.js`, in `self.onmessage` handler.

**Add near line 142:**
```js
let t1InteriorGames = 0, t1PerimeterGames = 0, t1BalancedGames = 0;
let t2InteriorGames = 0, t2PerimeterGames = 0, t2BalancedGames = 0;
let t1SloppyGames = 0, t1DisciplinedGames = 0;
let t2SloppyGames = 0, t2DisciplinedGames = 0;
```

**Inside the simulation loop, after generating styles:**
```js
// Track style distributions
if (t1Style.styleLabel === 'interior') t1InteriorGames++;
else if (t1Style.styleLabel === 'perimeter') t1PerimeterGames++;
else t1BalancedGames++;

if (t2Style.styleLabel === 'interior') t2InteriorGames++;
else if (t2Style.styleLabel === 'perimeter') t2PerimeterGames++;
else t2BalancedGames++;

if (t1Style.disciplineLabel === 'sloppy') t1SloppyGames++;
else if (t1Style.disciplineLabel === 'disciplined') t1DisciplinedGames++;

if (t2Style.disciplineLabel === 'sloppy') t2SloppyGames++;
else if (t2Style.disciplineLabel === 'disciplined') t2DisciplinedGames++;
```

### Step 3.2: Include style stats in worker response

**Add to `self.postMessage` (near line 273):**
```js
t1StyleDist: {
    interior: t1InteriorGames / numSims,
    perimeter: t1PerimeterGames / numSims,
    balanced: t1BalancedGames / numSims,
    sloppy: t1SloppyGames / numSims,
    disciplined: t1DisciplinedGames / numSims,
},
t2StyleDist: {
    interior: t2InteriorGames / numSims,
    perimeter: t2PerimeterGames / numSims,
    balanced: t2BalancedGames / numSims,
    sloppy: t2SloppyGames / numSims,
    disciplined: t2DisciplinedGames / numSims,
},
```

---

## Phase 4: Team-Specific Style Tendencies (Optional Enhancement)

### Step 4.1: Add style tendency parameters

Some teams have strong style identities — Houston drives to the rim, Gonzaga shoots 3s. We can bias the style generation toward a team's natural tendencies.

**File:** `static/index.html`, inside `modelMonteCarlo()`.

**Add after the existing stat computation (around line 1200):**

```js
// ── Style Tendency ──
// Compute each team's natural style tendency from their actual stats
// Teams that shoot lots of 3s have a perimeter tendency; teams with high FTR drive more
const t1StyleBias = (t1_3RateBase - AVG_3RATE) * -0.04 + (t1_FTR - AVG_FTR) * 0.03;
const t2StyleBias = (t2_3RateBase - AVG_3RATE) * -0.04 + (t2_FTR - AVG_FTR) * 0.03;
// Positive = interior tendency, Negative = perimeter tendency
// Range: roughly -0.5 to +0.5
```

**Add to `workerParams`:**
```js
t1StyleBias: t1StyleBias,
t2StyleBias: t2StyleBias,
```

### Step 4.2: Incorporate style bias into `generateGameStyle`

**Update the function signature and interior axis:**
```js
function generateGameStyle(baseVolatility, styleBias) {
    // styleBias shifts the mean of the interior axis
    // A team that naturally drives (positive bias) will have more interior games
    const interiorAxis = randNormal(styleBias || 0, 1.0) * baseVolatility;
    // ... rest unchanged ...
}
```

**Update calls in the simulation loop:**
```js
const t1Style = generateGameStyle(p.t1VolMod, p.t1StyleBias || 0);
const t2Style = generateGameStyle(p.t2VolMod, p.t2StyleBias || 0);
```

---

## Phase 5: Correlation Between Opponents

### Step 5.1: Add cross-team correlation

In real basketball, one team's style affects the other's. If Team A plays interior-heavy (lots of drives), Team B may get more transition opportunities and shoot more 3s in the open court.

**Add after generating both team styles:**

```js
// ── Opponent Reaction ──
// If one team drives heavily, opponent gets some fast break / open 3 opportunities
const reactionStrength = 0.15; // How much one team's style affects the other
g_t2_FG3 += t1Style.fg2Adj > 2 ? reactionStrength * 1.5 : 0; // Good interior game → opponent open 3s
g_t1_FG3 += t2Style.fg2Adj > 2 ? reactionStrength * 1.5 : 0;

// Sloppy play is somewhat contagious (chaotic games affect both teams)
const chaosFactor = (t1Style.toAdj + t2Style.toAdj) * 0.08;
g_t1_TO = clamp(g_t1_TO + chaosFactor, 6, 30);
g_t2_TO = clamp(g_t2_TO + chaosFactor, 6, 30);
```

---

## Validation Checklist

After implementation, verify the following:

1. **Stat correlation check:**
   - Run 5000 sims and log each game's FG2%, FG3%, 3PT rate, TO%, OR%, FTR
   - Compute Pearson correlation between FG2% and FTR → should be positive (r > 0.15)
   - Compute correlation between 3PT rate and OR% → should be negative (r < -0.10)
   - Compute correlation between FG2% and 3PT rate → should be negative (r < -0.15)
   - These correlations should match observed real-game patterns

2. **Box score realism:**
   - Sample 20 individual game outputs from the sim
   - Verify no impossible stat lines (e.g., 45% 3PT rate + 40% OR% should be extremely rare)
   - Compare stat distributions against actual NCAA box scores from this season

3. **Score distribution width:**
   - The standard deviation of final scores should be similar before and after
   - If anything, it should increase slightly (correlated swings produce more extreme outcomes)
   - But the MEAN score should remain unchanged (style shifts are zero-mean)

4. **Style distribution check:**
   - For a neutral matchup, `t1StyleDist` should show roughly equal interior/perimeter/balanced splits
   - For Houston (high FTR), `t1StyleDist.interior` should be noticeably higher than `perimeter`
   - For a 3PT-heavy team, `perimeter` should be highest

5. **Regression test:** Run identical matchups before/after. Average scores should change by < 1 point. Win% should change by < 2%.

---

## Constants Reference

| Constant | Value | Rationale |
|---|---|---|
| Interior axis SD | 1.0 | Produces meaningful but not extreme style variation |
| Discipline axis SD | 1.0 | Same |
| Residual SD | 0.8 | Independent noise is slightly smaller than structured variation |
| `fg2Adj` interior coefficient | 1.8 | Interior games boost 2PT% by ~2% on average |
| `fg3Adj` interior coefficient | -1.2 | Interior games reduce 3PT% by ~1.2% |
| `toAdj` discipline coefficient | 1.5 | Sloppy games increase TO% by ~1.5% |
| `orAdj` interior coefficient | 1.0 | Interior games boost OR% by ~1% |
| `rate3Adj` interior coefficient | -2.5 | Interior games reduce 3PT rate by ~2.5% |
| `ftrAdj` interior coefficient | 1.5 | Interior games boost FTR by ~1.5 |
| `reactionStrength` | 0.15 | Opponent reaction is subtle, not dominant |
| `chaosFactor` | 0.08 | Sloppy games are mildly contagious |
| `styleBias` range | -0.5 to +0.5 | Based on 3PT rate and FTR deviation from average |
