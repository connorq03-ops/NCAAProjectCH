"""
Elite Players Database for PGA Tour Golf.
Used by the simulation engine to assess player quality and by the
injury/WD analyzer to estimate impact.

Mirrors star_players.py (root of repo) exactly in structure.

Tiers:
  - "elite"     (impact 10): World top 5, major champions in peak form
  - "star"      (impact 9):  World top 15, consistent contenders
  - "key"       (impact 8):  World top 30, regular top-10 finishers
  - "solid"     (impact 7):  World top 50, reliable cut-makers
  - "rising"    (impact 6):  Emerging talent, recent breakout performers

Each entry: {
    "owgr_rank": int,
    "tier": str,
    "impact": int,
    "majors_won": int,
    "career_wins": int,
    "strengths": [str],    # e.g., ["sg_ott", "sg_app", "pressure"]
    "weaknesses": [str],   # e.g., ["sg_putt", "wind_play"]
    "note": str
}
"""

import re
from typing import Optional


ELITE_PLAYERS = {
    # ── ELITE (10) ── World top 5, major champions in peak form
    "Scottie Scheffler": {
        "owgr_rank": 1,
        "tier": "elite",
        "impact": 10,
        "majors_won": 2,
        "career_wins": 14,
        "strengths": ["sg_ott", "sg_app", "consistency", "pressure"],
        "weaknesses": ["sg_putt"],
        "note": "World #1, dominant ball-striker, 2x Masters champion",
    },
    "Xander Schauffele": {
        "owgr_rank": 2,
        "tier": "elite",
        "impact": 10,
        "majors_won": 2,
        "career_wins": 10,
        "strengths": ["sg_app", "sg_putt", "consistency", "major_performer"],
        "weaknesses": ["sg_ott"],
        "note": "2024 PGA + Open champion, elite all-around game",
    },
    "Rory McIlroy": {
        "owgr_rank": 3,
        "tier": "elite",
        "impact": 10,
        "majors_won": 4,
        "career_wins": 25,
        "strengths": ["sg_ott", "sg_app", "length", "links_experience"],
        "weaknesses": ["sg_arg", "closing"],
        "note": "4x major winner, elite driver, links pedigree",
    },
    "Jon Rahm": {
        "owgr_rank": 4,
        "tier": "elite",
        "impact": 10,
        "majors_won": 2,
        "career_wins": 12,
        "strengths": ["sg_app", "sg_ott", "pressure", "wind_play"],
        "weaknesses": ["sg_putt"],
        "note": "Masters + US Open champion, elite iron play",
    },
    u"Ludvig \u00c5berg": {
        "owgr_rank": 5,
        "tier": "elite",
        "impact": 10,
        "majors_won": 0,
        "career_wins": 3,
        "strengths": ["sg_ott", "sg_app", "length", "ball_striking"],
        "weaknesses": ["sg_arg", "major_experience"],
        "note": "Meteoric rise, elite ball-striker, Ryder Cup standout",
    },

    # ── STAR (9) ── World top 15, consistent contenders
    "Collin Morikawa": {
        "owgr_rank": 6,
        "tier": "star",
        "impact": 9,
        "majors_won": 2,
        "career_wins": 8,
        "strengths": ["sg_app", "accuracy", "iron_play", "pressure"],
        "weaknesses": ["sg_putt", "sg_ott"],
        "note": "2x major winner, best iron player on tour",
    },
    "Wyndham Clark": {
        "owgr_rank": 7,
        "tier": "star",
        "impact": 9,
        "majors_won": 1,
        "career_wins": 4,
        "strengths": ["sg_ott", "length", "sg_app"],
        "weaknesses": ["sg_arg", "consistency"],
        "note": "2023 US Open champion, powerful game",
    },
    "Viktor Hovland": {
        "owgr_rank": 8,
        "tier": "star",
        "impact": 9,
        "majors_won": 0,
        "career_wins": 7,
        "strengths": ["sg_app", "sg_ott", "ball_striking"],
        "weaknesses": ["sg_arg", "scrambling"],
        "note": "FedEx Cup champion, elite tee-to-green",
    },
    "Patrick Cantlay": {
        "owgr_rank": 9,
        "tier": "star",
        "impact": 9,
        "majors_won": 0,
        "career_wins": 9,
        "strengths": ["sg_putt", "sg_app", "course_management", "patience"],
        "weaknesses": ["sg_ott", "length"],
        "note": "2x FedEx Cup champion, cerebral player",
    },
    "Bryson DeChambeau": {
        "owgr_rank": 10,
        "tier": "star",
        "impact": 9,
        "majors_won": 2,
        "career_wins": 10,
        "strengths": ["sg_ott", "length", "power", "creativity"],
        "weaknesses": ["sg_arg", "accuracy"],
        "note": "2x US Open champion, longest hitter on tour",
    },
    "Brooks Koepka": {
        "owgr_rank": 11,
        "tier": "star",
        "impact": 9,
        "majors_won": 5,
        "career_wins": 10,
        "strengths": ["pressure", "sg_ott", "major_performer", "mental_toughness"],
        "weaknesses": ["sg_putt", "consistency"],
        "note": "5x major winner, peak-at-majors player",
    },
    "Tommy Fleetwood": {
        "owgr_rank": 12,
        "tier": "star",
        "impact": 9,
        "majors_won": 0,
        "career_wins": 7,
        "strengths": ["sg_app", "links_experience", "wind_play", "ball_striking"],
        "weaknesses": ["sg_putt", "closing"],
        "note": "Elite ball-striker, links specialist",
    },
    "Hideki Matsuyama": {
        "owgr_rank": 13,
        "tier": "star",
        "impact": 9,
        "majors_won": 1,
        "career_wins": 10,
        "strengths": ["sg_app", "iron_play", "sg_ott"],
        "weaknesses": ["sg_putt", "sg_arg"],
        "note": "Masters champion, elite iron play",
    },
    "Sahith Theegala": {
        "owgr_rank": 14,
        "tier": "star",
        "impact": 9,
        "majors_won": 0,
        "career_wins": 3,
        "strengths": ["sg_ott", "sg_app", "birdie_rate"],
        "weaknesses": ["sg_arg", "consistency"],
        "note": "Aggressive playmaker, fan favorite",
    },
    "Matt Fitzpatrick": {
        "owgr_rank": 15,
        "tier": "star",
        "impact": 9,
        "majors_won": 1,
        "career_wins": 9,
        "strengths": ["sg_app", "accuracy", "course_management", "scrambling"],
        "weaknesses": ["sg_ott", "length"],
        "note": "2022 US Open champion, precision player",
    },

    # ── KEY (8) ── World top 30, regular top-10 finishers
    "Tony Finau": {
        "owgr_rank": 16,
        "tier": "key",
        "impact": 8,
        "majors_won": 0,
        "career_wins": 5,
        "strengths": ["sg_ott", "length", "sg_app"],
        "weaknesses": ["sg_putt", "closing"],
        "note": "Powerful ball-striker, consistent top-10 finisher",
    },
    "Shane Lowry": {
        "owgr_rank": 17,
        "tier": "key",
        "impact": 8,
        "majors_won": 1,
        "career_wins": 6,
        "strengths": ["sg_arg", "scrambling", "links_experience", "wind_play"],
        "weaknesses": ["sg_ott", "length"],
        "note": "Open champion, elite short game and links pedigree",
    },
    "Justin Thomas": {
        "owgr_rank": 18,
        "tier": "key",
        "impact": 8,
        "majors_won": 2,
        "career_wins": 15,
        "strengths": ["sg_app", "birdie_rate", "pressure"],
        "weaknesses": ["sg_ott", "consistency"],
        "note": "2x PGA champion, streaky brilliance",
    },
    "Jordan Spieth": {
        "owgr_rank": 19,
        "tier": "key",
        "impact": 8,
        "majors_won": 3,
        "career_wins": 13,
        "strengths": ["sg_putt", "creativity", "pressure", "scrambling"],
        "weaknesses": ["sg_ott", "accuracy"],
        "note": "3x major winner, elite putter and short game",
    },
    "Sam Burns": {
        "owgr_rank": 20,
        "tier": "key",
        "impact": 8,
        "majors_won": 0,
        "career_wins": 5,
        "strengths": ["sg_putt", "sg_app", "bermuda_greens"],
        "weaknesses": ["sg_ott", "major_experience"],
        "note": "Elite putter, consistent winner",
    },
    "Sungjae Im": {
        "owgr_rank": 21,
        "tier": "key",
        "impact": 8,
        "majors_won": 0,
        "career_wins": 4,
        "strengths": ["consistency", "sg_app", "iron_play"],
        "weaknesses": ["sg_putt", "closing"],
        "note": "Iron man of golf, never misses a cut",
    },
    "Tom Kim": {
        "owgr_rank": 22,
        "tier": "key",
        "impact": 8,
        "majors_won": 0,
        "career_wins": 4,
        "strengths": ["sg_app", "sg_putt", "birdie_rate"],
        "weaknesses": ["sg_ott", "length"],
        "note": "Young Korean star, aggressive playmaker",
    },
    "Keegan Bradley": {
        "owgr_rank": 23,
        "tier": "key",
        "impact": 8,
        "majors_won": 1,
        "career_wins": 7,
        "strengths": ["sg_app", "pressure", "consistency"],
        "weaknesses": ["sg_putt", "sg_arg"],
        "note": "PGA champion, Ryder Cup captain",
    },
    "Russell Henley": {
        "owgr_rank": 24,
        "tier": "key",
        "impact": 8,
        "majors_won": 0,
        "career_wins": 4,
        "strengths": ["accuracy", "sg_app", "course_management"],
        "weaknesses": ["sg_ott", "length"],
        "note": "Precision player, consistent on tight courses",
    },
    "Cameron Young": {
        "owgr_rank": 25,
        "tier": "key",
        "impact": 8,
        "majors_won": 0,
        "career_wins": 1,
        "strengths": ["sg_ott", "length", "birdie_rate"],
        "weaknesses": ["sg_putt", "closing"],
        "note": "Longest hitter on PGA Tour, aggressive",
    },
    "Corey Conners": {
        "owgr_rank": 26,
        "tier": "key",
        "impact": 8,
        "majors_won": 0,
        "career_wins": 3,
        "strengths": ["sg_app", "iron_play", "accuracy", "ball_striking"],
        "weaknesses": ["sg_putt", "scrambling"],
        "note": "Elite ball-striker, best iron player stats",
    },
    "Chris Kirk": {
        "owgr_rank": 27,
        "tier": "key",
        "impact": 8,
        "majors_won": 0,
        "career_wins": 5,
        "strengths": ["sg_putt", "consistency", "course_management"],
        "weaknesses": ["sg_ott", "length"],
        "note": "Veteran presence, elite putter",
    },
    "Brian Harman": {
        "owgr_rank": 28,
        "tier": "key",
        "impact": 8,
        "majors_won": 1,
        "career_wins": 4,
        "strengths": ["sg_arg", "scrambling", "links_experience", "wind_play"],
        "weaknesses": ["sg_ott", "length"],
        "note": "2023 Open champion, lefty with elite short game",
    },
    "Min Woo Lee": {
        "owgr_rank": 29,
        "tier": "key",
        "impact": 8,
        "majors_won": 0,
        "career_wins": 4,
        "strengths": ["sg_ott", "sg_app", "birdie_rate"],
        "weaknesses": ["sg_putt", "consistency"],
        "note": "Exciting Australian talent, powerful game",
    },
    "Robert MacIntyre": {
        "owgr_rank": 30,
        "tier": "key",
        "impact": 8,
        "majors_won": 0,
        "career_wins": 4,
        "strengths": ["sg_app", "links_experience", "wind_play"],
        "weaknesses": ["sg_putt", "sg_ott"],
        "note": "Scottish lefty, natural links player",
    },

    # ── SOLID (7) ── World top 50, reliable cut-makers
    "Jason Day": {
        "owgr_rank": 31,
        "tier": "solid",
        "impact": 7,
        "majors_won": 1,
        "career_wins": 13,
        "strengths": ["sg_arg", "scrambling", "pressure"],
        "weaknesses": ["sg_ott", "consistency"],
        "note": "Former #1, PGA champion, elite short game when healthy",
    },
    "Max Homa": {
        "owgr_rank": 32,
        "tier": "solid",
        "impact": 7,
        "majors_won": 0,
        "career_wins": 6,
        "strengths": ["sg_app", "shot_shaping", "accuracy"],
        "weaknesses": ["sg_putt", "major_experience"],
        "note": "Ball-striker with great course management",
    },
    "Denny McCarthy": {
        "owgr_rank": 33,
        "tier": "solid",
        "impact": 7,
        "majors_won": 0,
        "career_wins": 2,
        "strengths": ["sg_putt", "putting", "consistency"],
        "weaknesses": ["sg_ott", "length"],
        "note": "Best putter on tour, short but deadly on greens",
    },
    "Billy Horschel": {
        "owgr_rank": 34,
        "tier": "solid",
        "impact": 7,
        "majors_won": 0,
        "career_wins": 7,
        "strengths": ["sg_putt", "bermuda_greens", "consistency"],
        "weaknesses": ["sg_ott", "length"],
        "note": "FedEx Cup champion, Southeast specialist",
    },
    "Sepp Straka": {
        "owgr_rank": 35,
        "tier": "solid",
        "impact": 7,
        "majors_won": 0,
        "career_wins": 3,
        "strengths": ["sg_app", "consistency", "accuracy"],
        "weaknesses": ["sg_putt", "birdie_rate"],
        "note": "Austrian ball-striker, steady performer",
    },
    "Taylor Moore": {
        "owgr_rank": 36,
        "tier": "solid",
        "impact": 7,
        "majors_won": 0,
        "career_wins": 1,
        "strengths": ["sg_app", "accuracy", "consistency"],
        "weaknesses": ["sg_ott", "major_experience"],
        "note": "Reliable ball-striker, consistent finisher",
    },
    "Akshay Bhatia": {
        "owgr_rank": 37,
        "tier": "solid",
        "impact": 7,
        "majors_won": 0,
        "career_wins": 3,
        "strengths": ["sg_ott", "sg_app", "shot_shaping"],
        "weaknesses": ["sg_putt", "consistency"],
        "note": "Lefty with elite ball-striking, creative shotmaker",
    },
    "Davis Thompson": {
        "owgr_rank": 38,
        "tier": "solid",
        "impact": 7,
        "majors_won": 0,
        "career_wins": 2,
        "strengths": ["sg_ott", "length", "sg_app"],
        "weaknesses": ["sg_putt", "major_experience"],
        "note": "Young power player, rising star",
    },
    "Maverick McNealy": {
        "owgr_rank": 39,
        "tier": "solid",
        "impact": 7,
        "majors_won": 0,
        "career_wins": 1,
        "strengths": ["sg_app", "accuracy", "course_management"],
        "weaknesses": ["sg_ott", "birdie_rate"],
        "note": "Smart, consistent player",
    },
    "Nick Dunlap": {
        "owgr_rank": 40,
        "tier": "solid",
        "impact": 7,
        "majors_won": 0,
        "career_wins": 2,
        "strengths": ["sg_app", "sg_putt", "birdie_rate"],
        "weaknesses": ["sg_ott", "consistency"],
        "note": "Won as amateur on tour, youngest winner in decades",
    },

    # ── RISING (6) ── Emerging talent, recent breakout performers
    "Matthieu Pavon": {
        "owgr_rank": 42,
        "tier": "rising",
        "impact": 6,
        "majors_won": 0,
        "career_wins": 2,
        "strengths": ["sg_app", "accuracy", "wind_play"],
        "weaknesses": ["sg_putt", "major_experience"],
        "note": "French breakout star, elite ball-striking",
    },
    "Jake Knapp": {
        "owgr_rank": 45,
        "tier": "rising",
        "impact": 6,
        "majors_won": 0,
        "career_wins": 1,
        "strengths": ["sg_ott", "length", "power"],
        "weaknesses": ["sg_arg", "consistency"],
        "note": "Massive hitter, exciting young talent",
    },
    "Austin Eckroat": {
        "owgr_rank": 47,
        "tier": "rising",
        "impact": 6,
        "majors_won": 0,
        "career_wins": 1,
        "strengths": ["sg_app", "accuracy", "consistency"],
        "weaknesses": ["sg_putt", "major_experience"],
        "note": "Steady ball-striker, emerging contender",
    },
    "Eric Cole": {
        "owgr_rank": 49,
        "tier": "rising",
        "impact": 6,
        "majors_won": 0,
        "career_wins": 1,
        "strengths": ["sg_ott", "length", "birdie_rate"],
        "weaknesses": ["sg_arg", "consistency"],
        "note": "Late bloomer, powerful game, tour breakout",
    },
}


# ═══════════════════════════════════════════════════════════════
# Helper Functions
# ═══════════════════════════════════════════════════════════════

def _normalize_name(name: str) -> str:
    """Lowercase and strip a name for matching."""
    return re.sub(r'\s+', ' ', name.strip().lower())


def get_player_info(name: str) -> Optional[dict]:
    """Get elite player info by name (case-insensitive fuzzy match)."""
    # Exact match first
    if name in ELITE_PLAYERS:
        return ELITE_PLAYERS[name]
    # Case-insensitive match
    name_lower = _normalize_name(name)
    for key, val in ELITE_PLAYERS.items():
        if _normalize_name(key) == name_lower:
            return val
    # Last name match with first initial check
    for key, val in ELITE_PLAYERS.items():
        parts = key.split()
        name_parts = name.split()
        if len(parts) >= 2 and len(name_parts) >= 2:
            if parts[-1].lower() == name_parts[-1].lower():
                if name_parts[0][0].lower() == parts[0][0].lower():
                    return val
    return None


def get_player_tier(name: str) -> Optional[str]:
    """Get player tier string."""
    info = get_player_info(name)
    return info["tier"] if info else None


def get_players_by_tier(tier: str) -> list:
    """Get all players in a given tier."""
    results = []
    for name, info in ELITE_PLAYERS.items():
        if info["tier"] == tier:
            results.append({"player": name, **info})
    results.sort(key=lambda x: x["owgr_rank"])
    return results


def get_player_strengths(name: str) -> list:
    """Get player's strength tags."""
    info = get_player_info(name)
    return info["strengths"] if info else []


def build_player_context(name: str) -> str:
    """Build a text description of a player for LLM prompts (used by WD scraper later)."""
    info = get_player_info(name)
    if not info:
        return f"{name}: No elite player data available"

    lines = [
        f"{name} — {info['tier'].upper()} (impact {info['impact']}/10)",
        f"  OWGR: #{info['owgr_rank']} | Majors: {info['majors_won']} | Wins: {info['career_wins']}",
        f"  Strengths: {', '.join(info['strengths'])}",
        f"  Weaknesses: {', '.join(info['weaknesses'])}",
        f"  {info['note']}",
    ]
    return "\n".join(lines)
