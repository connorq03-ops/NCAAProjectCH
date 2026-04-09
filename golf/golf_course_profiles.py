"""
Golf Course Profiles Database.
The golf equivalent of bracket_data.py (tournament structure constants) combined
with the course-fit logic from matchup_params.py's asymmetric_matchup() function.

Provides course metadata, SG weighting schemes, hole-by-hole data for majors,
and a course-fit scoring function analogous to asymmetric_matchup().
"""

import re
from typing import Optional


# ═══════════════════════════════════════════════════════════════
# League Average Constants (PGA Tour baseline)
# ═══════════════════════════════════════════════════════════════

AVG_SG_TOTAL = 0.0
AVG_SG_OTT = 0.0
AVG_SG_APP = 0.0
AVG_SG_ARG = 0.0
AVG_SG_PUTT = 0.0
AVG_DRIVING_DIST = 295.0
AVG_DRIVING_ACC = 60.0
AVG_GIR = 66.0
AVG_SCRAMBLING = 58.0
AVG_PUTTS = 29.0
AVG_SCORING = 71.5
AVG_CUT_RATE = 65.0


# ═══════════════════════════════════════════════════════════════
# Course Profile Data
# ═══════════════════════════════════════════════════════════════

COURSES = {
    # ── Major Championship Venues ─────────────────────────────

    "augusta_national": {
        "course_id": "augusta_national",
        "name": "Augusta National Golf Club",
        "location": "Augusta, Georgia",
        "par": 72,
        "yardage": 7545,
        "course_rating": 76.2,
        "slope": 148,
        "archetype": "parkland",
        "green_speed": "very_fast",
        "green_firmness": "firm",
        "fairway_width": "wide",
        "rough_severity": "medium",
        "elevation_ft": 450,
        "avg_wind_mph": 8.0,
        "lat": 33.503,
        "lon": -82.022,
        "sg_weights": {"sg_ott": 0.30, "sg_app": 0.30, "sg_arg": 0.25, "sg_putt": 0.15},
        "style_tags": ["length", "approach_precision", "fast_green_putting", "par5_scoring"],
        "historical_scoring_avg": -8.0,
        "historical_cut_line": 3,
        "historical_winning_score": -12,
        "tournament_name": "The Masters",
        "is_major": True,
        "holes": [
            {"hole": 1,  "par": 4, "yardage": 445, "difficulty_rank": 8,  "key_stat": "sg_app", "water": False, "bunkers": 1},
            {"hole": 2,  "par": 5, "yardage": 575, "difficulty_rank": 14, "key_stat": "sg_ott", "water": False, "bunkers": 2},
            {"hole": 3,  "par": 4, "yardage": 350, "difficulty_rank": 16, "key_stat": "sg_app", "water": False, "bunkers": 3},
            {"hole": 4,  "par": 3, "yardage": 240, "difficulty_rank": 10, "key_stat": "sg_app", "water": False, "bunkers": 3},
            {"hole": 5,  "par": 4, "yardage": 495, "difficulty_rank": 4,  "key_stat": "sg_ott", "water": False, "bunkers": 2},
            {"hole": 6,  "par": 3, "yardage": 180, "difficulty_rank": 15, "key_stat": "sg_putt", "water": False, "bunkers": 3},
            {"hole": 7,  "par": 4, "yardage": 450, "difficulty_rank": 9,  "key_stat": "sg_app", "water": False, "bunkers": 2},
            {"hole": 8,  "par": 5, "yardage": 570, "difficulty_rank": 13, "key_stat": "sg_ott", "water": False, "bunkers": 2},
            {"hole": 9,  "par": 4, "yardage": 460, "difficulty_rank": 6,  "key_stat": "sg_app", "water": False, "bunkers": 2},
            {"hole": 10, "par": 4, "yardage": 495, "difficulty_rank": 3,  "key_stat": "sg_app", "water": False, "bunkers": 2},
            {"hole": 11, "par": 4, "yardage": 520, "difficulty_rank": 1,  "key_stat": "sg_app", "water": True,  "bunkers": 2},
            {"hole": 12, "par": 3, "yardage": 155, "difficulty_rank": 5,  "key_stat": "sg_arg", "water": True,  "bunkers": 3},
            {"hole": 13, "par": 5, "yardage": 510, "difficulty_rank": 17, "key_stat": "sg_ott", "water": True,  "bunkers": 2},
            {"hole": 14, "par": 4, "yardage": 440, "difficulty_rank": 11, "key_stat": "sg_app", "water": False, "bunkers": 0},
            {"hole": 15, "par": 5, "yardage": 550, "difficulty_rank": 18, "key_stat": "sg_app", "water": True,  "bunkers": 2},
            {"hole": 16, "par": 3, "yardage": 170, "difficulty_rank": 12, "key_stat": "sg_putt", "water": True,  "bunkers": 3},
            {"hole": 17, "par": 4, "yardage": 440, "difficulty_rank": 7,  "key_stat": "sg_app", "water": False, "bunkers": 1},
            {"hole": 18, "par": 4, "yardage": 465, "difficulty_rank": 2,  "key_stat": "sg_ott", "water": False, "bunkers": 2},
        ],
    },

    "pinehurst_no2": {
        "course_id": "pinehurst_no2",
        "name": "Pinehurst No. 2",
        "location": "Pinehurst, North Carolina",
        "par": 70,
        "yardage": 7588,
        "course_rating": 76.5,
        "slope": 145,
        "archetype": "tree-lined",
        "green_speed": "fast",
        "green_firmness": "firm",
        "fairway_width": "medium",
        "rough_severity": "medium",
        "elevation_ft": 550,
        "avg_wind_mph": 7.0,
        "lat": 35.194,
        "lon": -79.469,
        "sg_weights": {"sg_ott": 0.15, "sg_app": 0.25, "sg_arg": 0.35, "sg_putt": 0.25},
        "style_tags": ["scrambling", "firm_greens", "accuracy", "patience"],
        "historical_scoring_avg": 4.0,
        "historical_cut_line": 6,
        "historical_winning_score": -6,
        "tournament_name": "U.S. Open",
        "is_major": True,
        "holes": [
            {"hole": 1,  "par": 4, "yardage": 404, "difficulty_rank": 14, "key_stat": "sg_app", "water": False, "bunkers": 4},
            {"hole": 2,  "par": 4, "yardage": 487, "difficulty_rank": 3,  "key_stat": "sg_ott", "water": False, "bunkers": 3},
            {"hole": 3,  "par": 4, "yardage": 384, "difficulty_rank": 13, "key_stat": "sg_arg", "water": False, "bunkers": 3},
            {"hole": 4,  "par": 4, "yardage": 564, "difficulty_rank": 1,  "key_stat": "sg_ott", "water": False, "bunkers": 5},
            {"hole": 5,  "par": 4, "yardage": 482, "difficulty_rank": 5,  "key_stat": "sg_app", "water": False, "bunkers": 6},
            {"hole": 6,  "par": 3, "yardage": 222, "difficulty_rank": 10, "key_stat": "sg_app", "water": False, "bunkers": 4},
            {"hole": 7,  "par": 4, "yardage": 416, "difficulty_rank": 9,  "key_stat": "sg_arg", "water": False, "bunkers": 4},
            {"hole": 8,  "par": 4, "yardage": 487, "difficulty_rank": 4,  "key_stat": "sg_ott", "water": False, "bunkers": 3},
            {"hole": 9,  "par": 3, "yardage": 194, "difficulty_rank": 15, "key_stat": "sg_putt", "water": False, "bunkers": 5},
            {"hole": 10, "par": 5, "yardage": 610, "difficulty_rank": 18, "key_stat": "sg_ott", "water": False, "bunkers": 4},
            {"hole": 11, "par": 4, "yardage": 478, "difficulty_rank": 6,  "key_stat": "sg_app", "water": False, "bunkers": 3},
            {"hole": 12, "par": 4, "yardage": 456, "difficulty_rank": 7,  "key_stat": "sg_arg", "water": False, "bunkers": 4},
            {"hole": 13, "par": 4, "yardage": 384, "difficulty_rank": 12, "key_stat": "sg_arg", "water": False, "bunkers": 3},
            {"hole": 14, "par": 4, "yardage": 484, "difficulty_rank": 2,  "key_stat": "sg_app", "water": False, "bunkers": 5},
            {"hole": 15, "par": 3, "yardage": 206, "difficulty_rank": 11, "key_stat": "sg_putt", "water": False, "bunkers": 4},
            {"hole": 16, "par": 4, "yardage": 513, "difficulty_rank": 8,  "key_stat": "sg_ott", "water": False, "bunkers": 3},
            {"hole": 17, "par": 3, "yardage": 197, "difficulty_rank": 16, "key_stat": "sg_arg", "water": False, "bunkers": 4},
            {"hole": 18, "par": 4, "yardage": 446, "difficulty_rank": 17, "key_stat": "sg_app", "water": False, "bunkers": 5},
        ],
    },

    "royal_troon": {
        "course_id": "royal_troon",
        "name": "Royal Troon Golf Club",
        "location": "Troon, Scotland",
        "par": 71,
        "yardage": 7385,
        "course_rating": 75.8,
        "slope": 142,
        "archetype": "links",
        "green_speed": "medium",
        "green_firmness": "firm",
        "fairway_width": "medium",
        "rough_severity": "heavy",
        "elevation_ft": 25,
        "avg_wind_mph": 18.0,
        "lat": 55.543,
        "lon": -4.652,
        "sg_weights": {"sg_ott": 0.25, "sg_app": 0.30, "sg_arg": 0.25, "sg_putt": 0.20},
        "style_tags": ["wind_play", "links_experience", "low_trajectory", "creativity"],
        "historical_scoring_avg": 1.0,
        "historical_cut_line": 5,
        "historical_winning_score": -10,
        "tournament_name": "The Open Championship",
        "is_major": True,
        "holes": [
            {"hole": 1,  "par": 4, "yardage": 370, "difficulty_rank": 15, "key_stat": "sg_app", "water": False, "bunkers": 2},
            {"hole": 2,  "par": 4, "yardage": 391, "difficulty_rank": 12, "key_stat": "sg_app", "water": False, "bunkers": 3},
            {"hole": 3,  "par": 4, "yardage": 379, "difficulty_rank": 14, "key_stat": "sg_arg", "water": False, "bunkers": 2},
            {"hole": 4,  "par": 5, "yardage": 557, "difficulty_rank": 17, "key_stat": "sg_ott", "water": False, "bunkers": 3},
            {"hole": 5,  "par": 3, "yardage": 210, "difficulty_rank": 9,  "key_stat": "sg_app", "water": False, "bunkers": 4},
            {"hole": 6,  "par": 5, "yardage": 601, "difficulty_rank": 16, "key_stat": "sg_ott", "water": False, "bunkers": 2},
            {"hole": 7,  "par": 4, "yardage": 402, "difficulty_rank": 8,  "key_stat": "sg_app", "water": False, "bunkers": 3},
            {"hole": 8,  "par": 3, "yardage": 126, "difficulty_rank": 18, "key_stat": "sg_putt", "water": False, "bunkers": 4},
            {"hole": 9,  "par": 4, "yardage": 423, "difficulty_rank": 7,  "key_stat": "sg_app", "water": False, "bunkers": 2},
            {"hole": 10, "par": 4, "yardage": 438, "difficulty_rank": 5,  "key_stat": "sg_ott", "water": False, "bunkers": 2},
            {"hole": 11, "par": 4, "yardage": 488, "difficulty_rank": 1,  "key_stat": "sg_ott", "water": False, "bunkers": 3},
            {"hole": 12, "par": 4, "yardage": 431, "difficulty_rank": 6,  "key_stat": "sg_app", "water": False, "bunkers": 3},
            {"hole": 13, "par": 4, "yardage": 472, "difficulty_rank": 3,  "key_stat": "sg_ott", "water": False, "bunkers": 2},
            {"hole": 14, "par": 3, "yardage": 198, "difficulty_rank": 10, "key_stat": "sg_app", "water": False, "bunkers": 3},
            {"hole": 15, "par": 4, "yardage": 502, "difficulty_rank": 2,  "key_stat": "sg_ott", "water": False, "bunkers": 2},
            {"hole": 16, "par": 5, "yardage": 542, "difficulty_rank": 13, "key_stat": "sg_app", "water": False, "bunkers": 3},
            {"hole": 17, "par": 3, "yardage": 223, "difficulty_rank": 4,  "key_stat": "sg_app", "water": False, "bunkers": 4},
            {"hole": 18, "par": 4, "yardage": 452, "difficulty_rank": 11, "key_stat": "sg_app", "water": False, "bunkers": 2},
        ],
    },

    "valhalla": {
        "course_id": "valhalla",
        "name": "Valhalla Golf Club",
        "location": "Louisville, Kentucky",
        "par": 72,
        "yardage": 7456,
        "course_rating": 76.0,
        "slope": 146,
        "archetype": "parkland",
        "green_speed": "fast",
        "green_firmness": "medium",
        "fairway_width": "medium",
        "rough_severity": "medium",
        "elevation_ft": 740,
        "avg_wind_mph": 8.0,
        "lat": 38.283,
        "lon": -85.491,
        "sg_weights": {"sg_ott": 0.30, "sg_app": 0.30, "sg_arg": 0.20, "sg_putt": 0.20},
        "style_tags": ["length", "par5_scoring", "approach_precision"],
        "historical_scoring_avg": -6.0,
        "historical_cut_line": 1,
        "historical_winning_score": -15,
        "tournament_name": "PGA Championship",
        "is_major": True,
        "holes": [
            {"hole": 1,  "par": 4, "yardage": 448, "difficulty_rank": 10, "key_stat": "sg_app", "water": False, "bunkers": 3},
            {"hole": 2,  "par": 4, "yardage": 510, "difficulty_rank": 3,  "key_stat": "sg_ott", "water": True,  "bunkers": 3},
            {"hole": 3,  "par": 3, "yardage": 206, "difficulty_rank": 14, "key_stat": "sg_app", "water": False, "bunkers": 4},
            {"hole": 4,  "par": 4, "yardage": 375, "difficulty_rank": 16, "key_stat": "sg_app", "water": False, "bunkers": 2},
            {"hole": 5,  "par": 4, "yardage": 466, "difficulty_rank": 7,  "key_stat": "sg_ott", "water": False, "bunkers": 3},
            {"hole": 6,  "par": 4, "yardage": 477, "difficulty_rank": 5,  "key_stat": "sg_app", "water": False, "bunkers": 4},
            {"hole": 7,  "par": 5, "yardage": 597, "difficulty_rank": 15, "key_stat": "sg_ott", "water": True,  "bunkers": 3},
            {"hole": 8,  "par": 3, "yardage": 177, "difficulty_rank": 12, "key_stat": "sg_putt", "water": False, "bunkers": 3},
            {"hole": 9,  "par": 4, "yardage": 419, "difficulty_rank": 9,  "key_stat": "sg_app", "water": False, "bunkers": 2},
            {"hole": 10, "par": 5, "yardage": 548, "difficulty_rank": 17, "key_stat": "sg_ott", "water": True,  "bunkers": 3},
            {"hole": 11, "par": 3, "yardage": 208, "difficulty_rank": 11, "key_stat": "sg_app", "water": False, "bunkers": 4},
            {"hole": 12, "par": 4, "yardage": 472, "difficulty_rank": 4,  "key_stat": "sg_ott", "water": False, "bunkers": 3},
            {"hole": 13, "par": 4, "yardage": 360, "difficulty_rank": 18, "key_stat": "sg_arg", "water": True,  "bunkers": 2},
            {"hole": 14, "par": 3, "yardage": 215, "difficulty_rank": 8,  "key_stat": "sg_app", "water": False, "bunkers": 5},
            {"hole": 15, "par": 4, "yardage": 440, "difficulty_rank": 6,  "key_stat": "sg_app", "water": False, "bunkers": 3},
            {"hole": 16, "par": 4, "yardage": 510, "difficulty_rank": 2,  "key_stat": "sg_ott", "water": False, "bunkers": 3},
            {"hole": 17, "par": 4, "yardage": 480, "difficulty_rank": 1,  "key_stat": "sg_app", "water": True,  "bunkers": 4},
            {"hole": 18, "par": 5, "yardage": 548, "difficulty_rank": 13, "key_stat": "sg_ott", "water": True,  "bunkers": 3},
        ],
    },

    "oakmont": {
        "course_id": "oakmont",
        "name": "Oakmont Country Club",
        "location": "Oakmont, Pennsylvania",
        "par": 70,
        "yardage": 7255,
        "course_rating": 77.5,
        "slope": 150,
        "archetype": "tree-lined",
        "green_speed": "very_fast",
        "green_firmness": "firm",
        "fairway_width": "narrow",
        "rough_severity": "heavy",
        "elevation_ft": 960,
        "avg_wind_mph": 7.0,
        "lat": 40.527,
        "lon": -79.826,
        "sg_weights": {"sg_ott": 0.20, "sg_app": 0.25, "sg_arg": 0.25, "sg_putt": 0.30},
        "style_tags": ["fast_green_putting", "accuracy", "patience", "mental_toughness"],
        "historical_scoring_avg": 5.0,
        "historical_cut_line": 7,
        "historical_winning_score": -5,
        "tournament_name": "U.S. Open",
        "is_major": True,
        "holes": None,
    },

    "quail_hollow": {
        "course_id": "quail_hollow",
        "name": "Quail Hollow Club",
        "location": "Charlotte, North Carolina",
        "par": 71,
        "yardage": 7554,
        "course_rating": 76.1,
        "slope": 147,
        "archetype": "parkland",
        "green_speed": "fast",
        "green_firmness": "medium",
        "fairway_width": "medium",
        "rough_severity": "medium",
        "elevation_ft": 680,
        "avg_wind_mph": 7.0,
        "lat": 35.124,
        "lon": -80.850,
        "sg_weights": {"sg_ott": 0.28, "sg_app": 0.28, "sg_arg": 0.20, "sg_putt": 0.24},
        "style_tags": ["length", "approach_precision", "putting"],
        "historical_scoring_avg": -6.0,
        "historical_cut_line": 1,
        "historical_winning_score": -14,
        "tournament_name": "PGA Championship 2025",
        "is_major": True,
        "holes": None,
    },

    # ── Key PGA Tour Venues ───────────────────────────────────

    "tpc_sawgrass": {
        "course_id": "tpc_sawgrass",
        "name": "TPC Sawgrass (Stadium Course)",
        "location": "Ponte Vedra Beach, Florida",
        "par": 72,
        "yardage": 7245,
        "course_rating": 75.8,
        "slope": 148,
        "archetype": "parkland",
        "green_speed": "fast",
        "green_firmness": "medium",
        "fairway_width": "narrow",
        "rough_severity": "medium",
        "elevation_ft": 15,
        "avg_wind_mph": 10.0,
        "lat": 30.198,
        "lon": -81.394,
        "sg_weights": {"sg_ott": 0.20, "sg_app": 0.30, "sg_arg": 0.25, "sg_putt": 0.25},
        "style_tags": ["accuracy", "water_management", "pressure", "consistency"],
        "historical_scoring_avg": -8.0,
        "historical_cut_line": 1,
        "historical_winning_score": -16,
        "tournament_name": "The Players Championship",
        "is_major": False,
        "holes": None,
    },

    "riviera": {
        "course_id": "riviera",
        "name": "Riviera Country Club",
        "location": "Pacific Palisades, California",
        "par": 71,
        "yardage": 7322,
        "course_rating": 75.5,
        "slope": 144,
        "archetype": "parkland",
        "green_speed": "fast",
        "green_firmness": "medium",
        "fairway_width": "medium",
        "rough_severity": "heavy",
        "elevation_ft": 300,
        "avg_wind_mph": 8.0,
        "lat": 34.048,
        "lon": -118.499,
        "sg_weights": {"sg_ott": 0.25, "sg_app": 0.30, "sg_arg": 0.20, "sg_putt": 0.25},
        "style_tags": ["ball_striking", "approach_precision", "kikuyu_rough"],
        "historical_scoring_avg": -8.0,
        "historical_cut_line": 0,
        "historical_winning_score": -15,
        "tournament_name": "Genesis Invitational",
        "is_major": False,
        "holes": None,
    },

    "bay_hill": {
        "course_id": "bay_hill",
        "name": "Bay Hill Club & Lodge",
        "location": "Orlando, Florida",
        "par": 72,
        "yardage": 7466,
        "course_rating": 76.0,
        "slope": 145,
        "archetype": "parkland",
        "green_speed": "fast",
        "green_firmness": "medium",
        "fairway_width": "medium",
        "rough_severity": "medium",
        "elevation_ft": 100,
        "avg_wind_mph": 10.0,
        "lat": 28.460,
        "lon": -81.506,
        "sg_weights": {"sg_ott": 0.28, "sg_app": 0.28, "sg_arg": 0.20, "sg_putt": 0.24},
        "style_tags": ["length", "water_management"],
        "historical_scoring_avg": -7.0,
        "historical_cut_line": 1,
        "historical_winning_score": -14,
        "tournament_name": "Arnold Palmer Invitational",
        "is_major": False,
        "holes": None,
    },

    "tpc_scottsdale": {
        "course_id": "tpc_scottsdale",
        "name": "TPC Scottsdale (Stadium Course)",
        "location": "Scottsdale, Arizona",
        "par": 71,
        "yardage": 7261,
        "course_rating": 74.8,
        "slope": 138,
        "archetype": "desert",
        "green_speed": "fast",
        "green_firmness": "medium",
        "fairway_width": "wide",
        "rough_severity": "light",
        "elevation_ft": 1510,
        "avg_wind_mph": 6.0,
        "lat": 33.640,
        "lon": -111.923,
        "sg_weights": {"sg_ott": 0.25, "sg_app": 0.25, "sg_arg": 0.20, "sg_putt": 0.30},
        "style_tags": ["putting", "birdie_fest", "low_scoring"],
        "historical_scoring_avg": -14.0,
        "historical_cut_line": -4,
        "historical_winning_score": -22,
        "tournament_name": "WM Phoenix Open",
        "is_major": False,
        "holes": None,
    },

    "torrey_pines_south": {
        "course_id": "torrey_pines_south",
        "name": "Torrey Pines (South Course)",
        "location": "La Jolla, California",
        "par": 72,
        "yardage": 7765,
        "course_rating": 77.2,
        "slope": 147,
        "archetype": "parkland",
        "green_speed": "medium",
        "green_firmness": "medium",
        "fairway_width": "medium",
        "rough_severity": "heavy",
        "elevation_ft": 340,
        "avg_wind_mph": 8.0,
        "lat": 32.899,
        "lon": -117.253,
        "sg_weights": {"sg_ott": 0.35, "sg_app": 0.25, "sg_arg": 0.20, "sg_putt": 0.20},
        "style_tags": ["length", "poa_annua_greens", "marine_layer"],
        "historical_scoring_avg": -9.0,
        "historical_cut_line": -1,
        "historical_winning_score": -15,
        "tournament_name": "Farmers Insurance Open",
        "is_major": False,
        "holes": None,
    },

    "pebble_beach": {
        "course_id": "pebble_beach",
        "name": "Pebble Beach Golf Links",
        "location": "Pebble Beach, California",
        "par": 72,
        "yardage": 6828,
        "course_rating": 74.8,
        "slope": 143,
        "archetype": "links",
        "green_speed": "medium",
        "green_firmness": "medium",
        "fairway_width": "narrow",
        "rough_severity": "medium",
        "elevation_ft": 60,
        "avg_wind_mph": 12.0,
        "lat": 36.568,
        "lon": -121.950,
        "sg_weights": {"sg_ott": 0.20, "sg_app": 0.30, "sg_arg": 0.25, "sg_putt": 0.25},
        "style_tags": ["wind_play", "creativity", "small_greens"],
        "historical_scoring_avg": -10.0,
        "historical_cut_line": -2,
        "historical_winning_score": -17,
        "tournament_name": "AT&T Pebble Beach Pro-Am",
        "is_major": False,
        "holes": None,
    },

    "east_lake": {
        "course_id": "east_lake",
        "name": "East Lake Golf Club",
        "location": "Atlanta, Georgia",
        "par": 70,
        "yardage": 7346,
        "course_rating": 75.6,
        "slope": 143,
        "archetype": "parkland",
        "green_speed": "fast",
        "green_firmness": "medium",
        "fairway_width": "medium",
        "rough_severity": "medium",
        "elevation_ft": 960,
        "avg_wind_mph": 6.0,
        "lat": 33.743,
        "lon": -84.315,
        "sg_weights": {"sg_ott": 0.25, "sg_app": 0.28, "sg_arg": 0.22, "sg_putt": 0.25},
        "style_tags": ["ball_striking", "bermuda_greens"],
        "historical_scoring_avg": -10.0,
        "historical_cut_line": 0,
        "historical_winning_score": -18,
        "tournament_name": "Tour Championship",
        "is_major": False,
        "holes": None,
    },

    "tpc_southwind": {
        "course_id": "tpc_southwind",
        "name": "TPC Southwind",
        "location": "Memphis, Tennessee",
        "par": 70,
        "yardage": 7244,
        "course_rating": 75.4,
        "slope": 142,
        "archetype": "parkland",
        "green_speed": "fast",
        "green_firmness": "medium",
        "fairway_width": "narrow",
        "rough_severity": "medium",
        "elevation_ft": 300,
        "avg_wind_mph": 7.0,
        "lat": 35.040,
        "lon": -89.793,
        "sg_weights": {"sg_ott": 0.22, "sg_app": 0.28, "sg_arg": 0.22, "sg_putt": 0.28},
        "style_tags": ["accuracy", "putting", "tight_fairways"],
        "historical_scoring_avg": -10.0,
        "historical_cut_line": -2,
        "historical_winning_score": -16,
        "tournament_name": "FedEx St. Jude Championship",
        "is_major": False,
        "holes": None,
    },

    "muirfield_village": {
        "course_id": "muirfield_village",
        "name": "Muirfield Village Golf Club",
        "location": "Dublin, Ohio",
        "par": 72,
        "yardage": 7543,
        "course_rating": 76.4,
        "slope": 148,
        "archetype": "parkland",
        "green_speed": "fast",
        "green_firmness": "medium",
        "fairway_width": "medium",
        "rough_severity": "medium",
        "elevation_ft": 900,
        "avg_wind_mph": 8.0,
        "lat": 40.093,
        "lon": -83.172,
        "sg_weights": {"sg_ott": 0.25, "sg_app": 0.30, "sg_arg": 0.25, "sg_putt": 0.20},
        "style_tags": ["approach_precision", "course_management", "length"],
        "historical_scoring_avg": -10.0,
        "historical_cut_line": -1,
        "historical_winning_score": -16,
        "tournament_name": "Memorial Tournament",
        "is_major": False,
        "holes": None,
    },

    "harbour_town": {
        "course_id": "harbour_town",
        "name": "Harbour Town Golf Links",
        "location": "Hilton Head Island, South Carolina",
        "par": 71,
        "yardage": 7188,
        "course_rating": 74.6,
        "slope": 141,
        "archetype": "tree-lined",
        "green_speed": "medium",
        "green_firmness": "medium",
        "fairway_width": "narrow",
        "rough_severity": "medium",
        "elevation_ft": 10,
        "avg_wind_mph": 12.0,
        "lat": 32.133,
        "lon": -80.814,
        "sg_weights": {"sg_ott": 0.20, "sg_app": 0.30, "sg_arg": 0.25, "sg_putt": 0.25},
        "style_tags": ["accuracy", "shot_shaping", "small_greens", "wind_play"],
        "historical_scoring_avg": -10.0,
        "historical_cut_line": -2,
        "historical_winning_score": -15,
        "tournament_name": "RBC Heritage",
        "is_major": False,
        "holes": None,
    },
}


# ═══════════════════════════════════════════════════════════════
# Helper Functions
# ═══════════════════════════════════════════════════════════════

def get_course_profile(course_id: str) -> Optional[dict]:
    """Returns course profile dict or None."""
    return COURSES.get(course_id)


def get_all_courses() -> list:
    """Returns all course profiles."""
    return list(COURSES.values())


def get_major_courses() -> list:
    """Returns only major championship venues (is_major=True)."""
    return [c for c in COURSES.values() if c.get("is_major")]


def get_courses_by_archetype(archetype: str) -> list:
    """Returns courses matching the given archetype."""
    return [c for c in COURSES.values() if c.get("archetype") == archetype]


def get_course_by_tournament(tournament_name: str) -> Optional[dict]:
    """Fuzzy match tournament name to course."""
    query = tournament_name.lower().strip()
    # Exact match first
    for course in COURSES.values():
        if course["tournament_name"].lower() == query:
            return course
    # Substring / fuzzy match
    for course in COURSES.values():
        if query in course["tournament_name"].lower() or course["tournament_name"].lower() in query:
            return course
    # Word overlap match
    query_words = set(re.split(r'\W+', query))
    best_match = None
    best_overlap = 0
    for course in COURSES.values():
        course_words = set(re.split(r'\W+', course["tournament_name"].lower()))
        overlap = len(query_words & course_words)
        if overlap > best_overlap:
            best_overlap = overlap
            best_match = course
    return best_match if best_overlap >= 1 else None


def normalize_player_name(name: str) -> str:
    """Normalize player name for consistent lookups."""
    name = name.strip()
    # Remove extra whitespace
    name = re.sub(r'\s+', ' ', name)
    # Title-case
    name = name.title()
    # Fix common suffixes
    name = name.replace(" Jr.", " Jr.").replace(" Iii", " III").replace(" Ii", " II")
    return name


# ═══════════════════════════════════════════════════════════════
# Course Fit Scoring Function
# ═══════════════════════════════════════════════════════════════

def calc_course_fit_score(player_sg: dict, course_profile: dict) -> float:
    """Calculate how well a player's SG splits fit a course.

    Analogous to asymmetric_matchup() in matchup_params.py (lines 44-73).

    Args:
        player_sg: dict with keys 'sg_ott', 'sg_app', 'sg_arg', 'sg_putt'
        course_profile: dict from COURSES

    Returns:
        float: course-adjusted SG projection
    """
    weights = course_profile.get("sg_weights", {})
    categories = ["sg_ott", "sg_app", "sg_arg", "sg_putt"]

    # Step 1: Compute base weighted SG
    base_weighted_sg = sum(
        player_sg.get(cat, 0.0) * weights.get(cat, 0.25)
        for cat in categories
    )

    # Step 2: Find player's strongest and weakest SG categories
    player_vals = {cat: player_sg.get(cat, 0.0) for cat in categories}
    best_cat = max(player_vals, key=player_vals.get)
    worst_cat = min(player_vals, key=player_vals.get)

    # Step 3: Find course's highest-weighted category
    top_course_cat = max(weights, key=weights.get)
    top_weight = weights[top_course_cat]

    # Step 4: Elite bonus — player's best matches course's most important
    elite_bonus = 0.0
    if best_cat == top_course_cat:
        elite_bonus = min(abs(player_vals[best_cat]) * top_weight * 0.15, 0.3)

    # Step 5: Weakness penalty — player's worst is course's most important
    weakness_penalty = 0.0
    if worst_cat == top_course_cat:
        weakness_penalty = min(abs(player_vals[worst_cat]) * top_weight * 0.10, 0.2)

    return base_weighted_sg + elite_bonus - weakness_penalty
