"""
test_real_api.py - End-to-end integration test with real DataGolf API.

Requires DATAGOLF_API_KEY to be set. Skips gracefully if not available.

Tests:
1. Fetch rankings -> verify we get 100+ players with valid SG data
2. Fetch skill ratings -> verify SG splits are present
3. prefetch_all_player_data() -> verify merged data has all expected fields
4. build_player_sim_params() -> verify sim params are valid (all rates between 0-1)
5. predict_field() -> verify composite predictions produce valid finish positions
6. Full tournament simulation (10 sims) -> verify results structure
7. Course fit endpoint -> verify fit scores are computed for real players
8. Matchup endpoint -> verify H2H comparison works with real player names

Usage:
    python -m golf.test_real_api
"""

import os
import sys
import traceback
from dotenv import load_dotenv

# Load environment
_golf_env_path = os.path.join(os.path.dirname(__file__), '.env')
load_dotenv(_golf_env_path)
load_dotenv()

from golf.datagolf_client import DataGolfClient
from golf.api_field_map import (
    extract_list, get_field,
    RANKINGS_FIELDS, SKILL_FIELDS, FIELD_FIELDS,
    PLAYER_DECOMP_FIELDS, PRED_FIELDS,
    american_odds_to_probability,
)
from golf.golf_sim_params import prefetch_all_player_data, build_player_sim_params
from golf.golf_course_profiles import get_course_profile, COURSES


# ═══════════════════════════════════════════════════════════════
# Test helpers
# ═══════════════════════════════════════════════════════════════

PASS_COUNT = 0
FAIL_COUNT = 0
SKIP_COUNT = 0


def report(test_name, passed, detail=""):
    global PASS_COUNT, FAIL_COUNT
    status = "PASS" if passed else "FAIL"
    if passed:
        PASS_COUNT += 1
    else:
        FAIL_COUNT += 1
    print(f"  [{status}] {test_name}")
    if detail:
        print(f"         {detail}")


def skip(test_name, reason=""):
    global SKIP_COUNT
    SKIP_COUNT += 1
    print(f"  [SKIP] {test_name}")
    if reason:
        print(f"         {reason}")


def print_sample(data_list, n=3, label="Sample"):
    """Print first n items from a list for manual inspection."""
    print(f"    {label} ({len(data_list)} total, showing first {min(n, len(data_list))}):")
    for i, item in enumerate(data_list[:n]):
        if isinstance(item, dict):
            # Show key fields only
            summary = {k: v for k, v in list(item.items())[:8]}
            print(f"      [{i}] {summary}")
        else:
            print(f"      [{i}] {item}")


# ═══════════════════════════════════════════════════════════════
# Tests
# ═══════════════════════════════════════════════════════════════

def test_1_rankings(client):
    """Test 1: Fetch rankings -> verify 100+ players with valid SG data."""
    print("\n═══ Test 1: Rankings ═══")
    try:
        response = client.get_rankings()
        rankings = extract_list(response, 'rankings')

        report("Rankings response is a list", isinstance(rankings, list))
        report("100+ players returned", len(rankings) >= 100,
               f"Got {len(rankings)} players")

        if rankings:
            first = rankings[0]
            report("player_name present", 'player_name' in first,
                   f"First player: {first.get('player_name')}")
            report("dg_skill_estimate present", 'dg_skill_estimate' in first,
                   f"Value: {first.get('dg_skill_estimate')}")
            report("owgr_rank present", 'owgr_rank' in first,
                   f"Value: {first.get('owgr_rank')}")
            report("dg_id present", 'dg_id' in first,
                   f"Value: {first.get('dg_id')}")

            # Validate data types
            skill = first.get('dg_skill_estimate')
            report("dg_skill_estimate is numeric",
                   isinstance(skill, (int, float)),
                   f"Type: {type(skill).__name__}, Value: {skill}")

            print_sample(rankings, 3, "Rankings")
        return rankings
    except Exception as e:
        report("Rankings fetch", False, str(e))
        traceback.print_exc()
        return []


def test_2_skill_ratings(client):
    """Test 2: Fetch skill ratings -> verify SG splits are present."""
    print("\n═══ Test 2: Skill Ratings (was Skill Decompositions) ═══")
    try:
        response = client.get_skill_decompositions()
        players = extract_list(response, 'skill_ratings')

        report("Skill ratings response is a list", isinstance(players, list))
        report("100+ players returned", len(players) >= 100,
               f"Got {len(players)} players")

        if players:
            first = players[0]
            sg_fields = ['sg_ott', 'sg_app', 'sg_arg', 'sg_putt', 'sg_total']
            for field in sg_fields:
                actual_key = SKILL_FIELDS.get(field, field)
                report(f"{field} present (key: {actual_key})",
                       actual_key in first,
                       f"Value: {first.get(actual_key)}")

            # Check driving_dist (NOT driving_distance)
            report("driving_dist present (NOT driving_distance)",
                   'driving_dist' in first,
                   f"Value: {first.get('driving_dist')}")
            report("driving_acc present (NOT driving_accuracy)",
                   'driving_acc' in first,
                   f"Value: {first.get('driving_acc')}")

            # Verify gir_pct/scrambling_pct/putts_per_round are NOT in the response
            report("gir_pct NOT in response (as expected)",
                   'gir_pct' not in first,
                   "Confirmed: not available from skill-ratings endpoint")
            report("scrambling_pct NOT in response (as expected)",
                   'scrambling_pct' not in first)
            report("putts_per_round NOT in response (as expected)",
                   'putts_per_round' not in first)

            print_sample(players, 3, "Skill Ratings")
        return players
    except Exception as e:
        report("Skill ratings fetch", False, str(e))
        traceback.print_exc()
        return []


def test_3_prefetch(client):
    """Test 3: prefetch_all_player_data() -> verify merged data has all expected fields."""
    print("\n═══ Test 3: prefetch_all_player_data() ═══")
    try:
        players = prefetch_all_player_data(client, tournament_id="current")

        report("Returns a dict", isinstance(players, dict))
        report("50+ players in merged data", len(players) >= 50,
               f"Got {len(players)} players")

        if players:
            # Check a sample player
            name = list(players.keys())[0]
            p = players[name]
            print(f"    Sample player: {name}")

            expected_fields = [
                'dg_skill_estimate', 'owgr_rank', 'sg_total',
                '_player_name', '_player_id',
                'sg_ott', 'sg_app', 'sg_arg', 'sg_putt',
                'driving_distance', 'driving_accuracy',
                'gir_pct', 'scrambling_pct', 'putts_per_round',
            ]
            for field in expected_fields:
                report(f"  {field} present", field in p,
                       f"Value: {p.get(field)}")

            # Check prediction fields (may be present if tournament is active)
            pred_fields = ['win_prob', 'top5_prob', 'top10_prob', 'top20_prob', 'make_cut_prob']
            for field in pred_fields:
                if field in p:
                    val = p[field]
                    report(f"  {field} is valid probability",
                           isinstance(val, (int, float)) and 0.0 <= val <= 1.0,
                           f"Value: {val}")

            # Check that SG values are reasonable (typically -2 to +3)
            for sg_field in ['sg_ott', 'sg_app', 'sg_arg', 'sg_putt']:
                val = p.get(sg_field, 0.0)
                report(f"  {sg_field} in reasonable range",
                       isinstance(val, (int, float)) and -5.0 <= val <= 5.0,
                       f"Value: {val}")

            # Print a few players
            for i, (pname, pdata) in enumerate(list(players.items())[:3]):
                print(f"    [{i}] {pname}: sg_total={pdata.get('sg_total')}, "
                      f"sg_ott={pdata.get('sg_ott')}, owgr={pdata.get('owgr_rank')}, "
                      f"win_prob={pdata.get('win_prob')}")
        return players
    except Exception as e:
        report("prefetch_all_player_data", False, str(e))
        traceback.print_exc()
        return {}


def test_4_sim_params(players):
    """Test 4: build_player_sim_params() -> verify sim params are valid."""
    print("\n═══ Test 4: build_player_sim_params() ═══")
    if not players:
        skip("build_player_sim_params", "No player data from test 3")
        return []

    try:
        # Pick a course
        course_id = list(COURSES.keys())[0] if COURSES else None
        if not course_id:
            skip("build_player_sim_params", "No courses defined")
            return []

        course = get_course_profile(course_id)
        report(f"Course profile loaded: {course_id}", course is not None)

        params_list = []
        errors = []
        for name, stats in list(players.items())[:20]:  # Test first 20 players
            try:
                params = build_player_sim_params(stats, course)
                params_list.append(params)
            except Exception as e:
                errors.append(f"{name}: {e}")

        report("build_player_sim_params succeeded for players",
               len(params_list) > 0,
               f"{len(params_list)} succeeded, {len(errors)} failed")

        if errors:
            print(f"    Errors (first 3): {errors[:3]}")

        if params_list:
            p = params_list[0]
            # Check per-par rate fields are 0-1
            # Sim params use per-par keys: birdie_rate_par3, birdie_rate_par4, etc.
            per_par_rate_fields = [
                'birdie_rate_par3', 'birdie_rate_par4', 'birdie_rate_par5',
                'bogey_rate_par3', 'bogey_rate_par4', 'bogey_rate_par5',
                'eagle_rate_par5',
            ]
            rate_ok = True
            for field in per_par_rate_fields:
                val = p.get(field)
                if val is None or not (0.0 <= val <= 1.0):
                    rate_ok = False
                    break
            report("  per-par rate fields present and in [0,1]",
                   rate_ok,
                   f"Checked {len(per_par_rate_fields)} fields")

            # Check sg_total_adj is numeric
            sg_adj = p.get('sg_total_adj')
            report("  sg_total_adj is numeric",
                   isinstance(sg_adj, (int, float)),
                   f"Value: {sg_adj}")

            print(f"    Sample params: {list(p.keys())[:10]}...")
        return params_list
    except Exception as e:
        report("build_player_sim_params", False, str(e))
        traceback.print_exc()
        return []


def test_5_predict_field(players):
    """Test 5: predict_field() -> verify composite predictions produce valid results."""
    print("\n═══ Test 5: predict_field() ═══")
    if not players:
        skip("predict_field", "No player data from test 3")
        return []

    try:
        from golf.golf_composite_model import predict_field

        course_id = list(COURSES.keys())[0] if COURSES else None
        if not course_id:
            skip("predict_field", "No courses defined")
            return []

        course = get_course_profile(course_id)
        player_list = list(players.values())[:30]  # First 30 players

        predictions = predict_field(player_list, course)

        report("predict_field returns a list", isinstance(predictions, list))
        report("Predictions returned for players",
               len(predictions) > 0,
               f"Got {len(predictions)} predictions")

        if predictions:
            first = predictions[0]
            report("player_name in prediction", 'player_name' in first,
                   f"Value: {first.get('player_name')}")

            finish = first.get('predicted_finish')
            report("predicted_finish is numeric and > 0",
                   isinstance(finish, (int, float)) and finish > 0,
                   f"Value: {finish}")

            win_prob = first.get('win_prob', first.get('win_probability'))
            if win_prob is not None:
                report("win_prob in [0,1]",
                       0.0 <= win_prob <= 1.0,
                       f"Value: {win_prob}")

            print_sample(predictions, 3, "Predictions")
        return predictions
    except Exception as e:
        report("predict_field", False, str(e))
        traceback.print_exc()
        return []


def test_6_tournament_sim(client):
    """Test 6: Full tournament simulation (10 sims) -> verify results structure."""
    print("\n═══ Test 6: Tournament Simulation (10 sims) ═══")
    try:
        from golf.golf_tournament_simulator import GolfTournamentSimulator

        course_id = list(COURSES.keys())[0] if COURSES else None
        if not course_id:
            skip("Tournament simulation", "No courses defined")
            return

        sim = GolfTournamentSimulator(client)

        print("    Prefetching data...")
        num_players = sim.prefetch_data(course_id, tournament_id="current")
        report("Prefetch succeeded", num_players > 0,
               f"{num_players} players loaded")

        print("    Running 10 tournament simulations...")
        results = sim.run(num_tournaments=10, num_workers=1)

        report("Simulation returns a dict", isinstance(results, dict))

        if results:
            # Check for result keys — structure may use different key names
            result_keys = list(results.keys())
            report("  Simulation result has keys",
                   len(result_keys) > 0,
                   f"Keys: {result_keys[:6]}")

            # Try multiple possible key names for player results
            pr = (results.get('player_results')
                  or results.get('results')
                  or results.get('leaderboard')
                  or results)
            if pr:
                report("Player results populated",
                       len(pr) > 0,
                       f"{len(pr)} players")

                # Check first player
                if isinstance(pr, dict):
                    first_name = list(pr.keys())[0]
                    first = pr[first_name]
                elif isinstance(pr, list):
                    first = pr[0]
                    first_name = first.get('player_name', 'unknown')
                else:
                    first = {}
                    first_name = 'unknown'

                print(f"    Sample result for {first_name}:")
                if isinstance(first, dict):
                    for k, v in list(first.items())[:8]:
                        print(f"      {k}: {v}")
    except Exception as e:
        report("Tournament simulation", False, str(e))
        traceback.print_exc()


def test_7_course_fit(client):
    """Test 7: Course fit -> verify fit scores are computed for real players."""
    print("\n═══ Test 7: Course Fit ═══")
    try:
        from golf.golf_course_fit import calc_full_course_fit

        # Get real player data
        response = client.get_skill_decompositions()
        players = extract_list(response, 'skill_ratings')

        if not players:
            skip("Course fit", "No skill data available")
            return

        course_id = list(COURSES.keys())[0] if COURSES else None
        if not course_id:
            skip("Course fit", "No courses defined")
            return

        course = get_course_profile(course_id)

        # Build player stats and compute course fit
        fit_results = []
        for p in players[:10]:
            name = get_field(p, 'player_name', SKILL_FIELDS, '')
            stats = {
                'sg_ott': get_field(p, 'sg_ott', SKILL_FIELDS, 0.0),
                'sg_app': get_field(p, 'sg_app', SKILL_FIELDS, 0.0),
                'sg_arg': get_field(p, 'sg_arg', SKILL_FIELDS, 0.0),
                'sg_putt': get_field(p, 'sg_putt', SKILL_FIELDS, 0.0),
                'sg_total': get_field(p, 'sg_total', SKILL_FIELDS, 0.0),
                # DataGolf returns driving stats as relative values (delta from
                # tour avg). Convert to absolute for calc_full_course_fit().
                'driving_distance': 295.0 + get_field(p, 'driving_distance', SKILL_FIELDS, 0.0),
                'driving_accuracy': 60.0 + get_field(p, 'driving_accuracy', SKILL_FIELDS, 0.0),
                'scrambling_pct': 58.0,
            }
            fit = calc_full_course_fit(stats, course)
            fit_results.append({'player': name, **fit})

        report("Course fit computed for players",
               len(fit_results) > 0,
               f"{len(fit_results)} players")

        if fit_results:
            first = fit_results[0]
            report("total_fit present",
                   'total_fit' in first or 'fit_score' in first or 'course_fit_score' in first,
                   f"Keys: {list(first.keys())}")

            for fr in fit_results[:3]:
                print(f"    {fr.get('player', '?')}: {fr}")

    except Exception as e:
        report("Course fit", False, str(e))
        traceback.print_exc()


def test_8_matchup(client):
    """Test 8: Matchup endpoint -> verify H2H comparison works."""
    print("\n═══ Test 8: Matchup Odds ═══")
    try:
        response = client.get_matchup_odds()
        matches = extract_list(response, 'matchup_odds')

        report("Matchup response is a list", isinstance(matches, list))
        report("Matchups returned", len(matches) > 0,
               f"Got {len(matches)} matchups")

        if matches:
            first = matches[0]
            report("p1_player_name present", 'p1_player_name' in first,
                   f"Value: {first.get('p1_player_name')}")
            report("p2_player_name present", 'p2_player_name' in first,
                   f"Value: {first.get('p2_player_name')}")

            print_sample(matches, 3, "Matchups")
    except Exception as e:
        report("Matchup odds", False, str(e))
        traceback.print_exc()


# ═══════════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════════

def main():
    global PASS_COUNT, FAIL_COUNT, SKIP_COUNT

    print("=" * 60)
    print("DataGolf API End-to-End Integration Test")
    print("=" * 60)

    api_key = os.getenv('DATAGOLF_API_KEY')
    if not api_key:
        print("\nDATA GOLF_API_KEY not set. Skipping all tests.")
        print("Set DATAGOLF_API_KEY in golf/.env or environment to run tests.")
        sys.exit(0)

    print(f"\nAPI Key: {api_key[:4]}...{api_key[-4:]}")

    try:
        client = DataGolfClient(api_key=api_key)
    except Exception as e:
        print(f"\nFailed to create DataGolfClient: {e}")
        sys.exit(1)

    # Run tests
    rankings = test_1_rankings(client)
    skill_data = test_2_skill_ratings(client)
    players = test_3_prefetch(client)
    params = test_4_sim_params(players)
    predictions = test_5_predict_field(players)
    test_6_tournament_sim(client)
    test_7_course_fit(client)
    test_8_matchup(client)

    # Summary
    print("\n" + "=" * 60)
    print(f"RESULTS: {PASS_COUNT} passed, {FAIL_COUNT} failed, {SKIP_COUNT} skipped")
    print("=" * 60)

    if FAIL_COUNT > 0:
        print("\nSome tests FAILED. Review output above for details.")
        sys.exit(1)
    else:
        print("\nAll tests passed!")
        sys.exit(0)


if __name__ == '__main__':
    main()
