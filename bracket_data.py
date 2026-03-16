"""
bracket_data.py - 2026 NCAA Tournament Bracket Data

Full 68-team bracket structure for the March Madness bracket simulator.
All team names use KenPom TeamName format for direct API compatibility.

Bracket tree encoding:
  Each region has 8 first-round matchups in bracket order.
  Adjacent pairs feed into Round of 32:
    matchups[0] winner vs matchups[1] winner  → R32 game A
    matchups[2] winner vs matchups[3] winner  → R32 game B
    matchups[4] winner vs matchups[5] winner  → R32 game C
    matchups[6] winner vs matchups[7] winner  → R32 game D
  Then:
    R32a winner vs R32b winner → Sweet 16 game A
    R32c winner vs R32d winner → Sweet 16 game B
  Then:
    S16a winner vs S16b winner → Elite 8 (region champion)
"""


# ═══════════════════════════════════════════════════════════════
# Team Name Mapping: ESPN / bracket display names → KenPom TeamName
# ═══════════════════════════════════════════════════════════════

TEAM_NAME_MAP = {
    # Common alternative names → KenPom canonical
    "UConn": "Connecticut",
    "UCF": "Central Florida",
    "Miami (FL)": "Miami FL",
    "Miami (OH)": "Miami OH",
    "BYU": "Brigham Young",
    "NC State": "N.C. State",
    "Michigan State": "Michigan St.",
    "Ohio State": "Ohio St.",
    "Iowa State": "Iowa St.",
    "Utah State": "Utah St.",
    "Tennessee State": "Tennessee St.",
    "North Dakota State": "North Dakota St.",
    "Wright State": "Wright St.",
    "Kennesaw State": "Kennesaw St.",
    "McNeese State": "McNeese",
    "Prairie View A&M": "Prairie View",
    "Long Island": "LIU",
    "San Diego State": "San Diego St.",
    "Kansas State": "Kansas St.",
    "Oregon State": "Oregon St.",
    "Boise State": "Boise St.",
    "Fresno State": "Fresno St.",
    "Mississippi State": "Mississippi St.",
    "USF": "South Florida",
    "UNC": "North Carolina",
    "Saint Mary's (CA)": "Saint Mary's",
    "Texas A&M": "Texas A&M",
}


def normalize_team_name(name):
    """Normalize a team name to KenPom TeamName format.

    Tries the explicit map first, then returns the name as-is.
    The matchup_params module can apply fuzzy matching as a fallback.
    """
    if name in TEAM_NAME_MAP:
        return TEAM_NAME_MAP[name]
    return name


# ═══════════════════════════════════════════════════════════════
# 2026 NCAA Tournament Bracket
# ═══════════════════════════════════════════════════════════════
# Each region: list of 8 first-round matchups as (higher_seed_team, lower_seed_team) tuples.
# Seed numbers stored separately in SEED_MAP for lookup.

REGIONS = {
    "East": [
        # Top half
        ("Duke", "Siena"),                          # 1 vs 16
        ("Ohio St.", "TCU"),                         # 8 vs 9
        ("St. John's", "Northern Iowa"),             # 5 vs 12
        ("Kansas", "Cal Baptist"),                   # 4 vs 13
        # Bottom half
        ("Louisville", "South Florida"),             # 6 vs 11
        ("Michigan St.", "North Dakota St."),         # 3 vs 14
        ("UCLA", "Central Florida"),                 # 7 vs 10
        ("Connecticut", "Furman"),                   # 2 vs 15
    ],
    "West": [
        # Top half
        ("Arizona", "LIU"),                          # 1 vs 16
        ("Villanova", "Utah St."),                   # 8 vs 9
        ("Wisconsin", "High Point"),                 # 5 vs 12
        ("Arkansas", "Hawaii"),                      # 4 vs 13
        # Bottom half
        ("Brigham Young", None),                     # 6 vs 11 (First Four: Texas/NC State)
        ("Gonzaga", "Kennesaw St."),                 # 3 vs 14
        ("Miami FL", "Missouri"),                    # 7 vs 10
        ("Purdue", "Queens"),                        # 2 vs 15
    ],
    "Midwest": [
        # Top half
        ("Michigan", None),                          # 1 vs 16 (First Four: UMBC/Howard)
        ("Georgia", "Saint Louis"),                  # 8 vs 9
        ("Texas Tech", "Akron"),                     # 5 vs 12
        ("Alabama", "Hofstra"),                      # 4 vs 13
        # Bottom half
        ("Tennessee", None),                         # 6 vs 11 (First Four: Miami OH/SMU)
        ("Virginia", "Wright St."),                  # 3 vs 14
        ("Kentucky", "Santa Clara"),                 # 7 vs 10
        ("Iowa St.", "Tennessee St."),               # 2 vs 15
    ],
    "South": [
        # Top half
        ("Florida", None),                           # 1 vs 16 (First Four: Prairie View/Lehigh)
        ("Clemson", "Iowa"),                         # 8 vs 9
        ("Vanderbilt", "McNeese"),                   # 5 vs 12
        ("Nebraska", "Troy"),                        # 4 vs 13
        # Bottom half
        ("North Carolina", "VCU"),                   # 6 vs 11
        ("Illinois", "Penn"),                        # 3 vs 14
        ("Saint Mary's", "Texas A&M"),               # 7 vs 10
        ("Houston", "Idaho"),                        # 2 vs 15
    ],
}

# ── First Four Play-In Games (Dayton, March 17-18) ──
# Winners fill in the None slots in REGIONS above.
FIRST_FOUR = [
    {
        "team1": "Texas",
        "team2": "N.C. State",
        "for_seed": 11,
        "region": "West",
        "slot_index": 4,        # REGIONS["West"][4] second element
    },
    {
        "team1": "UMBC",
        "team2": "Howard",
        "for_seed": 16,
        "region": "Midwest",
        "slot_index": 0,        # REGIONS["Midwest"][0] second element
    },
    {
        "team1": "Miami OH",
        "team2": "SMU",
        "for_seed": 11,
        "region": "Midwest",
        "slot_index": 4,        # REGIONS["Midwest"][4] second element
    },
    {
        "team1": "Prairie View",
        "team2": "Lehigh",
        "for_seed": 16,
        "region": "South",
        "slot_index": 0,        # REGIONS["South"][0] second element
    },
]

# ── Seed Map: team name → seed number ──
SEED_MAP = {
    # East
    "Duke": 1, "Siena": 16,
    "Ohio St.": 8, "TCU": 9,
    "St. John's": 5, "Northern Iowa": 12,
    "Kansas": 4, "Cal Baptist": 13,
    "Louisville": 6, "South Florida": 11,
    "Michigan St.": 3, "North Dakota St.": 14,
    "UCLA": 7, "Central Florida": 10,
    "Connecticut": 2, "Furman": 15,
    # West
    "Arizona": 1, "LIU": 16,
    "Villanova": 8, "Utah St.": 9,
    "Wisconsin": 5, "High Point": 12,
    "Arkansas": 4, "Hawaii": 13,
    "Brigham Young": 6,
    "Gonzaga": 3, "Kennesaw St.": 14,
    "Miami FL": 7, "Missouri": 10,
    "Purdue": 2, "Queens": 15,
    # West First Four
    "Texas": 11, "N.C. State": 11,
    # Midwest
    "Michigan": 1,
    "Georgia": 8, "Saint Louis": 9,
    "Texas Tech": 5, "Akron": 12,
    "Alabama": 4, "Hofstra": 13,
    "Tennessee": 6,
    "Virginia": 3, "Wright St.": 14,
    "Kentucky": 7, "Santa Clara": 10,
    "Iowa St.": 2, "Tennessee St.": 15,
    # Midwest First Four
    "UMBC": 16, "Howard": 16,
    "Miami OH": 11, "SMU": 11,
    # South
    "Florida": 1,
    "Clemson": 8, "Iowa": 9,
    "Vanderbilt": 5, "McNeese": 12,
    "Nebraska": 4, "Troy": 13,
    "North Carolina": 6, "VCU": 11,
    "Illinois": 3, "Penn": 14,
    "Saint Mary's": 7, "Texas A&M": 10,
    "Houston": 2, "Idaho": 15,
    # South First Four
    "Prairie View": 16, "Lehigh": 16,
}

# ── Region Map: team name → region ──
REGION_MAP = {}
for _region, _matchups in REGIONS.items():
    for _t1, _t2 in _matchups:
        if _t1:
            REGION_MAP[_t1] = _region
        if _t2:
            REGION_MAP[_t2] = _region
for _ff in FIRST_FOUR:
    REGION_MAP[_ff["team1"]] = _ff["region"]
    REGION_MAP[_ff["team2"]] = _ff["region"]

# ── Final Four Pairings ──
# Region champions from these pairs meet in the national semifinals.
FINAL_FOUR_PAIRINGS = [
    ("East", "West"),
    ("Midwest", "South"),
]

# ── Round Names ──
ROUND_NAMES = [
    "First Four",
    "Round of 64",
    "Round of 32",
    "Sweet 16",
    "Elite 8",
    "Final Four",
    "Championship",
    "Champion",
]


# ═══════════════════════════════════════════════════════════════
# Helper Functions
# ═══════════════════════════════════════════════════════════════

def get_all_team_names():
    """Return a sorted list of all 68 team names in KenPom format."""
    teams = set()
    for matchups in REGIONS.values():
        for t1, t2 in matchups:
            if t1:
                teams.add(t1)
            if t2:
                teams.add(t2)
    for ff in FIRST_FOUR:
        teams.add(ff["team1"])
        teams.add(ff["team2"])
    return sorted(teams)


def get_bracket_with_first_four_resolved(first_four_winners):
    """Return a copy of REGIONS with First Four winners filled in.

    Args:
        first_four_winners: dict mapping First Four game index (0-3) to winner name.
            e.g. {0: "Texas", 1: "Howard", 2: "SMU", 3: "Lehigh"}

    Returns:
        dict of region → list of 8 (team1, team2) matchup tuples, all filled in.
    """
    import copy
    bracket = copy.deepcopy(REGIONS)
    for i, ff in enumerate(FIRST_FOUR):
        winner = first_four_winners.get(i)
        if winner is None:
            raise ValueError(f"First Four game {i} has no winner")
        region = ff["region"]
        slot = ff["slot_index"]
        higher_seed = bracket[region][slot][0]
        bracket[region][slot] = (higher_seed, winner)
    return bracket


def get_seed(team_name):
    """Get the seed number for a team. Returns None if not found."""
    return SEED_MAP.get(team_name)


def get_region(team_name):
    """Get the region for a team. Returns None if not found."""
    return REGION_MAP.get(team_name)


def validate_bracket():
    """Sanity check the bracket data. Returns list of issues found."""
    issues = []

    # Check total team count
    all_teams = get_all_team_names()
    if len(all_teams) != 68:
        issues.append(f"Expected 68 teams, found {len(all_teams)}")

    # Check each region has 8 matchups
    for region, matchups in REGIONS.items():
        if len(matchups) != 8:
            issues.append(f"{region} has {len(matchups)} matchups (expected 8)")

    # Check First Four slots point to None entries
    for i, ff in enumerate(FIRST_FOUR):
        region = ff["region"]
        slot = ff["slot_index"]
        matchup = REGIONS[region][slot]
        if matchup[1] is not None:
            issues.append(
                f"First Four game {i}: slot {region}[{slot}] second element "
                f"should be None but is '{matchup[1]}'"
            )

    # Check all teams have seeds
    for team in all_teams:
        if team not in SEED_MAP:
            issues.append(f"Team '{team}' missing from SEED_MAP")

    # Check all teams have regions
    for team in all_teams:
        if team not in REGION_MAP:
            issues.append(f"Team '{team}' missing from REGION_MAP")

    # Check seed distribution per region (should have 1-16 represented)
    for region, matchups in REGIONS.items():
        seeds_in_region = set()
        for t1, t2 in matchups:
            if t1 and t1 in SEED_MAP:
                seeds_in_region.add(SEED_MAP[t1])
            if t2 and t2 in SEED_MAP:
                seeds_in_region.add(SEED_MAP[t2])
        # First Four regions will be missing the play-in seed until resolved
        expected_higher = {1, 2, 3, 4, 5, 6, 7, 8}
        missing_higher = expected_higher - seeds_in_region
        if missing_higher:
            issues.append(f"{region} missing higher seeds: {missing_higher}")

    if not issues:
        issues.append("OK — bracket validates cleanly")

    return issues


# ═══════════════════════════════════════════════════════════════
# Self-test when run directly
# ═══════════════════════════════════════════════════════════════

if __name__ == "__main__":
    print("=" * 60)
    print("  2026 NCAA Tournament Bracket Data")
    print("=" * 60)

    all_teams = get_all_team_names()
    print(f"\nTotal teams: {len(all_teams)}")

    for region in ["East", "West", "Midwest", "South"]:
        print(f"\n── {region} Region ──")
        for i, (t1, t2) in enumerate(REGIONS[region]):
            s1 = SEED_MAP.get(t1, "?")
            s2 = SEED_MAP.get(t2, "?") if t2 else "FF"
            t2_display = t2 if t2 else "(First Four)"
            print(f"  Game {i}: ({s1}) {t1} vs ({s2}) {t2_display}")

    print(f"\n── First Four ──")
    for i, ff in enumerate(FIRST_FOUR):
        s = ff["for_seed"]
        print(f"  Game {i}: {ff['team1']} vs {ff['team2']} → ({s})-seed in {ff['region']}")

    print(f"\n── Final Four Pairings ──")
    for r1, r2 in FINAL_FOUR_PAIRINGS:
        print(f"  {r1} champion vs {r2} champion")

    print(f"\n── Validation ──")
    for issue in validate_bracket():
        print(f"  {issue}")
