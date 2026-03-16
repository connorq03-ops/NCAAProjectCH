# Plan 7: Tempo Tug-of-War & Dynamic Pace Control

**Goal:** Replace the single per-game tempo draw (`randNormal(gameTempoCtr, 3.0)`) with an interactive tempo system where each team exerts influence over the game's pace based on their preferred tempo, tempo control ability, and in-game tactical adjustments. Fast-tempo teams facing slow-tempo teams should produce a contested pace that reflects the real-world "tempo tug-of-war" rather than a simple average.

**Impact:** Currently, the simulation draws one tempo number per game and both teams play the same number of possessions. In reality, Virginia (60 possessions preferred) vs Gonzaga (75 possessions preferred) produces a pace closer to 65-68, not the simple average of 67.5 — because Virginia's pack-line defense slows Gonzaga's transition game, but Gonzaga's athletes create some fast breaks anyway. This improvement will:
- Produce more realistic possession counts for tempo-mismatch games
- Create within-game tempo variation (first-half pace differs from second-half pace)
- Model the strategic reality that trailing teams push pace and leading teams slow it down
- Improve total score predictions for over/under accuracy

**Files to modify:**
- `static/mc-worker.js` — `simHalf()` function, `self.onmessage` handler
- `static/index.html` — `modelMonteCarlo()` parameter computation, UI diagnostics

---

## Phase 1: Compute Team Tempo Control Ratings

### Step 1.1: Define tempo control metrics in `modelMonteCarlo()`

**File:** `static/index.html`, inside `modelMonteCarlo()`.

**Add after the existing tempo computation (where `gameTempoCtr` is calculated):**

```js
// ── Tempo Control Ratings ──
// Each team has:
//   1. preferredTempo: Their natural pace (AdjTempo from KenPom)
//   2. tempoControl: How strongly they can impose their pace on opponents
//      - Teams with elite defenses control pace better (they dictate shot clock usage)
//      - Teams with low turnover rates control pace better (fewer chaotic possessions)
//      - Experienced teams control pace better

// Raw preferred tempo (AdjTempo, typically 64-75 possessions per game)
const t1PreferredTempo = t1_Tempo;  // Already available from KenPom data
const t2PreferredTempo = t2_Tempo;
const AVG_TEMPO = 67.5;  // D1 average

// Tempo Control Score: 0.3 (weak control) to 0.7 (elite control)
// Higher = team imposes its preferred pace more effectively
function calcTempoControl(adjDE, toRate, experience, adjTempo, avgTempo) {
    let control = 0.50;  // baseline

    // Elite defenses control pace (they force half-court sets)
    const defEliteness = clamp((100 - adjDE) / 10, -1, 1);  // Lower AdjDE = better defense
    control += defEliteness * 0.06;

    // Low turnover teams control pace (fewer chaotic, rushed possessions)
    const toCarefulness = clamp((AVG_TO - toRate) / 5, -1, 1);
    control += toCarefulness * 0.04;

    // Experienced teams control tempo better
    control += (experience - 1.8) * 0.02;  // avg experience ~1.8 years

    // Extreme tempo teams have stronger control
    // (they specifically game-plan for pace, e.g., Virginia's pack-line)
    const tempoExtremeness = Math.abs(adjTempo - avgTempo);
    control += tempoExtremeness * 0.008;

    return clamp(control, 0.30, 0.70);
}

const t1TempoControl = calcTempoControl(t1_AdjDE, t1_TO, t1_Exp || 1.8, t1_Tempo, AVG_TEMPO);
const t2TempoControl = calcTempoControl(t2_AdjDE, t2_TO, t2_Exp || 1.8, t2_Tempo, AVG_TEMPO);
```

**Add to `workerParams` object:**
```js
t1PreferredTempo: t1PreferredTempo,
t2PreferredTempo: t2PreferredTempo,
t1TempoControl: t1TempoControl,
t2TempoControl: t2TempoControl,
```

### Step 1.2: Replace simple tempo draw with contested tempo calculation

**File:** `static/mc-worker.js`, inside `self.onmessage` handler.

**Replace the current tempo draw (line 328):**
```js
// BEFORE:
const gamePoss = clamp(randNormal(p.gameTempoCtr, 3.0), 55, 85);

// AFTER:
// ── Contested Tempo ──
// Each team pulls toward their preferred pace, weighted by their control rating.
// Random noise represents game-specific chaos (turnovers, fast breaks, foul calls).
const t1Pull = p.t1PreferredTempo || p.gameTempoCtr;
const t2Pull = p.t2PreferredTempo || p.gameTempoCtr;
const t1Ctrl = p.t1TempoControl || 0.50;
const t2Ctrl = p.t2TempoControl || 0.50;

// Weighted average: each team's control score determines how much they influence pace
const totalCtrl = t1Ctrl + t2Ctrl;
const contestedTempo = (t1Pull * t1Ctrl + t2Pull * t2Ctrl) / totalCtrl;

// Add game-level noise (some games are naturally faster/slower due to foul calls, runs, etc.)
const tempoNoise = randNormal(0, 2.5);

// Defensive-leaning teams tend to win the tempo battle slightly more often
// because defense controls pace more than offense (you can slow the game by not
// giving up fast breaks, using shot clock, etc.)
const defTempoEdge = (t1Ctrl > t2Ctrl && t1Pull < t2Pull) ? -0.5 :
                     (t2Ctrl > t1Ctrl && t2Pull < t1Pull) ? -0.5 : 0;

const gamePoss = clamp(contestedTempo + tempoNoise + defTempoEdge, 55, 85);
```

---

## Phase 2: Within-Game Tempo Variation

### Step 2.1: Allow different paces per half

**File:** `static/mc-worker.js`, inside the half loop (around line 384).

**Currently, `halfPoss = gamePoss / 2` is fixed. Add half-specific tempo adjustments:**

```js
// ── Half-Specific Tempo ──
// Second halves tend to have slightly different pace:
//   - Trailing teams push pace → faster second half
//   - Leading teams slow it down → slower second half
//   - First halves are slightly faster (fresh legs, exploration)
let halfTempoAdj = 0;

if (half === 1) {
    const halftimeMargin = s1 - s2;  // Positive = team1 leads
    
    // Trailing team pushes pace, leading team slows it
    // Net effect: if margin is large, pace shifts toward the trailing team's preference
    if (Math.abs(halftimeMargin) > 5) {
        const trailingTeamWantsFast = halftimeMargin > 0
            ? (t2Pull > contestedTempo)   // T2 trailing and wants faster
            : (t1Pull > contestedTempo);  // T1 trailing and wants faster
        
        const paceShift = clamp(Math.abs(halftimeMargin) * 0.15, 0, 3);
        halfTempoAdj = trailingTeamWantsFast ? paceShift : -paceShift * 0.5;
    }
    
    // Close games in second half tend to slow down (more deliberate)
    if (Math.abs(halftimeMargin) <= 3) {
        halfTempoAdj -= 0.8;
    }
}

const halfPoss = Math.round((gamePoss + halfTempoAdj) / 2);
```

### Step 2.2: Add per-possession tempo micro-adjustments in `simHalf()`

**File:** `static/mc-worker.js`, inside `simHalf()`.

**The game-state system (Phase from Plan 2) already adjusts 3PT rate and TO rate in crunch time, but it doesn't directly affect PACE (number of possessions). Add pace-altering mechanics:**

**Add to the existing game-state block (inside `if (isSecondHalf)`, after line 97):**

```js
    // ── Tempo Strategy (Game-State Aware) ──
    // Leading team burns clock → effectively removes possessions
    // Trailing team speeds up → creates extra mini-possessions via quick shots
    if (isCrunchTime && runningLead >= 8) {
        // Leading by 8+ in crunch → burn clock aggressively
        // 15% chance per possession of running a full shot clock (wasted possession)
        if (Math.random() < 0.15) {
            // "Shot clock possession" — take a low-quality shot at buzzer
            if (Math.random() * 100 < fg2 * 0.60) { // 60% of normal FG%
                points += 2; makes2++; runningLead += 2;
                mom = Math.min(mom + 0.3, 3);
            }
            continue; // Possession consumed regardless
        }
    } else if (isCrunchTime && (-runningLead) >= 6) {
        // Trailing by 6+ in crunch → quick shots, push pace
        // 10% chance of a "fast possession" that creates an extra possession
        if (Math.random() < 0.10) {
            possLeft++; // Quick shot → bonus possession
        }
    }
```

---

## Phase 3: Tempo Diagnostics and Tracking

### Step 3.1: Track tempo stats across simulations

**File:** `static/mc-worker.js`, in `self.onmessage` handler.

**Add accumulators near line 318:**
```js
let totalContestedTempo = 0;
let tempoVariance = [];
let h1TempoTotal = 0, h2TempoTotal = 0;
```

**Inside the simulation loop, after computing gamePoss:**
```js
totalContestedTempo += gamePoss;
tempoVariance.push(gamePoss);
```

**Inside the half loop, track per-half tempos:**
```js
if (half === 0) h1TempoTotal += halfPoss * 2; // Convert half poss to full-game equivalent
else h2TempoTotal += halfPoss * 2;
```

### Step 3.2: Include tempo stats in worker response

**Add to `self.postMessage` (near line 540):**
```js
tempoStats: {
    avgContested: totalContestedTempo / numSims,
    t1Preferred: t1Pull,
    t2Preferred: t2Pull,
    tempoSpread: tempoVariance.length > 1 ?
        Math.sqrt(tempoVariance.reduce((sum, t) => sum + Math.pow(t - totalContestedTempo / numSims, 2), 0) / numSims)
        : 0,
    avgH1Tempo: h1TempoTotal / numSims,
    avgH2Tempo: h2TempoTotal / numSims,
    t1TempoControl: t1Ctrl,
    t2TempoControl: t2Ctrl,
},
```

---

## Phase 4: Display Tempo Analysis in UI

### Step 4.1: Show tempo tug-of-war in the Matchup Predictor

**File:** `static/index.html`

**In the Simulation Results section, add a tempo analysis widget:**

```html
<!-- Tempo Tug-of-War -->
<div class="mt-3 pt-3 border-t border-gray-700">
    <p class="text-xs text-gray-500 mb-2">
        <i class="fas fa-tachometer-alt mr-1"></i>Tempo Battle
    </p>
    <div class="flex items-center justify-between text-xs">
        <span class="text-blue-400">${t1Name}: ${mc.tempoStats?.t1Preferred?.toFixed(1)} pref</span>
        <span class="font-bold text-yellow-400">
            ${mc.tempoStats?.avgContested?.toFixed(1)} actual
        </span>
        <span class="text-red-400">${t2Name}: ${mc.tempoStats?.t2Preferred?.toFixed(1)} pref</span>
    </div>
    <div class="flex items-center justify-between text-xs mt-1 text-gray-500">
        <span>Control: ${(mc.tempoStats?.t1TempoControl * 100)?.toFixed(0)}%</span>
        <span>H1: ${mc.tempoStats?.avgH1Tempo?.toFixed(1)} • H2: ${mc.tempoStats?.avgH2Tempo?.toFixed(1)}</span>
        <span>Control: ${(mc.tempoStats?.t2TempoControl * 100)?.toFixed(0)}%</span>
    </div>
</div>
```

---

## Phase 5: Tempo-Mismatch Variance Effects

### Step 5.1: Tempo mismatches increase game variance

When two teams with very different preferred tempos play, the game tends to be more chaotic — neither team is in its comfort zone. This should increase the volatility modifier for both teams.

**File:** `static/mc-worker.js`, inside the simulation loop, after contested tempo calculation:**

```js
// ── Tempo Mismatch Chaos ──
// Large tempo differential = both teams out of comfort zone = higher variance
const tempoMismatch = Math.abs(t1Pull - t2Pull);
const mismatchChaos = tempoMismatch > 6 ? clamp((tempoMismatch - 6) * 0.008, 0, 0.04) : 0;
// Apply as extra volatility to game-level shooting swings
g_t1_FG2 += randNormal(0, mismatchChaos * 15);
g_t2_FG2 += randNormal(0, mismatchChaos * 15);
g_t1_FG3 += randNormal(0, mismatchChaos * 10);
g_t2_FG3 += randNormal(0, mismatchChaos * 10);
```

### Step 5.2: Slow-tempo teams get a defensive boost in low-possession games

Teams that successfully control pace to a low number of possessions get a slight defensive advantage — they've forced the opponent into their style of play.

**File:** `static/mc-worker.js`, after gamePoss is computed:**

```js
// ── Tempo Winner Bonus ──
// If the game pace is close to one team's preferred pace, that team gets a small edge
const t1TempoDelta = Math.abs(gamePoss - t1Pull);
const t2TempoDelta = Math.abs(gamePoss - t2Pull);
const tempoWinnerBonus = 0.004; // ~0.4% FG bonus for playing at your pace

if (t1TempoDelta < t2TempoDelta - 2) {
    // T1 is closer to their pace → they're comfortable, T2 is not
    g_t1_FG2 += tempoWinnerBonus * 100 * 0.5;
    g_t2_FG2 -= tempoWinnerBonus * 100 * 0.3;
} else if (t2TempoDelta < t1TempoDelta - 2) {
    g_t2_FG2 += tempoWinnerBonus * 100 * 0.5;
    g_t1_FG2 -= tempoWinnerBonus * 100 * 0.3;
}
```

---

## Validation Checklist

After implementation, verify the following:

1. **Tempo-mismatch games should show contested pace:**
   - Virginia (60 pref) vs Gonzaga (75 pref): actual pace should be ~65-68, not 67.5
   - If Virginia has higher tempo control, pace should lean lower (~64-66)

2. **Within-game tempo variation:**
   - H2 pace should differ from H1 in blowouts (trailing team speeds up)
   - Close games in H2 should be ~1-2 possessions slower

3. **Total score predictions:**
   - Games involving slow-tempo teams should have lower O/U predictions
   - Tempo-mismatch games should have wider total score variance

4. **Regression test:** Run 10,000 sims for Virginia vs Gonzaga. Average pace should be 65-68, not 67.5. Score totals should be 125-135, not the simple average of their typical outputs.

---

## Constants Reference

| Constant | Value | Rationale |
|---|---|---|
| `tempoControl` range | 0.30-0.70 | Weakest to strongest pace control ability |
| `tempoNoise` SD | 2.5 | Game-level pace variance (~4-5 possession swing) |
| `defTempoEdge` | -0.5 | Defensive-minded teams win tempo battles slightly more often |
| `halfTempoAdj` max | 3.0 | Up to 3 extra possessions in H2 due to pace pushing |
| `mismatchChaos` max | 0.04 | 4% extra FG variance in extreme tempo mismatches |
| `tempoWinnerBonus` | 0.004 | ~0.4% FG boost for the team playing at their preferred pace |
