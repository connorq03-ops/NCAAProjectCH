# Plan 4: Defensive Intensity & Matchup Asymmetry

**Goal:** Replace the current simple-average matchup adjustment formula with an asymmetric weighting system that gives elite defenses (and offenses) outsized influence on matchup-adjusted rates. Add a "defensive disruption" factor that affects opponent shot selection and turnover variance, not just aggregate rates.

**Impact:** Most important improvement for predicting upsets. Currently, a top-5 defense and a bottom-50 defense are weighted equally against the same offense — this underestimates shutdown defenses and overestimates bad ones.

**Files to modify:**
- `static/index.html` — matchup-adjusted rate computation in `modelMonteCarlo()` (lines ~1224-1238)
- `static/mc-worker.js` — `simHalf()` to handle defensive disruption effects

---

## Phase 1: Asymmetric Matchup Weighting

### Step 1.1: Understand the current formula

**File:** `static/index.html`, lines 1224-1238.

**Current matchup adjustment formula (example for turnovers):**
```js
const m_t1_TO = clamp(t1_TO + t2_DTO - AVG_TO, 8, 28);
```

This is equivalent to: `matchup = offense + defense - league_average`, which gives equal weight to offense and defense. For a team that forces 22% turnovers (elite D, +4.5 above avg) against a team with 15% TO rate (careful O, -2.5 below avg), the result is `15 + 22 - 17.5 = 19.5%`.

**Problem:** The formula treats offense and defense symmetrically. In reality:
- Elite defenses have MORE ability to force turnovers regardless of opponent (scheme, pressure, athleticism)
- Bad offenses are MORE susceptible to elite defenses (compounding effect)
- The effect is nonlinear: a top-10 defense disrupts everyone, not just bad offenses

### Step 1.2: Create an asymmetric weighting function

**File:** `static/index.html`, add this helper function inside the `<script>` block, before `modelMonteCarlo()`:**

```js
/**
 * Asymmetric matchup adjustment.
 * When one side is extreme (top/bottom 20%), weight it more heavily.
 *
 * @param {number} offRate - offensive team's rate (e.g., their TO%)
 * @param {number} defRate - defensive team's rate against opponents (e.g., forced TO%)
 * @param {number} avgRate - league average rate
 * @param {number} eliteThreshold - how many % points from average counts as "elite" (default 3)
 * @returns {number} matchup-adjusted rate
 */
function asymmetricMatchup(offRate, defRate, avgRate, eliteThreshold = 3) {
    const offDev = offRate - avgRate;   // positive = worse offense (higher TO%, etc.)
    const defDev = defRate - avgRate;   // positive = better defense (forces more TOs)

    // Base: equal weight (50/50)
    let offWeight = 0.50;
    let defWeight = 0.50;

    // If defense is elite (far from average), increase its weight
    const defExtremeness = Math.abs(defDev) / eliteThreshold;
    if (defExtremeness > 1.0) {
        // Elite defense: shift weight toward defense
        // At 2x threshold, defense gets 65% weight; at 3x, 72%
        const shift = Math.min(defExtremeness * 0.08, 0.22);
        defWeight += shift;
        offWeight -= shift;
    }

    // If offense is elite (far from average), increase its weight too
    const offExtremeness = Math.abs(offDev) / eliteThreshold;
    if (offExtremeness > 1.0) {
        const shift = Math.min(offExtremeness * 0.06, 0.15);
        offWeight += shift;
        defWeight -= shift;
    }

    // Normalize weights
    const total = offWeight + defWeight;
    offWeight /= total;
    defWeight /= total;

    // Compute matchup rate: weighted combination centered on average
    const matchupRate = avgRate + (offDev * offWeight) + (defDev * defWeight);

    // Compounding effect: when BOTH sides are extreme in the same direction, amplify
    // e.g., elite defense vs turnover-prone offense → even more TOs than linear sum
    const sameDirection = (offDev > 0 && defDev > 0) || (offDev < 0 && defDev < 0);
    const compoundBonus = sameDirection ? Math.min(Math.abs(offDev * defDev) * 0.015, 1.5) : 0;
    // Apply compound in the direction both point
    const compoundSign = (offDev + defDev) > 0 ? 1 : -1;

    return matchupRate + compoundBonus * compoundSign;
}
```

### Step 1.3: Replace all matchup-adjusted rate computations

**File:** `static/index.html`, inside `modelMonteCarlo()` (lines 1224-1238).

**Replace the current block:**
```js
// ── MATCHUP-ADJUSTED RATES ──
const m_t1_TO  = clamp(t1_TO + t2_DTO - AVG_TO, 8, 28);
const m_t2_TO  = clamp(t2_TO + t1_DTO - AVG_TO, 8, 28);
const m_t1_OR  = clamp(t1_OR + t2_DOR - AVG_OR, 15, 42);
const m_t2_OR  = clamp(t2_OR + t1_DOR - AVG_OR, 15, 42);
const m_t1_FTR = clamp(t1_FTR + t2_DFTR - AVG_FTR, 15, 50);
const m_t2_FTR = clamp(t2_FTR + t1_DFTR - AVG_FTR, 15, 50);
const t1_blkAdj = (t2_Blk - AVG_BLK) * 0.12;
const t2_blkAdj = (t1_Blk - AVG_BLK) * 0.12;
const m_t1_FG2 = clamp((t1_FG2 + t2_OppFG2) / 2 - t1_blkAdj, 30, 65);
const m_t2_FG2 = clamp((t2_FG2 + t1_OppFG2) / 2 - t2_blkAdj, 30, 65);
const m_t1_FG3 = clamp((t1_FG3 + t2_OppFG3) / 2, 22, 44);
const m_t2_FG3 = clamp((t2_FG3 + t1_OppFG3) / 2, 22, 44);
const m_t2StealRate = clamp((t2_Stl + AVG_STL) / 2, 5, 16);
const m_t1StealRate = clamp((t1_Stl + AVG_STL) / 2, 5, 16);
```

**With:**
```js
// ── MATCHUP-ADJUSTED RATES (Asymmetric Weighting) ──
// Turnovers: offense = team's own TO%, defense = opponent's forced TO%
const m_t1_TO  = clamp(asymmetricMatchup(t1_TO, t2_DTO, AVG_TO, 3.0), 8, 28);
const m_t2_TO  = clamp(asymmetricMatchup(t2_TO, t1_DTO, AVG_TO, 3.0), 8, 28);

// Offensive rebounds: offense = team's OR%, defense = opponent's DOR% allowed
const m_t1_OR  = clamp(asymmetricMatchup(t1_OR, t2_DOR, AVG_OR, 4.0), 15, 42);
const m_t2_OR  = clamp(asymmetricMatchup(t2_OR, t1_DOR, AVG_OR, 4.0), 15, 42);

// Free throw rate: offense = team's FTR, defense = opponent's allowed FTR
const m_t1_FTR = clamp(asymmetricMatchup(t1_FTR, t2_DFTR, AVG_FTR, 4.0), 15, 50);
const m_t2_FTR = clamp(asymmetricMatchup(t2_FTR, t1_DFTR, AVG_FTR, 4.0), 15, 50);

// FG2%: asymmetric matchup with block adjustment
const t1_blkAdj = (t2_Blk - AVG_BLK) * 0.18;  // Increased from 0.12 — blocks matter more
const t2_blkAdj = (t1_Blk - AVG_BLK) * 0.18;
const m_t1_FG2 = clamp(asymmetricMatchup(t1_FG2, t2_OppFG2, AVG_FG2, 3.0) - t1_blkAdj, 30, 65);
const m_t2_FG2 = clamp(asymmetricMatchup(t2_FG2, t1_OppFG2, AVG_FG2, 3.0) - t2_blkAdj, 30, 65);

// FG3%: asymmetric matchup
const m_t1_FG3 = clamp(asymmetricMatchup(t1_FG3, t2_OppFG3, AVG_FG3, 2.5), 22, 44);
const m_t2_FG3 = clamp(asymmetricMatchup(t2_FG3, t1_OppFG3, AVG_FG3, 2.5), 22, 44);

// Steal rate: asymmetric (elite steal teams are more impactful)
const m_t2StealRate = clamp(asymmetricMatchup(AVG_STL, t2_Stl, AVG_STL, 2.0), 5, 16);
const m_t1StealRate = clamp(asymmetricMatchup(AVG_STL, t1_Stl, AVG_STL, 2.0), 5, 16);
```

---

## Phase 2: Defensive Disruption Factor

### Step 2.1: Compute a defensive disruption score

**File:** `static/index.html`, inside `modelMonteCarlo()`.

**Add after the matchup-adjusted rates block (new code):**

```js
// ── DEFENSIVE DISRUPTION ──
// Elite defenses don't just affect average stats — they increase VARIANCE of opponent outcomes.
// A team facing Houston's defense doesn't just shoot worse on average — they have wilder swings
// (some possessions are completely disrupted, others are open looks from defensive rotations).
//
// Disruption score: composite of how far above average the defense is in key categories
// Scale: 0 (average defense) to 1.0 (elite shutdown defense)

function calcDisruptionScore(dto, dor, doppFg2, doppFg3, stl, blk) {
    let score = 0;
    // Forced TOs above average
    score += clamp((dto - AVG_TO) / 5, 0, 1) * 0.25;
    // Defensive rebounding (low DOR% = good defense, opponents don't get ORebs)
    score += clamp((AVG_OR - dor) / 6, 0, 1) * 0.15;
    // Opponent FG2% suppression
    score += clamp((AVG_FG2 - doppFg2) / 5, 0, 1) * 0.20;
    // Opponent FG3% suppression
    score += clamp((AVG_FG3 - doppFg3) / 4, 0, 1) * 0.20;
    // Steals
    score += clamp((stl - AVG_STL) / 3, 0, 1) * 0.10;
    // Blocks
    score += clamp((blk - AVG_BLK) / 3, 0, 1) * 0.10;
    return clamp(score, 0, 1);
}

const t1DefDisruption = calcDisruptionScore(t1_DTO, t1_DOR, t1_OppFG2, t1_OppFG3, t1_Stl, t1_Blk);
const t2DefDisruption = calcDisruptionScore(t2_DTO, t2_DOR, t2_OppFG2, t2_OppFG3, t2_Stl, t2_Blk);
```

**Add to `workerParams` object:**
```js
t1DefDisruption: t1DefDisruption,
t2DefDisruption: t2DefDisruption,
```

### Step 2.2: Apply disruption effects in the worker

**File:** `static/mc-worker.js`, inside `self.onmessage` handler.

**The disruption score affects the OPPONENT's stats. Team 1's defense disrupts Team 2's offense.**

**Inside the simulation loop, after generating game-level adjusted rates, add:**

```js
// ── Defensive Disruption Effects ──
// Elite defenses increase opponent's VARIANCE (more wild possessions)
// and shift opponent's shot selection toward worse options

// Team 2 faces Team 1's defense:
// Increase TO variance for Team 2 based on Team 1's disruption
const t2_TOVarianceBoost = p.t1DefDisruption * 2.5; // up to +2.5% extra TO variance
g_t2_TO = clamp(g_t2_TO + randNormal(0, t2_TOVarianceBoost), 6, 30);

// Team 1 faces Team 2's defense:
const t1_TOVarianceBoost = p.t2DefDisruption * 2.5;
g_t1_TO = clamp(g_t1_TO + randNormal(0, t1_TOVarianceBoost), 6, 30);
```

### Step 2.3: Shot selection disruption in `simHalf()`

**File:** `static/mc-worker.js`

**Add a new parameter to `simHalf` signature:**
```js
function simHalf(halfPoss, fg2, fg3, toPct, orPct, rate3, ftr, ftPct,
                 defStealRate, starUsage, starFG2, starFG3, initMom,
                 defDisruption)   // NEW: opponent's defensive disruption score (0-1)
```

**Add inside the `while` loop, after the star usage check:**

```js
// ── Defensive Disruption: Shot Quality ──
// Elite defenses force worse shot selection on individual possessions
// Random per-possession effect: some possessions are well-defended, some aren't
const isDisruptedPoss = Math.random() < defDisruption * 0.6; // Up to 60% of possessions disrupted by elite D
const disruptFGMod = isDisruptedPoss ? -3.5 : 0;  // -3.5% FG on disrupted possessions
const disruptStarMod = isDisruptedPoss ? 0.5 : 1.0; // Stars are 50% less effective on disrupted possessions
```

**Apply disruption modifiers to the FG checks:**

**3-point check:**
```js
// BEFORE:
if (Math.random() * 100 < fg3 + sFG3 + momFG * 0.5) {

// AFTER:
if (Math.random() * 100 < fg3 + (sFG3 * disruptStarMod) + momFG * 0.5 + disruptFGMod) {
```

**2-point check:**
```js
// BEFORE:
if (Math.random() * 100 < fg2 + sFG2 + momFG * 0.7) {

// AFTER:
if (Math.random() * 100 < fg2 + (sFG2 * disruptStarMod) + momFG * 0.7 + disruptFGMod * 0.7) {
// Note: 2PT disruption is 70% as strong (rim attacks are harder to fully contest)
```

### Step 2.4: Update `simHalf` calls to pass disruption score

**File:** `static/mc-worker.js`, in `self.onmessage` handler.

**Team 1 call (Team 1 faces Team 2's defense):**
```js
const r1 = simHalf(halfPoss,
    g_t1_FG2 + t1_FG2Adj + t1StarDeg, g_t1_FG3 + t1StarDeg * 0.7,
    g_t1_TO + t1_TOAdj, g_t1_OR,
    clamp(p.t1_3Rate + t1_3Adj, 20, 55), p.m_t1_FTR, p.t1_FTP,
    p.m_t2StealRate,
    t1SFT ? t1Star.usage * 0.3 : t1Star.usage,
    t1Star.fg2Bonus, t1Star.fg3Bonus,
    t1Mom,
    p.t2DefDisruption || 0);    // NEW: T1 faces T2's defense
```

**Team 2 call (Team 2 faces Team 1's defense):**
```js
const r2 = simHalf(halfPoss,
    g_t2_FG2 + t2_FG2Adj + t2StarDeg, g_t2_FG3 + t2StarDeg * 0.7,
    g_t2_TO + t2_TOAdj, g_t2_OR,
    clamp(p.t2_3Rate + t2_3Adj, 20, 55), p.m_t2_FTR, p.t2_FTP,
    p.m_t1StealRate,
    t2SFT ? t2Star.usage * 0.3 : t2Star.usage,
    t2Star.fg2Bonus, t2Star.fg3Bonus,
    t2Mom,
    p.t1DefDisruption || 0);    // NEW: T2 faces T1's defense
```

---

## Phase 3: Perimeter vs Interior Defensive Profiles

### Step 3.1: Split disruption into perimeter and interior components

**File:** `static/index.html`, inside `modelMonteCarlo()`.

**Replace the single `calcDisruptionScore` with a profiled version:**

```js
/**
 * Compute separate perimeter and interior defensive disruption scores.
 * This determines WHAT TYPE of shots the defense disrupts most.
 */
function calcDefensiveProfile(dto, dor, doppFg2, doppFg3, stl, blk) {
    // Perimeter disruption: affects 3PT shots, steals, passing lanes
    const perimeterDisruption = clamp(
        (clamp((AVG_FG3 - doppFg3) / 4, 0, 1) * 0.40 +
         clamp((stl - AVG_STL) / 3, 0, 1) * 0.35 +
         clamp((dto - AVG_TO) / 5, 0, 1) * 0.25),
        0, 1
    );

    // Interior disruption: affects 2PT shots, blocks, defensive rebounding
    const interiorDisruption = clamp(
        (clamp((AVG_FG2 - doppFg2) / 5, 0, 1) * 0.35 +
         clamp((blk - AVG_BLK) / 3, 0, 1) * 0.35 +
         clamp((AVG_OR - dor) / 6, 0, 1) * 0.30),
        0, 1
    );

    // Overall disruption (for backward compatibility)
    const overall = (perimeterDisruption + interiorDisruption) / 2;

    return { perimeter: perimeterDisruption, interior: interiorDisruption, overall };
}

const t1DefProfile = calcDefensiveProfile(t1_DTO, t1_DOR, t1_OppFG2, t1_OppFG3, t1_Stl, t1_Blk);
const t2DefProfile = calcDefensiveProfile(t2_DTO, t2_DOR, t2_OppFG2, t2_OppFG3, t2_Stl, t2_Blk);
```

**Update `workerParams`:**
```js
t1DefProfile: t1DefProfile,   // replaces t1DefDisruption
t2DefProfile: t2DefProfile,   // replaces t2DefDisruption
```

### Step 3.2: Apply profiled disruption in `simHalf()`

**Update `simHalf` signature:**
```js
function simHalf(halfPoss, fg2, fg3, toPct, orPct, rate3, ftr, ftPct,
                 defStealRate, starUsage, starFG2, starFG3, initMom,
                 defProfile)   // NEW: { perimeter, interior, overall }
```

**Update the disruption logic inside the `while` loop:**
```js
// ── Defensive Disruption: Profiled Shot Quality ──
const defP = defProfile || { perimeter: 0, interior: 0, overall: 0 };
const isDisruptedPoss = Math.random() < defP.overall * 0.6;

// Apply disruption differently for 2PT vs 3PT
const disrupt3Mod = isDisruptedPoss ? -(defP.perimeter * 5.0) : 0;  // Up to -5% FG3
const disrupt2Mod = isDisruptedPoss ? -(defP.interior * 4.0) : 0;   // Up to -4% FG2
const disruptStarMod = isDisruptedPoss ? (1 - defP.overall * 0.5) : 1.0;
```

**Update FG checks:**
```js
// 3-point check:
if (Math.random() * 100 < fg3 + (sFG3 * disruptStarMod) + momFG * 0.5 + disrupt3Mod) {

// 2-point check:
if (Math.random() * 100 < fg2 + (sFG2 * disruptStarMod) + momFG * 0.7 + disrupt2Mod) {
```

### Step 3.3: Update `simHalf` calls to pass profile object

**In the worker's `simHalf` calls, pass the full profile instead of a single number:**

```js
// Team 1 faces Team 2's defense:
    ..., p.t2DefProfile || { perimeter: 0, interior: 0, overall: 0 });

// Team 2 faces Team 1's defense:
    ..., p.t1DefProfile || { perimeter: 0, interior: 0, overall: 0 });
```

---

## Phase 4: Display Defensive Profile in UI

### Step 4.1: Show defensive profiles in the Matchup Predictor

**File:** `static/index.html`

**In the Possession-Level Simulation section (around line 1805-1860), add a defensive profile row:**

```html
<!-- Defensive Disruption Profiles -->
<div class="grid grid-cols-2 gap-4 mt-3">
    <div class="text-center">
        <p class="text-xs text-gray-400 mb-1">${t1.TeamName} Defense</p>
        <div class="flex justify-center gap-3 text-xs">
            <span class="text-blue-400">Perim: ${(mc.t1DefProfile?.perimeter * 100 || 0).toFixed(0)}%</span>
            <span class="text-orange-400">Interior: ${(mc.t1DefProfile?.interior * 100 || 0).toFixed(0)}%</span>
        </div>
        <p class="text-xs mt-1 text-gray-500">Disruption rating</p>
    </div>
    <div class="text-center">
        <p class="text-xs text-gray-400 mb-1">${t2.TeamName} Defense</p>
        <div class="flex justify-center gap-3 text-xs">
            <span class="text-blue-400">Perim: ${(mc.t2DefProfile?.perimeter * 100 || 0).toFixed(0)}%</span>
            <span class="text-orange-400">Interior: ${(mc.t2DefProfile?.interior * 100 || 0).toFixed(0)}%</span>
        </div>
        <p class="text-xs mt-1 text-gray-500">Disruption rating</p>
    </div>
</div>
```

### Step 4.2: Add defensive profile data to worker response

**File:** `static/mc-worker.js`, in `self.postMessage`:**

```js
t1DefProfile: p.t1DefProfile,
t2DefProfile: p.t2DefProfile,
```

---

## Phase 5: Matchup-Specific 3PT Defense Adjustment

### Step 5.1: Penalize 3PT-heavy offenses against elite perimeter defenses

**File:** `static/index.html`, inside `modelMonteCarlo()`.

**Add after computing defensive profiles:**

```js
// ── Matchup-Specific 3PT Suppression ──
// A 3PT-heavy team facing an elite perimeter defense gets an extra FG3% penalty
// This is ON TOP of the matchup-adjusted FG3% already computed
const t1_3Heavy = (t1_3RateBase - AVG_3RATE) / AVG_3RATE; // % above avg 3PT reliance
const t2_3Heavy = (t2_3RateBase - AVG_3RATE) / AVG_3RATE;

// If Team 1 shoots lots of 3s AND faces Team 2's elite perimeter D → extra penalty
const t1_perimMatchupPenalty = clamp(t1_3Heavy * t2DefProfile.perimeter * 3.0, 0, 2.5);
const t2_perimMatchupPenalty = clamp(t2_3Heavy * t1DefProfile.perimeter * 3.0, 0, 2.5);

// Apply to matchup-adjusted FG3%
const m_t1_FG3_final = clamp(m_t1_FG3 - t1_perimMatchupPenalty, 22, 44);
const m_t2_FG3_final = clamp(m_t2_FG3 - t2_perimMatchupPenalty, 22, 44);
```

**Update `workerParams` to use `m_t1_FG3_final` instead of `m_t1_FG3`:**
```js
m_t1_FG3: m_t1_FG3_final,
m_t2_FG3: m_t2_FG3_final,
```

---

## Validation Checklist

After implementation, verify the following:

1. **Asymmetric weighting check:**
   - For a matchup of average offense (TO% = 17.5) vs elite defense (forced TO% = 22.5):
     - Old formula: `17.5 + 22.5 - 17.5 = 22.5%`
     - New formula should produce ~23-24% (defense weighted more)
   - For average offense vs average defense, result should be unchanged (~17.5%)

2. **Disruption score check:**
   - Houston (elite D): disruption score should be > 0.6
   - Average team: disruption score should be 0.2-0.4
   - Bad defense: disruption score should be < 0.15

3. **Elite defense matchup:**
   - Run 5000 sims of Houston vs a 3PT-heavy mid-major
   - The mid-major's avg FG3% should be noticeably lower than their season average
   - The mid-major's avg TO% should be higher than their season average
   - Houston's win% should be slightly higher than before this change

4. **Perimeter vs Interior profile:**
   - A team with elite 3PT defense but average interior D should show high perimeter disruption, average interior disruption
   - This should suppress 3PT-heavy opponents more than interior-heavy opponents

5. **Compounding effect:**
   - Elite defense vs bad offense should show GREATER impact than the sum of individual effects
   - e.g., Houston (forces 22% TO) vs a careless team (20% TO): result should be > 24.5% (old formula would give 24.5%)

6. **Regression test:** Run 10 matchups before/after. Average margin should shift by < 2 points. Win% for evenly matched teams should be unchanged. Win% for defense-heavy matchups should shift by 2-4% toward the better defensive team.

---

## Constants Reference

| Constant | Value | Rationale |
|---|---|---|
| `eliteThreshold` (TO) | 3.0 | ~1 SD of TO% across D1 |
| `eliteThreshold` (OR) | 4.0 | ~1 SD of OR% across D1 |
| `eliteThreshold` (FG2) | 3.0 | ~1 SD of FG2% across D1 |
| `eliteThreshold` (FG3) | 2.5 | 3PT% has tighter distribution |
| Max defense weight shift | 0.22 | Defense can get up to 72% weight vs offense 28% |
| Max offense weight shift | 0.15 | Offense can get up to 65% weight vs defense 35% |
| Compound bonus max | 1.5% | Caps the compounding effect |
| Block coefficient | 0.18 | Increased from 0.12 (blocks have more impact than current model) |
| `disrupt3Mod` max | -5.0% | Elite perimeter D can reduce FG3% by up to 5% on disrupted possessions |
| `disrupt2Mod` max | -4.0% | Elite interior D can reduce FG2% by up to 4% on disrupted possessions |
| `disruptStarMod` min | 0.5 | Stars are still 50% effective even on disrupted possessions |
| `perimMatchupPenalty` max | 2.5% | Extra FG3% penalty for 3PT-heavy vs elite perimeter D |
