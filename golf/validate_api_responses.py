"""
validate_api_responses.py - Validate real DataGolf API responses against code assumptions.

Hits each endpoint, logs the actual JSON structure (keys, types, nesting),
compares against what prefetch_all_player_data() and golf_app.py expect,
and reports mismatches.

Usage:
    python -m golf.validate_api_responses

Requires DATAGOLF_API_KEY in .env or golf/.env
"""

import os
import sys
import json
from typing import Any, Dict, List, Optional, Tuple

# Ensure golf/.env is loaded
from dotenv import load_dotenv
_golf_env_path = os.path.join(os.path.dirname(__file__), '.env')
load_dotenv(_golf_env_path)
load_dotenv()  # also try root .env as fallback

from golf.datagolf_client import DataGolfClient


# ═══════════════════════════════════════════════════════════════
# Configuration: what the code currently expects
# ═══════════════════════════════════════════════════════════════

EXPECTED = {
    'get_rankings': {
        'wrapper_keys': ['rankings'],  # code does rankings.get("rankings", [])
        'item_fields': {
            'player_name': str,
            'dg_skill_estimate': (int, float),
            'owgr_rank': (int, float),
            'dg_id': (int, str),
        },
        'code_refs': [
            'golf_sim_params.py:50-63 — prefetch_all_player_data()',
            'golf_app.py:221-224 — get_player_profile()',
            'golf_app.py:321-326 — get_course_fit()',
            'golf_app.py:645-648 — get_matchup()',
            'golf_backtester.py:289-290 — backtest_tournament()',
        ],
    },
    'get_skill_decompositions': {
        'wrapper_keys': ['decompositions', 'players'],
        'item_fields': {
            'player_name': str,
            'sg_ott': (int, float),
            'sg_app': (int, float),
            'sg_arg': (int, float),
            'sg_putt': (int, float),
            'driving_distance': (int, float),
            'driving_accuracy': (int, float),
            'gir_pct': (int, float),
            'scrambling_pct': (int, float),
            'putts_per_round': (int, float),
        },
        'code_refs': [
            'golf_sim_params.py:66-86 — prefetch_all_player_data()',
            'golf_app.py:329-339 — get_course_fit()',
            'golf_app.py:652-658 — get_matchup()',
        ],
    },
    'get_field_updates': {
        'wrapper_keys': ['field'],
        'item_fields': {
            'player_name': str,
            'dg_id': (int, str),
        },
        'code_refs': [
            'golf_sim_params.py:90-106 — prefetch_all_player_data()',
        ],
    },
    'get_player_decompositions': {
        'wrapper_keys': ['decompositions', 'players'],
        'item_fields': {
            'player_name': str,
            'sg_ott': (int, float),
            'sg_app': (int, float),
            'sg_arg': (int, float),
            'sg_putt': (int, float),
        },
        'code_refs': [
            'golf_sim_params.py:109-120 — prefetch_all_player_data()',
        ],
    },
    'get_pre_tournament_preds': {
        'wrapper_keys': ['predictions', 'players'],
        'item_fields': {
            'player_name': str,
            'win_prob': (int, float),
            'top_5': (int, float),
            'top_10': (int, float),
            'top_20': (int, float),
            'make_cut': (int, float),
        },
        'code_refs': [
            'golf_sim_params.py:122-133 — prefetch_all_player_data()',
        ],
    },
    'get_outright_odds': {
        'wrapper_keys': [],
        'item_fields': {},
        'code_refs': ['golf_app.py:922-933 — get_outright_odds()'],
    },
    'get_matchup_odds': {
        'wrapper_keys': [],
        'item_fields': {},
        'code_refs': ['golf_app.py:936-946 — get_matchup_odds()'],
    },
    'get_historical_events': {
        'wrapper_keys': ['events', 'tournaments'],
        'item_fields': {
            'event_id': (int, str),
            'event_name': str,
            'date': str,
        },
        'code_refs': [
            'golf_backtester.py:484-485 — backtest_date_range()',
        ],
    },
    'get_historical_rounds': {
        'wrapper_keys': ['rounds', 'results'],
        'item_fields': {
            'player_name': str,
            'fin_num': (int, str),
            'made_cut': (bool, int, str),
            'total_to_par': (int, float),
            'event_name': str,
        },
        'code_refs': [
            'golf_backtester.py:212-251 — backtest_tournament()',
        ],
    },
    'get_live_model': {
        'wrapper_keys': [],
        'item_fields': {},
        'code_refs': [],
    },
    'get_general_info': {
        'wrapper_keys': [],
        'item_fields': {},
        'code_refs': [],
    },
}


# ═══════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════

def describe_type(val: Any) -> str:
    """Get a human-readable type description."""
    if val is None:
        return 'NoneType'
    if isinstance(val, list):
        if len(val) > 0:
            return f'list[{describe_type(val[0])}] (len={len(val)})'
        return 'list[] (empty)'
    if isinstance(val, dict):
        return f'dict (keys={list(val.keys())[:10]})'
    return type(val).__name__


def describe_item(item: dict, max_fields: int = 30) -> Dict[str, str]:
    """Describe the fields and types of a dict item."""
    result = {}
    for i, (k, v) in enumerate(item.items()):
        if i >= max_fields:
            result['...'] = f'({len(item) - max_fields} more fields)'
            break
        result[k] = describe_type(v)
    return result


def find_list_data(response: Any, wrapper_keys: List[str]) -> Tuple[Optional[List], str]:
    """Try to extract a list from the response using expected wrapper keys.

    Returns:
        (list_data, extraction_method) tuple
    """
    if isinstance(response, list):
        return response, 'bare list (no wrapper key)'

    if isinstance(response, dict):
        # Try expected wrapper keys
        for key in wrapper_keys:
            if key in response:
                val = response[key]
                if isinstance(val, list):
                    return val, f'dict["{key}"]'

        # Try all keys to find any list
        for key, val in response.items():
            if isinstance(val, list) and len(val) > 0:
                return val, f'dict["{key}"] (unexpected key)'

    return None, 'could not extract list'


def check_fields(item: dict, expected_fields: Dict[str, type]) -> List[str]:
    """Check if expected fields exist in an item and have correct types.

    Returns list of mismatch descriptions.
    """
    mismatches = []
    for field_name, expected_type in expected_fields.items():
        if field_name not in item:
            # Check for similar field names (fuzzy)
            similar = [k for k in item.keys()
                       if field_name.lower().replace('_', '') in k.lower().replace('_', '')
                       or k.lower().replace('_', '') in field_name.lower().replace('_', '')]
            if similar:
                mismatches.append(
                    f"  - Expected key '{field_name}' NOT FOUND. "
                    f"Similar keys: {similar}")
            else:
                mismatches.append(
                    f"  - Expected key '{field_name}' NOT FOUND in response item")
        else:
            val = item[field_name]
            if val is not None and not isinstance(val, expected_type):
                mismatches.append(
                    f"  - Key '{field_name}' has type {type(val).__name__}, "
                    f"expected {expected_type}")
    return mismatches


# ═══════════════════════════════════════════════════════════════
# Main Validation
# ═══════════════════════════════════════════════════════════════

def validate_endpoint(client: DataGolfClient, endpoint_name: str,
                      call_fn, expected_config: dict,
                      cache_dir: str) -> Dict[str, Any]:
    """Validate a single endpoint.

    Args:
        client: DataGolfClient instance
        endpoint_name: Name of the endpoint method
        call_fn: Callable that returns the API response
        expected_config: Expected structure config
        cache_dir: Directory to save raw responses

    Returns:
        dict with validation results
    """
    result = {
        'endpoint': endpoint_name,
        'status': 'unknown',
        'response_type': None,
        'top_level_keys': None,
        'first_item_keys': None,
        'extraction_method': None,
        'mismatches': [],
        'notes': [],
    }

    print(f"\n{'='*60}")
    print(f"=== {endpoint_name}() ===")
    print(f"{'='*60}")

    try:
        response = call_fn()
    except Exception as e:
        result['status'] = 'error'
        result['notes'].append(f"API call failed: {type(e).__name__}: {e}")
        print(f"ERROR: {e}")
        return result

    # Save raw response to cache
    cache_file = os.path.join(cache_dir, f"{endpoint_name.replace('get_', '')}.json")
    try:
        with open(cache_file, 'w') as f:
            json.dump(response, f, indent=2, default=str)
        print(f"Saved raw response to {cache_file}")
    except Exception as e:
        print(f"Warning: Could not save cache: {e}")

    # Analyze response type
    result['response_type'] = describe_type(response)
    print(f"Response type: {result['response_type']}")

    if isinstance(response, dict):
        result['top_level_keys'] = list(response.keys())
        print(f"Top-level keys: {result['top_level_keys']}")
    elif isinstance(response, list):
        print(f"Response is a bare list with {len(response)} items")
        result['top_level_keys'] = '(bare list)'

    # Try to extract list data
    wrapper_keys = expected_config.get('wrapper_keys', [])
    list_data, extraction = find_list_data(response, wrapper_keys)
    result['extraction_method'] = extraction
    print(f"List extraction: {extraction}")

    if list_data and len(list_data) > 0:
        first_item = list_data[0]
        if isinstance(first_item, dict):
            item_desc = describe_item(first_item)
            result['first_item_keys'] = item_desc
            print(f"\nFirst item fields ({len(first_item)} total):")
            for k, v in item_desc.items():
                print(f"  {k}: {v}")

            # Check expected fields
            expected_fields = expected_config.get('item_fields', {})
            if expected_fields:
                mismatches = check_fields(first_item, expected_fields)
                result['mismatches'] = mismatches

                print(f"\nEXPECTED by code:")
                for ref in expected_config.get('code_refs', []):
                    print(f"  {ref}")
                for fname in expected_fields:
                    print(f"  - {fname}")

                print(f"\nACTUAL first item keys: {list(first_item.keys())}")

                if mismatches:
                    print(f"\nMISMATCHES ({len(mismatches)}):")
                    for m in mismatches:
                        print(m)
                else:
                    print(f"\n✓ All expected fields found with correct types")
        else:
            result['notes'].append(f"First item is {type(first_item).__name__}, not dict")
            print(f"First item is {type(first_item).__name__}: {str(first_item)[:200]}")
    elif list_data is not None and len(list_data) == 0:
        result['notes'].append("List is empty (endpoint may have no active data)")
        print("List is empty (no active data)")
    else:
        # Response might be a dict with non-list data
        if isinstance(response, dict):
            result['notes'].append("Response is a dict without list data")
            print("Response is a dict without extractable list")
            for k, v in response.items():
                print(f"  {k}: {describe_type(v)}")

    # Check wrapper key mismatches
    if isinstance(response, dict) and wrapper_keys:
        found_wrapper = None
        for wk in wrapper_keys:
            if wk in response:
                found_wrapper = wk
                break
        if found_wrapper is None:
            actual_list_keys = [k for k, v in response.items() if isinstance(v, list)]
            if actual_list_keys:
                result['mismatches'].append(
                    f"  - Expected wrapper key from {wrapper_keys}, "
                    f"actual list keys: {actual_list_keys}")
    elif isinstance(response, list) and wrapper_keys:
        result['mismatches'].append(
            f"  - Expected wrapper key from {wrapper_keys}, "
            f"but response is a bare list")

    result['status'] = 'ok' if not result['mismatches'] else 'mismatches_found'
    return result


def run_validation():
    """Run the full validation suite."""
    api_key = os.getenv('DATAGOLF_API_KEY')
    if not api_key:
        print("ERROR: DATAGOLF_API_KEY not found in environment.")
        print("Set it in golf/.env or root .env")
        sys.exit(1)

    client = DataGolfClient(api_key=api_key)
    print(f"DataGolf client initialized (base_url={client.base_url})")

    # Create cache directory
    cache_dir = os.path.join(os.path.dirname(__file__), '.api_response_cache')
    os.makedirs(cache_dir, exist_ok=True)
    print(f"Cache directory: {cache_dir}")

    all_results = []

    # Define all endpoint calls
    endpoints = [
        ('get_rankings', lambda: client.get_rankings(), EXPECTED['get_rankings']),
        ('get_skill_decompositions', lambda: client.get_skill_decompositions(), EXPECTED['get_skill_decompositions']),
        ('get_field_updates', lambda: client.get_field_updates(), EXPECTED['get_field_updates']),
        ('get_player_decompositions', lambda: client.get_player_decompositions(), EXPECTED['get_player_decompositions']),
        ('get_pre_tournament_preds', lambda: client.get_pre_tournament_preds(), EXPECTED['get_pre_tournament_preds']),
        ('get_outright_odds', lambda: client.get_outright_odds(), EXPECTED['get_outright_odds']),
        ('get_matchup_odds', lambda: client.get_matchup_odds(), EXPECTED['get_matchup_odds']),
        ('get_historical_events', lambda: client.get_historical_events(), EXPECTED['get_historical_events']),
        ('get_live_model', lambda: client.get_live_model(), EXPECTED['get_live_model']),
        ('get_general_info', lambda: client.get_general_info(), EXPECTED['get_general_info']),
    ]

    for name, call_fn, expected_config in endpoints:
        result = validate_endpoint(client, name, call_fn, expected_config, cache_dir)
        all_results.append(result)

    # Special case: get_historical_rounds needs an event_id
    # Try to get one from the events list
    events_cache = os.path.join(cache_dir, 'historical_events.json')
    event_id = None
    if os.path.exists(events_cache):
        try:
            with open(events_cache, 'r') as f:
                events_data = json.load(f)
            event_list = events_data if isinstance(events_data, list) else []
            if isinstance(events_data, dict):
                for key in events_data:
                    if isinstance(events_data[key], list) and len(events_data[key]) > 0:
                        event_list = events_data[key]
                        break
            if event_list:
                # Get a recent event
                for ev in event_list[-5:]:
                    eid = ev.get('event_id', ev.get('id', ev.get('dg_id')))
                    if eid:
                        event_id = eid
                        break
        except Exception:
            pass

    if event_id:
        print(f"\nUsing event_id={event_id} for historical_rounds")
        result = validate_endpoint(
            client, 'get_historical_rounds',
            lambda: client.get_historical_rounds(event_id=str(event_id)),
            EXPECTED['get_historical_rounds'],
            cache_dir,
        )
        all_results.append(result)
    else:
        print("\nSkipping get_historical_rounds (no event_id available)")

    # ═══════════════════════════════════════════════════════════════
    # Summary
    # ═══════════════════════════════════════════════════════════════

    print(f"\n\n{'='*60}")
    print("VALIDATION SUMMARY")
    print(f"{'='*60}")

    total_mismatches = 0
    for r in all_results:
        status_icon = '✓' if r['status'] == 'ok' else '✗' if r['status'] == 'mismatches_found' else '⚠'
        print(f"\n{status_icon} {r['endpoint']}  [{r['status']}]")
        if r['mismatches']:
            for m in r['mismatches']:
                print(m)
                total_mismatches += 1
        if r['notes']:
            for n in r['notes']:
                print(f"  NOTE: {n}")

    print(f"\n{'='*60}")
    print(f"Total endpoints tested: {len(all_results)}")
    print(f"Total mismatches found: {total_mismatches}")
    print(f"Raw responses saved to: {cache_dir}")
    print(f"{'='*60}")

    # Save summary to cache dir
    summary_file = os.path.join(cache_dir, '_validation_summary.json')
    summary = {
        'total_endpoints': len(all_results),
        'total_mismatches': total_mismatches,
        'results': all_results,
    }
    with open(summary_file, 'w') as f:
        json.dump(summary, f, indent=2, default=str)
    print(f"Summary saved to {summary_file}")

    return all_results


if __name__ == '__main__':
    run_validation()
