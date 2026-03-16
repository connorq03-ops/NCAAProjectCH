# Plan 1: Fatigue & Substitution Modeling

**Goal:** Add realistic player fatigue degradation and bench substitution patterns to the Monte Carlo possession-level simulation so that star efficiency decays over time and bench depth meaningfully impacts scoring (not just volatility).

**Impact:** Blowout/close-game calibration, late-half scoring realism, better spread accuracy for shallow-bench teams.

**Files to modify:**
- `static/mc-worker.js` — core simulation engine
- `static/index.html` — parameter computation in `modelMonteCarlo()` (lines ~1177-1328)

---

## Phase 1: Add Fatigue State to `simHalf()`

### Step 1.1: Add fatigue parameters to `simHalf` function signature

**File:** `static/mc-worker.js`

**Current signature (line 27):**
```js
function simHalf(halfPoss, fg2, fg3, toPct, orPct, rate3, ftr, ftPct,
                 defStealRate, starUsage, starFG2, starFG3, initMom)
```

**New signature:**
```js
function simHalf(halfPoss, fg2, fg3, toPct, orPct, rate3, ftr, ftPct,
                 defStealRate, starUsage, starFG2, starFG3, initMom,
                 benchDepth, isSecondHalf)
```

- `benchDepth`: float 0-100, from `ht.Bench` (higher = deeper bench = less fatigue)
- `isSecondHalf`: boolean, fatigue effects are stronger in the second half

### Step 1.2: Compute per-possession fatigue factor inside `simHalf`

**Location:** Inside the `while (possLeft > 0 ...)` loop in `simHalf()`, at the top of each iteration (after `possLeft--; possUsed++;`).

**Add the following block immediately after line 38 (`possLeft--; possUsed++;`):**

```js
// ── Fatigue Curve ──
// possUsed goes from 1 to ~35 per half
// Fatigue starts kicking in after 60% of half possessions are used
// Deeper benches (higher benchDepth) delay fatigue onset and reduce magnitude
const fatigueOnsetPct = 0.55 + (benchDepth / 100) * 0.15; // range: 0.55 (no bench) to 0.70 (elite bench)
const fatigueProgress = Math.max(0, (possUsed / halfPoss) - fatigueOnsetPct) / (1 - fatigueOnsetPct);
// fatigueProgress: 0 early in half, ramps to 1.0 at end of half
// Second half fatigue is 40% stronger
const halfMultiplier = isSecondHalf ? 1.4 : 1.0;
const fatiguePenalty = fatigueProgress * halfMultiplier * 0.06; // max ~6% FG reduction in H1, ~8.4% in H2
// fatiguePenalty is applied as a multiplier reduction to shooting percentages
const fatigueFGMod = 1 - fatiguePenalty; // e.g., 0.94 at worst in H1
const fatigueTOMod = 1 + fatiguePenalty * 0.5; // turnovers increase slightly with fatigue
```

### Step 1.3: Apply fatigue modifiers to shooting checks

**Modify the FG checks in `simHalf()` to incorporate `fatigueFGMod`:**

**3-point check (currently line 94):**
```js
// BEFORE:
if (Math.random() * 100 < fg3 + sFG3 + momFG * 0.5) {

// AFTER:
if (Math.random() * 100 < (fg3 + sFG3 + momFG * 0.5) * fatigueFGMod) {
```

**2-point check (currently line 107):**
```js
// BEFORE:
if (Math.random() * 100 < fg2 + sFG2 + momFG * 0.7) {

// AFTER:
if (Math.random() * 100 < (fg2 + sFG2 + momFG * 0.7) * fatigueFGMod) {
```

**Turnover check (currently line 46):**
```js
// BEFORE:
if (Math.random() * 100 < toPct) {

// AFTER:
if (Math.random() * 100 < toPct * fatigueTOMod) {
```

### Step 1.4: Add bench rotation "rest" possessions

**Add inside the `while` loop, after the fatigue calculations from Step 1.2, before the turnover check:**

```js
// ── Bench Rotation: Star Rest ──
// Deep benches rest stars during mid-half possessions (roughly possessions 10-18 of ~33)
// During rest possessions, star usage drops to 0 (role players only)
const restWindowStart = Math.floor(halfPoss * 0.28); // ~possession 9-10
const restWindowEnd = Math.floor(halfPoss * 0.52);   // ~possession 17-18
const inRestWindow = possUsed >= restWindowStart && possUsed <= restWindowEnd;
// Probability of being in rest rotation depends on bench depth
// Deep bench (bench=40+): 70% chance stars rest during window
// Shallow bench (bench=20): 25% chance
const restProb = inRestWindow ? clamp(0.15 + (benchDepth / 100) * 0.65, 0.15, 0.75) : 0;
const isRestPoss = Math.random() < restProb;
// Override star usage for this possession
const effectiveStarUsage = isRestPoss ? starUsage * 0.15 : starUsage;
```

### Step 1.5: Update star usage reference

**Replace the existing star usage check (currently line 40):**
```js
// BEFORE:
const isStar = Math.random() < starUsage;

// AFTER:
const isStar = Math.random() < effectiveStarUsage;
```

---

## Phase 2: Pass Bench Depth from Main Thread to Worker

### Step 2.1: Add bench depth to worker parameters

**File:** `static/index.html`, inside `modelMonteCarlo()` function.

**Current bench depth is computed at lines 1254-1257 but only used for volatility. Add to the `workerParams` object (line 1281-1292):**

```js
// Add these two fields to the workerParams object:
t1Bench: t1Bench,
t2Bench: t2Bench,
```

**The full workerParams block should include these new fields after `t2VolMod`:**
```js
const workerParams = {
    numSims, hca1, hca2, gameTempoCtr,
    m_t1_FG2, m_t2_FG2, m_t1_FG3, m_t2_FG3,
    m_t1_TO, m_t2_TO, m_t1_OR, m_t2_OR,
    m_t1_FTR, m_t2_FTR, m_t1StealRate, m_t2StealRate,
    t1_3Rate, t2_3Rate, t1_FTP, t2_FTP,
    t1VolMod, t2VolMod, t1HgtORBonus,
    t1Hgt, t2Hgt, t1HgtEff, t2HgtEff,
    t1Bench, t2Bench,                          // NEW
    coachEdge, ftClutchEdge, c1Exp, c2Exp,
    totalAdj, kpT1ExpOE, kpT2ExpOE, t1Favored,
    stars1: extra.stars1 || [], stars2: extra.stars2 || [],
};
```

### Step 2.2: Update `simHalf` calls in the worker

**File:** `static/mc-worker.js`, inside `self.onmessage` handler.

**Update the two `simHalf` calls (lines 192-208) to pass bench depth and half indicator:**

```js
// Team 1 first-half call:
const r1 = simHalf(halfPoss,
    g_t1_FG2 + t1_FG2Adj + t1StarDeg, g_t1_FG3 + t1StarDeg * 0.7,
    g_t1_TO + t1_TOAdj, g_t1_OR,
    clamp(p.t1_3Rate + t1_3Adj, 20, 55), p.m_t1_FTR, p.t1_FTP,
    p.m_t2StealRate,
    t1SFT ? t1Star.usage * 0.3 : t1Star.usage,
    t1Star.fg2Bonus, t1Star.fg3Bonus,
    t1Mom,
    p.t1Bench || 30, half === 1);   // NEW: benchDepth, isSecondHalf

// Team 2 first-half call:
const r2 = simHalf(halfPoss,
    g_t2_FG2 + t2_FG2Adj + t2StarDeg, g_t2_FG3 + t2StarDeg * 0.7,
    g_t2_TO + t2_TOAdj, g_t2_OR,
    clamp(p.t2_3Rate + t2_3Adj, 20, 55), p.m_t2_FTR, p.t2_FTP,
    p.m_t1StealRate,
    t2SFT ? t2Star.usage * 0.3 : t2Star.usage,
    t2Star.fg2Bonus, t2Star.fg3Bonus,
    t2Mom,
    p.t2Bench || 30, half === 1);   // NEW: benchDepth, isSecondHalf
```

---

## Phase 3: Return Fatigue Diagnostics

### Step 3.1: Track fatigue stats in `simHalf` return value

**File:** `static/mc-worker.js`

**Modify the return statement of `simHalf` (line 121-122) to include fatigue info:**

**Add these tracking variables at the top of `simHalf` (near line 29):**
```js
let totalFatiguePenalty = 0;
let restPossCount = 0;
```

**Inside the loop, after computing `fatiguePenalty`:**
```js
totalFatiguePenalty += fatiguePenalty;
if (isRestPoss) restPossCount++;
```

**Updated return (line 121-122):**
```js
return { points, possUsed, makes2, makes3, tos, ftMade, ftAtt, orebs, attempts,
         transitionPts, momentum: mom, defFouls,
         avgFatiguePenalty: possUsed > 0 ? totalFatiguePenalty / possUsed : 0,
         restPossessions: restPossCount };
```

### Step 3.2: Aggregate fatigue stats across simulations

**File:** `static/mc-worker.js`, in `self.onmessage` handler.

**Add accumulators near line 142:**
```js
let totalT1Fatigue = 0, totalT2Fatigue = 0;
let totalT1RestPoss = 0, totalT2RestPoss = 0;
```

**Inside the half loop, after r1/r2 are computed, accumulate:**
```js
totalT1Fatigue += r1.avgFatiguePenalty;
totalT2Fatigue += r2.avgFatiguePenalty;
totalT1RestPoss += r1.restPossessions;
totalT2RestPoss += r2.restPossessions;
```

**Add to `self.postMessage` (near line 273):**
```js
t1AvgFatigue: totalT1Fatigue / (numSims * 2),  // avg across both halves
t2AvgFatigue: totalT2Fatigue / (numSims * 2),
t1AvgRestPoss: totalT1RestPoss / (numSims * 2),
t2AvgRestPoss: totalT2RestPoss / (numSims * 2),
```

---

## Phase 4: Display Fatigue Info in UI (Optional Enhancement)

### Step 4.1: Show fatigue diagnostics in the Matchup Predictor

**File:** `static/index.html`

**In the Possession-Level Simulation section (around line 1805-1860), add a new row to the diagnostics grid:**

```html
<div class="text-center">
    <p class="text-xs text-gray-400 mb-1">Fatigue Impact</p>
    <p class="text-lg font-bold">${(mc.t1AvgFatigue * 100)?.toFixed(1) || '0'}% <span class="text-gray-500 text-sm">v</span> ${(mc.t2AvgFatigue * 100)?.toFixed(1) || '0'}%</p>
    <p class="text-xs mt-1 text-gray-500">Avg FG% reduction</p>
</div>
```

---

## Validation Checklist

After implementation, verify the following:

1. **Shallow bench teams (Bench < 25) should show:**
   - Higher avg fatigue penalty (3-5%)
   - Fewer rest possessions per half (1-3)
   - Slightly lower scoring in second halves

2. **Deep bench teams (Bench > 35) should show:**
   - Lower avg fatigue penalty (1-2%)
   - More rest possessions per half (4-7)
   - More consistent scoring across halves

3. **Score distributions should shift:**
   - Blowout % should increase slightly for mismatches (tired underdogs collapse)
   - Close game % should increase slightly for evenly matched teams (both fatigue equally)

4. **Regression test:** Run 10,000 sims for Duke vs a mid-major. Compare avg scores, margins, and win% before/after. Scores should change by no more than 2-3 points; win% by no more than 3-4%.

---

## Constants Reference

| Constant | Value | Rationale |
|---|---|---|
| `fatigueOnsetPct` | 0.55 - 0.70 | Fatigue starts after 55-70% of half possessions |
| `halfMultiplier` | 1.4 for H2 | Second half fatigue is 40% stronger |
| `fatiguePenalty` max | ~6% H1, ~8.4% H2 | Based on NBA tracking data showing 5-8% FG drop in final 5 min |
| `fatigueTOMod` | 1 + penalty * 0.5 | Turnovers increase at half the rate of FG decline |
| `restWindowStart` | 28% of half | Typical sub pattern: starters rest at ~6-7 min mark |
| `restWindowEnd` | 52% of half | Rest window is ~8 possessions wide |
| `restProb` range | 0.15 - 0.75 | Shallow bench rarely rests stars; deep bench almost always does |
