#!/usr/bin/env python3
"""
Usage: python3 world_cup_debug.py <team>
Example: python3 world_cup_debug.py czechia

Reads data/teams.json and prints the full probability/EV breakdown for one team.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

_DATA = Path(__file__).parent / "data" / "teams.json"

TOURNAMENT_PTS = {
    "p_1st":          90,
    "p_2nd":          70,
    "p_3rd":          55,
    "p_4th":          40,
    "p_qf_exit":      30,
    "p_r16_exit":     15,
    "p_r32_exit":      5,
    "p_group_exit":    0,
    "p_wooden_spoon":  5,
}
GROUP_PTS = {
    "p_grp_1st": 20,
    "p_grp_2nd": 10,
    "p_grp_3rd":  0,
    "p_grp_4th":  5,
}
LABELS = {
    "p_1st":          "1st (WC winner)",
    "p_2nd":          "2nd (runner-up)",
    "p_3rd":          "3rd place",
    "p_4th":          "4th place",
    "p_qf_exit":      "QF exit",
    "p_r16_exit":     "R16 exit",
    "p_r32_exit":     "R32 exit",
    "p_group_exit":   "Group exit",
    "p_wooden_spoon": "Wooden spoon",
    "p_grp_1st":      "1st in group",
    "p_grp_2nd":      "2nd in group",
    "p_grp_3rd":      "3rd in group",
    "p_grp_4th":      "4th in group (ws bonus)",
}


def _find_team(query: str, teams: list[dict]) -> dict | None:
    q = query.lower().strip()
    for t in teams:
        if t["key"].lower() == q or t["name"].lower() == q:
            return t
    # Fuzzy: partial match
    for t in teams:
        if q in t["key"].lower() or q in t["name"].lower():
            return t
    return None


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: python3 world_cup_debug.py <team>", file=sys.stderr)
        sys.exit(1)

    query = " ".join(sys.argv[1:])

    if not _DATA.exists():
        print(f"[ERROR] {_DATA} not found — run fetch_data.py first", file=sys.stderr)
        sys.exit(1)

    with open(_DATA) as f:
        d = json.load(f)

    teams = d.get("teams", [])
    t = _find_team(query, teams)
    if t is None:
        keys = [x["key"] for x in teams]
        print(f"[ERROR] Team '{query}' not found. Available keys: {', '.join(sorted(keys))}", file=sys.stderr)
        sys.exit(1)

    bd = t.get("breakdown", {})
    ev_t = t.get("ev_tournament", 0)
    ev_g = t.get("ev_group", 0)
    ev_e = t.get("ev_entertainment", 0)
    ev_total = t.get("ev_total", 0)
    last_updated = d.get("last_updated", "?")[:16].replace("T", " ")

    W = 62
    print()
    print("═" * W)
    print(f"  {t['name']}  (Group {t.get('group','?')})  [{t.get('data_quality','?')}]")
    print(f"  ev_total: {ev_total:.1f}  (group {ev_g:.1f} + tournament {ev_t:.1f} + ent {ev_e:.1f})")
    print(f"  data as of {last_updated}Z")
    print("═" * W)

    print(f"\n  {'TOURNAMENT':<28}  {'P':>7}   {'pts':>4}   {'EV':>6}")
    print("  " + "─" * (W - 2))
    for key, pts in TOURNAMENT_PTS.items():
        p = bd.get(key, 0.0)
        ev = p * pts
        label = LABELS[key]
        print(f"  {label:<28}  {p:>6.2%}  × {pts:>3}  = {ev:>6.2f}")
    print(f"  {'':28}  {'':>7}   {'':>4}   {'──────':>6}")
    print(f"  {'Tournament EV':<28}  {'':>7}   {'':>4}   {ev_t:>6.2f}")

    print(f"\n  {'GROUP':<28}  {'P':>7}   {'pts':>4}   {'EV':>6}")
    print("  " + "─" * (W - 2))
    for key, pts in GROUP_PTS.items():
        p = bd.get(key, 0.0)
        ev = p * pts
        label = LABELS[key]
        print(f"  {label:<28}  {p:>6.2%}  × {pts:>3}  = {ev:>6.2f}")
    print(f"  {'':28}  {'':>7}   {'':>4}   {'──────':>6}")
    print(f"  {'Group EV':<28}  {'':>7}   {'':>4}   {ev_g:>6.2f}")

    print(f"\n  {'Entertainment EV':<28}  {'':>7}   {'':>4}   {ev_e:>6.2f}")
    print(f"  {'TOTAL EV':<28}  {'':>7}   {'':>4}   {ev_total:>6.2f}")
    print()
    print("═" * W)
    print()


if __name__ == "__main__":
    main()
