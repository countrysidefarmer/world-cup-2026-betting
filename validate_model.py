#!/usr/bin/env python3
"""
Backtest the multiplier model against completed WC 2026 matches.

Joins model_snapshots.json (pre-kickoff model inputs saved by the scanner)
against actual ESPN settlement data (goals × weighted_cards × corners).

Usage:
    python3 validate_model.py              # show summary stats
    python3 validate_model.py --verbose    # show per-match breakdown
"""

import json
import re
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import requests

DATA_DIR      = Path(__file__).parent / "data"
SNAPSHOT_FILE = DATA_DIR / "model_snapshots.json"
STATS_FILE    = DATA_DIR / "wc2026_stats.json"

ESPN_SCOREBOARD_URL = (
    "https://site.api.espn.com/apis/site/v2/sports/soccer/fifa.world/scoreboard"
)
ESPN_SUMMARY_URL = (
    "https://site.api.espn.com/apis/site/v2/sports/soccer/fifa.world/summary"
)
ESPN_DATE_RANGE = "20260612-20260702"


def _fetch_espn_settlements() -> Dict[str, Dict]:
    """
    Fetch actual match stats (goals, yellows, reds, corners) for all completed matches.
    Returns dict keyed by ESPN event_id.
    """
    try:
        r = requests.get(ESPN_SCOREBOARD_URL,
                         params={"dates": ESPN_DATE_RANGE, "limit": 200},
                         timeout=15)
        r.raise_for_status()
        events = r.json().get("events", [])
    except Exception as exc:
        print(f"[WARN] ESPN scoreboard unavailable: {exc}", file=sys.stderr)
        return {}

    settlements: Dict[str, Dict] = {}
    for ev in events:
        event_id = str(ev.get("id", ""))
        for comp in ev.get("competitions", []):
            if comp.get("status", {}).get("type", {}).get("name") != "STATUS_FULL_TIME":
                continue
            competitors = comp.get("competitors", [])
            if len(competitors) != 2:
                continue

            home = competitors[0]
            away = competitors[1]
            home_name = home.get("team", {}).get("displayName", "")
            away_name = away.get("team", {}).get("displayName", "")
            home_goals = int(home.get("score", 0))
            away_goals = int(away.get("score", 0))
            total_goals = home_goals + away_goals

            # Fetch detailed stats from summary endpoint
            yellows = reds = corners = 0
            try:
                sr = requests.get(ESPN_SUMMARY_URL,
                                  params={"event": event_id}, timeout=10)
                sr.raise_for_status()
                sd = sr.json()
                for team in sd.get("boxscore", {}).get("teams", []):
                    for stat in team.get("statistics", []):
                        try:
                            v = int(stat.get("displayValue", "0"))
                        except (ValueError, TypeError):
                            v = 0
                        n = stat["name"]
                        if n == "yellowCards":
                            yellows += v
                        elif n == "redCards":
                            reds += v
                        elif n == "wonCorners":
                            corners += v
            except Exception:
                pass  # use zeros if summary unavailable

            weighted_cards = yellows + 2 * reds
            actual_mult = total_goals * weighted_cards * corners

            settlements[event_id] = {
                "event_id":       event_id,
                "home":           home_name,
                "away":           away_name,
                "goals":          total_goals,
                "yellows":        yellows,
                "reds":           reds,
                "corners":        corners,
                "weighted_cards": weighted_cards,
                "settlement":     actual_mult,
            }
            break  # one competition per event

    return settlements


def _match_snapshot_to_settlement(
    snapshots: Dict[str, Dict],
    settlements: Dict[str, Dict],
) -> List[Dict]:
    """Fuzzy-join snapshots to settlements by team name matching."""
    results = []
    for key, snap in snapshots.items():
        match_str = snap.get("match", "")   # "Team A vs Team B"
        parts = match_str.split(" vs ")
        if len(parts) != 2:
            continue
        team_a, team_b = parts[0].strip(), parts[1].strip()

        # Find matching settlement by team name substring
        matched = None
        for ev in settlements.values():
            home_ok = any(w in ev["home"] for w in team_a.split() if len(w) > 3)
            away_ok = any(w in ev["away"] for w in team_b.split() if len(w) > 3)
            alt_home = any(w in ev["away"] for w in team_a.split() if len(w) > 3)
            alt_away = any(w in ev["home"] for w in team_b.split() if len(w) > 3)
            if (home_ok and away_ok) or (alt_home and alt_away):
                matched = ev
                break

        if matched is None:
            continue

        theo = snap.get("theo", 0) or 0
        actual = matched["settlement"]
        error = actual - theo
        pct_error = error / max(theo, 1)

        results.append({
            "match":          match_str,
            "kickoff":        snap.get("kickoff", ""),
            "theo":           theo,
            "actual":         actual,
            "goals":          matched["goals"],
            "weighted_cards": matched["weighted_cards"],
            "corners":        matched["corners"],
            "error":          round(error, 1),
            "pct_error":      round(pct_error, 3),
            "mkt_goals":      snap.get("mkt_goals"),
            "mkt_corners":    snap.get("mkt_corners"),
        })

    return results


def main() -> None:
    verbose = "--verbose" in sys.argv

    if not SNAPSHOT_FILE.exists():
        print("No snapshots yet. Run tychemkt_scanner.py first to start logging.")
        return

    with open(SNAPSHOT_FILE) as f:
        snapshots = json.load(f)

    print(f"Loading settlements from ESPN ({len(snapshots)} snapshot(s) to match)...")
    settlements = _fetch_espn_settlements()
    print(f"Found {len(settlements)} completed match(es) on ESPN.")

    results = _match_snapshot_to_settlement(snapshots, settlements)

    if not results:
        print("\nNo matched snapshot+settlement pairs yet — matches may not have settled.")
        return

    errors     = [r["pct_error"] for r in results]
    abs_errors = [abs(e) for e in errors]
    bias       = sum(errors) / len(errors)
    mae        = sum(abs_errors) / len(abs_errors)
    over_count = sum(1 for e in errors if e < 0)   # theo > actual

    print(f"\n{'═'*68}")
    print(f"  Multiplier Model Backtest  ({len(results)} settled match(es))")
    print(f"{'═'*68}")
    print(f"  Mean Absolute Error:  {mae:.1%}")
    print(f"  Bias (actual−theo):   {bias:+.1%}  ({'model OVER-estimates' if bias < 0 else 'model UNDER-estimates'})")
    print(f"  Theo > Actual:        {over_count}/{len(results)} matches")
    print(f"{'─'*68}")

    if verbose:
        print(f"  {'Match':<28}  {'Theo':>6}  {'Actual':>7}  {'Error':>7}  {'%Err':>6}  Mkt")
        print(f"  {'─'*64}")
        for r in sorted(results, key=lambda x: abs(x["pct_error"]), reverse=True):
            mkt = ("[MC]" if r["mkt_goals"] and r["mkt_corners"]
                   else "[M]" if r["mkt_goals"]
                   else "[C]" if r["mkt_corners"] else "")
            print(
                f"  {r['match']:<28}  {r['theo']:>6.1f}  {r['actual']:>7}  "
                f"{r['error']:>+7.1f}  {r['pct_error']:>+6.1%}  {mkt}"
            )
            if verbose:
                print(f"       G={r['goals']}  WC={r['weighted_cards']}  CK={r['corners']}")
        print(f"{'═'*68}")

    # Component-level insight: is goals, cards, or corners the biggest driver of error?
    if results:
        avg_goals   = sum(r["goals"]          for r in results) / len(results)
        avg_wc      = sum(r["weighted_cards"]  for r in results) / len(results)
        avg_corners = sum(r["corners"]         for r in results) / len(results)
        print(f"\n  Actual averages:  G={avg_goals:.2f}  WC={avg_wc:.2f}  CK={avg_corners:.2f}")
        print(f"  Product of avgs:  {avg_goals * avg_wc * avg_corners:.1f}  (expected vs mean theo {sum(r['theo'] for r in results)/len(results):.1f})")


if __name__ == "__main__":
    main()
