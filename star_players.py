"""
Star Players Database for NCAA Men's Basketball 2025-26.
Used by the injury analyzer to accurately assess impact when a player is injured.

Tiers:
  - "superstar"  (impact 10): National Player of the Year candidates, consensus All-Americans
  - "star"       (impact 9):  All-American caliber, top ~20 players nationally
  - "key_star"   (impact 8):  All-conference first team, top ~50 players
  - "starter"    (impact 7):  Important starters on ranked teams, all-conf 2nd team
  - "rotation"   (impact 6):  Key rotation players on contenders

Each entry: { "team": str, "position": str, "tier": str, "impact": int, "note": str }
"""

STAR_PLAYERS = {
    # ── SUPERSTAR (10) ── National POTY candidates / Consensus All-Americans
    "Cooper Flagg":         {"team": "Duke", "position": "PF", "tier": "superstar", "impact": 10, "note": "Projected #1 pick, NPOY candidate"},
    "Dylan Harper":         {"team": "Rutgers", "position": "SG", "tier": "superstar", "impact": 10, "note": "Projected top-3 pick, elite scorer"},
    "Ace Bailey":           {"team": "Rutgers", "position": "SF", "tier": "superstar", "impact": 10, "note": "Projected top-5 pick, two-way wing"},
    "Kasparas Jakucionis":  {"team": "Illinois", "position": "PG", "tier": "superstar", "impact": 10, "note": "Big Ten POY, projected lottery pick"},
    "VJ Edgecombe":         {"team": "Baylor", "position": "SG", "tier": "superstar", "impact": 10, "note": "Explosive scorer, projected lottery pick"},
    "Liam McNeeley":        {"team": "Connecticut", "position": "SF", "tier": "superstar", "impact": 10, "note": "UConn's go-to scorer, lottery prospect"},

    # ── STAR (9) ── All-American caliber / Top ~20 nationally
    "Mark Sears":           {"team": "Alabama", "position": "PG", "tier": "star", "impact": 9, "note": "SEC POY candidate, elite playmaker"},
    "Johni Broome":         {"team": "Auburn", "position": "C", "tier": "star", "impact": 9, "note": "Dominant big, NPOY candidate"},
    "RJ Davis":             {"team": "North Carolina", "position": "PG", "tier": "star", "impact": 9, "note": "5th-year senior, ACC's top guard"},
    "Otega Oweh":           {"team": "Michigan", "position": "SF", "tier": "star", "impact": 9, "note": "Michigan's top scorer, lottery prospect"},
    "Tre Johnson":          {"team": "Texas", "position": "SG", "tier": "star", "impact": 9, "note": "Elite freshman scorer, projected top-5 pick"},
    "Chaz Lanier":          {"team": "Tennessee", "position": "SG", "tier": "star", "impact": 9, "note": "SEC leading scorer, sharpshooting transfer"},
    "Danny Wolf":           {"team": "Michigan", "position": "C", "tier": "star", "impact": 9, "note": "7-footer, versatile big, lottery prospect"},
    "Jeremiah Fears":       {"team": "Oklahoma", "position": "PG", "tier": "star", "impact": 9, "note": "5-star freshman, projected lottery pick"},
    "Kon Knueppel":         {"team": "Duke", "position": "SG", "tier": "star", "impact": 9, "note": "Duke's second scorer, elite shooting"},
    "Nolan Traore":         {"team": "Creighton", "position": "PG", "tier": "star", "impact": 9, "note": "French lottery prospect, elite passer"},
    "Zuby Ejiofor":         {"team": "Duke", "position": "C", "tier": "star", "impact": 9, "note": "Duke's anchor, elite rim protector"},
    "AJ Storr":             {"team": "Kansas", "position": "SG", "tier": "star", "impact": 9, "note": "Big 12 elite scorer, transfer from Wisconsin"},
    "Hunter Dickinson":     {"team": "Kansas", "position": "C", "tier": "star", "impact": 9, "note": "Veteran 7-footer, Big 12 POY candidate"},
    "Darryn Peterson":      {"team": "Kansas", "position": "PF", "tier": "star", "impact": 9, "note": "#1 recruit, projected top-5 pick"},
    "Egor Demin":           {"team": "Baylor", "position": "PG", "tier": "star", "impact": 9, "note": "Russian lottery prospect, elite size for PG"},
    "Collin Murray-Boyles":  {"team": "South Carolina", "position": "PF", "tier": "star", "impact": 9, "note": "SEC star, versatile forward"},

    # ── KEY STAR (8) ── All-Conference First Team / Top ~50 nationally
    "JT Toppin":            {"team": "Texas Tech", "position": "F", "tier": "key_star", "impact": 9, "note": "Big 12 star, elite two-way forward, transfer from Kentucky"},
    "Tyrese Proctor":       {"team": "Duke", "position": "PG", "tier": "key_star", "impact": 8, "note": "Duke's floor general, experienced guard"},
    "Ian Jackson":          {"team": "North Carolina", "position": "SG", "tier": "key_star", "impact": 8, "note": "5-star freshman, projected lottery pick"},
    "Caleb Love":           {"team": "Arizona", "position": "PG", "tier": "key_star", "impact": 8, "note": "Experienced guard, Arizona's engine"},
    "Jalen Bridges":        {"team": "Houston", "position": "SF", "tier": "key_star", "impact": 8, "note": "Houston's veteran leader"},
    "Emanuel Sharp":        {"team": "Houston", "position": "SG", "tier": "key_star", "impact": 8, "note": "Houston elite shooter"},
    "J'Wan Roberts":        {"team": "Houston", "position": "PF", "tier": "key_star", "impact": 8, "note": "Houston's interior anchor"},
    "L.J. Cryer":           {"team": "Houston", "position": "PG", "tier": "key_star", "impact": 8, "note": "Houston floor general, scoring guard"},
    "Terrence Shannon Jr.": {"team": "Illinois", "position": "SG", "tier": "key_star", "impact": 8, "note": "Elite two-way guard"},
    "Curtis Jones":         {"team": "Iowa St.", "position": "PG", "tier": "key_star", "impact": 8, "note": "Big 12 elite guard, Iowa St. leader"},
    "Tamin Lipsey":         {"team": "Iowa St.", "position": "PG", "tier": "key_star", "impact": 8, "note": "Elite defender, Iowa St. engine"},
    "Milan Momcilovic":     {"team": "Iowa St.", "position": "SG", "tier": "key_star", "impact": 8, "note": "Elite shooter, stretch scoring"},
    "Braden Smith":         {"team": "Purdue", "position": "PG", "tier": "key_star", "impact": 8, "note": "Big Ten's best passer, floor general"},
    "Trey Kaufman-Renn":    {"team": "Purdue", "position": "PF", "tier": "key_star", "impact": 8, "note": "Purdue's post anchor after Edey"},
    "Ryan Nembhard":        {"team": "Gonzaga", "position": "PG", "tier": "key_star", "impact": 8, "note": "WCC POY candidate, elite distributor"},
    "Graham Ike":           {"team": "Gonzaga", "position": "PF", "tier": "key_star", "impact": 8, "note": "Gonzaga's interior scorer"},
    "Alex Karaban":         {"team": "Connecticut", "position": "PF", "tier": "key_star", "impact": 8, "note": "UConn's versatile veteran big"},
    "Ahmad Nowell":         {"team": "Florida", "position": "PG", "tier": "key_star", "impact": 8, "note": "Florida's engine, SEC elite guard"},
    "Walter Clayton Jr.":   {"team": "Florida", "position": "SG", "tier": "key_star", "impact": 8, "note": "Florida's leading scorer"},
    "Jaylin Williams":      {"team": "Michigan St.", "position": "SF", "tier": "key_star", "impact": 8, "note": "MSU's top scorer, experienced wing"},
    "Jase Richardson":      {"team": "Michigan St.", "position": "PG", "tier": "key_star", "impact": 8, "note": "5-star freshman, MSU's future"},
    "Derik Queen":          {"team": "Maryland", "position": "C", "tier": "key_star", "impact": 8, "note": "Big Ten star center, double-double machine"},
    "Taylor Hendricks":     {"team": "Vanderbilt", "position": "PF", "tier": "key_star", "impact": 8, "note": "Vandy's versatile big, SEC breakout"},
    "Tyler Kolek":          {"team": "Marquette", "position": "PG", "tier": "key_star", "impact": 8, "note": "Big East POY candidate, elite assist man"},
    "Kam Jones":            {"team": "Marquette", "position": "SG", "tier": "key_star", "impact": 8, "note": "Marquette's top scorer, clutch performer"},
    "Dalton Knecht":        {"team": "Tennessee", "position": "SF", "tier": "key_star", "impact": 8, "note": "Vols veteran scorer"},
    "Zakai Zeigler":        {"team": "Tennessee", "position": "PG", "tier": "key_star", "impact": 8, "note": "Tennessee's heart, elite defender"},
    "J.P. Estrella":        {"team": "Tennessee", "position": "PF", "tier": "key_star", "impact": 8, "note": "Tennessee's interior presence"},
    "Labaron Philon":       {"team": "Alabama", "position": "SG", "tier": "key_star", "impact": 8, "note": "5-star freshman, Alabama's future"},
    "Aden Holloway":        {"team": "Alabama", "position": "PG", "tier": "key_star", "impact": 8, "note": "Alabama's explosive guard"},
    "Cliff Omoruyi":        {"team": "Alabama", "position": "C", "tier": "key_star", "impact": 8, "note": "Alabama's rim protector"},
    "Flory Bidunga":        {"team": "Arkansas", "position": "C", "tier": "key_star", "impact": 8, "note": "5-star big, elite shot blocker"},
    "D.J. Wagner":          {"team": "Arkansas", "position": "G", "tier": "key_star", "impact": 8, "note": "Transfer from Kentucky, scoring guard"},
    "Justin Edwards":       {"team": "Kentucky", "position": "SF", "tier": "key_star", "impact": 8, "note": "Kentucky wing, NBA talent"},
    "Otis Livingston II":   {"team": "Kentucky", "position": "PG", "tier": "key_star", "impact": 8, "note": "Kentucky floor general"},
    "Jaxson Robinson":      {"team": "Kentucky", "position": "SG", "tier": "key_star", "impact": 8, "note": "Elite shooter, transfer impact"},
    "Jaylen Crocker-Johnson": {"team": "Minnesota", "position": "F", "tier": "key_star", "impact": 8, "note": "Minnesota's top player, foot injury"},
    "Kadary Richmond":      {"team": "St. John's", "position": "PG", "tier": "key_star", "impact": 8, "note": "Big East star guard, St. John's leader"},
    "RJ Luis Jr.":          {"team": "St. John's", "position": "SF", "tier": "key_star", "impact": 8, "note": "St. John's versatile wing scorer"},
    "Pop Isaacs":           {"team": "Baylor", "position": "SG", "tier": "key_star", "impact": 8, "note": "Baylor's sharpshooting guard"},
    "Cody Williams":        {"team": "Arizona", "position": "SF", "tier": "key_star", "impact": 8, "note": "Arizona's lottery prospect wing"},
    "Matas Buzelis":        {"team": "Wake Forest", "position": "SF", "tier": "key_star", "impact": 8, "note": "Transfer impact, NBA prospect"},
    "Isaiah Collier":       {"team": "USC", "position": "PG", "tier": "key_star", "impact": 8, "note": "USC's top point guard, lottery talent"},
    "Boogie Fland":         {"team": "Arkansas", "position": "PG", "tier": "key_star", "impact": 8, "note": "5-star freshman, explosive scorer"},
    "Karter Knox":          {"team": "Arkansas", "position": "SF", "tier": "key_star", "impact": 8, "note": "Arkansas wing, starter"},
    "Malique Ewin":         {"team": "Arkansas", "position": "C", "tier": "key_star", "impact": 7, "note": "Arkansas center, rotation big"},
    "Jason Sheldon":        {"team": "Nebraska", "position": "PG", "tier": "key_star", "impact": 8, "note": "Nebraska's lead guard"},
    "Brice Williams":       {"team": "Nebraska", "position": "SF", "tier": "key_star", "impact": 8, "note": "Nebraska's veteran wing"},
    "Tobe Awaka":           {"team": "Vanderbilt", "position": "C", "tier": "key_star", "impact": 8, "note": "Vanderbilt interior, SEC starter"},
    "Jason Edwards":        {"team": "Vanderbilt", "position": "SG", "tier": "key_star", "impact": 8, "note": "Vanderbilt scoring guard"},

    # ── STARTER (7) ── Important starters on ranked teams
    "Johnell Davis":        {"team": "Florida", "position": "SF", "tier": "starter", "impact": 7, "note": "Florida's key transfer wing"},
    "Sam Rubin":            {"team": "Florida", "position": "PF", "tier": "starter", "impact": 7, "note": "Florida's stretch four"},
    "Jalen Wilson":         {"team": "Kansas", "position": "SF", "tier": "starter", "impact": 7, "note": "Kansas experienced forward"},
    "KJ Adams Jr.":         {"team": "Kansas", "position": "PF", "tier": "starter", "impact": 7, "note": "Kansas's energy forward"},
    "Dajuan Harris Jr.":    {"team": "Kansas", "position": "PG", "tier": "starter", "impact": 7, "note": "Kansas veteran point guard"},
    "Reed Sheppard":        {"team": "Houston", "position": "SG", "tier": "starter", "impact": 7, "note": "Transfer sharpshooter"},
    "Terrance Arceneaux":   {"team": "Houston", "position": "SF", "tier": "starter", "impact": 7, "note": "Houston wing depth"},
    "Dawson Garcia":        {"team": "North Carolina", "position": "PF", "tier": "starter", "impact": 7, "note": "UNC's stretch big, veteran"},
    "Elliot Cadeau":        {"team": "North Carolina", "position": "PG", "tier": "starter", "impact": 7, "note": "UNC's young point guard"},
    "Jeremy Roach":         {"team": "Baylor", "position": "PG", "tier": "starter", "impact": 7, "note": "Experienced guard, transfer from Duke"},
    "Jalen Duren":          {"team": "Villanova", "position": "C", "tier": "starter", "impact": 7, "note": "Villanova center"},
    "Chris Cenac Jr.":      {"team": "Saint Louis", "position": "PF", "tier": "starter", "impact": 7, "note": "A-10 star forward"},
    "Robbie Avila":         {"team": "Saint Louis", "position": "C", "tier": "starter", "impact": 7, "note": "A-10 star center, double-double threat"},
    "Jordan Pope":          {"team": "Texas Tech", "position": "PG", "tier": "starter", "impact": 7, "note": "Tech's floor general"},
    "Darrion Williams":     {"team": "Texas Tech", "position": "SF", "tier": "starter", "impact": 7, "note": "Tech's versatile wing"},
    "Chance McMillian":     {"team": "Texas Tech", "position": "SG", "tier": "starter", "impact": 7, "note": "Tech's scoring guard"},
}


def get_star_player(player_name: str) -> dict:
    """Look up a player by name. Returns None if not in the database."""
    # Exact match first
    if player_name in STAR_PLAYERS:
        return STAR_PLAYERS[player_name]
    # Fuzzy: check if last name matches
    name_lower = player_name.lower().strip()
    for key, val in STAR_PLAYERS.items():
        if key.lower() == name_lower:
            return val
        # Last name match (e.g. "Toppin" matches "JT Toppin")
        parts = key.split()
        if len(parts) >= 2 and parts[-1].lower() == name_lower.split()[-1].lower():
            # Verify first initial or name similarity
            if name_lower.split()[0][0] == parts[0][0].lower():
                return val
    return None


def get_team_stars(team_name: str) -> list:
    """Get all star players for a given team."""
    team_lower = team_name.lower().strip()
    results = []
    for name, info in STAR_PLAYERS.items():
        if info["team"].lower() == team_lower:
            results.append({"player": name, **info})
    results.sort(key=lambda x: x["impact"], reverse=True)
    return results


def build_star_context(team1: str, team2: str) -> str:
    """Build a text block of star players for two teams, to inject into Claude prompts."""
    lines = []
    for team in [team1, team2]:
        stars = get_team_stars(team)
        if stars:
            lines.append(f"\n{team} KEY PLAYERS:")
            for s in stars:
                lines.append(f"  - {s['player']} ({s['position']}) — {s['tier'].upper()} (impact {s['impact']}/10) — {s['note']}")
        else:
            lines.append(f"\n{team}: No star player data available")
    return "\n".join(lines)
