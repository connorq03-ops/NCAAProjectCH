// Monte Carlo Simulation Web Worker
// Runs possession-level two-half basketball simulation off the main thread

function randNormal(mean, sd) {
    let u = 0, v = 0;
    while (u === 0) u = Math.random();
    while (v === 0) v = Math.random();
    return mean + sd * Math.sqrt(-2 * Math.log(u)) * Math.cos(2 * Math.PI * v);
}

function clamp(val, min, max) { return Math.max(min, Math.min(max, val)); }

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
                 defStealRate, starUsage, starFG2, starFG3, initMom) {
    let points = 0, possUsed = 0, makes2 = 0, makes3 = 0, tos = 0;
    let ftMade = 0, ftAtt = 0, orebs = 0, attempts = 0;
    let transitionPts = 0;
    let possLeft = Math.round(halfPoss);
    const maxPoss = possLeft + 10;
    let mom = initMom;
    let defFouls = 0;

    while (possLeft > 0 && possUsed < maxPoss) {
        possLeft--; possUsed++;

        const isStar = Math.random() < starUsage;
        const sFG2 = isStar ? starFG2 : 0;
        const sFG3 = isStar ? starFG3 : 0;

        const momFG = mom * 0.4;

        if (Math.random() * 100 < toPct) {
            tos++;
            mom = Math.max(mom - 1, -2);
            if (Math.random() < (defStealRate / Math.max(toPct, 8)) * 0.65) {
                const r = Math.random();
                if (r < 0.55) transitionPts += 2;
                else if (r < 0.70) transitionPts += 3;
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
                    if (Math.random() * 100 < ftPct) { points++; ftMade++; bonusMade++; }
                }
            } else {
                ftAtt++;
                if (Math.random() * 100 < ftPct) {
                    points++; ftMade++; bonusMade++;
                    ftAtt++;
                    if (Math.random() * 100 < ftPct) { points++; ftMade++; bonusMade++; }
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
                if (Math.random() * 100 < ftPct) { points++; ftMade++; made++; }
            }
            mom = made > 0 ? Math.min(mom + 0.5, 3) : Math.max(mom - 0.5, -2);
            continue;
        }

        attempts++;
        const is3pt = Math.random() * 100 < rate3;
        if (is3pt) {
            if (Math.random() * 100 < fg3 + sFG3 + momFG * 0.5) {
                points += 3; makes3++;
                mom = Math.min(mom + 1.5, 3);
                if (Math.random() < 0.02) {
                    defFouls++;
                    ftAtt++;
                    if (Math.random() * 100 < ftPct) { points++; ftMade++; }
                }
            } else {
                mom = Math.max(mom - 0.5, -2);
                if (Math.random() * 100 < orPct * 0.80) { possLeft++; orebs++; }
            }
        } else {
            if (Math.random() * 100 < fg2 + sFG2 + momFG * 0.7) {
                points += 2; makes2++;
                mom = Math.min(mom + 1, 3);
                if (Math.random() < 0.06) {
                    defFouls++;
                    ftAtt++;
                    if (Math.random() * 100 < ftPct) { points++; ftMade++; }
                }
            } else {
                mom = Math.max(mom - 0.5, -2);
                if (Math.random() * 100 < orPct) { possLeft++; orebs++; }
            }
        }
    }
    return { points, possUsed, makes2, makes3, tos, ftMade, ftAtt, orebs, attempts,
             transitionPts, momentum: mom, defFouls };
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

        const t1StarFT = t1Star.usage > 0 && Math.random() < 0.08;
        const t2StarFT = t2Star.usage > 0 && Math.random() < 0.08;

        let s1 = 0, s2 = 0;
        let t1Mom = 0, t2Mom = 0;
        let gP1 = 0, gP2 = 0, g2_1 = 0, g2_2 = 0, g3_1 = 0, g3_2 = 0;
        let gTO1 = 0, gTO2 = 0, gOR1 = 0, gOR2 = 0, gFT1 = 0, gFT2 = 0;

        for (let half = 0; half < 2; half++) {
            const halfPoss = gamePoss / 2;

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

            const t1SFT = t1StarFT && half === 1;
            const t2SFT = t2StarFT && half === 1;
            const t1StarDeg = t1SFT ? -1.5 : 0;
            const t2StarDeg = t2SFT ? -1.5 : 0;

            const r1 = simHalf(halfPoss,
                g_t1_FG2 + t1_FG2Adj + t1StarDeg, g_t1_FG3 + t1StarDeg * 0.7,
                g_t1_TO + t1_TOAdj, g_t1_OR,
                clamp(p.t1_3Rate + t1_3Adj, 20, 55), p.m_t1_FTR, p.t1_FTP,
                p.m_t2StealRate,
                t1SFT ? t1Star.usage * 0.3 : t1Star.usage,
                t1Star.fg2Bonus, t1Star.fg3Bonus,
                t1Mom);

            const r2 = simHalf(halfPoss,
                g_t2_FG2 + t2_FG2Adj + t2StarDeg, g_t2_FG3 + t2StarDeg * 0.7,
                g_t2_TO + t2_TOAdj, g_t2_OR,
                clamp(p.t2_3Rate + t2_3Adj, 20, 55), p.m_t2_FTR, p.t2_FTP,
                p.m_t1StealRate,
                t2SFT ? t2Star.usage * 0.3 : t2Star.usage,
                t2Star.fg2Bonus, t2Star.fg3Bonus,
                t2Mom);

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
        t1EffSD: SHOT_SWING_SD * p.t1VolMod, t2EffSD: SHOT_SWING_SD * p.t2VolMod,
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
        label: 'Monte Carlo',
        desc: `${numSims.toLocaleString()} two-half sims (transition, stars, fouls, momentum)`
    });
};
