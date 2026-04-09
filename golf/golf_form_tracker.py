"""
Golf Form Tracker Module.
Tracks recent tournament results, computes form trends, and detects
performance shifts for PGA Tour players.

New component with no direct basketball equivalent (closest is star_scraper.py
which dynamically builds star player data).

Uses DataGolfClient (when available) for historical round data, and the
GolfWDCache pattern for caching form results.
"""

import os
import json
import hashlib
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any

from golf.golf_wd_scraper import GolfWDCache
from golf.golf_elite_players import get_player_info


# ═══════════════════════════════════════════════════════════════
# Form Tracker
# ═══════════════════════════════════════════════════════════════

class FormTracker:
    """Tracks player form over recent tournaments."""

    def __init__(self, client=None):
        """
        Args:
            client: optional DataGolfClient for fetching historical data
        """
        self.client = client
        self.cache = GolfWDCache(cache_dir=".golf_form_cache")

    def get_recent_results(self, player_name: str, num_events: int = 12) -> List[Dict]:
        """Get a player's last N tournament results.

        Returns list of dicts: [{tournament, finish, score_to_par, sg_total, date}, ...]

        If client is available, fetches from DataGolf historical rounds API.
        Otherwise returns empty list.
        """
        if self.client is None:
            return []

        cache_key = f"recent_results_{player_name}_{num_events}"
        cached = self.cache.get(cache_key, max_age_minutes=360)
        if cached and 'results' in cached:
            return cached['results']

        try:
            rounds_data = self.client.get_historical_rounds(tour='pga')
            if not isinstance(rounds_data, list):
                return []

            # Filter for this player (case-insensitive match)
            name_lower = player_name.lower()
            player_rounds = [
                r for r in rounds_data
                if r.get('player_name', '').lower() == name_lower
            ]

            # Group by event and take the most recent num_events
            events = {}
            for r in player_rounds:
                event_id = r.get('event_id', r.get('event_name', ''))
                if event_id not in events:
                    events[event_id] = {
                        'tournament': r.get('event_name', event_id),
                        'finish': r.get('fin_text', ''),
                        'score_to_par': r.get('total_to_par', 0),
                        'sg_total': r.get('sg_total', 0.0),
                        'date': r.get('start_date', r.get('date', '')),
                    }

            # Sort by date descending, take num_events
            results = sorted(
                events.values(),
                key=lambda x: x.get('date', ''),
                reverse=True,
            )[:num_events]

            self.cache.set(cache_key, {'results': results})
            return results
        except Exception as e:
            print(f"[FormTracker] Error fetching results for {player_name}: {e}")
            return []

    def calc_form_metrics(self, results: List[Dict]) -> Dict[str, Any]:
        """Calculate form metrics from recent results.

        Returns dict with averages, trends, cuts made, top10s, wins, consistency.
        """
        if not results:
            return {
                "last_4_avg_finish": 0.0,
                "last_8_avg_finish": 0.0,
                "last_12_avg_finish": 0.0,
                "last_4_avg_sg": 0.0,
                "last_8_avg_sg": 0.0,
                "last_12_avg_sg": 0.0,
                "trend": 0.0,
                "trend_label": "stable",
                "cuts_made_last_8": 0,
                "top10s_last_8": 0,
                "wins_last_12": 0,
                "consistency": 0.5,
            }

        # Parse finish positions (handle "T3", "CUT", "WD", etc.)
        def parse_finish(finish_str: str) -> Optional[int]:
            if not finish_str:
                return None
            clean = str(finish_str).strip().upper()
            if clean in ('CUT', 'MC', 'WD', 'DQ', 'DNS', 'W/D'):
                return None
            clean = clean.lstrip('T')
            try:
                return int(clean)
            except (ValueError, TypeError):
                return None

        finishes = []
        for r in results:
            pos = parse_finish(r.get('finish', ''))
            if pos is not None:
                finishes.append(pos)

        sg_values = [r.get('sg_total', 0.0) for r in results if r.get('sg_total') is not None]

        # Averages for different windows
        def avg(lst: list, n: int) -> float:
            subset = lst[:n] if len(lst) >= n else lst
            return sum(subset) / len(subset) if subset else 0.0

        last_4_avg_finish = avg(finishes, 4)
        last_8_avg_finish = avg(finishes, 8)
        last_12_avg_finish = avg(finishes, 12)
        last_4_avg_sg = avg(sg_values, 4)
        last_8_avg_sg = avg(sg_values, 8)
        last_12_avg_sg = avg(sg_values, 12)

        # Trend calculation
        trend = self.calc_trend(results)

        # Get career SG for form label
        player_info = None
        # We don't have career SG in elite_players, use last_12 as baseline
        career_sg_estimate = last_12_avg_sg
        trend_label = self.get_form_label(trend, last_4_avg_sg, career_sg_estimate)

        # Cuts made in last 8
        last_8_results = results[:8]
        cuts_made = sum(
            1 for r in last_8_results
            if parse_finish(r.get('finish', '')) is not None
        )

        # Top 10s in last 8
        top10s = sum(
            1 for r in last_8_results
            if (parse_finish(r.get('finish', '')) or 99) <= 10
        )

        # Wins in last 12
        wins = sum(
            1 for r in results[:12]
            if parse_finish(r.get('finish', '')) == 1
        )

        # Consistency: based on finish position variance (lower variance = higher consistency)
        if len(finishes) >= 2:
            mean_finish = sum(finishes) / len(finishes)
            variance = sum((f - mean_finish) ** 2 for f in finishes) / len(finishes)
            std_dev = variance ** 0.5
            # Normalize: std_dev of 5 = high consistency (0.85), std_dev of 25 = low (0.25)
            consistency = max(0.0, min(1.0, 1.0 - (std_dev - 5) / 25))
        else:
            consistency = 0.5

        return {
            "last_4_avg_finish": round(last_4_avg_finish, 1),
            "last_8_avg_finish": round(last_8_avg_finish, 1),
            "last_12_avg_finish": round(last_12_avg_finish, 1),
            "last_4_avg_sg": round(last_4_avg_sg, 3),
            "last_8_avg_sg": round(last_8_avg_sg, 3),
            "last_12_avg_sg": round(last_12_avg_sg, 3),
            "trend": round(trend, 4),
            "trend_label": trend_label,
            "cuts_made_last_8": cuts_made,
            "top10s_last_8": top10s,
            "wins_last_12": wins,
            "consistency": round(consistency, 3),
        }

    def calc_trend(self, results: List[Dict]) -> float:
        """Calculate form trend using weighted linear regression.

        More recent events weighted higher.
        Returns float: slope of trend line (positive = improving).
        """
        if len(results) < 2:
            return 0.0

        sg_values = []
        for r in results:
            sg = r.get('sg_total')
            if sg is not None:
                sg_values.append(float(sg))

        if len(sg_values) < 2:
            return 0.0

        n = len(sg_values)
        # Reverse so index 0 = oldest, index n-1 = most recent
        sg_reversed = list(reversed(sg_values))

        # Weights: more recent events get higher weight
        # Weight = position index + 1 (so most recent = n, oldest = 1)
        weights = [i + 1 for i in range(n)]
        total_weight = sum(weights)

        # Weighted means
        w_mean_x = sum(w * i for i, w in enumerate(weights)) / total_weight
        w_mean_y = sum(w * y for w, y in zip(weights, sg_reversed)) / total_weight

        # Weighted slope
        numerator = sum(
            w * (i - w_mean_x) * (y - w_mean_y)
            for i, (w, y) in enumerate(zip(weights, sg_reversed))
        )
        denominator = sum(
            w * (i - w_mean_x) ** 2
            for i, w in enumerate(weights)
        )

        if abs(denominator) < 1e-10:
            return 0.0

        slope = numerator / denominator
        return slope

    def get_form_label(self, trend: float, last_4_sg: float, career_sg: float) -> str:
        """Convert trend + recent SG into a human-readable label.

        Returns: "hot" | "improving" | "stable" | "declining" | "cold"
        """
        if trend > 0.1 and last_4_sg > career_sg + 0.5:
            return "hot"
        elif trend > 0.05:
            return "improving"
        elif trend < -0.1 and last_4_sg < career_sg - 0.5:
            return "cold"
        elif trend < -0.05:
            return "declining"
        else:
            return "stable"

    def get_player_form(self, player_name: str) -> Dict[str, Any]:
        """Full form analysis for a player.

        Returns dict with recent_results + form_metrics + form_label.
        Caches results for 6 hours.
        """
        cache_key = f"player_form_{player_name}"
        cached = self.cache.get(cache_key, max_age_minutes=360)
        if cached and 'form_metrics' in cached:
            return cached

        results = self.get_recent_results(player_name)
        metrics = self.calc_form_metrics(results)

        form_data = {
            "player_name": player_name,
            "recent_results": results,
            "form_metrics": metrics,
            "form_label": metrics.get("trend_label", "stable"),
            "fetched_at": datetime.now().isoformat(),
        }

        self.cache.set(cache_key, form_data)
        return form_data

    def get_field_form(self, field: List[str]) -> Dict[str, Dict[str, Any]]:
        """Get form analysis for entire tournament field.

        Args:
            field: list of player name strings

        Returns: dict keyed by player name with form dicts
        """
        result = {}
        for player_name in field:
            result[player_name] = self.get_player_form(player_name)
        return result
