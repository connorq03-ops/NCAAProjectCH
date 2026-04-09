// Golf Monte Carlo Simulation Web Worker
// Runs hole-by-hole tournament simulation off the main thread
// Port of golf/golf_mc_engine.py for client-side simulation

function randNormal(mean, sd) {
    let u = 0, v = 0;
    while (u === 0) u = Math.random();
    while (v === 0) v = Math.random();
    return mean + sd * Math.sqrt(-2 * Math.log(u)) * Math.cos(2 * Math.PI * v);
}

function clamp(val, lo, hi) { return Math.max(lo, Math.min(hi, val)); }

// ─── Constants ──────────────────────────────────────────────────────────────

const STREAK_DECAY = 0.6;
const CUT_LINE_POSITION = 65;
const MOMENTUM_CARRY_DECAY = 0.3;
const DEFAULT_PAR = 72;

// ─── Round Style Generation ─────────────────────────────────────────────────

function generateRoundStyle(volatility, streakiness) {
    const aggressionAxis = randNormal(0, 1.0) * volatility * 0.15;
    const consistencyAxis = randNormal(0, 1.0) * volatility * 0.12;
    const residualSD = 0.3;

    const birdieAdj = aggressionAxis * 0.04 + consistencyAxis * 0.02 + randNormal(0, residualSD * 0.01);
    const bogeyAdj = -aggressionAxis * 0.02 + consistencyAxis * 0.03 + randNormal(0, residualSD * 0.01);
    const puttingAdj = randNormal(0, 0.3) * streakiness * 0.05;
    const drivingAdj = aggressionAxis * 0.03 + randNormal(0, residualSD * 0.01);

    let styleLabel;
    if (aggressionAxis > 0.3) styleLabel = 'aggressive';
    else if (aggressionAxis < -0.3) styleLabel = 'conservative';
    else styleLabel = 'balanced';

    return { birdieAdj, bogeyAdj, puttingAdj, drivingAdj, styleLabel };
}

// ─── Hole Simulation ────────────────────────────────────────────────────────

function simHole(cfg) {
    const par = cfg.par;
    const difficultyRank = cfg.difficultyRank;
    const birdieRate = cfg.birdieRate;
    const bogeyRate = cfg.bogeyRate;
    const doubleRate = cfg.doubleRate;
    const eagleRate = cfg.eagleRate || 0.0;
    const momentum = cfg.momentum || 0.0;
    const streakiness = cfg.streakiness || 0.5;
    const weatherAdj = cfg.weatherAdj || 0.0;
    const pressureAdj = cfg.pressureAdj || 0.0;
    const fatigueAdj = cfg.fatigueAdj || 0.0;
    const playerSgForKeyStat = cfg.playerSgForKeyStat || 0.0;
    const roundStyle = cfg.roundStyle || {};

    const difficultyAdj = (19 - difficultyRank) / 18 * 0.03;
    const keyStatBonus = playerSgForKeyStat * 0.015;
    const weatherPenalty = Math.abs(weatherAdj) * 0.02;
    const fatiguePenalty = fatigueAdj * 0.01;

    const roundBirdieAdj = roundStyle.birdieAdj || 0.0;
    const roundBogeyAdj = roundStyle.bogeyAdj || 0.0;

    let effBirdie = clamp(
        birdieRate + roundBirdieAdj + momentum * streakiness * 0.02
        + keyStatBonus - weatherPenalty - fatiguePenalty - pressureAdj * 0.01,
        0.02, 0.55
    );
    let effBogey = clamp(
        bogeyRate - roundBogeyAdj - momentum * streakiness * 0.01
        + weatherPenalty + fatiguePenalty + difficultyAdj + pressureAdj * 0.005,
        0.05, 0.45
    );
    let effDouble = clamp(
        doubleRate + weatherPenalty * 0.5 + fatiguePenalty * 0.3
        + pressureAdj * 0.003,
        0.01, 0.15
    );
    let effEagle = (par === 5 && eagleRate > 0)
        ? clamp(eagleRate + keyStatBonus * 0.5, 0.005, 0.10)
        : 0.0;

    // Normalize
    const totalProb = effEagle + effBirdie + effBogey + effDouble;
    if (totalProb > 0.95) {
        const scale = 0.95 / totalProb;
        effEagle *= scale;
        effBirdie *= scale;
        effBogey *= scale;
        effDouble *= scale;
    }

    const roll = Math.random();
    let cum = 0.0;

    // Eagle
    cum += effEagle;
    if (roll < cum) {
        return {
            scoreRelToPar: -2,
            momentumAfter: clamp(momentum + 2.0, -3, 3),
            isBirdie: false, isBogey: false, isDouble: false, isEagle: true
        };
    }
    // Birdie
    cum += effBirdie;
    if (roll < cum) {
        return {
            scoreRelToPar: -1,
            momentumAfter: clamp(momentum + 1.0, -3, 3),
            isBirdie: true, isBogey: false, isDouble: false, isEagle: false
        };
    }
    // Bogey
    cum += effBogey;
    if (roll < cum) {
        return {
            scoreRelToPar: 1,
            momentumAfter: clamp(momentum - 1.0, -3, 3),
            isBirdie: false, isBogey: true, isDouble: false, isEagle: false
        };
    }
    // Double
    cum += effDouble;
    if (roll < cum) {
        return {
            scoreRelToPar: 2,
            momentumAfter: clamp(momentum - 2.0, -3, 3),
            isBirdie: false, isBogey: false, isDouble: true, isEagle: false
        };
    }
    // Par
    return {
        scoreRelToPar: 0,
        momentumAfter: momentum * STREAK_DECAY,
        isBirdie: false, isBogey: false, isDouble: false, isEagle: false
    };
}

// ─── Helper: birdie/bogey rate by par ───────────────────────────────────────

function getBirdieRateForPar(params, par) {
    if (par === 3) return params.birdieRatePar3 || 0.12;
    if (par === 5) return params.birdieRatePar5 || 0.45;
    return params.birdieRatePar4 || 0.18;
}

function getBogeyRateForPar(params, par) {
    if (par === 3) return params.bogeyRatePar3 || 0.22;
    if (par === 5) return params.bogeyRatePar5 || 0.12;
    return params.bogeyRatePar4 || 0.20;
}

// ─── Round Simulation ───────────────────────────────────────────────────────

function simRound(playerParams, holes, roundNumber, roundConfig) {
    roundConfig = roundConfig || {};

    const volatility = playerParams.roundVolatility || 2.8;
    const streakiness = playerParams.streakiness || 0.5;
    const style = generateRoundStyle(volatility, streakiness);

    let pressureAdj = 0.0;
    if (roundNumber >= 3) {
        pressureAdj = (playerParams.pressureModifier || 0.0) * 0.02 * (roundNumber - 2);
        const currentPos = roundConfig.currentPosition || 999;
        if (currentPos <= 5) pressureAdj *= 1.5;
    }

    const fatigueBase = (playerParams.fatigueFactor || 0.5) * 0.01 * (roundNumber - 1);

    const weatherAdj = playerParams.weatherAdj || 0.0;
    const weatherResilience = playerParams.weatherResilience || 0.5;
    let effectiveWeather = weatherAdj * (1.0 - weatherResilience * 0.5);

    if (roundConfig.weather) {
        const cAdj = roundConfig.weather.combinedAdj ?? roundConfig.weather.combined_adj;
        effectiveWeather = cAdj ?? effectiveWeather;
    }

    let momentum = roundConfig.carryMomentum || 0.0;
    let scoreToPar = 0;
    const holeScores = [];
    let birdies = 0, bogeys = 0, doublesPlus = 0, eagles = 0;
    let currentHot = 0, currentCold = 0, maxHot = 0, maxCold = 0;

    const coursePar = holes.reduce((s, h) => s + h.par, 0);

    for (let i = 0; i < holes.length; i++) {
        const hole = holes[i];
        const par = hole.par;
        const difficultyRank = hole.difficultyRank || hole.difficulty_rank || 9;
        const keyStat = hole.keyStat || hole.key_stat || 'sgApp';

        const fatigueAdj = fatigueBase + (i >= 13 ? 0.5 : 0.0) * (playerParams.fatigueFactor || 0.5) * 0.01;
        const playerSgForKey = playerParams[keyStat] || playerParams[keyStat.replace(/_([a-z])/g, (_, c) => c.toUpperCase())] || 0.0;

        const result = simHole({
            par,
            difficultyRank,
            birdieRate: getBirdieRateForPar(playerParams, par),
            bogeyRate: getBogeyRateForPar(playerParams, par),
            doubleRate: playerParams.doubleRate || playerParams.double_rate || 0.03,
            eagleRate: par === 5 ? (playerParams.eagleRatePar5 || playerParams.eagle_rate_par5 || 0.04) : 0.0,
            momentum,
            streakiness,
            weatherAdj: effectiveWeather,
            pressureAdj,
            fatigueAdj,
            playerSgForKeyStat: playerSgForKey,
            roundStyle: style
        });

        momentum = result.momentumAfter;
        scoreToPar += result.scoreRelToPar;
        holeScores.push(result.scoreRelToPar);

        if (result.isEagle) { eagles++; currentHot++; currentCold = 0; }
        else if (result.isBirdie) { birdies++; currentHot++; currentCold = 0; }
        else if (result.isBogey) { bogeys++; currentCold++; currentHot = 0; }
        else if (result.isDouble) { doublesPlus++; currentCold++; currentHot = 0; }
        else { currentHot = 0; currentCold = 0; }

        maxHot = Math.max(maxHot, currentHot);
        maxCold = Math.max(maxCold, currentCold);
    }

    return {
        scoreToPar,
        totalScore: coursePar + scoreToPar,
        holeScores,
        birdies,
        bogeys,
        doublesPlus,
        eagles,
        maxHotStreak: maxHot,
        maxColdStreak: maxCold,
        momentum
    };
}

// ─── Tournament Simulation ──────────────────────────────────────────────────

function simTournamentSingle(players, holes, weatherPerRound) {
    const coursePar = holes.reduce((s, h) => s + h.par, 0);

    // Init player state
    const state = {};
    for (const p of players) {
        const name = p._player_name || p.playerName || ('Player_' + Math.random().toString(36).slice(2, 8));
        state[name] = {
            params: p, rounds: [], totalToPar: 0, momentum: 0.0,
            madeCut: true, birdies: 0, bogeys: 0, doublesPlus: 0, eagles: 0
        };
    }

    // Rounds 1-2
    for (let rd = 1; rd <= 2; rd++) {
        const weather = (weatherPerRound && weatherPerRound[rd - 1]) || null;
        for (const name of Object.keys(state)) {
            const s = state[name];
            const cfg = { carryMomentum: s.momentum * MOMENTUM_CARRY_DECAY, isWeekend: false, currentPosition: 999, weather };
            const res = simRound(s.params, holes, rd, cfg);
            s.rounds.push(res.scoreToPar);
            s.totalToPar += res.scoreToPar;
            s.momentum = res.momentum;
            s.birdies += res.birdies;
            s.bogeys += res.bogeys;
            s.doublesPlus += res.doublesPlus;
            s.eagles += res.eagles;
        }
    }

    // Cut after R2
    const entries = Object.entries(state).sort((a, b) => a[1].totalToPar - b[1].totalToPar);
    let cutScore = 0;
    if (entries.length > CUT_LINE_POSITION) {
        cutScore = entries[CUT_LINE_POSITION - 1][1].totalToPar;
        for (const [, s] of Object.entries(state)) {
            if (s.totalToPar > cutScore) s.madeCut = false;
        }
    } else if (entries.length > 0) {
        cutScore = entries[entries.length - 1][1].totalToPar;
    }

    const playersMadeCut = Object.values(state).filter(s => s.madeCut).length;

    // Rounds 3-4
    for (let rd = 3; rd <= 4; rd++) {
        const weather = (weatherPerRound && weatherPerRound[rd - 1]) || null;
        const active = Object.entries(state).filter(([, s]) => s.madeCut).sort((a, b) => a[1].totalToPar - b[1].totalToPar);
        const posMap = {};
        active.forEach(([n], i) => { posMap[n] = i + 1; });

        for (const name of Object.keys(state)) {
            const s = state[name];
            if (!s.madeCut) continue;
            const cfg = { carryMomentum: s.momentum * MOMENTUM_CARRY_DECAY, isWeekend: true, currentPosition: posMap[name] || 999, weather };
            const res = simRound(s.params, holes, rd, cfg);
            s.rounds.push(res.scoreToPar);
            s.totalToPar += res.scoreToPar;
            s.momentum = res.momentum;
            s.birdies += res.birdies;
            s.bogeys += res.bogeys;
            s.doublesPlus += res.doublesPlus;
            s.eagles += res.eagles;
        }
    }

    // Build standings
    const standings = [];
    for (const [name, s] of Object.entries(state)) {
        standings.push({
            playerName: name, totalToPar: s.totalToPar,
            total: coursePar * s.rounds.length + s.totalToPar,
            rounds: s.rounds.slice(), madeCut: s.madeCut,
            birdies: s.birdies, bogeys: s.bogeys
        });
    }
    standings.sort((a, b) => {
        if (a.madeCut !== b.madeCut) return a.madeCut ? -1 : 1;
        return a.totalToPar - b.totalToPar;
    });

    let pos = 1;
    for (let i = 0; i < standings.length; i++) {
        if (i > 0 && standings[i].totalToPar === standings[i - 1].totalToPar && standings[i].madeCut === standings[i - 1].madeCut) {
            standings[i].position = standings[i - 1].position;
        } else {
            standings[i].position = pos;
        }
        pos++;
    }

    // Winner (playoff for ties)
    const cutStandings = standings.filter(s => s.madeCut);
    let winner = '';
    if (cutStandings.length > 0) {
        const best = cutStandings[0].totalToPar;
        const tied = cutStandings.filter(s => s.totalToPar === best);
        if (tied.length === 1) {
            winner = tied[0].playerName;
        } else {
            // Weighted random playoff
            const weights = tied.map(t => {
                const sg = state[t.playerName].params.sg_total_adj || state[t.playerName].params.sgTotalAdj || 0;
                return Math.exp(sg * 0.3);
            });
            const totalW = weights.reduce((a, b) => a + b, 0);
            let roll = Math.random() * totalW, cum = 0;
            for (let i = 0; i < tied.length; i++) {
                cum += weights[i];
                if (roll < cum) { winner = tied[i].playerName; break; }
            }
            if (!winner) winner = tied[tied.length - 1].playerName;
        }
    }

    return { standings, cutLine: cutScore, playersMadeCut, winner };
}

// ─── Worker Message Handler ─────────────────────────────────────────────────

self.onmessage = function(e) {
    const { players, holes, numSims, weatherPerRound } = e.data;
    const n = numSims || 1000;
    const progressInterval = Math.max(1, Math.floor(n / 10));

    // Initialize accumulators
    const accum = {};
    for (const p of players) {
        const name = p._player_name || p.playerName || 'Unknown';
        accum[name] = {
            wins: 0, top5: 0, top10: 0, top20: 0, cutsMade: 0,
            totalFinish: 0, totalScoreToPar: 0, totalBirdies: 0, totalBogeys: 0,
            totalRounds: 0, bestFinish: 999, worstFinish: 0
        };
    }

    for (let i = 0; i < n; i++) {
        const result = simTournamentSingle(players, holes, weatherPerRound);

        if (result.winner && accum[result.winner]) {
            accum[result.winner].wins++;
        }

        for (const entry of result.standings) {
            const name = entry.playerName;
            if (!accum[name]) continue;
            const a = accum[name];
            const pos = entry.position;
            a.totalFinish += pos;
            a.totalScoreToPar += entry.totalToPar;
            a.totalBirdies += entry.birdies || 0;
            a.totalBogeys += entry.bogeys || 0;
            a.totalRounds += entry.rounds.length;
            if (entry.madeCut) a.cutsMade++;
            if (pos <= 5) a.top5++;
            if (pos <= 10) a.top10++;
            if (pos <= 20) a.top20++;
            a.bestFinish = Math.min(a.bestFinish, pos);
            a.worstFinish = Math.max(a.worstFinish, pos);
        }

        // Progress updates every 10%
        if ((i + 1) % progressInterval === 0) {
            self.postMessage({ type: 'progress', pct: Math.round((i + 1) / n * 100) });
        }
    }

    // Build final results
    const output = {};
    for (const [name, a] of Object.entries(accum)) {
        const totalRounds = Math.max(a.totalRounds, 1);
        output[name] = {
            winPct: a.wins / n * 100,
            top5Pct: a.top5 / n * 100,
            top10Pct: a.top10 / n * 100,
            top20Pct: a.top20 / n * 100,
            cutPct: a.cutsMade / n * 100,
            avgFinish: a.totalFinish / n,
            avgScore: a.totalScoreToPar / n,
            avgBirdiesPerRound: a.totalBirdies / totalRounds,
            avgBogeysPerRound: a.totalBogeys / totalRounds,
            bestFinish: a.bestFinish < 999 ? a.bestFinish : 0,
            worstFinish: a.worstFinish
        };
    }

    self.postMessage({ type: 'result', data: output });
};
