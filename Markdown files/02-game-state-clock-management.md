# Plan 2: Game State & Clock Management

**Goal:** Make the Monte Carlo simulation aware of the current score differential and remaining possessions within each half, so that late-game strategy (intentional fouling, shot clock burning, desperation 3s, pace changes) is realistically modeled.

**Impact:** Highest-impact improvement for spread accuracy. Currently the biggest gap in realism — the sim plays the same basketball whether a team is up 20 or down 2 with 2 minutes left.

**Files to modify:**
- `static/mc-worker.js` — `simHalf()` function
- `static/index.html` — `modelMonteCarlo()` parameter setup (lines ~1177-1328)

---

## Phase 1: Track Game Clock State Inside `simHalf()`

### Step 1.1: Add score-tracking parameters to `simHalf` signature

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
                 incomingLead, isSecondHalf)
```

- `incomingLead`: integer, this team's lead entering the half (positive = leading, negative = trailing). For first half, always 0.
- `isSecondHalf`: boolean, enables late-game strategy adjustments.

### Step 1.2: Define game-clock phases

**Add immediately after `let defFouls = 0;` (line 35) inside `simHalf()`:**

```js
// ── Game Clock Phases ──
// Divide each half into phases based on remaining possessions
// ~33 possessions per half is typical
const totalHalfPoss = Math.round(halfPoss);
const PHASE_NORMAL_END = 0.75;     // First 75% of half: normal basketball
const PHASE_LATE_START = 0.75;     // 75-90%: late-half adjustments
const PHASE_CRUNCH_START = 0.90;   // Final 10%: crunch time / desperation

// Running score differential for THIS team relative to opponent
// Updated each possession as points are scored
let runningLead = incomingLead; // Will be updated by caller between halves
```

### Step 1.3: Compute per-possession game-state modifiers

**Add inside the `while` loop, after `possLeft--; possUsed++;` (line 38):**

```js
// ── Game State Awareness ──
const progressPct = possUsed / totalHalfPoss; // 0.0 to 1.0
const isLateHalf = isSecondHalf && progressPct >= PHASE_LATE_START;
const isCrunchTime = isSecondHalf && progressPct >= PHASE_CRUNCH_START;
const possRemaining = possLeft;

// Only apply game-state strategy in the second half
let gs_3RateAdj = 0;      // 3PT rate adjustment
let gs_toPctAdj = 0;      // turnover rate adjustment
let gs_ftrAdj = 0;        // free throw rate adjustment (intentional fouls)
let gs_paceAdj = 0;       // extra possessions (clock burning = fewer, fouling = more)
let gs_fgPenalty = 0;      // FG% penalty from rushed/contested shots

if (isSecondHalf) {
    const deficit = -runningLead; // positive when trailing
    
    if (isCrunchTime && deficit >= 6) {
        // ── DESPERATION MODE ──
        // Trailing by 6+ in final 10% of second half
        // Jack up 3s, play ultra-aggressive, foul intentionally on defense
        const desperationScale = clamp((deficit - 5) / 15, 0, 1); // 0 at deficit=5, 1 at deficit=20
        gs_3RateAdj = 8 + desperationScale * 12;   // +8 to +20% more 3PT attempts
        gs_toPctAdj = 1.5 + desperationScale * 2;  // +1.5 to +3.5% more turnovers (aggressive play)
        gs_fgPenalty = 2 + desperationScale * 3;    // -2 to -5% FG (rushed, contested shots)
    } else if (isCrunchTime && deficit >= 3) {
        // ── URGENT MODE ──
        // Trailing by 3-5 in crunch time
        // More 3s, slightly more aggressive
        gs_3RateAdj = 5;
        gs_toPctAdj = 0.8;
        gs_fgPenalty = 1;
    } else if (isLateHalf && deficit >= 8) {
        // ── CATCHING UP ──
        // Trailing by 8+ in late half (not yet crunch)
        // Push pace, take more 3s
        gs_3RateAdj = 4;
        gs_toPctAdj = 0.5;
    } else if (isCrunchTime && runningLead >= 6) {
        // ── PROTECT LEAD MODE ──
        // Leading by 6+ in crunch time
        // Burn clock, take safe shots, draw fouls
        gs_3RateAdj = -6;            // Fewer 3s, more 2PT/drives
        gs_toPctAdj = -1;            // More careful with ball
        gs_ftrAdj = 4;               // Draw fouls to get to the line
    } else if (isLateHalf && runningLead >= 10) {
        // ── CRUISE CONTROL ──
        // Leading by 10+ in late half
        // Slightly more conservative
        gs_3RateAdj = -3;
        gs_toPctAdj = -0.5;
    }
}
```

### Step 1.4: Apply game-state modifiers to possession outcomes

**Modify the turnover check (currently line 46):**
```js
// BEFORE:
if (Math.random() * 100 < toPct) {

// AFTER:
if (Math.random() * 100 < toPct + gs_toPctAdj) {
```

**Modify the 3PT rate check (currently line 92):**
```js
// BEFORE:
const is3pt = Math.random() * 100 < rate3;

// AFTER:
const is3pt = Math.random() * 100 < clamp(rate3 + gs_3RateAdj, 15, 65);
```

**Modify the FG checks to apply `gs_fgPenalty`:**

**3-point check (currently line 94):**
```js
// BEFORE:
if (Math.random() * 100 < fg3 + sFG3 + momFG * 0.5) {

// AFTER:
if (Math.random() * 100 < fg3 + sFG3 + momFG * 0.5 - gs_fgPenalty) {
```

**2-point check (currently line 107):**
```js
// BEFORE:
if (Math.random() * 100 < fg2 + sFG2 + momFG * 0.7) {

// AFTER:
if (Math.random() * 100 < fg2 + sFG2 + momFG * 0.7 - gs_fgPenalty * 0.5) {
// Note: 2PT penalty is halved because drives to the rim are less affected than jumpers
```

### Step 1.5: Add intentional fouling mechanic in crunch time

**Add after the existing foul/FT logic (after line 89, before `attempts++;`):**

```js
// ── Intentional Fouling (Opponent's Perspective) ──
// When the opposing team is in desperation mode, they foul intentionally
// This gives the leading team extra FT attempts
// We model this from the OFFENSIVE team's perspective:
// if we are LEADING in crunch time and opponent is desperate, we get bonus FTs
if (isCrunchTime && isSecondHalf && runningLead >= 6 && gs_ftrAdj > 0) {
    // Intentional foul — go to the line
    if (Math.random() * 100 < gs_ftrAdj * 6) { // ~24% chance per possession when leading by 6+
        defFouls++;
        let made = 0;
        for (let f = 0; f < 2; f++) {
            ftAtt++;
            if (Math.random() * 100 < ftPct) { points++; ftMade++; made++; }
        }
        // Update running lead
        runningLead += made;
        mom = made > 0 ? Math.min(mom + 0.3, 3) : Math.max(mom - 0.3, -2);
        continue; // possession consumed by intentional foul
    }
}
```

### Step 1.6: Update running score after each possession

**At the end of each possession (before the `while` loop continues), add score tracking.**

**After each scoring event (makes, free throws), update `runningLead`:**

After the 3-point make block (after `points += 3;`):
```js
runningLead += 3;
```

After the 2-point make block (after `points += 2;`):
```js
runningLead += 2;
```

After free throw makes in bonus/shooting foul blocks (after `points++;` in FT sections):
```js
runningLead += 1; // per made FT
```

After transition points scored by opponent (these REDUCE our lead):
```js
// Note: transition points are scored by the OPPONENT (after our turnover)
// We need to track that the opponent scored, reducing our lead
// After the transition scoring block (lines 50-53):
if (r < 0.55) { transitionPts += 2; runningLead -= 2; }
else if (r < 0.70) { transitionPts += 3; runningLead -= 3; }
```

### Step 1.7: Return game-state diagnostics from `simHalf()`

**Add tracking variables at the top of `simHalf` (near line 29):**
```js
let crunchTimePoss = 0;
let desperationPoss = 0;
let intentionalFoulPoss = 0;
```

**Increment inside the loop where appropriate:**
```js
if (isCrunchTime) crunchTimePoss++;
if (isCrunchTime && (-runningLead) >= 6) desperationPoss++;
// (intentionalFoulPoss incremented inside the intentional foul block)
```

**Update the return statement (line 121-122):**
```js
return { points, possUsed, makes2, makes3, tos, ftMade, ftAtt, orebs, attempts,
         transitionPts, momentum: mom, defFouls,
         crunchTimePoss, desperationPoss, intentionalFoulPoss,
         finalLead: runningLead };
```

---

## Phase 2: Wire Up `incomingLead` Between Halves

### Step 2.1: Pass first-half score differential to second-half calls

**File:** `static/mc-worker.js`, inside `self.onmessage` handler.

**Currently the two-half loop (lines 169-223) doesn't pass score info between halves. Modify as follows:**

```js
for (let half = 0; half < 2; half++) {
    const halfPoss = gamePoss / 2;

    // ── Compute incoming lead for each team ──
    // s1 and s2 track cumulative scores for team 1 and team 2
    const t1IncomingLead = half === 0 ? 0 : (s1 - s2);
    const t2IncomingLead = half === 0 ? 0 : (s2 - s1);

    // ... existing halftime adjustment code (lines 172-185) stays here ...

    const r1 = simHalf(halfPoss,
        g_t1_FG2 + t1_FG2Adj + t1StarDeg, g_t1_FG3 + t1StarDeg * 0.7,
        g_t1_TO + t1_TOAdj, g_t1_OR,
        clamp(p.t1_3Rate + t1_3Adj, 20, 55), p.m_t1_FTR, p.t1_FTP,
        p.m_t2StealRate,
        t1SFT ? t1Star.usage * 0.3 : t1Star.usage,
        t1Star.fg2Bonus, t1Star.fg3Bonus,
        t1Mom,
        t1IncomingLead, half === 1);   // NEW params

    const r2 = simHalf(halfPoss,
        g_t2_FG2 + t2_FG2Adj + t2StarDeg, g_t2_FG3 + t2StarDeg * 0.7,
        g_t2_TO + t2_TOAdj, g_t2_OR,
        clamp(p.t2_3Rate + t2_3Adj, 20, 55), p.m_t2_FTR, p.t2_FTP,
        p.m_t1StealRate,
        t2SFT ? t2Star.usage * 0.3 : t2Star.usage,
        t2Star.fg2Bonus, t2Star.fg3Bonus,
        t2Mom,
        t2IncomingLead, half === 1);   // NEW params

    // ... rest of half scoring logic unchanged ...
}
```

### Step 2.2: Remove the existing crude second-half adjustment

**The current second-half 3PT/TO adjustment (lines 172-185) can be REMOVED since it's now superseded by the per-possession game-state system:**

```js
// REMOVE this entire block (lines 172-185):
let t1_3Adj = 0, t2_3Adj = 0, t1_TOAdj = 0, t2_TOAdj = 0;
let t1_FG2Adj = 0, t2_FG2Adj = 0;
if (half === 1) {
    const margin = s1 - s2;
    if (margin > 8) {
        t2_3Adj = Math.min(margin * 0.5, 7);
        t2_TOAdj = Math.min(margin * 0.12, 1.5);
        t1_3Adj = -2; t1_FG2Adj = 1;
    } else if (margin < -8) {
        t1_3Adj = Math.min(-margin * 0.5, 7);
        t1_TOAdj = Math.min(-margin * 0.12, 1.5);
        t2_3Adj = -2; t2_FG2Adj = 1;
    }
}

// REPLACE with just zeroed-out adjustments (the game-state logic is now inside simHalf):
let t1_3Adj = 0, t2_3Adj = 0, t1_TOAdj = 0, t2_TOAdj = 0;
let t1_FG2Adj = 0, t2_FG2Adj = 0;
// Game-state adjustments are now handled per-possession inside simHalf()
```

**Important:** Also update the `simHalf` calls to remove the `+ t1_3Adj`, `+ t1_TOAdj`, `+ t1_FG2Adj` parameters since those are now zero and the logic lives inside `simHalf`. Or simply leave them as `+0` for clarity.

---

## Phase 3: Aggregate Game-State Diagnostics

### Step 3.1: Track game-state stats across simulations

**File:** `static/mc-worker.js`, in `self.onmessage` handler.

**Add accumulators near line 142:**
```js
let totalCrunchPoss = 0, totalDesperationPoss = 0;
let totalIntentionalFouls = 0;
```

**Inside the half loop, after r1/r2:**
```js
totalCrunchPoss += r1.crunchTimePoss + r2.crunchTimePoss;
totalDesperationPoss += r1.desperationPoss + r2.desperationPoss;
totalIntentionalFouls += r1.intentionalFoulPoss + r2.intentionalFoulPoss;
```

**Add to `self.postMessage` (near line 273):**
```js
avgCrunchPoss: totalCrunchPoss / numSims,
avgDesperationPoss: totalDesperationPoss / numSims,
avgIntentionalFouls: totalIntentionalFouls / numSims,
```

---

## Phase 4: Display Game-State Info in UI

### Step 4.1: Show game-state diagnostics in the Matchup Predictor

**File:** `static/index.html`

**In the Simulation Results section (around line 1864-1893), add a new sub-section after the existing margin/range stats:**

```html
<!-- Game State Dynamics -->
<div class="mt-3 pt-3 border-t border-gray-700">
    <p class="text-xs text-gray-500">
        <i class="fas fa-clock mr-1"></i>Game Dynamics:
        Avg crunch-time possessions: ${mc.avgCrunchPoss?.toFixed(1) || '0'} •
        Desperation possessions: ${mc.avgDesperationPoss?.toFixed(1) || '0'} •
        Intentional fouls: ${mc.avgIntentionalFouls?.toFixed(1) || '0'}
    </p>
</div>
```

---

## Phase 5: Edge Case Handling

### Step 5.1: Handle first-half-only awareness

Even though full game-state strategy only activates in the second half (`isSecondHalf === true`), we should still track `runningLead` in the first half for diagnostic purposes. The first-half code path should work correctly with `isSecondHalf = false` — all the `if (isSecondHalf)` guards will skip the strategy adjustments.

### Step 5.2: Prevent runaway scoring from intentional fouling

**Add a safety cap:** If intentional fouls have already added more than 10 FT attempts in a single half, disable further intentional fouling to prevent unrealistic scores:

```js
// Inside the intentional fouling block:
if (intentionalFoulPoss < 6) { // Max 6 intentional foul possessions per half
    // ... existing intentional foul logic ...
    intentionalFoulPoss++;
}
```

### Step 5.3: Prevent `runningLead` from drifting

Since `runningLead` is updated by only one team's perspective but the opponent also scores, we need to account for the fact that we don't see opponent scoring inside our `simHalf`. 

**Solution:** `runningLead` within `simHalf` only tracks THIS team's contribution to the lead. The actual lead is computed between halves by the caller (`s1 - s2`). Within a half, `runningLead` serves as an approximation. This is acceptable because:
- The first-half lead is exact (passed in as `incomingLead`)
- Within the second half, drift is bounded by ~5-10 points
- Game-state triggers use generous thresholds (6+, 8+, 10+) that tolerate this error

**Alternative (more accurate but complex):** Run both team simulations interleaved possession-by-possession rather than independently. This is a larger architectural change and should be considered a future enhancement beyond this plan.

---

## Validation Checklist

After implementation, verify the following:

1. **Trailing team behavior in blowouts:**
   - Run 5000 sims of #1 team vs #300 team
   - The underdog should show higher 3PT rate in the second half (~+5-10%)
   - The underdog should show slightly higher TO rate in the second half
   - Verify by checking `mc.avgDesperationPoss > 0`

2. **Leading team behavior:**
   - The favorite in blowout sims should show lower 3PT rate in late second half
   - Higher FTM in the second half (intentional fouls → free throws)

3. **Close games should be unaffected:**
   - Run 5000 sims of two evenly matched teams
   - `avgDesperationPoss` should be near 0
   - `avgIntentionalFouls` should be near 0
   - Score distributions should be nearly identical to pre-change

4. **Spread distribution should tighten:**
   - Blowout margins should be slightly less extreme (leading team coasts)
   - This should improve ATS accuracy for large spreads

5. **Regression test:** Compare avg margin and win% for 10 matchups before/after. Margins should shift by less than 2 points; win% by less than 3%.

---

## Constants Reference

| Constant | Value | Rationale |
|---|---|---|
| `PHASE_NORMAL_END` | 0.75 | First 75% of half is normal play |
| `PHASE_LATE_START` | 0.75 | Late-half strategy starts at 75% |
| `PHASE_CRUNCH_START` | 0.90 | Final 10% of half is crunch time (~3 min) |
| Desperation 3PT boost | +8 to +20% | Based on observed late-game 3PT rate increase |
| Desperation TO increase | +1.5 to +3.5% | Aggressive play leads to more turnovers |
| Desperation FG penalty | -2 to -5% | Rushed/contested shots are less accurate |
| Protect-lead 3PT reduction | -6% | Leading teams take fewer 3s |
| Intentional foul chance | ~24% per poss | When leading 6+ in crunch time |
| Intentional foul cap | 6 per half | Prevents runaway FT scoring |
| `gs_fgPenalty` 2PT multiplier | 0.5x | Drives less affected than jumpers |
