// Monte Carlo Simulation Web Worker
// Runs possession-level two-half basketball simulation off the main thread

function randNormal(mean, sd) {
    let u = 0, v = 0;
    while (u === 0) u = Math.random();
    while (v === 0) v = Math.random();
    return mean + sd * Math.sqrt(-2 * Math.log(u)) * Math.cos(2 * Math.PI * v);
}

function clamp(val, min, max) { return Math.max(min, Math.min(max, val)); }

function generateGameStyle(baseVolatility, styleBias) {
    const interiorAxis = randNormal(styleBias || 0, 1.0) * baseVolatility;
    const disciplineAxis = randNormal(0, 1.0) * baseVolatility;
    const residualSD = 0.8;
    return {
        fg2Adj: interiorAxis * 1.8 - disciplineAxis * 0.5 + randNormal(0, residualSD),
        fg3Adj: -interiorAxis * 1.2 - disciplineAxis * 0.4 + randNormal(0, residualSD),
        toAdj: interiorAxis * 0.3 + disciplineAxis * 1.5 + randNormal(0, residualSD * 0.5),
        orAdj: interiorAxis * 1.0 + disciplineAxis * 0.6 + randNormal(0, residualSD),
        rate3Adj: -interiorAxis * 2.5 + randNormal(0, residualSD * 0.5),
        ftrAdj: interiorAxis * 1.5 + randNormal(0, residualSD * 0.3),
        styleLabel: interiorAxis > 0.5 ? 'interior' : interiorAxis < -0.5 ? 'perimeter' : 'balanced',
        disciplineLabel: disciplineAxis > 0.5 ? 'sloppy' : disciplineAxis < -0.5 ? 'disciplined' : 'neutral',
    };
}

function calcStarImpact(stars) {
    if (!stars || stars.length === 0) return { usage: 0, fg2Bonus: 0, fg3Bonus: 0 };
    let totalUsage = 0, wFG2 = 0, wFG3 = 0;
    for (const s of stars) {
        const imp = s.impact || 5;
        const u = imp >= 9 ? 0.30 : imp >= 8 ? 0.22 : imp >= 7 ? 0.15 : 0.08;
        totalUsage += u;
        wFG2 += (imp >= 9 ? 3.0 : imp >= 8 ? 2.0 : 1.0) * u;
        wFG3 += (imp >= 9 ? 2.0 : imp >= 8 ? 1.5 : 0.5) * u;
    }
    const cap = Math.min(totalUsage, 0.45);
    return { usage: cap, fg2Bonus: totalUsage > 0 ? wFG2 / totalUsage : 0, fg3Bonus: totalUsage > 0 ? wFG3 / totalUsage : 0 };
}

function simHalf(cfg) {
    const { halfPoss, fg2, fg3, toPct, orPct, rate3, ftr, ftPct,
            defStealRate, starUsage, starFG2, starFG3, initMom,
            benchDepth, isSecondHalf, incomingLead, defProfile,
            starFoulState, starFoulProneness, foulClimate, streakiness } = cfg;
    let points = 0, possUsed = 0, makes2 = 0, makes3 = 0, tos = 0;
    let ftMade = 0, ftAtt = 0, orebs = 0, attempts = 0;
    let transitionPts = 0;
    let possLeft = Math.round(halfPoss);
    const maxPoss = possLeft + 10;
    let mom = initMom;
    let defFouls = 0;
    let totalFatiguePenalty = 0;
    let restPossCount = 0;

    // ── Game Clock Phases ──
    const totalHalfPoss = Math.round(halfPoss);
    const PHASE_LATE_START = 0.75;
    const PHASE_CRUNCH_START = 0.90;
    let runningLead = incomingLead || 0;
    let crunchTimePoss = 0;
    let desperationPoss = 0;
    let intentionalFoulPoss = 0;

    // ── Plan 09: Referee Foul Climate ──
    const refClimate = foulClimate || 1.0;
    let bonusReachedPoss = -1;

    // ── Plan 06: Player Foul Trouble ──
    const MAX_FOULS = 5;
    const FOUL_SIT_THRESHOLD_H1 = 2;
    const FOUL_SIT_THRESHOLD_H2 = 4;
    const FOUL_RETURN_PCT = 0.80;
    let starFouls = starFoulState ? starFoulState.fouls : 0;
    let starIsSitting = starFoulState ? starFoulState.isSitting : false;
    let starSatPoss = 0;
    let starFouledOut = false;
    // Scale by sqrt(refClimate) — dampened to avoid compounding with Plan 09's defFoul scaling
    const baseStarFoulRate = (0.035 + (starFoulProneness || 0) * 0.02) * Math.sqrt(refClimate);

    // ── Plan 08: Three-Point Streak Tracking ──
    const teamStreakiness = streakiness || 1.0;
    let streak3 = 0;
    const STREAK_DECAY = 0.65;
    const HOT_BONUS_PER = 1.2 * teamStreakiness;
    const COLD_PENALTY_PER = 1.0 * teamStreakiness;
    const MAX_STREAK_EFFECT = 5.0 * teamStreakiness;
    const STREAK_RATE_BONUS = 0.8 * teamStreakiness;
    const STREAK_RATE_PENALTY = 0.6 * teamStreakiness;
    let maxHotStreak = 0, maxColdStreak = 0;
    let hotPossessions = 0, coldPossessions = 0;

    while (possLeft > 0 && possUsed < maxPoss) {
        possLeft--; possUsed++;

        // ── Game State Awareness ──
        const progressPct = possUsed / totalHalfPoss;
        const isLateHalf = isSecondHalf && progressPct >= PHASE_LATE_START;
        const isCrunchTime = isSecondHalf && progressPct >= PHASE_CRUNCH_START;
        if (isCrunchTime) crunchTimePoss++;

        let gs_3RateAdj = 0, gs_toPctAdj = 0, gs_ftrAdj = 0, gs_fgPenalty = 0;
        if (isSecondHalf) {
            const deficit = -runningLead;
            if (isCrunchTime && deficit >= 6) {
                const desperationScale = clamp((deficit - 5) / 15, 0, 1);
                gs_3RateAdj = 8 + desperationScale * 12;
                gs_toPctAdj = 1.5 + desperationScale * 2;
                gs_fgPenalty = 2 + desperationScale * 3;
                desperationPoss++;
            } else if (isCrunchTime && deficit >= 3) {
                gs_3RateAdj = 5;
                gs_toPctAdj = 0.8;
                gs_fgPenalty = 1;
            } else if (isLateHalf && deficit >= 8) {
                gs_3RateAdj = 4;
                gs_toPctAdj = 0.5;
            } else if (isCrunchTime && runningLead >= 6) {
                gs_3RateAdj = -6;
                gs_toPctAdj = -1;
                gs_ftrAdj = 4;
                // Plan 07: Leading by 8+ in crunch → burn clock aggressively
                if (runningLead >= 8 && Math.random() < 0.15) {
                    if (Math.random() * 100 < fg2 * 0.60) {
                        points += 2; makes2++; runningLead += 2;
                        mom = Math.min(mom + 0.3, 3);
                    }
                    possLeft--; possUsed++;
                    streak3 = Math.round(streak3 * STREAK_DECAY);
                    continue;
                }
            } else if (isCrunchTime && (-runningLead) >= 6) {
                // Plan 07: Trailing by 6+ in crunch → quick shots, push pace
                if (Math.random() < 0.10) { possLeft++; }
            } else if (isLateHalf && runningLead >= 10) {
                gs_3RateAdj = -3;
                gs_toPctAdj = -0.5;
            }
        }

        // ── Fatigue Curve ──
        const fatigueOnsetPct = 0.55 + (benchDepth / 100) * 0.15;
        const fatigueProgress = Math.max(0, (possUsed / halfPoss) - fatigueOnsetPct) / (1 - fatigueOnsetPct);
        const halfMultiplier = isSecondHalf ? 1.4 : 1.0;
        const fatiguePenalty = fatigueProgress * halfMultiplier * 0.06;
        const fatigueFGMod = 1 - fatiguePenalty;
        const fatigueTOMod = 1 + fatiguePenalty * 0.5;
        totalFatiguePenalty += fatiguePenalty;

        // ── Plan 06: Star Foul Trouble Check ──
        if (!starIsSitting && !starFouledOut && Math.random() < baseStarFoulRate) {
            starFouls++;
            if (starFouls >= MAX_FOULS) {
                starFouledOut = true;
                starIsSitting = true;
            } else if (!isSecondHalf && starFouls >= FOUL_SIT_THRESHOLD_H1) {
                starIsSitting = true;
            } else if (isSecondHalf && starFouls >= FOUL_SIT_THRESHOLD_H2) {
                starIsSitting = true;
            }
        }
        // Star Return from Foul Trouble
        if (starIsSitting && !starFouledOut) {
            const returnThreshold = isSecondHalf ? PHASE_CRUNCH_START - 0.05 : FOUL_RETURN_PCT;
            if (progressPct >= returnThreshold) {
                starIsSitting = false;
            }
        }
        if (starIsSitting) starSatPoss++;

        // ── Bench Rotation: Star Rest ──
        const restWindowStart = Math.floor(halfPoss * 0.28);
        const restWindowEnd = Math.floor(halfPoss * 0.52);
        const inRestWindow = possUsed >= restWindowStart && possUsed <= restWindowEnd;
        const restProb = inRestWindow ? clamp(0.15 + (benchDepth / 100) * 0.65, 0.15, 0.75) : 0;
        const isRestPoss = Math.random() < restProb;
        if (isRestPoss) restPossCount++;
        const effectiveStarUsage = isRestPoss ? starUsage * 0.15 : starUsage;

        // Plan 06: Override star usage when sitting due to foul trouble
        const foulTroubleStarUsage = starIsSitting ? starUsage * 0.10 : effectiveStarUsage;

        const isStar = Math.random() < foulTroubleStarUsage;
        const sFG2 = isStar ? starFG2 : 0;
        const sFG3 = isStar ? starFG3 : 0;

        // ── Defensive Disruption: Shot Quality ──
        const defP = defProfile || { perimeter: 0, interior: 0, overall: 0 };
        const isDisruptedPoss = Math.random() < defP.overall * 0.6;
        const disrupt3Mod = isDisruptedPoss ? -(defP.perimeter * 5.0) : 0;
        const disrupt2Mod = isDisruptedPoss ? -(defP.interior * 4.0) : 0;
        const disruptStarMod = isDisruptedPoss ? (1 - defP.overall * 0.5) : 1.0;

        // Plan 08: Elite perimeter defense cools hot streaks faster
        if (streak3 > 0 && isDisruptedPoss && defP.perimeter > 0.4) {
            streak3 = Math.max(0, streak3 - 1);
        }

        const momFG = mom * 0.4;

        if (Math.random() * 100 < (toPct + gs_toPctAdj) * fatigueTOMod) {
            tos++;
            mom = Math.max(mom - 1, -2);
            if (Math.random() < (defStealRate / Math.max(toPct, 8)) * 0.65) {
                const r = Math.random();
                if (r < 0.55) { transitionPts += 2; runningLead -= 2; }
                else if (r < 0.70) { transitionPts += 3; runningLead -= 3; }
            }
            streak3 = Math.round(streak3 * STREAK_DECAY);
            continue;
        }

        // Plan 09: Foul probability scaled by referee climate
        const baseFoulProb = 0.20 * refClimate;
        const drewFoul = Math.random() < baseFoulProb;
        if (drewFoul) defFouls++;

        if (drewFoul && defFouls >= 7 && bonusReachedPoss === -1) bonusReachedPoss = possUsed;
        if (drewFoul && defFouls >= 7 && Math.random() < 0.45) {
            let bonusMade = 0;
            if (defFouls >= 10) {
                for (let f = 0; f < 2; f++) {
                    ftAtt++;
                    if (Math.random() * 100 < ftPct) { points++; ftMade++; bonusMade++; runningLead += 1; }
                }
            } else {
                ftAtt++;
                if (Math.random() * 100 < ftPct) {
                    points++; ftMade++; bonusMade++; runningLead += 1;
                    ftAtt++;
                    if (Math.random() * 100 < ftPct) { points++; ftMade++; bonusMade++; runningLead += 1; }
                }
            }
            mom = bonusMade > 0 ? Math.min(mom + 0.5, 3) : Math.max(mom - 0.5, -2);
            streak3 = Math.round(streak3 * STREAK_DECAY);
            continue;
        }

        // Plan 09: FTR-based shooting foul, scaled by referee foul climate
        if (!drewFoul && Math.random() < (ftr * refClimate) / 100 * 0.38) {
            defFouls++;
            const numFTs = Math.random() < 0.25 ? 3 : 2;
            let made = 0;
            for (let f = 0; f < numFTs; f++) {
                ftAtt++;
                if (Math.random() * 100 < ftPct) { points++; ftMade++; made++; runningLead += 1; }
            }
            mom = made > 0 ? Math.min(mom + 0.5, 3) : Math.max(mom - 0.5, -2);
            streak3 = Math.round(streak3 * STREAK_DECAY);
            continue;
        }

        // ── Intentional Fouling (when leading in crunch time) ──
        if (isCrunchTime && isSecondHalf && runningLead >= 6 && gs_ftrAdj > 0 && intentionalFoulPoss < 6) {
            if (Math.random() * 100 < gs_ftrAdj * 6) {
                intentionalFoulPoss++;
                defFouls++;
                let made = 0;
                for (let f = 0; f < 2; f++) {
                    ftAtt++;
                    if (Math.random() * 100 < ftPct) { points++; ftMade++; made++; runningLead += 1; }
                }
                mom = made > 0 ? Math.min(mom + 0.3, 3) : Math.max(mom - 0.3, -2);
                streak3 = Math.round(streak3 * STREAK_DECAY);
                continue;
            }
        }

        attempts++;
        // Plan 08: Streak-modified 3PT rate and accuracy
        const streakRateAdj = streak3 > 0
            ? Math.min(streak3 * STREAK_RATE_BONUS, 4)
            : Math.max(streak3 * STREAK_RATE_PENALTY, -3);
        const streakFGAdj = streak3 > 0
            ? Math.min(streak3 * HOT_BONUS_PER, MAX_STREAK_EFFECT)
            : Math.max(streak3 * COLD_PENALTY_PER, -MAX_STREAK_EFFECT);

        const is3pt = Math.random() * 100 < clamp(rate3 + gs_3RateAdj + streakRateAdj, 15, 65);
        if (is3pt) {
            const effectiveFG3 = clamp(fg3 + sFG3 * disruptStarMod + momFG * 0.5 - gs_fgPenalty + disrupt3Mod + streakFGAdj, 15, 50);
            if (Math.random() * 100 < effectiveFG3 * fatigueFGMod) {
                points += 3; makes3++; runningLead += 3;
                mom = Math.min(mom + 1.5, 3);
                streak3 = streak3 > 0 ? streak3 + 1 : 1;
                if (Math.random() < 0.02) {
                    defFouls++;
                    ftAtt++;
                    if (Math.random() * 100 < ftPct) { points++; ftMade++; runningLead += 1; }
                }
            } else {
                mom = Math.max(mom - 0.5, -2);
                streak3 = streak3 < 0 ? streak3 - 1 : -1;
                if (Math.random() * 100 < orPct * 0.80) { possLeft++; orebs++; }
            }
        } else {
            streak3 = Math.round(streak3 * STREAK_DECAY);
            const effectiveFG2 = clamp(fg2 + sFG2 * disruptStarMod + momFG * 0.7 - gs_fgPenalty * 0.5 + disrupt2Mod, 25, 70);
            if (Math.random() * 100 < effectiveFG2 * fatigueFGMod) {
                points += 2; makes2++; runningLead += 2;
                mom = Math.min(mom + 1, 3);
                if (Math.random() < 0.06) {
                    defFouls++;
                    ftAtt++;
                    if (Math.random() * 100 < ftPct) { points++; ftMade++; runningLead += 1; }
                }
            } else {
                mom = Math.max(mom - 0.5, -2);
                if (Math.random() * 100 < orPct) { possLeft++; orebs++; }
            }
        }
        // Plan 08: Track streak extremes
        if (streak3 > maxHotStreak) maxHotStreak = streak3;
        if (streak3 < -maxColdStreak) maxColdStreak = -streak3;
        if (streak3 >= 2) hotPossessions++;
        if (streak3 <= -2) coldPossessions++;
    }
    return { points, possUsed, makes2, makes3, tos, ftMade, ftAtt, orebs, attempts,
             transitionPts, momentum: mom, defFouls,
             avgFatiguePenalty: possUsed > 0 ? totalFatiguePenalty / possUsed : 0,
             restPossessions: restPossCount,
             crunchTimePoss, desperationPoss, intentionalFoulPoss,
             finalLead: runningLead,
             starFoulState: { fouls: starFouls, isSitting: starIsSitting, fouledOut: starFouledOut },
             starSatPoss, starFouledOut,
             bonusReachedAtPoss: bonusReachedPoss,
             maxHotStreak, maxColdStreak, hotPossessions, coldPossessions };
}

// ── Interleaved Half Simulation ──
// Alternates possessions between T1 and T2 so that `lead` is always exact.
function simHalfInterleaved(cfg) {
    const { halfPoss, isSecondHalf, incomingLead, foulClimate,
            t1_fg2, t1_fg3, t1_toPct, t1_orPct, t1_rate3, t1_ftr, t1_ftPct,
            t1_defStealRate, t1_starUsage, t1_starFG2, t1_starFG3, t1_initMom,
            t1_benchDepth, t1_defProfile, t1_starFoulState, t1_starFoulProneness, t1_streakiness,
            t2_fg2, t2_fg3, t2_toPct, t2_orPct, t2_rate3, t2_ftr, t2_ftPct,
            t2_defStealRate, t2_starUsage, t2_starFG2, t2_starFG3, t2_initMom,
            t2_benchDepth, t2_defProfile, t2_starFoulState, t2_starFoulProneness, t2_streakiness,
    } = cfg;

    // ── Shared state ──
    let lead = incomingLead || 0; // positive = T1 leads

    // ── Per-team scoring / stat accumulators ──
    let t1_points = 0, t2_points = 0;
    let t1_possUsed = 0, t2_possUsed = 0;
    let t1_makes2 = 0, t2_makes2 = 0;
    let t1_makes3 = 0, t2_makes3 = 0;
    let t1_tos = 0, t2_tos = 0;
    let t1_ftMade = 0, t2_ftMade = 0;
    let t1_ftAtt = 0, t2_ftAtt = 0;
    let t1_orebs = 0, t2_orebs = 0;
    let t1_attempts = 0, t2_attempts = 0;
    let t1_totalFatiguePenalty = 0, t2_totalFatiguePenalty = 0;
    let t1_restPossCount = 0, t2_restPossCount = 0;

    // Possession counters
    let t1PossLeft = Math.round(halfPoss);
    let t2PossLeft = Math.round(halfPoss);
    const t1MaxPoss = t1PossLeft + 10;
    const t2MaxPoss = t2PossLeft + 10;

    // ── Game Clock Phases ──
    const totalHalfPoss = Math.round(halfPoss);
    const PHASE_LATE_START = 0.75;
    const PHASE_CRUNCH_START = 0.90;
    let t1_crunchTimePoss = 0, t2_crunchTimePoss = 0;
    let t1_desperationPoss = 0, t2_desperationPoss = 0;
    let t1_intentionalFoulPoss = 0, t2_intentionalFoulPoss = 0;

    // ── Referee Foul Climate ──
    const refClimate = foulClimate || 1.0;
    let t1_defFouls = 0, t2_defFouls = 0;
    let t1_bonusReachedPoss = -1, t2_bonusReachedPoss = -1;

    // ── Momentum ──
    let t1_mom = t1_initMom;
    let t2_mom = t2_initMom;

    // ── Plan 06: Star Foul Trouble (per team) ──
    const MAX_FOULS = 5;
    const FOUL_SIT_THRESHOLD_H1 = 2;
    const FOUL_SIT_THRESHOLD_H2 = 4;
    const FOUL_RETURN_PCT = 0.80;

    let t1_starFouls = t1_starFoulState ? t1_starFoulState.fouls : 0;
    let t1_starIsSitting = t1_starFoulState ? t1_starFoulState.isSitting : false;
    let t1_starSatPoss = 0;
    let t1_starFouledOut = false;
    const t1_baseStarFoulRate = (0.035 + (t1_starFoulProneness || 0) * 0.02) * Math.sqrt(refClimate);

    let t2_starFouls = t2_starFoulState ? t2_starFoulState.fouls : 0;
    let t2_starIsSitting = t2_starFoulState ? t2_starFoulState.isSitting : false;
    let t2_starSatPoss = 0;
    let t2_starFouledOut = false;
    const t2_baseStarFoulRate = (0.035 + (t2_starFoulProneness || 0) * 0.02) * Math.sqrt(refClimate);

    // ── Plan 08: Three-Point Streak Tracking (per team) ──
    const t1_teamStreakiness = t1_streakiness || 1.0;
    let t1_streak3 = 0;
    const t1_HOT_BONUS_PER = 1.2 * t1_teamStreakiness;
    const t1_COLD_PENALTY_PER = 1.0 * t1_teamStreakiness;
    const t1_MAX_STREAK_EFFECT = 5.0 * t1_teamStreakiness;
    const t1_STREAK_RATE_BONUS = 0.8 * t1_teamStreakiness;
    const t1_STREAK_RATE_PENALTY = 0.6 * t1_teamStreakiness;
    let t1_maxHotStreak = 0, t1_maxColdStreak = 0;
    let t1_hotPossessions = 0, t1_coldPossessions = 0;

    const t2_teamStreakiness = t2_streakiness || 1.0;
    let t2_streak3 = 0;
    const t2_HOT_BONUS_PER = 1.2 * t2_teamStreakiness;
    const t2_COLD_PENALTY_PER = 1.0 * t2_teamStreakiness;
    const t2_MAX_STREAK_EFFECT = 5.0 * t2_teamStreakiness;
    const t2_STREAK_RATE_BONUS = 0.8 * t2_teamStreakiness;
    const t2_STREAK_RATE_PENALTY = 0.6 * t2_teamStreakiness;
    let t2_maxHotStreak = 0, t2_maxColdStreak = 0;
    let t2_hotPossessions = 0, t2_coldPossessions = 0;

    const STREAK_DECAY = 0.65;

    // ── Helper: simulate one possession for a given team ──
    // `teamSign` is +1 for T1, -1 for T2 (used to update `lead`)
    // All the team-specific variables are passed/returned via closure.
    function simOnePossession(
        fg2, fg3, toPct, orPct, rate3, ftr, ftPct,
        defStealRate, starUsage, starFG2, starFG3,
        benchDepth, defProfile,
        // mutable state refs (objects so we can mutate)
        st // { mom, defFouls, possUsed, possLeft, maxPoss, points, makes2, makes3, tos, ftMade, ftAtt, orebs, attempts, totalFatiguePenalty, restPossCount, crunchTimePoss, desperationPoss, intentionalFoulPoss, bonusReachedPoss, starFouls, starIsSitting, starSatPoss, starFouledOut, baseStarFoulRate, streak3, maxHotStreak, maxColdStreak, hotPossessions, coldPossessions, HOT_BONUS_PER, COLD_PENALTY_PER, MAX_STREAK_EFFECT, STREAK_RATE_BONUS, STREAK_RATE_PENALTY }
    , teamSign) {
        if (st.possLeft <= 0 || st.possUsed >= st.maxPoss) return false;
        st.possLeft--; st.possUsed++;

        // ── Game State Awareness ──
        // Use average of both teams' possUsed for game clock progress
        const totalPossUsed = t1_possUsed + t2_possUsed;
        const progressPct = totalPossUsed / (totalHalfPoss * 2);
        const isLateHalf = isSecondHalf && progressPct >= PHASE_LATE_START;
        const isCrunchTime = isSecondHalf && progressPct >= PHASE_CRUNCH_START;
        if (isCrunchTime) st.crunchTimePoss++;

        // `teamLead` is from this team's perspective: positive = this team leads
        const teamLead = lead * teamSign;

        let gs_3RateAdj = 0, gs_toPctAdj = 0, gs_ftrAdj = 0, gs_fgPenalty = 0;
        if (isSecondHalf) {
            const deficit = -teamLead; // positive when trailing
            if (isCrunchTime && deficit >= 6) {
                const desperationScale = clamp((deficit - 5) / 15, 0, 1);
                gs_3RateAdj = 8 + desperationScale * 12;
                gs_toPctAdj = 1.5 + desperationScale * 2;
                gs_fgPenalty = 2 + desperationScale * 3;
                st.desperationPoss++;
            } else if (isCrunchTime && deficit >= 3) {
                gs_3RateAdj = 5;
                gs_toPctAdj = 0.8;
                gs_fgPenalty = 1;
            } else if (isLateHalf && deficit >= 8) {
                gs_3RateAdj = 4;
                gs_toPctAdj = 0.5;
            } else if (isCrunchTime && teamLead >= 6) {
                gs_3RateAdj = -6;
                gs_toPctAdj = -1;
                gs_ftrAdj = 4;
                // Plan 07: Leading by 8+ in crunch → burn clock aggressively
                if (teamLead >= 8 && Math.random() < 0.15) {
                    if (Math.random() * 100 < fg2 * 0.60) {
                        st.points += 2; st.makes2++; lead += 2 * teamSign;
                        st.mom = Math.min(st.mom + 0.3, 3);
                    }
                    st.possLeft--; st.possUsed++;
                    st.streak3 = Math.round(st.streak3 * STREAK_DECAY);
                    return true;
                }
            } else if (isCrunchTime && deficit >= 6) {
                // Trailing by 6+ in crunch → quick shots, push pace
                if (Math.random() < 0.10) { st.possLeft++; }
            } else if (isLateHalf && teamLead >= 10) {
                gs_3RateAdj = -3;
                gs_toPctAdj = -0.5;
            }
        }

        // ── Fatigue Curve ──
        const fatigueOnsetPct = 0.55 + (benchDepth / 100) * 0.15;
        const fatigueProgress = Math.max(0, (st.possUsed / halfPoss) - fatigueOnsetPct) / (1 - fatigueOnsetPct);
        const halfMultiplier = isSecondHalf ? 1.4 : 1.0;
        const fatiguePenalty = fatigueProgress * halfMultiplier * 0.06;
        const fatigueFGMod = 1 - fatiguePenalty;
        const fatigueTOMod = 1 + fatiguePenalty * 0.5;
        st.totalFatiguePenalty += fatiguePenalty;

        // ── Plan 06: Star Foul Trouble Check ──
        if (!st.starIsSitting && !st.starFouledOut && Math.random() < st.baseStarFoulRate) {
            st.starFouls++;
            if (st.starFouls >= MAX_FOULS) {
                st.starFouledOut = true;
                st.starIsSitting = true;
            } else if (!isSecondHalf && st.starFouls >= FOUL_SIT_THRESHOLD_H1) {
                st.starIsSitting = true;
            } else if (isSecondHalf && st.starFouls >= FOUL_SIT_THRESHOLD_H2) {
                st.starIsSitting = true;
            }
        }
        // Star Return from Foul Trouble
        if (st.starIsSitting && !st.starFouledOut) {
            const returnThreshold = isSecondHalf ? PHASE_CRUNCH_START - 0.05 : FOUL_RETURN_PCT;
            if (progressPct >= returnThreshold) {
                st.starIsSitting = false;
            }
        }
        if (st.starIsSitting) st.starSatPoss++;

        // ── Bench Rotation: Star Rest ──
        const restWindowStart = Math.floor(halfPoss * 0.28);
        const restWindowEnd = Math.floor(halfPoss * 0.52);
        const inRestWindow = st.possUsed >= restWindowStart && st.possUsed <= restWindowEnd;
        const restProb = inRestWindow ? clamp(0.15 + (benchDepth / 100) * 0.65, 0.15, 0.75) : 0;
        const isRestPoss = Math.random() < restProb;
        if (isRestPoss) st.restPossCount++;
        const effectiveStarUsage = isRestPoss ? starUsage * 0.15 : starUsage;

        // Plan 06: Override star usage when sitting due to foul trouble
        const foulTroubleStarUsage = st.starIsSitting ? starUsage * 0.10 : effectiveStarUsage;

        const isStar = Math.random() < foulTroubleStarUsage;
        const sFG2 = isStar ? starFG2 : 0;
        const sFG3 = isStar ? starFG3 : 0;

        // ── Defensive Disruption: Shot Quality ──
        const defP = defProfile || { perimeter: 0, interior: 0, overall: 0 };
        const isDisruptedPoss = Math.random() < defP.overall * 0.6;
        const disrupt3Mod = isDisruptedPoss ? -(defP.perimeter * 5.0) : 0;
        const disrupt2Mod = isDisruptedPoss ? -(defP.interior * 4.0) : 0;
        const disruptStarMod = isDisruptedPoss ? (1 - defP.overall * 0.5) : 1.0;

        // Plan 08: Elite perimeter defense cools hot streaks faster
        if (st.streak3 > 0 && isDisruptedPoss && defP.perimeter > 0.4) {
            st.streak3 = Math.max(0, st.streak3 - 1);
        }

        const momFG = st.mom * 0.4;

        // ── Turnover check ──
        if (Math.random() * 100 < (toPct + gs_toPctAdj) * fatigueTOMod) {
            st.tos++;
            st.mom = Math.max(st.mom - 1, -2);
            if (Math.random() < (defStealRate / Math.max(toPct, 8)) * 0.65) {
                const r = Math.random();
                // Transition scoring goes to the OTHER team
                if (r < 0.55) { lead -= 2 * teamSign; }
                else if (r < 0.70) { lead -= 3 * teamSign; }
                // Credit the other team's points directly
                if (teamSign === 1) {
                    if (r < 0.55) t2_points += 2;
                    else if (r < 0.70) t2_points += 3;
                } else {
                    if (r < 0.55) t1_points += 2;
                    else if (r < 0.70) t1_points += 3;
                }
            }
            st.streak3 = Math.round(st.streak3 * STREAK_DECAY);
            return true;
        }

        // ── Plan 09: Foul probability scaled by referee climate ──
        const baseFoulProb = 0.20 * refClimate;
        const drewFoul = Math.random() < baseFoulProb;
        if (drewFoul) st.defFouls++;

        if (drewFoul && st.defFouls >= 7 && st.bonusReachedPoss === -1) st.bonusReachedPoss = st.possUsed;
        if (drewFoul && st.defFouls >= 7 && Math.random() < 0.45) {
            let bonusMade = 0;
            if (st.defFouls >= 10) {
                for (let f = 0; f < 2; f++) {
                    st.ftAtt++;
                    if (Math.random() * 100 < ftPct) { st.points++; st.ftMade++; bonusMade++; lead += 1 * teamSign; }
                }
            } else {
                st.ftAtt++;
                if (Math.random() * 100 < ftPct) {
                    st.points++; st.ftMade++; bonusMade++; lead += 1 * teamSign;
                    st.ftAtt++;
                    if (Math.random() * 100 < ftPct) { st.points++; st.ftMade++; bonusMade++; lead += 1 * teamSign; }
                }
            }
            st.mom = bonusMade > 0 ? Math.min(st.mom + 0.5, 3) : Math.max(st.mom - 0.5, -2);
            st.streak3 = Math.round(st.streak3 * STREAK_DECAY);
            return true;
        }

        // Plan 09: FTR-based shooting foul, scaled by referee foul climate
        if (!drewFoul && Math.random() < (ftr * refClimate) / 100 * 0.38) {
            st.defFouls++;
            const numFTs = Math.random() < 0.25 ? 3 : 2;
            let made = 0;
            for (let f = 0; f < numFTs; f++) {
                st.ftAtt++;
                if (Math.random() * 100 < ftPct) { st.points++; st.ftMade++; made++; lead += 1 * teamSign; }
            }
            st.mom = made > 0 ? Math.min(st.mom + 0.5, 3) : Math.max(st.mom - 0.5, -2);
            st.streak3 = Math.round(st.streak3 * STREAK_DECAY);
            return true;
        }

        // ── Intentional Fouling (opponent fouls this team when this team leads in crunch) ──
        if (isCrunchTime && isSecondHalf && teamLead >= 6 && gs_ftrAdj > 0 && st.intentionalFoulPoss < 6) {
            if (Math.random() * 100 < gs_ftrAdj * 6) {
                st.intentionalFoulPoss++;
                st.defFouls++;
                let made = 0;
                for (let f = 0; f < 2; f++) {
                    st.ftAtt++;
                    if (Math.random() * 100 < ftPct) { st.points++; st.ftMade++; made++; lead += 1 * teamSign; }
                }
                st.mom = made > 0 ? Math.min(st.mom + 0.3, 3) : Math.max(st.mom - 0.3, -2);
                st.streak3 = Math.round(st.streak3 * STREAK_DECAY);
                return true;
            }
        }

        // ── Shot selection ──
        st.attempts++;
        // Plan 08: Streak-modified 3PT rate and accuracy
        const streakRateAdj = st.streak3 > 0
            ? Math.min(st.streak3 * st.STREAK_RATE_BONUS, 4)
            : Math.max(st.streak3 * st.STREAK_RATE_PENALTY, -3);
        const streakFGAdj = st.streak3 > 0
            ? Math.min(st.streak3 * st.HOT_BONUS_PER, st.MAX_STREAK_EFFECT)
            : Math.max(st.streak3 * st.COLD_PENALTY_PER, -st.MAX_STREAK_EFFECT);

        const is3pt = Math.random() * 100 < clamp(rate3 + gs_3RateAdj + streakRateAdj, 15, 65);
        if (is3pt) {
            const effectiveFG3 = clamp(fg3 + sFG3 * disruptStarMod + momFG * 0.5 - gs_fgPenalty + disrupt3Mod + streakFGAdj, 15, 50);
            if (Math.random() * 100 < effectiveFG3 * fatigueFGMod) {
                st.points += 3; st.makes3++; lead += 3 * teamSign;
                st.mom = Math.min(st.mom + 1.5, 3);
                st.streak3 = st.streak3 > 0 ? st.streak3 + 1 : 1;
                if (Math.random() < 0.02) {
                    st.defFouls++;
                    st.ftAtt++;
                    if (Math.random() * 100 < ftPct) { st.points++; st.ftMade++; lead += 1 * teamSign; }
                }
            } else {
                st.mom = Math.max(st.mom - 0.5, -2);
                st.streak3 = st.streak3 < 0 ? st.streak3 - 1 : -1;
                if (Math.random() * 100 < orPct * 0.80) { st.possLeft++; st.orebs++; }
            }
        } else {
            st.streak3 = Math.round(st.streak3 * STREAK_DECAY);
            const effectiveFG2 = clamp(fg2 + sFG2 * disruptStarMod + momFG * 0.7 - gs_fgPenalty * 0.5 + disrupt2Mod, 25, 70);
            if (Math.random() * 100 < effectiveFG2 * fatigueFGMod) {
                st.points += 2; st.makes2++; lead += 2 * teamSign;
                st.mom = Math.min(st.mom + 1, 3);
                if (Math.random() < 0.06) {
                    st.defFouls++;
                    st.ftAtt++;
                    if (Math.random() * 100 < ftPct) { st.points++; st.ftMade++; lead += 1 * teamSign; }
                }
            } else {
                st.mom = Math.max(st.mom - 0.5, -2);
                if (Math.random() * 100 < orPct) { st.possLeft++; st.orebs++; }
            }
        }
        // Plan 08: Track streak extremes
        if (st.streak3 > st.maxHotStreak) st.maxHotStreak = st.streak3;
        if (st.streak3 < -st.maxColdStreak) st.maxColdStreak = -st.streak3;
        if (st.streak3 >= 2) st.hotPossessions++;
        if (st.streak3 <= -2) st.coldPossessions++;

        return true;
    }

    // ── Build mutable state objects for each team ──
    const t1St = {
        mom: t1_mom, defFouls: t1_defFouls, possUsed: t1_possUsed, possLeft: t1PossLeft, maxPoss: t1MaxPoss,
        points: t1_points, makes2: t1_makes2, makes3: t1_makes3, tos: t1_tos,
        ftMade: t1_ftMade, ftAtt: t1_ftAtt, orebs: t1_orebs, attempts: t1_attempts,
        totalFatiguePenalty: t1_totalFatiguePenalty, restPossCount: t1_restPossCount,
        crunchTimePoss: t1_crunchTimePoss, desperationPoss: t1_desperationPoss,
        intentionalFoulPoss: t1_intentionalFoulPoss, bonusReachedPoss: t1_bonusReachedPoss,
        starFouls: t1_starFouls, starIsSitting: t1_starIsSitting, starSatPoss: t1_starSatPoss,
        starFouledOut: t1_starFouledOut, baseStarFoulRate: t1_baseStarFoulRate,
        streak3: t1_streak3, maxHotStreak: t1_maxHotStreak, maxColdStreak: t1_maxColdStreak,
        hotPossessions: t1_hotPossessions, coldPossessions: t1_coldPossessions,
        HOT_BONUS_PER: t1_HOT_BONUS_PER, COLD_PENALTY_PER: t1_COLD_PENALTY_PER,
        MAX_STREAK_EFFECT: t1_MAX_STREAK_EFFECT, STREAK_RATE_BONUS: t1_STREAK_RATE_BONUS,
        STREAK_RATE_PENALTY: t1_STREAK_RATE_PENALTY,
    };
    const t2St = {
        mom: t2_mom, defFouls: t2_defFouls, possUsed: t2_possUsed, possLeft: t2PossLeft, maxPoss: t2MaxPoss,
        points: t2_points, makes2: t2_makes2, makes3: t2_makes3, tos: t2_tos,
        ftMade: t2_ftMade, ftAtt: t2_ftAtt, orebs: t2_orebs, attempts: t2_attempts,
        totalFatiguePenalty: t2_totalFatiguePenalty, restPossCount: t2_restPossCount,
        crunchTimePoss: t2_crunchTimePoss, desperationPoss: t2_desperationPoss,
        intentionalFoulPoss: t2_intentionalFoulPoss, bonusReachedPoss: t2_bonusReachedPoss,
        starFouls: t2_starFouls, starIsSitting: t2_starIsSitting, starSatPoss: t2_starSatPoss,
        starFouledOut: t2_starFouledOut, baseStarFoulRate: t2_baseStarFoulRate,
        streak3: t2_streak3, maxHotStreak: t2_maxHotStreak, maxColdStreak: t2_maxColdStreak,
        hotPossessions: t2_hotPossessions, coldPossessions: t2_coldPossessions,
        HOT_BONUS_PER: t2_HOT_BONUS_PER, COLD_PENALTY_PER: t2_COLD_PENALTY_PER,
        MAX_STREAK_EFFECT: t2_MAX_STREAK_EFFECT, STREAK_RATE_BONUS: t2_STREAK_RATE_BONUS,
        STREAK_RATE_PENALTY: t2_STREAK_RATE_PENALTY,
    };

    // ── Main alternating loop ──
    while (t1St.possLeft > 0 || t2St.possLeft > 0) {
        let t1Ran = false, t2Ran = false;
        // T1 possession
        if (t1St.possLeft > 0 && t1St.possUsed < t1St.maxPoss) {
            simOnePossession(
                t1_fg2, t1_fg3, t1_toPct, t1_orPct, t1_rate3, t1_ftr, t1_ftPct,
                t1_defStealRate, t1_starUsage, t1_starFG2, t1_starFG3,
                t1_benchDepth, t1_defProfile,
                t1St, +1
            );
            t1Ran = true;
        }
        // T2 possession
        if (t2St.possLeft > 0 && t2St.possUsed < t2St.maxPoss) {
            simOnePossession(
                t2_fg2, t2_fg3, t2_toPct, t2_orPct, t2_rate3, t2_ftr, t2_ftPct,
                t2_defStealRate, t2_starUsage, t2_starFG2, t2_starFG3,
                t2_benchDepth, t2_defProfile,
                t2St, -1
            );
            t2Ran = true;
        }
        if (!t1Ran && !t2Ran) break; // safety: avoid infinite loop if both hit maxPoss
    }

    // ── Build return value matching simHalf() shape per team ──
    return {
        t1: {
            points: t1St.points, possUsed: t1St.possUsed, makes2: t1St.makes2, makes3: t1St.makes3,
            tos: t1St.tos, ftMade: t1St.ftMade, ftAtt: t1St.ftAtt, orebs: t1St.orebs, attempts: t1St.attempts,
            transitionPts: 0, // transition pts already folded into opponent's points
            momentum: t1St.mom, defFouls: t1St.defFouls,
            avgFatiguePenalty: t1St.possUsed > 0 ? t1St.totalFatiguePenalty / t1St.possUsed : 0,
            restPossessions: t1St.restPossCount,
            crunchTimePoss: t1St.crunchTimePoss, desperationPoss: t1St.desperationPoss,
            intentionalFoulPoss: t1St.intentionalFoulPoss,
            finalLead: lead,
            starFoulState: { fouls: t1St.starFouls, isSitting: t1St.starIsSitting, fouledOut: t1St.starFouledOut },
            starSatPoss: t1St.starSatPoss, starFouledOut: t1St.starFouledOut,
            bonusReachedAtPoss: t1St.bonusReachedPoss,
            maxHotStreak: t1St.maxHotStreak, maxColdStreak: t1St.maxColdStreak,
            hotPossessions: t1St.hotPossessions, coldPossessions: t1St.coldPossessions,
        },
        t2: {
            points: t2St.points, possUsed: t2St.possUsed, makes2: t2St.makes2, makes3: t2St.makes3,
            tos: t2St.tos, ftMade: t2St.ftMade, ftAtt: t2St.ftAtt, orebs: t2St.orebs, attempts: t2St.attempts,
            transitionPts: 0,
            momentum: t2St.mom, defFouls: t2St.defFouls,
            avgFatiguePenalty: t2St.possUsed > 0 ? t2St.totalFatiguePenalty / t2St.possUsed : 0,
            restPossessions: t2St.restPossCount,
            crunchTimePoss: t2St.crunchTimePoss, desperationPoss: t2St.desperationPoss,
            intentionalFoulPoss: t2St.intentionalFoulPoss,
            finalLead: -lead,
            starFoulState: { fouls: t2St.starFouls, isSitting: t2St.starIsSitting, fouledOut: t2St.starFouledOut },
            starSatPoss: t2St.starSatPoss, starFouledOut: t2St.starFouledOut,
            bonusReachedAtPoss: t2St.bonusReachedPoss,
            maxHotStreak: t2St.maxHotStreak, maxColdStreak: t2St.maxColdStreak,
            hotPossessions: t2St.hotPossessions, coldPossessions: t2St.coldPossessions,
        },
        finalLead: lead,
    };
}

function simOvertime(fg2, fg3, toPct, orPct, rate3, ftr, ftPct,
                     defStealRate, starUsage, starFG2, starFG3, momentum,
                     otNumber, starFouledOut, foulClimate) {
    const OT_POSSESSIONS = 5;
    let points = 0, possUsed = 0;
    let makes2 = 0, makes3 = 0, tos = 0;
    let ftMade = 0, ftAtt = 0, orebs = 0;
    let mom = momentum * 0.5;

    const refClimate = foulClimate || 1.0;
    const otFatiguePenalty = 0.04 + (otNumber - 1) * 0.025;
    const fatigueFGMod = 1 - otFatiguePenalty;
    const fatigueTOMod = 1 + otFatiguePenalty * 0.6;
    const otFTRBoost = 8;
    const effectiveFTR = (ftr + otFTRBoost) * refClimate;

    let possLeft = OT_POSSESSIONS;
    let streak3 = 0;
    const STREAK_DECAY = 0.65;

    while (possLeft > 0) {
        possLeft--;
        possUsed++;

        const effectiveStarUsage = starFouledOut ? 0 : starUsage;
        const isStar = Math.random() < effectiveStarUsage;
        const sFG2 = isStar ? starFG2 * fatigueFGMod : 0;
        const sFG3 = isStar ? starFG3 * fatigueFGMod : 0;
        const momFG = mom * 0.3;

        if (Math.random() * 100 < toPct * fatigueTOMod) {
            tos++;
            mom = Math.max(mom - 1, -2);
            streak3 = Math.round(streak3 * STREAK_DECAY);
            continue;
        }

        if (Math.random() < effectiveFTR / 100 * 0.45) {
            const numFTs = Math.random() < 0.20 ? 3 : 2;
            let made = 0;
            for (let f = 0; f < numFTs; f++) {
                ftAtt++;
                const clutchFTPct = ftPct - 2;
                if (Math.random() * 100 < clutchFTPct) { points++; ftMade++; made++; }
            }
            mom = made > 0 ? Math.min(mom + 0.5, 2) : Math.max(mom - 0.5, -2);
            streak3 = Math.round(streak3 * STREAK_DECAY);
            continue;
        }

        const streakFGAdj = streak3 > 0
            ? Math.min(streak3 * 1.2, 5.0)
            : Math.max(streak3 * 1.0, -5.0);
        const streakRateAdj = streak3 > 0
            ? Math.min(streak3 * 0.8, 4)
            : Math.max(streak3 * 0.6, -3);

        const is3pt = Math.random() * 100 < clamp(rate3 + streakRateAdj, 15, 65);
        if (is3pt) {
            if (Math.random() * 100 < (fg3 + sFG3 + momFG * 0.5 + streakFGAdj) * fatigueFGMod) {
                points += 3; makes3++;
                mom = Math.min(mom + 1.5, 2);
                streak3 = streak3 > 0 ? streak3 + 1 : 1;
            } else {
                mom = Math.max(mom - 0.5, -2);
                streak3 = streak3 < 0 ? streak3 - 1 : -1;
                if (Math.random() * 100 < orPct * 0.75) { possLeft++; orebs++; }
            }
        } else {
            streak3 = Math.round(streak3 * STREAK_DECAY);
            if (Math.random() * 100 < (fg2 + sFG2 + momFG * 0.7) * fatigueFGMod) {
                points += 2; makes2++;
                mom = Math.min(mom + 1, 2);
            } else {
                mom = Math.max(mom - 0.5, -2);
                if (Math.random() * 100 < orPct * 0.90) { possLeft++; orebs++; }
            }
        }
    }

    return { points, possUsed, makes2, makes3, tos, ftMade, ftAtt, orebs };
}

// Main simulation runner — receives pre-computed parameters from main thread
self.onmessage = function(e) {
    const p = e.data; // all pre-computed matchup parameters

    const t1Star = calcStarImpact(p.stars1);
    const t2Star = calcStarImpact(p.stars2);

    let t1Wins = 0, t2Wins = 0, ties = 0;
    let totalT1Score = 0, totalT2Score = 0;
    let marginDist = [];
    let blowouts = 0, closeGames = 0, upsets = 0;
    let totalT1Poss = 0, totalT2Poss = 0;
    let totalT1_3Made = 0, totalT2_3Made = 0;
    let totalT1_2Made = 0, totalT2_2Made = 0;
    let totalT1_TOs = 0, totalT2_TOs = 0;
    let totalT1_ORebs = 0, totalT2_ORebs = 0;
    let totalT1_FTM = 0, totalT2_FTM = 0;
    let totalTransition = 0;
    let totalT1Fatigue = 0, totalT2Fatigue = 0;
    let totalT1RestPoss = 0, totalT2RestPoss = 0;
    let totalCrunchPoss = 0, totalDesperationPoss = 0;
    let totalIntentionalFouls = 0;
    let totalOTPeriods = 0, gamesWithOT = 0;
    let doubleOTGames = 0, tripleOTGames = 0;
    let t1InteriorGames = 0, t1PerimeterGames = 0, t1BalancedGames = 0;
    let t2InteriorGames = 0, t2PerimeterGames = 0, t2BalancedGames = 0;
    let t1SloppyGames = 0, t1DisciplinedGames = 0;
    let t2SloppyGames = 0, t2DisciplinedGames = 0;

    // ── Plan 07: Tempo Tracking ──
    const t1Pull = p.t1PreferredTempo || p.gameTempoCtr;
    const t2Pull = p.t2PreferredTempo || p.gameTempoCtr;
    const t1Ctrl = p.t1TempoControl || 0.50;
    const t2Ctrl = p.t2TempoControl || 0.50;
    let totalContestedTempo = 0;
    let h1TempoTotal = 0, h2TempoTotal = 0;

    // ── Plan 06: Foul Trouble Tracking ──
    let t1StarSatTotal = 0, t2StarSatTotal = 0;
    let t1StarFouledOutGames = 0, t2StarFouledOutGames = 0;
    let t1StarFoulTroubleGames = 0, t2StarFoulTroubleGames = 0;

    // ── Plan 08: Streak Tracking ──
    let t1TotalHotPoss = 0, t2TotalHotPoss = 0;
    let t1TotalColdPoss = 0, t2TotalColdPoss = 0;
    let t1MaxHotEver = 0, t2MaxHotEver = 0;

    // ── Plan 09: Referee Stats Tracking ──
    const refClimate = p.refFoulClimate || 1.0;
    let totalT1DefFouls = 0, totalT2DefFouls = 0;
    let t1EarlyBonusGames = 0, t2EarlyBonusGames = 0;

    for (let i = 0; i < p.numSims; i++) {
        // ── Shared game environment factor ──
        const gameFactor = randNormal(0, 1.2);

        // ── Plan 07: Contested Tempo ──
        // Each team pulls toward their preferred pace, weighted by control rating
        const totalCtrl = t1Ctrl + t2Ctrl;
        const contestedTempo = (t1Pull * t1Ctrl + t2Pull * t2Ctrl) / totalCtrl;
        const tempoNoise = randNormal(0, 2.5);
        // Defensive-leaning slow teams win the tempo battle slightly more often
        const defTempoEdge = (t1Ctrl > t2Ctrl && t1Pull < t2Pull) ? -0.5 :
                             (t2Ctrl > t1Ctrl && t2Pull < t1Pull) ? -0.5 : 0;
        let gamePoss = clamp(contestedTempo + tempoNoise + defTempoEdge, 55, 85);
        totalContestedTempo += gamePoss;

        // ── Plan 07: Tempo Mismatch Chaos ──
        const tempoMismatch = Math.abs(t1Pull - t2Pull);
        const mismatchChaos = tempoMismatch > 6 ? clamp((tempoMismatch - 6) * 0.008, 0, 0.04) : 0;

        // ── Correlated game-style factors per team ──
        const t1Style = generateGameStyle(p.t1VolMod, p.t1StyleBias || 0);
        const t2Style = generateGameStyle(p.t2VolMod, p.t2StyleBias || 0);

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

        // Apply correlated style adjustments
        let g_t1_FG2 = clamp(p.m_t1_FG2 + t1Style.fg2Adj + gameFactor * 0.25 + randNormal(0, mismatchChaos * 15), 28, 68);
        let g_t2_FG2 = clamp(p.m_t2_FG2 + t2Style.fg2Adj + gameFactor * 0.25 + randNormal(0, mismatchChaos * 15), 28, 68);
        let g_t1_FG3 = clamp(p.m_t1_FG3 + t1Style.fg3Adj + gameFactor * 0.15 + randNormal(0, mismatchChaos * 10), 18, 48);
        let g_t2_FG3 = clamp(p.m_t2_FG3 + t2Style.fg3Adj + gameFactor * 0.15 + randNormal(0, mismatchChaos * 10), 18, 48);

        // ── Plan 07: Tempo Winner Bonus ──
        const t1TempoDelta = Math.abs(gamePoss - t1Pull);
        const t2TempoDelta = Math.abs(gamePoss - t2Pull);
        if (t1TempoDelta < t2TempoDelta - 2) {
            g_t1_FG2 += 0.2; g_t2_FG2 -= 0.12;
        } else if (t2TempoDelta < t1TempoDelta - 2) {
            g_t2_FG2 += 0.2; g_t1_FG2 -= 0.12;
        }
        let g_t1_TO  = clamp(p.m_t1_TO + t1Style.toAdj, 6, 30);
        let g_t2_TO  = clamp(p.m_t2_TO + t2Style.toAdj, 6, 30);
        const g_t1_OR  = clamp(p.m_t1_OR + p.t1HgtORBonus + t1Style.orAdj, 12, 45);
        const g_t2_OR  = clamp(p.m_t2_OR - p.t1HgtORBonus + t2Style.orAdj, 12, 45);

        // Per-game style-adjusted 3PT rate and FTR
        const g_t1_3Rate = clamp(p.t1_3Rate + t1Style.rate3Adj, 20, 55);
        const g_t2_3Rate = clamp(p.t2_3Rate + t2Style.rate3Adj, 20, 55);
        const g_t1_FTR = clamp(p.m_t1_FTR + t1Style.ftrAdj, 15, 50);
        const g_t2_FTR = clamp(p.m_t2_FTR + t2Style.ftrAdj, 15, 50);

        // ── Opponent Reaction ──
        const reactionStrength = 0.15;
        if (t1Style.fg2Adj > 2) g_t2_FG3 = clamp(g_t2_FG3 + reactionStrength * 1.5, 18, 48);
        if (t2Style.fg2Adj > 2) g_t1_FG3 = clamp(g_t1_FG3 + reactionStrength * 1.5, 18, 48);
        const chaosFactor = (t1Style.toAdj + t2Style.toAdj) * 0.08;
        g_t1_TO = clamp(g_t1_TO + chaosFactor, 6, 30);
        g_t2_TO = clamp(g_t2_TO + chaosFactor, 6, 30);

        // ── Defensive Disruption: TO Variance Boost ──
        const t2_TOVarBoost = (p.t1DefProfile ? p.t1DefProfile.overall : 0) * 2.5;
        const t1_TOVarBoost = (p.t2DefProfile ? p.t2DefProfile.overall : 0) * 2.5;
        if (t2_TOVarBoost > 0) g_t2_TO = clamp(g_t2_TO + randNormal(0, t2_TOVarBoost), 6, 30);
        if (t1_TOVarBoost > 0) g_t1_TO = clamp(g_t1_TO + randNormal(0, t1_TOVarBoost), 6, 30);

        let s1 = 0, s2 = 0;
        let t1Mom = 0, t2Mom = 0;
        let gP1 = 0, gP2 = 0, g2_1 = 0, g2_2 = 0, g3_1 = 0, g3_2 = 0;
        let gTO1 = 0, gTO2 = 0, gOR1 = 0, gOR2 = 0, gFT1 = 0, gFT2 = 0;

        // Plan 06: Per-game foul state (carried between halves)
        let t1StarFoulState = { fouls: 0, isSitting: false };
        let t2StarFoulState = { fouls: 0, isSitting: false };
        let gameT1SatPoss = 0, gameT2SatPoss = 0;
        let gameT1FouledOut = false, gameT2FouledOut = false;

        for (let half = 0; half < 2; half++) {
            // ── Plan 07: Half-Specific Tempo ──
            let halfTempoAdj = 0;
            if (half === 1) {
                const halftimeMargin = s1 - s2;
                // Trailing team pushes pace, leading team slows it
                if (Math.abs(halftimeMargin) > 5) {
                    const trailingTeamWantsFast = halftimeMargin > 0
                        ? (t2Pull > contestedTempo)
                        : (t1Pull > contestedTempo);
                    const paceShift = clamp(Math.abs(halftimeMargin) * 0.15, 0, 3);
                    halfTempoAdj = trailingTeamWantsFast ? paceShift : -paceShift * 0.5;
                }
                // Close games in second half tend to slow down
                if (Math.abs(halftimeMargin) <= 3) {
                    halfTempoAdj -= 0.8;
                }
            }
            const halfPoss = Math.round((gamePoss + halfTempoAdj) / 2);
            if (half === 0) h1TempoTotal += halfPoss * 2;
            else h2TempoTotal += halfPoss * 2;

            // Game-state adjustments now handled per-possession inside simHalfInterleaved()
            const interleavedIncomingLead = half === 0 ? 0 : (s1 - s2);

            const halfResult = simHalfInterleaved({
                halfPoss, isSecondHalf: half === 1,
                incomingLead: interleavedIncomingLead,
                foulClimate: refClimate,
                // T1 params
                t1_fg2: g_t1_FG2, t1_fg3: g_t1_FG3,
                t1_toPct: g_t1_TO, t1_orPct: g_t1_OR,
                t1_rate3: clamp(g_t1_3Rate, 20, 55), t1_ftr: g_t1_FTR, t1_ftPct: p.t1_FTP,
                t1_defStealRate: p.m_t2StealRate,
                t1_starUsage: t1Star.usage,
                t1_starFG2: t1Star.fg2Bonus, t1_starFG3: t1Star.fg3Bonus,
                t1_initMom: t1Mom,
                t1_benchDepth: p.t1Bench || 30,
                t1_defProfile: p.t2DefProfile || { perimeter: 0, interior: 0, overall: 0 },
                t1_starFoulState: t1StarFoulState,
                t1_starFoulProneness: p.t1StarFoulProneness || 0,
                t1_streakiness: p.t1Streakiness || 1.0,
                // T2 params
                t2_fg2: g_t2_FG2, t2_fg3: g_t2_FG3,
                t2_toPct: g_t2_TO, t2_orPct: g_t2_OR,
                t2_rate3: clamp(g_t2_3Rate, 20, 55), t2_ftr: g_t2_FTR, t2_ftPct: p.t2_FTP,
                t2_defStealRate: p.m_t1StealRate,
                t2_starUsage: t2Star.usage,
                t2_starFG2: t2Star.fg2Bonus, t2_starFG3: t2Star.fg3Bonus,
                t2_initMom: t2Mom,
                t2_benchDepth: p.t2Bench || 30,
                t2_defProfile: p.t1DefProfile || { perimeter: 0, interior: 0, overall: 0 },
                t2_starFoulState: t2StarFoulState,
                t2_starFoulProneness: p.t2StarFoulProneness || 0,
                t2_streakiness: p.t2Streakiness || 1.0,
            });
            const r1 = halfResult.t1;
            const r2 = halfResult.t2;

            // Plan 06: Carry foul state to next half
            t1StarFoulState = r1.starFoulState || { fouls: 0, isSitting: false };
            t2StarFoulState = r2.starFoulState || { fouls: 0, isSitting: false };
            if (half === 0) {
                if (t1StarFoulState.fouls < 4) t1StarFoulState.isSitting = false;
                if (t2StarFoulState.fouls < 4) t2StarFoulState.isSitting = false;
            }
            gameT1SatPoss += r1.starSatPoss || 0;
            gameT2SatPoss += r2.starSatPoss || 0;
            if (r1.starFouledOut) gameT1FouledOut = true;
            if (r2.starFouledOut) gameT2FouledOut = true;

            // Transition pts already folded into each team's points in interleaved sim
            s1 += r1.points;
            s2 += r2.points;

            t1Mom = r1.momentum * (half === 0 ? 0.3 : 1);
            t2Mom = r2.momentum * (half === 0 ? 0.3 : 1);

            gP1 += r1.possUsed; gP2 += r2.possUsed;
            g2_1 += r1.makes2; g2_2 += r2.makes2;
            g3_1 += r1.makes3; g3_2 += r2.makes3;
            gTO1 += r1.tos; gTO2 += r2.tos;
            gOR1 += r1.orebs; gOR2 += r2.orebs;
            gFT1 += r1.ftMade; gFT2 += r2.ftMade;
            totalTransition += 0; // transition pts folded into team points in interleaved sim
            totalT1Fatigue += r1.avgFatiguePenalty;
            totalT2Fatigue += r2.avgFatiguePenalty;
            totalT1RestPoss += r1.restPossessions;
            totalT2RestPoss += r2.restPossessions;
            totalCrunchPoss += r1.crunchTimePoss + r2.crunchTimePoss;
            totalDesperationPoss += r1.desperationPoss + r2.desperationPoss;
            totalIntentionalFouls += r1.intentionalFoulPoss + r2.intentionalFoulPoss;

            // Plan 08: Streak accumulators
            t1TotalHotPoss += r1.hotPossessions || 0;
            t2TotalHotPoss += r2.hotPossessions || 0;
            t1TotalColdPoss += r1.coldPossessions || 0;
            t2TotalColdPoss += r2.coldPossessions || 0;
            if ((r1.maxHotStreak || 0) > t1MaxHotEver) t1MaxHotEver = r1.maxHotStreak;
            if ((r2.maxHotStreak || 0) > t2MaxHotEver) t2MaxHotEver = r2.maxHotStreak;

            // Plan 09: Referee foul accumulators
            totalT1DefFouls += r1.defFouls;
            totalT2DefFouls += r2.defFouls;
            if (r1.bonusReachedAtPoss > 0 && r1.bonusReachedAtPoss < 20) t1EarlyBonusGames++;
            if (r2.bonusReachedAtPoss > 0 && r2.bonusReachedAtPoss < 20) t2EarlyBonusGames++;
        }

        // Plan 06: Per-game foul trouble accumulators
        t1StarSatTotal += gameT1SatPoss;
        t2StarSatTotal += gameT2SatPoss;
        if (gameT1FouledOut) t1StarFouledOutGames++;
        if (gameT2FouledOut) t2StarFouledOutGames++;
        if (gameT1SatPoss >= 3) t1StarFoulTroubleGames++;
        if (gameT2SatPoss >= 3) t2StarFoulTroubleGames++;

        const kpS1 = p.kpT1ExpOE * (gamePoss / 100);
        const kpS2 = p.kpT2ExpOE * (gamePoss / 100);
        s1 = s1 * 0.82 + kpS1 * 0.18;
        s2 = s2 * 0.82 + kpS2 * 0.18;

        s1 += p.totalAdj / 2 + p.hca1;
        s2 += -p.totalAdj / 2 + p.hca2;

        const rawMargin = Math.abs(s1 - s2);
        if (rawMargin <= 6) {
            const clutchScale = 1 - (rawMargin / 6);
            s1 += p.coachEdge * clutchScale;
            s2 -= p.coachEdge * clutchScale;
            s1 += p.ftClutchEdge * clutchScale * 0.5;
            s2 -= p.ftClutchEdge * clutchScale * 0.5;
        }

        // ── OVERTIME RESOLUTION ──
        let otPeriods = 0;
        const MAX_OT = 4;
        while (Math.abs(s1 - s2) <= 2 && otPeriods < MAX_OT) {
            otPeriods++;
            const t1LastPoss = (otPeriods % 2 === 1);

            const ot1 = simOvertime(
                g_t1_FG2, g_t1_FG3, g_t1_TO, g_t1_OR,
                g_t1_3Rate, g_t1_FTR, p.t1_FTP,
                p.m_t2StealRate, t1Star.usage, t1Star.fg2Bonus, t1Star.fg3Bonus,
                t1Mom, otPeriods, gameT1FouledOut, refClimate);
            const ot2 = simOvertime(
                g_t2_FG2, g_t2_FG3, g_t2_TO, g_t2_OR,
                g_t2_3Rate, g_t2_FTR, p.t2_FTP,
                p.m_t1StealRate, t2Star.usage, t2Star.fg2Bonus, t2Star.fg3Bonus,
                t2Mom, otPeriods, gameT2FouledOut, refClimate);

            s1 += ot1.points;
            s2 += ot2.points;

            if (Math.abs(s1 - s2) <= 1) {
                if (t1LastPoss) {
                    if (Math.random() < 0.38) { s1 += Math.random() < 0.30 ? 3 : 2; }
                } else {
                    if (Math.random() < 0.38) { s2 += Math.random() < 0.30 ? 3 : 2; }
                }
            }

            t1Mom = ot1.points > ot2.points ? 1 : ot1.points < ot2.points ? -1 : 0;
            t2Mom = -t1Mom;

            gP1 += ot1.possUsed; gP2 += ot2.possUsed;
            g2_1 += ot1.makes2; g2_2 += ot2.makes2;
            g3_1 += ot1.makes3; g3_2 += ot2.makes3;
            gTO1 += ot1.tos; gTO2 += ot2.tos;
            gOR1 += ot1.orebs; gOR2 += ot2.orebs;
            gFT1 += ot1.ftMade; gFT2 += ot2.ftMade;
        }

        // Score rounding and final tie-break
        s1 = Math.round(s1);
        s2 = Math.round(s2);
        if (s1 === s2) { if (Math.random() < 0.5) s1++; else s2++; }

        if (otPeriods > 0) {
            gamesWithOT++;
            totalOTPeriods += otPeriods;
            if (otPeriods >= 2) doubleOTGames++;
            if (otPeriods >= 3) tripleOTGames++;
        }

        totalT1Score += s1; totalT2Score += s2;
        const m = s1 - s2;
        marginDist.push(m);

        if (s1 > s2) { t1Wins++; if (!p.t1Favored) upsets++; }
        else if (s2 > s1) { t2Wins++; if (p.t1Favored) upsets++; }
        else ties++;
        if (Math.abs(m) >= 15) blowouts++;
        if (Math.abs(m) <= 5) closeGames++;

        totalT1Poss += gP1; totalT2Poss += gP2;
        totalT1_3Made += g3_1; totalT2_3Made += g3_2;
        totalT1_2Made += g2_1; totalT2_2Made += g2_2;
        totalT1_TOs += gTO1; totalT2_TOs += gTO2;
        totalT1_ORebs += gOR1; totalT2_ORebs += gOR2;
        totalT1_FTM += gFT1; totalT2_FTM += gFT2;
    }

    const numSims = p.numSims;
    const t1WinPct = (t1Wins + ties * 0.5) / numSims;
    const avgT1Score = totalT1Score / numSims;
    const avgT2Score = totalT2Score / numSims;
    const avgMargin = marginDist.reduce((a, b) => a + b, 0) / numSims;

    marginDist.sort((a, b) => a - b);
    const medianMargin = marginDist[Math.floor(numSims / 2)];
    const p10 = marginDist[Math.floor(numSims * 0.10)];
    const p25 = marginDist[Math.floor(numSims * 0.25)];
    const p75 = marginDist[Math.floor(numSims * 0.75)];
    const p90 = marginDist[Math.floor(numSims * 0.90)];

    self.postMessage({
        t1Score: avgT1Score, t2Score: avgT2Score,
        margin: avgMargin, medianMargin,
        p10, p25, p75, p90,
        t1WinProb: t1WinPct,
        t1Wins, t2Wins, ties,
        blowouts, closeGames, upsets,
        coachEdge: p.coachEdge, c1Exp: p.c1Exp, c2Exp: p.c2Exp, ftEdge: p.ftClutchEdge,
        heightEdge: p.t1HgtORBonus, t1Hgt: p.t1Hgt, t2Hgt: p.t2Hgt,
        t1HgtEff: p.t1HgtEff, t2HgtEff: p.t2HgtEff,
        t1EffSD: 3.2 * p.t1VolMod, t2EffSD: 3.2 * p.t2VolMod,
        tempo: p.gameTempoCtr, numSims,
        tempoStats: {
            avgContested: totalContestedTempo / numSims,
            t1Preferred: t1Pull,
            t2Preferred: t2Pull,
            avgH1Tempo: h1TempoTotal / numSims,
            avgH2Tempo: h2TempoTotal / numSims,
            t1TempoControl: t1Ctrl,
            t2TempoControl: t2Ctrl,
        },
        t1Star, t2Star,
        avgTransition: totalTransition / numSims,
        t1AvgPoss: totalT1Poss / numSims, t2AvgPoss: totalT2Poss / numSims,
        t1Avg3s: totalT1_3Made / numSims, t2Avg3s: totalT2_3Made / numSims,
        t1Avg2s: totalT1_2Made / numSims, t2Avg2s: totalT2_2Made / numSims,
        t1AvgTOs: totalT1_TOs / numSims, t2AvgTOs: totalT2_TOs / numSims,
        t1AvgORebs: totalT1_ORebs / numSims, t2AvgORebs: totalT2_ORebs / numSims,
        t1AvgFTM: totalT1_FTM / numSims, t2AvgFTM: totalT2_FTM / numSims,
        m_t1_FG2: p.m_t1_FG2, m_t2_FG2: p.m_t2_FG2,
        m_t1_FG3: p.m_t1_FG3, m_t2_FG3: p.m_t2_FG3,
        m_t1_TO: p.m_t1_TO, m_t2_TO: p.m_t2_TO,
        m_t1_OR: p.m_t1_OR, m_t2_OR: p.m_t2_OR,
        t1AvgFatigue: totalT1Fatigue / (numSims * 2),
        t2AvgFatigue: totalT2Fatigue / (numSims * 2),
        t1AvgRestPoss: totalT1RestPoss / (numSims * 2),
        t2AvgRestPoss: totalT2RestPoss / (numSims * 2),
        avgCrunchPoss: totalCrunchPoss / numSims,
        avgDesperationPoss: totalDesperationPoss / numSims,
        avgIntentionalFouls: totalIntentionalFouls / numSims,
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
        t1DefProfile: p.t1DefProfile || { perimeter: 0, interior: 0, overall: 0 },
        t2DefProfile: p.t2DefProfile || { perimeter: 0, interior: 0, overall: 0 },
        otStats: {
            gamesWithOT, otRate: gamesWithOT / numSims,
            avgOTPeriods: gamesWithOT > 0 ? totalOTPeriods / gamesWithOT : 0,
            doubleOTGames, tripleOTGames,
            doubleOTRate: doubleOTGames / numSims,
        },
        foulTrouble: {
            t1AvgSatPoss: t1StarSatTotal / numSims,
            t2AvgSatPoss: t2StarSatTotal / numSims,
            t1FouledOutRate: t1StarFouledOutGames / numSims,
            t2FouledOutRate: t2StarFouledOutGames / numSims,
            t1FoulTroubleRate: t1StarFoulTroubleGames / numSims,
            t2FoulTroubleRate: t2StarFoulTroubleGames / numSims,
        },
        streakStats: {
            t1AvgHotPoss: t1TotalHotPoss / (numSims * 2),
            t2AvgHotPoss: t2TotalHotPoss / (numSims * 2),
            t1AvgColdPoss: t1TotalColdPoss / (numSims * 2),
            t2AvgColdPoss: t2TotalColdPoss / (numSims * 2),
            t1MaxHotStreak: t1MaxHotEver,
            t2MaxHotStreak: t2MaxHotEver,
            t1Streakiness: p.t1Streakiness || 1.0,
            t2Streakiness: p.t2Streakiness || 1.0,
        },
        refStats: {
            foulClimate: refClimate,
            t1AvgFoulsDrawn: totalT1DefFouls / numSims,
            t2AvgFoulsDrawn: totalT2DefFouls / numSims,
            t1EarlyBonusRate: t1EarlyBonusGames / numSims,
            t2EarlyBonusRate: t2EarlyBonusGames / numSims,
        },
        label: 'Monte Carlo',
        desc: `${numSims.toLocaleString()} two-half sims (contested-tempo, foul-trouble, 3PT-streaks, ref-climate, defense-profiled, style-correlated, game-state, fatigue, transition, stars, momentum)`
    });
};
