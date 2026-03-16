# Plan 6: Player Foul Trouble & Rotation Impact

**Goal:** Track individual player foul accumulation during the possession-level simulation so that star players who pick up early fouls are forced to sit, creating realistic stretches of degraded team performance. This produces the scoring droughts and runs that characterize real games — when a star sits with 2 first-half fouls, the team's offensive output drops measurably for 5-8 possessions.

**Impact:** Improves within-game scoring variance and game-flow realism. Currently, the star usage rate is constant across all non-rest possessions in a half. In reality, foul trouble is one of the biggest drivers of within-game momentum swings and is a key reason underdogs cover spreads (the favorite's star sits with foul trouble, the underdog goes on a run). Expected ATS improvement: 1-2% accuracy gain for games involving teams with foul-prone stars.

**Files to modify:**
- `static/mc-worker.js` — `simHalf()` and `simOvertime()` functions, `self.onmessage` handler
- `static/index.html` — `modelMonteCarlo()` parameter computation, UI diagnostics display

---

## Phase 1: Model Individual Foul Accumulation in `simHalf()`

### Step 1.1: Add foul-tracking parameters to `simHalf` signature

**File:** `static/mc-worker.js`

**Current signature (line 43-45):**
```js
function simHalf(halfPoss, fg2, fg3, toPct, orPct, rate3, ftr, ftPct,
                 defStealRate, starUsage, starFG2, starFG3, initMom,
                 benchDepth, isSecondHalf, incomingLead, defProfile)
```

**New signature:**
```js
function simHalf(halfPoss, fg2, fg3, toPct, orPct, rate3, ftr, ftPct,
                 defStealRate, starUsage, starFG2, starFG3, initMom,
                 benchDepth, isSecondHalf, incomingLead, defProfile,
                 starFoulState, starFoulProneness)
```

- `starFoulState`: object `{ fouls: number, isSitting: boolean }` — carries foul count from first half to second half
- `starFoulProneness`: float 0.0-1.0 — how likely the star is to draw fouls (derived from the star's play style and the opponent's defensive foul rate)

### Step 1.2: Define foul-trouble constants and state tracking

**Add inside `simHalf()`, after the existing game-clock phase variables (after line 63):**

```js
// ── Player Foul Trouble ──
// NCAA rules: 5 personal fouls = disqualification
// Coaches typically bench stars at:
//   - 2 fouls in the first half (sit until second half)
//   - 4 fouls early in the second half (sit until ~5 min left)
const MAX_FOULS = 5;
const FOUL_SIT_THRESHOLD_H1 = 2;  // Sit star at 2 fouls in H1
const FOUL_SIT_THRESHOLD_H2 = 4;  // Sit star at 4 fouls in H2
const FOUL_RETURN_PCT = 0.80;     // Star returns when 80% of half is over

let starFouls = starFoulState ? starFoulState.fouls : 0;
let starIsSitting = starFoulState ? starFoulState.isSitting : false;
let starSatPoss = 0;              // Possessions star spent on bench due to foul trouble
let starFouledOut = false;        // True if star hit 5 fouls (disqualified)

// Per-possession probability that the star commits a foul
// Base rate: ~3-4% per possession for average player
// Adjusted by starFoulProneness (aggressive players foul more)
const baseStarFoulRate = 0.035 + (starFoulProneness || 0) * 0.02;
```

### Step 1.3: Add per-possession foul check and bench logic

**Add inside the `while` loop, after the bench rotation / star rest block (after line 116), before the `isStar` check:**

```js
// ── Star Foul Trouble Check ──
// Each possession, there's a chance the star picks up a personal foul
// This is SEPARATE from team fouls (defFouls) — this tracks the individual star
if (!starIsSitting && !starFouledOut && Math.random() < baseStarFoulRate) {
    starFouls++;
    
    if (starFouls >= MAX_FOULS) {
        // Star fouled out — disqualified for the rest of the game
        starFouledOut = true;
        starIsSitting = true;
    } else if (!isSecondHalf && starFouls >= FOUL_SIT_THRESHOLD_H1) {
        // 2 fouls in first half — coach sits the star
        starIsSitting = true;
    } else if (isSecondHalf && starFouls >= FOUL_SIT_THRESHOLD_H2) {
        // 4 fouls in second half — coach sits the star until crunch time
        starIsSitting = true;
    }
}

// ── Star Return from Foul Trouble ──
// Star comes back when enough of the half has elapsed
if (starIsSitting && !starFouledOut) {
    const returnThreshold = isSecondHalf ? PHASE_CRUNCH_START - 0.05 : FOUL_RETURN_PCT;
    if (progressPct >= returnThreshold) {
        starIsSitting = false;
    }
}

if (starIsSitting) {
    starSatPoss++;
}

// Override effectiveStarUsage when star is sitting due to foul trouble
const foulTroubleStarUsage = starIsSitting ? starUsage * 0.10 : effectiveStarUsage;
```

### Step 1.4: Replace the existing `isStar` check to use foul-trouble-aware usage

**Replace (line 118):**
```js
// BEFORE:
const isStar = Math.random() < effectiveStarUsage;

// AFTER:
const isStar = Math.random() < foulTroubleStarUsage;
```

### Step 1.5: Return foul-trouble diagnostics from `simHalf()`

**Modify the return statement (lines 221-226) to include foul state:**

```js
return { points, possUsed, makes2, makes3, tos, ftMade, ftAtt, orebs, attempts,
         transitionPts, momentum: mom, defFouls,
         avgFatiguePenalty: possUsed > 0 ? totalFatiguePenalty / possUsed : 0,
         restPossessions: restPossCount,
         crunchTimePoss, desperationPoss, intentionalFoulPoss,
         finalLead: runningLead,
         starFoulState: { fouls: starFouls, isSitting: starIsSitting, fouledOut: starFouledOut },
         starSatPoss, starFouledOut };
```

---

## Phase 2: Carry Foul State Between Halves

### Step 2.1: Pass first-half foul state to second-half `simHalf` call

**File:** `static/mc-worker.js`, inside `self.onmessage` handler.

**Before the half loop (around line 384), initialize foul state:**

```js
let t1StarFoulState = { fouls: 0, isSitting: false };
let t2StarFoulState = { fouls: 0, isSitting: false };
```

**After r1 and r2 are computed (after line 416), update foul state for next half:**

```js
// Carry foul state to next half
t1StarFoulState = r1.starFoulState || { fouls: 0, isSitting: false };
t2StarFoulState = r2.starFoulState || { fouls: 0, isSitting: false };

// At halftime, sitting stars return (unless they have 4+ fouls going into H2)
if (half === 0) {
    if (t1StarFoulState.fouls < 4) t1StarFoulState.isSitting = false;
    if (t2StarFoulState.fouls < 4) t2StarFoulState.isSitting = false;
}
```

### Step 2.2: Update `simHalf` calls to pass foul state and proneness

**Update the r1 call (lines 396-405) to include foul parameters:**

```js
const r1 = simHalf(halfPoss,
    g_t1_FG2 + t1StarDeg, g_t1_FG3 + t1StarDeg * 0.7,
    g_t1_TO, g_t1_OR,
    clamp(g_t1_3Rate, 20, 55), g_t1_FTR, p.t1_FTP,
    p.m_t2StealRate,
    t1SFT ? t1Star.usage * 0.3 : t1Star.usage,
    t1Star.fg2Bonus, t1Star.fg3Bonus,
    t1Mom,
    p.t1Bench || 30, half === 1, t1IncomingLead,
    p.t2DefProfile || { perimeter: 0, interior: 0, overall: 0 },
    t1StarFoulState, p.t1StarFoulProneness || 0);  // NEW
```

**Update the r2 call similarly.**

---

## Phase 3: Compute Foul Proneness from Team Stats

### Step 3.1: Derive star foul proneness in `modelMonteCarlo()`

**File:** `static/index.html`, inside `modelMonteCarlo()`.

**Add after the existing stat computation (near where `t1Bench` is computed):**

```js
// ── Star Foul Proneness ──
// Players on teams with high FTR tend to draw more contact (driving, physical play)
// but they also COMMIT more fouls. Teams facing high-FTR opponents get fouled more.
// Stars on interior-heavy teams are more foul-prone.
//
// Proneness scale: 0.0 (disciplined guard who avoids contact)
//                  0.5 (average)
//                  1.0 (aggressive post player who picks up fouls)

const t1StarFoulProneness = clamp(
    (t1_FTR - AVG_FTR) * 0.02          // Teams with high FTR play physical
    + (t2_Stl - AVG_STL) * 0.03        // Facing a high-steal team → more aggressive D → more foul calls
    + (t1Star.usage > 0.25 ? 0.15 : 0) // High-usage stars draw more attention/contact
    , 0, 1);

const t2StarFoulProneness = clamp(
    (t2_FTR - AVG_FTR) * 0.02
    + (t1_Stl - AVG_STL) * 0.03
    + (t2Star.usage > 0.25 ? 0.15 : 0)
    , 0, 1);
```

**Add to `workerParams` object:**
```js
t1StarFoulProneness: t1StarFoulProneness,
t2StarFoulProneness: t2StarFoulProneness,
```

---

## Phase 4: Aggregate Foul-Trouble Diagnostics

### Step 4.1: Track foul-trouble stats across simulations

**File:** `static/mc-worker.js`, in `self.onmessage` handler.

**Add accumulators near line 318:**
```js
let t1StarSatTotal = 0, t2StarSatTotal = 0;
let t1StarFouledOutGames = 0, t2StarFouledOutGames = 0;
let t1StarFoulTroubleGames = 0, t2StarFoulTroubleGames = 0; // games where star sat 3+ poss
```

**Inside the half loop, after r1/r2:**
```js
t1StarSatTotal += r1.starSatPoss || 0;
t2StarSatTotal += r2.starSatPoss || 0;
```

**After the half loop (after OT resolution), per-game tracking:**
```js
if (r1.starFouledOut) t1StarFouledOutGames++;
if (r2.starFouledOut) t2StarFouledOutGames++;
if ((r1.starSatPoss || 0) >= 3) t1StarFoulTroubleGames++;
if ((r2.starSatPoss || 0) >= 3) t2StarFoulTroubleGames++;
```

### Step 4.2: Include foul-trouble stats in worker response

**Add to `self.postMessage` (near line 540):**
```js
foulTrouble: {
    t1AvgSatPoss: t1StarSatTotal / (numSims * 2),
    t2AvgSatPoss: t2StarSatTotal / (numSims * 2),
    t1FouledOutRate: t1StarFouledOutGames / numSims,
    t2FouledOutRate: t2StarFouledOutGames / numSims,
    t1FoulTroubleRate: t1StarFoulTroubleGames / numSims,
    t2FoulTroubleRate: t2StarFoulTroubleGames / numSims,
},
```

---

## Phase 5: Display Foul-Trouble Info in UI

### Step 5.1: Show foul-trouble diagnostics in the Matchup Predictor

**File:** `static/index.html`

**In the Simulation Results section, add a new row to the diagnostics grid:**

```html
<!-- Foul Trouble Risk -->
<div class="mt-2">
    <p class="text-xs text-gray-500">
        <i class="fas fa-hand-paper mr-1"></i>
        Foul Trouble: ${t1Name} star sits ${mc.foulTrouble?.t1AvgSatPoss?.toFixed(1) || '0'} poss/game
        (fouled out ${((mc.foulTrouble?.t1FouledOutRate || 0) * 100).toFixed(1)}%) •
        ${t2Name} star sits ${mc.foulTrouble?.t2AvgSatPoss?.toFixed(1) || '0'} poss/game
        (fouled out ${((mc.foulTrouble?.t2FouledOutRate || 0) * 100).toFixed(1)}%)
    </p>
</div>
```

---

## Validation Checklist

After implementation, verify the following:

1. **Foul trouble frequency:**
   - Stars should pick up 2+ first-half fouls in ~15-20% of simulations
   - Stars should foul out in ~2-4% of simulations
   - Stars should sit an average of 2-4 possessions per game due to foul trouble

2. **Performance impact when star sits:**
   - Team scoring should drop by ~0.3-0.5 points per possession when star is sitting
   - The team with more star-sitting possessions should win less often

3. **Interaction with existing systems:**
   - Foul trouble should compound with fatigue (star returns fatigued from sitting, then plays hard)
   - Foul trouble in crunch time should rarely happen (coaches accept risk of fouls when trailing)
   - Star rest possessions (bench rotation) should NOT count as foul-trouble sitting

4. **Regression test:** Run 10,000 sims for a matchup with a high-usage star vs an elite defensive team. The star should get into foul trouble more often than against a passive defense.

---

## Constants Reference

| Constant | Value | Rationale |
|---|---|---|
| `MAX_FOULS` | 5 | NCAA personal foul limit |
| `FOUL_SIT_THRESHOLD_H1` | 2 | Standard coaching practice: sit star at 2 fouls in H1 |
| `FOUL_SIT_THRESHOLD_H2` | 4 | Standard coaching practice: sit star at 4 fouls in H2 |
| `FOUL_RETURN_PCT` | 0.80 | Star returns when 80% of the half is over |
| `baseStarFoulRate` | 0.035-0.055 | ~3.5-5.5% chance of committing a foul per possession |
| `starUsage * 0.10` | 10% of normal | When star sits, team plays like they have no star (minimal residual usage from secondary playmakers) |
