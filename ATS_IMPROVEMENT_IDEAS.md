# ATS Model Improvement Ideas

Five strategies to learn from Against The Spread results and improve future predictions.

---

## 1. Spread Calibration Feedback Loop

Track the average signed error between our predicted spread and the actual result, segmented by spread size. If we consistently overpredict blowouts (e.g., we say -15, result is -8), tighten the `calibrateSpread()` dampening curve. If we underpredict close games, loosen it. Store historical ATS errors in a SQLite table and adjust the piecewise coefficients (currently 0.92/0.85/log) daily.

**Effort:** Medium | **Impact:** High

## 2. Conference-Specific Line Bias Detection

Track ATS hit rate by conference. If we consistently lose ATS bets in the SEC but crush them in the WCC, it suggests our conference strength adjustments (`calcConfAdj`) are miscalibrated for certain leagues. Weight the `confMap` ratings up or down based on observed ATS performance per conference over the last 30 days.

**Effort:** Medium | **Impact:** Medium

## 3. Model Weight Rebalancing Based on ATS Edge

Currently the composite uses fixed weights (KenPom 10%, SOS 10%, ConRat 20%, MC 60%). Track which individual sub-model's spread would have produced the best ATS record. If Monte Carlo consistently has the best ATS but ConRat is dragging it down, dynamically shift weights toward the sub-model with the highest rolling ATS hit rate over the last N games.

**Effort:** High | **Impact:** High

## 4. Line Value Threshold — Only Bet When Edge Is Large ✅ IMPLEMENTED

Not every game where our spread differs from DK is worth betting. Track ATS hit rate by the **size of disagreement** between our spread and the DK line (|ourSpread - dkSpread|). If bets with <1pt edge are coin flips but >3pt edge hits 65%, add a minimum edge threshold filter and only count ATS bets where our model has meaningful conviction.

**Effort:** Low | **Impact:** High

## 5. Situational Spot Adjustments

Track ATS results by game context: neutral site vs. home, back-to-back games, conference tournament rounds, early vs. late season. If the model consistently misses ATS on neutral-site conference tournament games, add a situational dampening factor — e.g., reduce predicted margin by 10% for neutral site tourney games where travel/fatigue aren't captured by KenPom.

**Effort:** Medium | **Impact:** Medium

---

## Implementation Priority

1. **#4** (edge threshold) — quickest win, just filter ATS display
2. **#1** (calibration feedback) — biggest long-term accuracy gain
3. **#3** (weight rebalancing) — needs historical data pipeline
4. **#2** (conference bias) — useful once we have enough sample size
5. **#5** (situational spots) — requires game context tagging
