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

function simHalf(halfPoss, fg2, fg3, toPct, orPct, rate3, ftr, ftPct,
                 defStealRate, starUsage, starFG2, starFG3, initMom,
                 benchDepth, isSecondHalf, incomingLead, defProfile) {
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

        // ── Bench Rotation: Star Rest ──
        const restWindowStart = Math.floor(halfPoss * 0.28);
        const restWindowEnd = Math.floor(halfPoss * 0.52);
        const inRestWindow = possUsed >= restWindowStart && possUsed <= restWindowEnd;
        const restProb = inRestWindow ? clamp(0.15 + (benchDepth / 100) * 0.65, 0.15, 0.75) : 0;
        const isRestPoss = Math.random() < restProb;
        if (isRestPoss) restPossCount++;
        const effectiveStarUsage = isRestPoss ? starUsage * 0.15 : starUsage;

        const isStar = Math.random() < effectiveStarUsage;
        const sFG2 = isStar ? starFG2 : 0;
        const sFG3 = isStar ? starFG3 : 0;

        // ── Defensive Disruption: Shot Quality ──
        const defP = defProfile || { perimeter: 0, interior: 0, overall: 0 };
        const isDisruptedPoss = Math.random() < defP.overall * 0.6;
        const disrupt3Mod = isDisruptedPoss ? -(defP.perimeter * 5.0) : 0;
        const disrupt2Mod = isDisruptedPoss ? -(defP.interior * 4.0) : 0;
        const disruptStarMod = isDisruptedPoss ? (1 - defP.overall * 0.5) : 1.0;

        const momFG = mom * 0.4;

        if (Math.random() * 100 < (toPct + gs_toPctAdj) * fatigueTOMod) {
            tos++;
            mom = Math.max(mom - 1, -2);
            if (Math.random() < (defStealRate / Math.max(toPct, 8)) * 0.65) {
                const r = Math.random();
                if (r < 0.55) { transitionPts += 2; runningLead -= 2; }
                else if (r < 0.70) { transitionPts += 3; runningLead -= 3; }
            }
            continue;
        }

        const drewFoul = Math.random() < 0.20;
        if (drewFoul) defFouls++;

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
            continue;
        }

        if (!drewFoul && Math.random() < ftr / 100 * 0.38) {
            defFouls++;
            const numFTs = Math.random() < 0.25 ? 3 : 2;
            let made = 0;
            for (let f = 0; f < numFTs; f++) {
                ftAtt++;
                if (Math.random() * 100 < ftPct) { points++; ftMade++; made++; runningLead += 1; }
            }
            mom = made > 0 ? Math.min(mom + 0.5, 3) : Math.max(mom - 0.5, -2);
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
                continue;
            }
        }

        attempts++;
        const is3pt = Math.random() * 100 < clamp(rate3 + gs_3RateAdj, 15, 65);
        if (is3pt) {
            if (Math.random() * 100 < (fg3 + sFG3 * disruptStarMod + momFG * 0.5 - gs_fgPenalty + disrupt3Mod) * fatigueFGMod) {
                points += 3; makes3++; runningLead += 3;
                mom = Math.min(mom + 1.5, 3);
                if (Math.random() < 0.02) {
                    defFouls++;
                    ftAtt++;
                    if (Math.random() * 100 < ftPct) { points++; ftMade++; runningLead += 1; }
                }
            } else {
                mom = Math.max(mom - 0.5, -2);
                if (Math.random() * 100 < orPct * 0.80) { possLeft++; orebs++; }
            }
        } else {
            if (Math.random() * 100 < (fg2 + sFG2 * disruptStarMod + momFG * 0.7 - gs_fgPenalty * 0.5 + disrupt2Mod) * fatigueFGMod) {
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
    }
    return { points, possUsed, makes2, makes3, tos, ftMade, ftAtt, orebs, attempts,
             transitionPts, momentum: mom, defFouls,
             avgFatiguePenalty: possUsed > 0 ? totalFatiguePenalty / possUsed : 0,
             restPossessions: restPossCount,
             crunchTimePoss, desperationPoss, intentionalFoulPoss,
             finalLead: runningLead };
}

function simOvertime(fg2, fg3, toPct, orPct, rate3, ftr, ftPct,
                     defStealRate, starUsage, starFG2, starFG3, momentum,
                     otNumber) {
    const OT_POSSESSIONS = 5;
    let points = 0, possUsed = 0;
    let makes2 = 0, makes3 = 0, tos = 0;
    let ftMade = 0, ftAtt = 0, orebs = 0;
    let mom = momentum * 0.5;

    const otFatiguePenalty = 0.04 + (otNumber - 1) * 0.025;
    const fatigueFGMod = 1 - otFatiguePenalty;
    const fatigueTOMod = 1 + otFatiguePenalty * 0.6;
    const otFTRBoost = 8;
    const effectiveFTR = ftr + otFTRBoost;

    let possLeft = OT_POSSESSIONS;

    while (possLeft > 0) {
        possLeft--;
        possUsed++;

        const isStar = Math.random() < starUsage;
        const sFG2 = isStar ? starFG2 * fatigueFGMod : 0;
        const sFG3 = isStar ? starFG3 * fatigueFGMod : 0;
        const momFG = mom * 0.3;

        if (Math.random() * 100 < toPct * fatigueTOMod) {
            tos++;
            mom = Math.max(mom - 1, -2);
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
            continue;
        }

        const is3pt = Math.random() * 100 < rate3;
        if (is3pt) {
            if (Math.random() * 100 < (fg3 + sFG3 + momFG * 0.5) * fatigueFGMod) {
                points += 3; makes3++;
                mom = Math.min(mom + 1.5, 2);
            } else {
                mom = Math.max(mom - 0.5, -2);
                if (Math.random() * 100 < orPct * 0.75) { possLeft++; orebs++; }
            }
        } else {
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

    for (let i = 0; i < p.numSims; i++) {
        // ── Shared game environment factor ──
        const gameFactor = randNormal(0, 1.2);
        const gamePoss = clamp(randNormal(p.gameTempoCtr, 3.0), 55, 85);

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
        let g_t1_FG2 = clamp(p.m_t1_FG2 + t1Style.fg2Adj + gameFactor * 0.25, 28, 68);
        let g_t2_FG2 = clamp(p.m_t2_FG2 + t2Style.fg2Adj + gameFactor * 0.25, 28, 68);
        let g_t1_FG3 = clamp(p.m_t1_FG3 + t1Style.fg3Adj + gameFactor * 0.15, 18, 48);
        let g_t2_FG3 = clamp(p.m_t2_FG3 + t2Style.fg3Adj + gameFactor * 0.15, 18, 48);
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

        const t1StarFT = t1Star.usage > 0 && Math.random() < 0.08;
        const t2StarFT = t2Star.usage > 0 && Math.random() < 0.08;

        let s1 = 0, s2 = 0;
        let t1Mom = 0, t2Mom = 0;
        let gP1 = 0, gP2 = 0, g2_1 = 0, g2_2 = 0, g3_1 = 0, g3_2 = 0;
        let gTO1 = 0, gTO2 = 0, gOR1 = 0, gOR2 = 0, gFT1 = 0, gFT2 = 0;

        for (let half = 0; half < 2; half++) {
            const halfPoss = gamePoss / 2;

            // Game-state adjustments now handled per-possession inside simHalf()
            const t1IncomingLead = half === 0 ? 0 : (s1 - s2);
            const t2IncomingLead = half === 0 ? 0 : (s2 - s1);

            const t1SFT = t1StarFT && half === 1;
            const t2SFT = t2StarFT && half === 1;
            const t1StarDeg = t1SFT ? -1.5 : 0;
            const t2StarDeg = t2SFT ? -1.5 : 0;

            const r1 = simHalf(halfPoss,
                g_t1_FG2 + t1StarDeg, g_t1_FG3 + t1StarDeg * 0.7,
                g_t1_TO, g_t1_OR,
                clamp(g_t1_3Rate, 20, 55), g_t1_FTR, p.t1_FTP,
                p.m_t2StealRate,
                t1SFT ? t1Star.usage * 0.3 : t1Star.usage,
                t1Star.fg2Bonus, t1Star.fg3Bonus,
                t1Mom,
                p.t1Bench || 30, half === 1, t1IncomingLead,
                p.t2DefProfile || { perimeter: 0, interior: 0, overall: 0 });

            const r2 = simHalf(halfPoss,
                g_t2_FG2 + t2StarDeg, g_t2_FG3 + t2StarDeg * 0.7,
                g_t2_TO, g_t2_OR,
                clamp(g_t2_3Rate, 20, 55), g_t2_FTR, p.t2_FTP,
                p.m_t1StealRate,
                t2SFT ? t2Star.usage * 0.3 : t2Star.usage,
                t2Star.fg2Bonus, t2Star.fg3Bonus,
                t2Mom,
                p.t2Bench || 30, half === 1, t2IncomingLead,
                p.t1DefProfile || { perimeter: 0, interior: 0, overall: 0 });

            s1 += r1.points + r2.transitionPts;
            s2 += r2.points + r1.transitionPts;

            t1Mom = r1.momentum * (half === 0 ? 0.3 : 1);
            t2Mom = r2.momentum * (half === 0 ? 0.3 : 1);

            gP1 += r1.possUsed; gP2 += r2.possUsed;
            g2_1 += r1.makes2; g2_2 += r2.makes2;
            g3_1 += r1.makes3; g3_2 += r2.makes3;
            gTO1 += r1.tos; gTO2 += r2.tos;
            gOR1 += r1.orebs; gOR2 += r2.orebs;
            gFT1 += r1.ftMade; gFT2 += r2.ftMade;
            totalTransition += r1.transitionPts + r2.transitionPts;
            totalT1Fatigue += r1.avgFatiguePenalty;
            totalT2Fatigue += r2.avgFatiguePenalty;
            totalT1RestPoss += r1.restPossessions;
            totalT2RestPoss += r2.restPossessions;
            totalCrunchPoss += r1.crunchTimePoss + r2.crunchTimePoss;
            totalDesperationPoss += r1.desperationPoss + r2.desperationPoss;
            totalIntentionalFouls += r1.intentionalFoulPoss + r2.intentionalFoulPoss;
        }

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
                t1Mom, otPeriods);
            const ot2 = simOvertime(
                g_t2_FG2, g_t2_FG3, g_t2_TO, g_t2_OR,
                g_t2_3Rate, g_t2_FTR, p.t2_FTP,
                p.m_t1StealRate, t2Star.usage, t2Star.fg2Bonus, t2Star.fg3Bonus,
                t2Mom, otPeriods);

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
        label: 'Monte Carlo',
        desc: `${numSims.toLocaleString()} two-half sims (defense-profiled, style-correlated, game-state, fatigue, transition, stars, fouls, momentum)`
    });
};
