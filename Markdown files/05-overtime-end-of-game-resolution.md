# Plan 5: Overtime & End-of-Game Resolution

**Goal:** Replace the current tie-splitting shortcut (ties counted as 0.5 wins each) with actual overtime simulation. When a game ends within ±2 points, run a 5-possession OT period with distinct OT dynamics (elevated foul rates, fatigue, last-possession advantage). This produces realistic 1-2 point margins and properly calibrates the tails of the spread distribution.

**Impact:** Affects ~5-6% of simulated games. Improves margin distribution realism (currently can't produce OT-style final scores), close-game calibration, and spread prediction accuracy for tight matchups.

**Files to modify:**
- `static/mc-worker.js` — main simulation loop and new `simOvertime()` function
- `static/index.html` — minor: display OT stats in matchup UI

---

## Phase 1: Create the Overtime Simulation Function

### Step 1.1: Define `simOvertime()` function

**File:** `static/mc-worker.js`

**Add a new function after `simHalf()` (after line 123):**

```js
/**
 * Simulate a single overtime period.
 * OT has distinct dynamics vs regulation:
 *   - 5 possessions per team (representing a 5-minute OT period)
 *   - Elevated foul rate (both teams in bonus immediately)
 *   - Fatigue penalty on shooting (starters have played 40+ min)
 *   - Slightly higher turnover rate (pressure, fatigue)
 *   - Last-possession advantage for trailing team
 *
 * @param {number} fg2 - base FG2%
 * @param {number} fg3 - base FG3%
 * @param {number} toPct - base turnover %
 * @param {number} orPct - base offensive rebound %
 * @param {number} rate3 - 3PT attempt rate
 * @param {number} ftr - free throw rate
 * @param {number} ftPct - free throw %
 * @param {number} defStealRate - opponent's steal rate
 * @param {number} starUsage - star player usage rate
 * @param {number} starFG2 - star FG2 bonus
 * @param {number} starFG3 - star FG3 bonus
 * @param {number} momentum - carry-over momentum from regulation
 * @param {number} otNumber - which OT period (1, 2, 3...) — fatigue compounds
 * @returns {Object} { points, possUsed, ftMade, ftAtt, makes2, makes3, tos }
 */
function simOvertime(fg2, fg3, toPct, orPct, rate3, ftr, ftPct,
                     defStealRate, starUsage, starFG2, starFG3, momentum,
                     otNumber) {
    const OT_POSSESSIONS = 5;
    let points = 0, possUsed = 0;
    let makes2 = 0, makes3 = 0, tos = 0;
    let ftMade = 0, ftAtt = 0;
    let mom = momentum * 0.5; // Momentum partially resets between periods

    // ── OT Fatigue Penalty ──
    // Players have been playing 40+ minutes. Each additional OT compounds fatigue.
    const otFatiguePenalty = 0.04 + (otNumber - 1) * 0.025; // 4% in 1st OT, 6.5% in 2nd, 9% in 3rd
    const fatigueFGMod = 1 - otFatiguePenalty;
    const fatigueTOMod = 1 + otFatiguePenalty * 0.6;

    // ── OT Foul Dynamics ──
    // Both teams are in the bonus from the start of OT
    // Higher FT rate, more cautious play
    const otFTRBoost = 8; // +8% FTR in OT
    const effectiveFTR = ftr + otFTRBoost;

    let possLeft = OT_POSSESSIONS;

    while (possLeft > 0) {
        possLeft--;
        possUsed++;

        const isStar = Math.random() < starUsage;
        const sFG2 = isStar ? starFG2 * fatigueFGMod : 0; // Stars are also fatigued
        const sFG3 = isStar ? starFG3 * fatigueFGMod : 0;
        const momFG = mom * 0.3; // Reduced momentum effect in OT (tighter defense)

        // Turnover check (elevated in OT)
        if (Math.random() * 100 < toPct * fatigueTOMod) {
            tos++;
            mom = Math.max(mom - 1, -2);
            continue;
        }

        // Free throw attempt (both teams in bonus — higher rate)
        if (Math.random() < effectiveFTR / 100 * 0.45) {
            const numFTs = Math.random() < 0.20 ? 3 : 2;
            let made = 0;
            for (let f = 0; f < numFTs; f++) {
                ftAtt++;
                // Clutch FT: slight penalty under pressure
                const clutchFTPct = ftPct - 2; // -2% FT in OT pressure
                if (Math.random() * 100 < clutchFTPct) { points++; ftMade++; made++; }
            }
            mom = made > 0 ? Math.min(mom + 0.5, 2) : Math.max(mom - 0.5, -2);
            continue;
        }

        // Shot attempt
        const is3pt = Math.random() * 100 < rate3;
        if (is3pt) {
            if (Math.random() * 100 < (fg3 + sFG3 + momFG * 0.5) * fatigueFGMod) {
                points += 3; makes3++;
                mom = Math.min(mom + 1.5, 2);
            } else {
                mom = Math.max(mom - 0.5, -2);
                if (Math.random() * 100 < orPct * 0.75) { possLeft++; } // Slightly fewer ORebs in OT
            }
        } else {
            if (Math.random() * 100 < (fg2 + sFG2 + momFG * 0.7) * fatigueFGMod) {
                points += 2; makes2++;
                mom = Math.min(mom + 1, 2);
            } else {
                mom = Math.max(mom - 0.5, -2);
                if (Math.random() * 100 < orPct * 0.90) { possLeft++; }
            }
        }
    }

    return { points, possUsed, makes2, makes3, tos, ftMade, ftAtt };
}
```

---

## Phase 2: Integrate OT into the Main Simulation Loop

### Step 2.1: Detect near-ties and trigger OT

**File:** `static/mc-worker.js`, inside `self.onmessage` handler.

**Currently (lines 225-258), after both halves are simulated, the code applies KenPom calibration, adjustments, and then records the result. We need to add OT logic AFTER the final score computation but BEFORE recording the result.**

**Locate the section after the clutch adjustments (lines 233-240):**
```js
const rawMargin = Math.abs(s1 - s2);
if (rawMargin <= 6) {
    const clutchScale = 1 - (rawMargin / 6);
    s1 += p.coachEdge * clutchScale;
    s2 -= p.coachEdge * clutchScale;
    s1 += p.ftClutchEdge * clutchScale * 0.5;
    s2 -= p.ftClutchEdge * clutchScale * 0.5;
}
```

**Add OT logic immediately AFTER this block, BEFORE the result recording (line 242):**

```js
        // ── OVERTIME RESOLUTION ──
        // If the game is within 2 points (effective tie), simulate overtime
        // In real CBB, ~5-6% of games go to OT
        let otPeriods = 0;
        const MAX_OT = 4; // Safety cap: max 4 OT periods

        while (Math.abs(s1 - s2) <= 2 && otPeriods < MAX_OT) {
            otPeriods++;

            // Determine which team gets last possession in OT
            // Home team (or higher-seeded team) typically gets strategic advantage
            // We alternate: odd OT periods favor t1, even favor t2
            const t1LastPoss = (otPeriods % 2 === 1);

            // Simulate OT for both teams
            const ot1 = simOvertime(
                g_t1_FG2, g_t1_FG3, g_t1_TO, g_t1_OR,
                p.t1_3Rate, p.m_t1_FTR, p.t1_FTP,
                p.m_t2StealRate, t1Star.usage, t1Star.fg2Bonus, t1Star.fg3Bonus,
                t1Mom, otPeriods
            );

            const ot2 = simOvertime(
                g_t2_FG2, g_t2_FG3, g_t2_TO, g_t2_OR,
                p.t2_3Rate, p.m_t2_FTR, p.t2_FTP,
                p.m_t1StealRate, t2Star.usage, t2Star.fg2Bonus, t2Star.fg3Bonus,
                t2Mom, otPeriods
            );

            s1 += ot1.points;
            s2 += ot2.points;

            // Last-possession advantage: the team with last possession
            // gets a small bonus (~0.8 points) representing the ability to
            // hold for a final shot
            if (Math.abs(s1 - s2) <= 1) {
                if (t1LastPoss) {
                    // T1 holds for last shot
                    const lastShotMade = Math.random() < 0.38; // ~38% last-shot conversion
                    if (lastShotMade) {
                        const is3 = Math.random() < 0.30; // 30% chance it's a 3
                        s1 += is3 ? 3 : 2;
                    }
                } else {
                    const lastShotMade = Math.random() < 0.38;
                    if (lastShotMade) {
                        const is3 = Math.random() < 0.30;
                        s2 += is3 ? 3 : 2;
                    }
                }
            }

            // Update momentum for potential additional OT
            t1Mom = ot1.points > ot2.points ? 1 : ot1.points < ot2.points ? -1 : 0;
            t2Mom = -t1Mom;

            // Accumulate OT stats into game totals
            gP1 += ot1.possUsed; gP2 += ot2.possUsed;
            g2_1 += ot1.makes2; g2_2 += ot2.makes2;
            g3_1 += ot1.makes3; g3_2 += ot2.makes3;
            gTO1 += ot1.tos; gTO2 += ot2.tos;
            gFT1 += ot1.ftMade; gFT2 += ot2.ftMade;
        }

        // If still tied after MAX_OT, coin flip (extremely rare, <0.01% of sims)
        if (Math.abs(s1 - s2) < 0.5) {
            if (Math.random() < 0.5) s1 += 1;
            else s2 += 1;
        }
```

### Step 2.2: Track OT statistics

**Add accumulators near line 142:**
```js
let totalOTPeriods = 0;
let gamesWithOT = 0;
let doubleOTGames = 0;
let tripleOTGames = 0;
```

**Inside the simulation loop, after OT resolution:**
```js
if (otPeriods > 0) {
    gamesWithOT++;
    totalOTPeriods += otPeriods;
    if (otPeriods >= 2) doubleOTGames++;
    if (otPeriods >= 3) tripleOTGames++;
}
```

### Step 2.3: Update win/loss recording

**The current tie handling (lines 246-248) needs updating:**

```js
// BEFORE:
if (s1 > s2) { t1Wins++; if (!p.t1Favored) upsets++; }
else if (s2 > s1) { t2Wins++; if (p.t1Favored) upsets++; }
else ties++;

// AFTER:
// With OT, true ties should be extremely rare (only from MAX_OT safety cap)
if (s1 > s2) { t1Wins++; if (!p.t1Favored) upsets++; }
else if (s2 > s1) { t2Wins++; if (p.t1Favored) upsets++; }
else { ties++; } // Should happen < 0.01% of the time with OT
```

### Step 2.4: Update win probability calculation

**The current formula (line 261) splits ties 50/50:**
```js
// BEFORE:
const t1WinPct = (t1Wins + ties * 0.5) / numSims;

// AFTER (ties are now negligible, but keep the formula for safety):
const t1WinPct = (t1Wins + ties * 0.5) / numSims;
// No change needed — the formula still works, but ties will now be ~0 instead of ~5%
```

---

## Phase 3: Add OT Stats to Worker Response

### Step 3.1: Include OT diagnostics in postMessage

**File:** `static/mc-worker.js`, add to `self.postMessage` (near line 273):**

```js
otStats: {
    gamesWithOT: gamesWithOT,
    otRate: gamesWithOT / numSims,
    avgOTPeriods: gamesWithOT > 0 ? totalOTPeriods / gamesWithOT : 0,
    doubleOTGames: doubleOTGames,
    tripleOTGames: tripleOTGames,
    doubleOTRate: doubleOTGames / numSims,
},
```

---

## Phase 4: Display OT Stats in UI

### Step 4.1: Show OT rate in the Simulation Results section

**File:** `static/index.html`

**In the Simulation Results section (around line 1864-1893), add after the existing blowouts/close/upsets row:**

```html
<!-- Overtime Stats -->
<div class="mt-2">
    <p class="text-xs text-gray-500">
        <i class="fas fa-clock mr-1"></i>
        OT Rate: ${((mc.otStats?.otRate || 0) * 100).toFixed(1)}%
        ${mc.otStats?.gamesWithOT > 0 ? 
            `(${mc.otStats.gamesWithOT} games • avg ${mc.otStats.avgOTPeriods?.toFixed(1)} OT periods` +
            (mc.otStats.doubleOTGames > 0 ? ` • ${mc.otStats.doubleOTGames} 2OT` : '') +
            (mc.otStats.tripleOTGames > 0 ? ` • ${mc.otStats.tripleOTGames} 3OT+` : '') +
            ')'
            : ''}
    </p>
</div>
```

### Step 4.2: Update close game percentage display

**The current close game threshold is ±5 points (line 250). This should be updated to clarify it includes OT results:**

```js
// No code change needed, but the label in the UI (line 1883) should note OT:
// "Close (≤5)" → "Close (≤5, incl. OT)"
```

**Update the display text (line 1883):**
```html
<p class="text-gray-500">Close (≤5, incl. OT)</p>
```

---

## Phase 5: Calibrate OT Frequency

### Step 5.1: Validate OT rate against real data

**Expected OT rate:** ~5-6% of NCAA men's basketball games go to overtime. After implementation, verify that the sim produces OT rates in this range.

**If OT rate is too high (>8%):**
- Tighten the OT trigger threshold from ±2 to ±1:
  ```js
  while (Math.abs(s1 - s2) <= 1 && otPeriods < MAX_OT) {
  ```

**If OT rate is too low (<3%):**
- Widen the OT trigger threshold to ±3:
  ```js
  while (Math.abs(s1 - s2) <= 3 && otPeriods < MAX_OT) {
  ```

### Step 5.2: Validate OT score additions

**Expected OT scoring:** Teams average ~12-15 points per OT period (combined). Each team should average ~6-8 points per OT.

**Check:** After running 5000 sims, the average total OT score per OT game should be 12-16 points. If too high, reduce `OT_POSSESSIONS` to 4. If too low, increase to 6.

### Step 5.3: Multi-OT frequency

**Expected rates:**
- Single OT: ~4-5% of games
- Double OT: ~0.3-0.5% of games
- Triple+ OT: <0.1% of games

**If double OT rate is too high:** The OT resolution (last-possession mechanic) should prevent most ties from persisting. If double OT rate exceeds 1%, increase `lastShotMade` probability from 0.38 to 0.45.

---

## Phase 6: Edge Case Handling

### Step 6.1: Score rounding for OT trigger

**The current simulation produces floating-point scores (due to the KenPom calibration blend on lines 225-228). The OT trigger uses `Math.abs(s1 - s2) <= 2`, which works correctly with floats — a margin of 1.7 still triggers OT.**

**However, final reported scores should be integers. Add rounding AFTER OT resolution:**

```js
// After OT resolution and before recording results:
s1 = Math.round(s1);
s2 = Math.round(s2);

// Handle the case where rounding creates a tie
if (s1 === s2) {
    if (Math.random() < 0.5) s1++;
    else s2++;
}
```

### Step 6.2: Prevent infinite OT loops

**The `MAX_OT = 4` cap prevents infinite loops. In the extremely rare case that 4 OT periods don't resolve the game, the coin flip at the end handles it. Log this for diagnostics:**

```js
// After the coin flip fallback:
// (No actual logging in the worker, but track it)
// The `ties` counter will capture any remaining ties
```

### Step 6.3: Variable access for OT

**The OT simulation needs access to game-level adjusted stats (`g_t1_FG2`, `g_t1_FG3`, etc.) which are scoped inside the simulation loop. This is fine — OT is called within the same loop iteration.**

**OT also needs `t1Star` and `t2Star` objects, `t1Mom` and `t2Mom` momentum values, and the half-loop tracking variables (`gP1`, `g2_1`, etc.). All of these are already in scope.**

---

## Validation Checklist

After implementation, verify the following:

1. **OT frequency:**
   - Run 10,000 sims of two evenly matched teams (AdjEM ±1)
   - OT rate should be 4-8%
   - Double OT rate should be < 1%

2. **OT frequency for mismatches:**
   - Run 10,000 sims of #1 vs #300
   - OT rate should be < 1% (blowouts rarely go to OT)

3. **Margin distribution:**
   - Before this change: margins of exactly 0 appear in ~5% of sims (recorded as ties)
   - After this change: margins of exactly 0 should be < 0.1%
   - Margins of 1-3 should appear more frequently (OT games producing tight finishes)

4. **Score realism:**
   - Average scores should increase by < 1 point (OT adds a few points to ~5% of games)
   - Total score standard deviation should decrease very slightly (OT compresses some formerly tied games into 1-3 point margins instead of splitting them)

5. **Win probability unchanged:**
   - For evenly matched teams, win% should still be ~50/50
   - The OT mechanic should not systematically favor either team
   - Verify by comparing t1WinPct before and after: should differ by < 0.5%

6. **Last-possession mechanics:**
   - The team with last possession should win OT slightly more often
   - This advantage should alternate between teams across OT periods (odd/even)
   - Net effect should be close to neutral over many sims

7. **Performance impact:**
   - OT only runs for ~5-6% of sims, and each OT is 5 possessions
   - Total simulation time should increase by < 3%

---

## Constants Reference

| Constant | Value | Rationale |
|---|---|---|
| `OT_POSSESSIONS` | 5 | 5-minute OT period ≈ 5 possessions per team |
| OT trigger threshold | ±2 points | Games within 2 points at end of regulation go to OT |
| `MAX_OT` | 4 | Safety cap; 4+ OT is extraordinarily rare |
| `otFatiguePenalty` (1st OT) | 4% | Players at 40+ min show ~4% FG% drop |
| OT fatigue compound | +2.5% per OT | Each additional OT adds more fatigue |
| `otFTRBoost` | +8% | Both teams in bonus from OT start |
| Clutch FT penalty | -2% | Pressure FTs are slightly worse |
| Momentum carry-over | 50% | Momentum partially resets between periods |
| Last-shot conversion | 38% | Based on observed last-possession efficiency |
| Last-shot 3PT rate | 30% | ~30% of buzzer-beater attempts are 3s |
| OReb rate reduction (OT) | 75-90% of regulation | Fewer ORebs in fatigued, tight play |
| Momentum cap in OT | ±2 | Tighter momentum range than regulation (±3) |
