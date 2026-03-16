# Plan 8: Three-Point Shooting Streaks & Hot/Cold Modeling

**Goal:** Replace the current independent-draw 3-point shooting model with an autocorrelated "hot hand / cold streak" system where consecutive makes or misses affect the probability of the next attempt. This produces the clustered scoring runs that define real basketball — a team draining 4 threes in 5 possessions, or going 0-for-7 from deep — rather than the smooth, independent coin-flip results the current model produces.

**Impact:** The current simulation treats each 3-point attempt as an independent event at a fixed (per-game) probability. In reality, basketball exhibits measurable autocorrelation in shooting (the "hot hand" effect, supported by modern statistical research). This improvement will:
- Produce more realistic run distributions (8-0 runs, 12-2 runs that happen in real games)
- Improve within-game momentum dynamics (hot shooting drives momentum, which is already modeled but currently fed by independent noise)
- Better calibrate the tails of the margin distribution (blowouts often come from one team getting hot)
- Improve ATS accuracy for volatile 3-point shooting teams (teams that live by the 3 die by it)

**Files to modify:**
- `static/mc-worker.js` — `simHalf()` function, new streak tracking system
- `static/index.html` — `modelMonteCarlo()` parameter computation, UI display

---

## Phase 1: Add Shooting Streak State to `simHalf()`

### Step 1.1: Define streak-tracking variables

**File:** `static/mc-worker.js`, inside `simHalf()`.

**Add after the existing variable declarations (after line 54):**

```js
// ── Three-Point Streak Tracking ──
// Track consecutive 3PT makes/misses to model the "hot hand" and "cold streak" effects.
//
// Research basis:
//   - Miller & Sanjurjo (2018): The hot hand exists and is measurable (~2-4% FG boost)
//   - Gilovich, Vallone & Tversky (1985): Original "hot hand fallacy" paper,
//     later shown to have a statistical bias (undercounted real streakiness)
//   - NCAA 3PT% average: ~34%. Hot streaks boost to ~38-42%. Cold streaks drop to ~26-30%.
//
// Model: After each 3PT attempt, update a streak counter.
//   - Consecutive makes: counter goes +1, +2, +3, etc.
//   - Consecutive misses: counter goes -1, -2, -3, etc.
//   - The streak counter modifies the NEXT 3PT attempt probability.
//   - Streak effects decay partially each possession (not a permanent shift).

let streak3 = 0;          // Current streak: positive = hot, negative = cold
const STREAK_DECAY = 0.65; // Each non-3PT possession, streak decays by 35%
const HOT_BONUS_PER = 1.2; // +1.2% FG3 per consecutive make (up to ~+4.8% at 4 in a row)
const COLD_PENALTY_PER = 1.0; // -1.0% FG3 per consecutive miss (up to ~-5% at 5 in a row)
const MAX_STREAK_EFFECT = 5.0; // Cap the streak FG3 modifier at ±5%

// Streak also affects shot SELECTION (hot team shoots more 3s, cold team drives more)
const STREAK_RATE_BONUS = 0.8; // +0.8% 3PT rate per consecutive make
const STREAK_RATE_PENALTY = 0.6; // -0.6% 3PT rate per consecutive miss
```

### Step 1.2: Apply streak modifier to 3-point shooting

**Modify the shot attempt block inside the `while` loop.**

**Before the 3PT rate check (currently line 192), add streak-based adjustments:**

```js
// ── Streak-Modified 3PT Rate ──
// Hot teams jack up more 3s (confidence). Cold teams go to the rim more.
const streakRateAdj = streak3 > 0
    ? Math.min(streak3 * STREAK_RATE_BONUS, 4)    // Cap: +4% 3PT rate when hot
    : Math.max(streak3 * STREAK_RATE_PENALTY, -3); // Cap: -3% 3PT rate when cold
```

**Update the 3PT rate check (line 192):**
```js
// BEFORE:
const is3pt = Math.random() * 100 < clamp(rate3 + gs_3RateAdj, 15, 65);

// AFTER:
const is3pt = Math.random() * 100 < clamp(rate3 + gs_3RateAdj + streakRateAdj, 15, 65);
```

**Update the 3PT make check (line 194) to include streak FG bonus:**
```js
// ── Streak-Modified 3PT Accuracy ──
const streakFGAdj = streak3 > 0
    ? Math.min(streak3 * HOT_BONUS_PER, MAX_STREAK_EFFECT)       // Hot: up to +5% FG3
    : Math.max(streak3 * COLD_PENALTY_PER, -MAX_STREAK_EFFECT);  // Cold: down to -5% FG3

// BEFORE:
if (Math.random() * 100 < (fg3 + sFG3 * disruptStarMod + momFG * 0.5 - gs_fgPenalty + disrupt3Mod) * fatigueFGMod) {

// AFTER:
if (Math.random() * 100 < (fg3 + sFG3 * disruptStarMod + momFG * 0.5 - gs_fgPenalty + disrupt3Mod + streakFGAdj) * fatigueFGMod) {
```

### Step 1.3: Update streak counter after each 3PT attempt

**After the 3PT make block (after `points += 3; makes3++; runningLead += 3;`):**
```js
streak3 = Math.min(streak3 + 1, 0) + 1; // Reset cold, add to hot (net: +1 from last)
// More precisely: if was cold, reset to +1. If was hot (+N), go to +(N+1).
// Simplified: streak3 = streak3 > 0 ? streak3 + 1 : 1;
streak3 = streak3 > 0 ? streak3 + 1 : 1;
```

**After the 3PT miss block (after `mom = Math.max(mom - 0.5, -2);`):**
```js
streak3 = streak3 < 0 ? streak3 - 1 : -1; // Reset hot, add to cold
```

### Step 1.4: Decay streak on non-3PT possessions

**After any non-3PT possession outcome (2PT attempts, turnovers, FT trips), apply streak decay:**

**Add at the very END of the while loop (before the closing `}` on line 220), as a catch-all:**
```js
// ── Streak Decay ──
// Non-shooting possessions (TOs, FTs, 2PT attempts) partially reset the 3PT streak
// This prevents unrealistic multi-half streaks
if (!is3pt) {
    streak3 = Math.round(streak3 * STREAK_DECAY);
    // Decay toward zero: +3 → +2, -4 → -3, etc.
}
```

**Note:** For possessions that exit via `continue` (turnovers, bonus FTs, intentional fouls), add streak decay before the `continue`:**
```js
// After each `continue` statement in TO, bonus FT, and intentional foul blocks:
streak3 = Math.round(streak3 * STREAK_DECAY);
```

---

## Phase 2: Team-Specific Streak Volatility

### Step 2.1: Add streak volatility parameter

Some teams are streakier than others. Teams that shoot a high volume of 3s with moderate accuracy tend to have more volatile shooting nights. Teams with high 3PT% but low volume are more consistent.

**File:** `static/index.html`, inside `modelMonteCarlo()`.

**Add after the existing stat computation:**

```js
// ── 3PT Streak Volatility ──
// How streaky is this team's 3-point shooting?
// High volume + moderate accuracy = streaky (Villanova, Marquette)
// Low volume + high accuracy = consistent (Virginia, Wisconsin)
//
// Scale: 0.6 (very consistent) to 1.5 (extremely streaky)

function calc3PTStreakiness(rate3, fg3, tempo) {
    let streakiness = 1.0;

    // High 3PT rate → more streak opportunities
    const rateAboveAvg = (rate3 - 33) / 10; // avg ~33% of shots are 3s
    streakiness += rateAboveAvg * 0.15;

    // Moderate 3PT% (30-36%) is streakier than very high or very low
    // Very high shooters are consistently good; very low shooters are consistently bad
    const fg3Dev = Math.abs(fg3 - 33); // 33% is avg
    streakiness -= fg3Dev * 0.02; // Extreme shooters (good or bad) are less streaky

    // Fast tempo → more shots → more streak opportunities per half
    streakiness += (tempo - 67.5) * 0.01;

    return clamp(streakiness, 0.6, 1.5);
}

const t1Streakiness = calc3PTStreakiness(t1_3RateBase, t1_FG3, t1_Tempo);
const t2Streakiness = calc3PTStreakiness(t2_3RateBase, t2_FG3, t2_Tempo);
```

**Add to `workerParams` object:**
```js
t1Streakiness: t1Streakiness,
t2Streakiness: t2Streakiness,
```

### Step 2.2: Scale streak effects by team streakiness

**File:** `static/mc-worker.js`, inside `simHalf()`.

**Add `streakiness` as a new parameter to the `simHalf` signature:**
```js
function simHalf(halfPoss, fg2, fg3, toPct, orPct, rate3, ftr, ftPct,
                 defStealRate, starUsage, starFG2, starFG3, initMom,
                 benchDepth, isSecondHalf, incomingLead, defProfile,
                 streakiness)  // NEW
```

**Scale the streak constants by team streakiness:**
```js
const teamStreakiness = streakiness || 1.0;
const HOT_BONUS_PER = 1.2 * teamStreakiness;
const COLD_PENALTY_PER = 1.0 * teamStreakiness;
const MAX_STREAK_EFFECT = 5.0 * teamStreakiness;
const STREAK_RATE_BONUS = 0.8 * teamStreakiness;
const STREAK_RATE_PENALTY = 0.6 * teamStreakiness;
```

**Update `simHalf` calls to pass streakiness:**
```js
// In r1 call, add at end:
p.t1Streakiness || 1.0);

// In r2 call, add at end:
p.t2Streakiness || 1.0);
```

---

## Phase 3: Defensive Impact on Opponent Streaks

### Step 3.1: Elite perimeter defense breaks streaks

**File:** `static/mc-worker.js`, inside `simHalf()`.

**The existing `defProfile.perimeter` score should interact with streaks — elite perimeter defenses "cool off" hot shooters faster:**

**Modify the streak decay to factor in defensive pressure:**

```js
// ── Defensive Streak Disruption ──
// Elite perimeter defenses break hot streaks faster
// by contesting shots, switching on screens, and closing out hard
const defStreakDisruption = defP.perimeter * 0.3; // Up to 30% faster streak decay vs elite perimeter D

// Updated streak decay (replace the simple STREAK_DECAY):
const effectiveDecay = STREAK_DECAY + (streak3 > 0 ? defStreakDisruption : 0);
// Hot streaks decay faster vs good defense; cold streaks are unaffected
```

**Replace the streak decay block:**
```js
if (!is3pt) {
    const decay = streak3 > 0 ? (STREAK_DECAY + defStreakDisruption) : STREAK_DECAY;
    streak3 = Math.round(streak3 * (1 - decay + 0.35)); // Net effect: faster cooling vs elite D
}
```

---

## Phase 4: Track Streak Diagnostics

### Step 4.1: Return streak stats from `simHalf()`

**Add tracking variables inside `simHalf()` (near line 54):**
```js
let maxHotStreak = 0;      // Longest hot streak in this half
let maxColdStreak = 0;     // Longest cold streak
let hotPossessions = 0;    // Possessions spent in hot state (streak3 >= 2)
let coldPossessions = 0;   // Possessions spent in cold state (streak3 <= -2)
```

**Update inside the loop after streak counter changes:**
```js
if (streak3 > maxHotStreak) maxHotStreak = streak3;
if (streak3 < -maxColdStreak) maxColdStreak = -streak3;
if (streak3 >= 2) hotPossessions++;
if (streak3 <= -2) coldPossessions++;
```

**Add to `simHalf` return value:**
```js
maxHotStreak, maxColdStreak, hotPossessions, coldPossessions,
```

### Step 4.2: Aggregate streak stats across simulations

**File:** `static/mc-worker.js`, in `self.onmessage` handler.

**Add accumulators:**
```js
let t1TotalHotPoss = 0, t2TotalHotPoss = 0;
let t1TotalColdPoss = 0, t2TotalColdPoss = 0;
let t1MaxHotEver = 0, t2MaxHotEver = 0;
let t1MaxColdEver = 0, t2MaxColdEver = 0;
```

**Inside half loop, after r1/r2:**
```js
t1TotalHotPoss += r1.hotPossessions || 0;
t2TotalHotPoss += r2.hotPossessions || 0;
t1TotalColdPoss += r1.coldPossessions || 0;
t2TotalColdPoss += r2.coldPossessions || 0;
if ((r1.maxHotStreak || 0) > t1MaxHotEver) t1MaxHotEver = r1.maxHotStreak;
if ((r2.maxHotStreak || 0) > t2MaxHotEver) t2MaxHotEver = r2.maxHotStreak;
```

**Add to `self.postMessage`:**
```js
streakStats: {
    t1AvgHotPoss: t1TotalHotPoss / (numSims * 2),
    t2AvgHotPoss: t2TotalHotPoss / (numSims * 2),
    t1AvgColdPoss: t1TotalColdPoss / (numSims * 2),
    t2AvgColdPoss: t2TotalColdPoss / (numSims * 2),
    t1MaxHotStreak: t1MaxHotEver,
    t2MaxHotStreak: t2MaxHotEver,
    t1Streakiness: p.t1Streakiness,
    t2Streakiness: p.t2Streakiness,
},
```

---

## Phase 5: Display Streak Analysis in UI

### Step 5.1: Show streak volatility in the Matchup Predictor

**File:** `static/index.html`

```html
<!-- 3PT Streak Volatility -->
<div class="mt-2">
    <p class="text-xs text-gray-500">
        <i class="fas fa-fire mr-1"></i>
        3PT Streakiness: ${t1Name}
        ${mc.streakStats?.t1Streakiness?.toFixed(2) || '1.00'}x
        (${mc.streakStats?.t1AvgHotPoss?.toFixed(1) || '0'} hot /
         ${mc.streakStats?.t1AvgColdPoss?.toFixed(1) || '0'} cold poss/half) •
        ${t2Name}
        ${mc.streakStats?.t2Streakiness?.toFixed(2) || '1.00'}x
        (${mc.streakStats?.t2AvgHotPoss?.toFixed(1) || '0'} hot /
         ${mc.streakStats?.t2AvgColdPoss?.toFixed(1) || '0'} cold poss/half)
    </p>
</div>
```

---

## Validation Checklist

After implementation, verify the following:

1. **Streak frequency:**
   - Teams should be in a "hot" state (streak3 >= 2) for ~15-25% of 3PT-attempt possessions
   - Teams should be in a "cold" state (streak3 <= -2) for ~15-25% of 3PT-attempt possessions
   - Maximum hot streak in a single half should be 3-5 consecutive makes (occasionally 6-7)

2. **Scoring run distribution:**
   - Compare 8-0 run frequency before/after. Should increase by ~20-30%
   - Compare 15+ point blowout rate: should increase slightly for streaky teams

3. **3PT% variance:**
   - Within-game 3PT% standard deviation should increase by 3-5% compared to independent model
   - Season-level 3PT% averages should be unchanged (streaks are symmetric around the mean)

4. **Interaction with existing systems:**
   - Hot streaks should boost momentum (already connected: `mom = Math.min(mom + 1.5, 3)`)
   - Defensive disruption should counteract hot streaks (Phase 3)
   - Game-state 3PT rate adjustments should stack with streak rate adjustments (desperation + cold = very bad)

5. **Regression test:** Run 10,000 sims for a high-volume 3PT team (e.g., Villanova) vs a low-volume team (e.g., Virginia). Villanova's game-to-game scoring variance should be ~10-15% higher with streaks enabled.

---

## Research Notes

| Finding | Source | Application |
|---|---|---|
| Hot hand exists: +2-4% FG boost after consecutive makes | Miller & Sanjurjo (2018) | `HOT_BONUS_PER = 1.2%` per make |
| Cold streaks are slightly weaker than hot streaks | Bocskocsky et al. (2014) | `COLD_PENALTY_PER = 1.0%` (vs 1.2 for hot) |
| Streak effects decay over ~3-4 non-shooting possessions | NBA tracking data analysis | `STREAK_DECAY = 0.65` |
| Shot selection changes during streaks (+3-5% rate shift) | Arkes (2010) | `STREAK_RATE_BONUS = 0.8%` per make |
| Good defense can "cool off" hot shooters 30% faster | Csapo et al. (2015) | `defStreakDisruption = perimeter * 0.3` |
