#!/usr/bin/env python3
"""
Fetch WC 2026 winner probabilities from Polymarket and calculate expected values.
Writes data/teams.json consumed by index.html.
"""

import json
import re
import sys
import time
from datetime import datetime, timezone
from difflib import get_close_matches
from pathlib import Path

import requests
from typing import Dict, Optional, Any

# ── Scoring constants ─────────────────────────────────────────────────────────

GROUP_PTS = {1: 20, 2: 10, 3: 0, 4: 5}  # 4th place gets +5 consolation

TOURNAMENT_PTS = {
    "1st":          90,
    "2nd":          70,
    "3rd":          55,
    "4th":          40,
    "qf_exit":      30,   # 5th–8th
    "r16_exit":     15,   # 9th–16th
    "r32_exit":      5,   # 17th–32nd
    "group_exit":    0,   # 33rd–47th
    "wooden_spoon":  5,   # 48th (absolute last)
}

ENTERTAINMENT_BONUS = 15  # +15 to 1 team: most combined goals in group stage
N_TEAMS = 48
N_ADVANCE = 32            # teams advancing from groups to knockout stage

# ── All 48 WC 2026 teams ──────────────────────────────────────────────────────
# key → (display_name, flag_emoji, group)

TEAMS = {
    # Group A
    "Mexico":       ("Mexico",         "🇲🇽", "A"),
    "South Korea":  ("South Korea",    "🇰🇷", "A"),
    "South Africa": ("South Africa",   "🇿🇦", "A"),
    "Czechia":      ("Czechia",        "🇨🇿", "A"),
    # Group B
    "Canada":       ("Canada",         "🇨🇦", "B"),
    "Qatar":        ("Qatar",          "🇶🇦", "B"),
    "Switzerland":  ("Switzerland",    "🇨🇭", "B"),
    "Bosnia and Herzegovina": ("Bosnia & Herz.", "🇧🇦", "B"),
    # Group C
    "Brazil":       ("Brazil",         "🇧🇷", "C"),
    "Morocco":      ("Morocco",        "🇲🇦", "C"),
    "Scotland":     ("Scotland",       "🏴󠁧󠁢󠁳󠁣󠁴󠁿", "C"),
    "Haiti":        ("Haiti",          "🇭🇹", "C"),
    # Group D
    "USA":          ("USA",            "🇺🇸", "D"),
    "Australia":    ("Australia",      "🇦🇺", "D"),
    "Paraguay":     ("Paraguay",       "🇵🇾", "D"),
    "Turkiye":      ("Türkiye",        "🇹🇷", "D"),
    # Group E
    "Germany":      ("Germany",        "🇩🇪", "E"),
    "Ivory Coast":  ("Ivory Coast",    "🇨🇮", "E"),
    "Ecuador":      ("Ecuador",        "🇪🇨", "E"),
    "Curacao":      ("Curaçao",        "🇨🇼", "E"),
    # Group F
    "Japan":        ("Japan",          "🇯🇵", "F"),
    "Netherlands":  ("Netherlands",    "🇳🇱", "F"),
    "Sweden":       ("Sweden",         "🇸🇪", "F"),
    "Tunisia":      ("Tunisia",        "🇹🇳", "F"),
    # Group G
    "Belgium":      ("Belgium",        "🇧🇪", "G"),
    "Iran":         ("Iran",           "🇮🇷", "G"),
    "Egypt":        ("Egypt",          "🇪🇬", "G"),
    "New Zealand":  ("New Zealand",    "🇳🇿", "G"),
    # Group H
    "Spain":        ("Spain",          "🇪🇸", "H"),
    "Uruguay":      ("Uruguay",        "🇺🇾", "H"),
    "Cape Verde":   ("Cape Verde",     "🇨🇻", "H"),
    "Saudi Arabia": ("Saudi Arabia",   "🇸🇦", "H"),
    # Group I
    "France":       ("France",         "🇫🇷", "I"),
    "Norway":       ("Norway",         "🇳🇴", "I"),
    "Senegal":      ("Senegal",        "🇸🇳", "I"),
    "Iraq":         ("Iraq",           "🇮🇶", "I"),
    # Group J
    "Argentina":    ("Argentina",      "🇦🇷", "J"),
    "Algeria":      ("Algeria",        "🇩🇿", "J"),
    "Austria":      ("Austria",        "🇦🇹", "J"),
    "Jordan":       ("Jordan",         "🇯🇴", "J"),
    # Group K
    "Portugal":     ("Portugal",       "🇵🇹", "K"),
    "Colombia":     ("Colombia",       "🇨🇴", "K"),
    "Uzbekistan":   ("Uzbekistan",     "🇺🇿", "K"),
    "DR Congo":     ("DR Congo",       "🇨🇩", "K"),
    # Group L
    "England":      ("England",        "🏴󠁧󠁢󠁥󠁮󠁧󠁿", "L"),
    "Croatia":      ("Croatia",        "🇭🇷", "L"),
    "Ghana":        ("Ghana",          "🇬🇭", "L"),
    "Panama":       ("Panama",         "🇵🇦", "L"),
}

# Polymarket name variants → TEAMS key
POLY_ALIASES: Dict[str, str] = {
    "South Korea":            "South Korea",
    "Korea Republic":         "South Korea",
    "Republic of Korea":      "South Korea",
    "Ivory Coast":            "Ivory Coast",
    "Cote d'Ivoire":          "Ivory Coast",
    "Côte d'Ivoire":          "Ivory Coast",
    "United States":          "USA",
    "USA":                    "USA",
    "US":                     "USA",
    "Turkey":                 "Turkiye",
    "Türkiye":                "Turkiye",
    "Curacao":                "Curacao",
    "Curaçao":                "Curacao",
    "Bosnia":                 "Bosnia and Herzegovina",
    "Bosnia & Herzegovina":   "Bosnia and Herzegovina",
    "Bosnia And Herzegovina": "Bosnia and Herzegovina",
    "Bosnia and Herzegovina": "Bosnia and Herzegovina",
    "Congo DR":               "DR Congo",
    "Congo, DR":              "DR Congo",
    "DR Congo":               "DR Congo",
    **{k: k for k in TEAMS},
}

# ── Polymarket API ────────────────────────────────────────────────────────────

GAMMA_BASE = "https://gamma-api.polymarket.com"
_WINNER_RE = re.compile(
    r"^Will\s+(.+?)\s+win\s+the\s+2026\s+FIFA\s+World\s+Cup",
    re.IGNORECASE,
)


def _get(path: str, params: dict) -> Optional[Any]:
    url = f"{GAMMA_BASE}{path}"
    for attempt in range(3):
        try:
            r = requests.get(url, params=params, timeout=20)
            if r.status_code == 429:
                wait = int(r.headers.get("Retry-After", 5))
                time.sleep(wait)
                continue
            r.raise_for_status()
            return r.json()
        except Exception as exc:
            print(f"[WARN] {url} attempt {attempt + 1}: {exc}", file=sys.stderr)
            time.sleep(2 ** attempt)
    return None


def _normalize_name(raw: str) -> Optional[str]:
    raw = raw.strip()
    if raw in POLY_ALIASES:
        return POLY_ALIASES[raw]
    matches = get_close_matches(raw, POLY_ALIASES.keys(), n=1, cutoff=0.8)
    if matches:
        resolved = POLY_ALIASES[matches[0]]
        print(f"[FUZZY] '{raw}' → '{resolved}'", file=sys.stderr)
        return resolved
    print(f"[SKIP] unknown team: '{raw}'", file=sys.stderr)
    return None


def fetch_win_probs() -> Dict[str, float]:
    """Return {TEAMS key: p_win} from binary WC winner markets."""
    probs: dict[str, float] = {}
    # Fetch with generous limit; the search filter is loose so we post-filter
    data = _get("/markets", {
        "search": "World Cup",
        "limit":  200,
        "active": "true",
        "closed": "false",
    })
    if not data:
        print("[WARN] No data from Polymarket", file=sys.stderr)
        return probs
    if isinstance(data, dict):
        data = data.get("results", [])
    for m in data:
        q = m.get("question", "")
        match = _WINNER_RE.match(q)
        if not match:
            continue
        key = _normalize_name(match.group(1))
        if key is None or key not in TEAMS:
            continue
        try:
            prices = json.loads(m.get("outcomePrices", "[]"))
            p_yes = float(prices[0])
            if 0.0 < p_yes < 1.0:
                probs[key] = p_yes
        except (IndexError, ValueError, json.JSONDecodeError):
            pass
    return probs


# ── Probability derivation ────────────────────────────────────────────────────

def derive_probs(p_win: float) -> Dict[str, float]:
    """
    Scale from P(win WC) to all intermediate tournament probabilities.
    Scale factors reflect how many teams remain at each stage relative to 1 winner.
    Caps prevent physically impossible values.
    """
    p_final     = min(p_win * 2.0,  0.82)
    p_sf        = min(p_win * 4.0,  0.88)
    p_qf        = min(p_win * 8.0,  0.92)
    p_advance   = min(p_win * 32.0, 0.97)
    p_win_group = min(p_win * 6.0,  0.88)  # scale=6 keeps most teams below cap

    # Monotonicity
    p_final     = min(p_final, p_sf)
    p_win_group = min(p_win_group, p_advance)

    # Stage-exit probabilities
    p_2nd      = max(0.0, p_final - p_win)
    p_sf_exit  = max(0.0, p_sf - p_final)
    p_3rd      = p_sf_exit / 2.0
    p_4th      = p_sf_exit / 2.0
    p_qf_exit  = max(0.0, p_qf - p_sf)
    p_r16_r32  = max(0.0, p_advance - p_qf)
    p_r16_exit = p_r16_r32 / 2.0
    p_r32_exit = p_r16_r32 / 2.0

    p_group_exit = max(0.0, 1.0 - p_advance)
    # 1 of 16 group-exiting teams is the wooden spoon
    p_wooden_spoon       = p_group_exit / 16.0
    p_group_exit_normal  = p_group_exit - p_wooden_spoon

    # Group finish split
    # 12 group winners + 12 runners-up + 8 3rd-place wildcards = 32 advance
    # Of non-first-place advancers: 12/20 are 2nd, 8/20 are 3rd wildcard
    p_adv_not_first  = max(0.0, p_advance - p_win_group)
    p_grp_2nd        = p_adv_not_first * (12.0 / 20.0)
    p_grp_3rd_adv    = p_adv_not_first * (8.0 / 20.0)
    # Of 16 group exits: 12 are 4th place, 4 are worst 3rd-place
    p_grp_4th        = p_group_exit * (12.0 / 16.0)
    p_grp_3rd_exit   = p_group_exit * (4.0 / 16.0)

    return {
        "p_win":            p_win,
        "p_final":          p_final,
        "p_sf":             p_sf,
        "p_qf":             p_qf,
        "p_advance":        p_advance,
        "p_win_group":      p_win_group,
        "p_2nd":            p_2nd,
        "p_3rd":            p_3rd,
        "p_4th":            p_4th,
        "p_qf_exit":        p_qf_exit,
        "p_r16_exit":       p_r16_exit,
        "p_r32_exit":       p_r32_exit,
        "p_group_exit":     p_group_exit,
        "p_wooden_spoon":   p_wooden_spoon,
        "p_grp_1st":        p_win_group,
        "p_grp_2nd":        p_grp_2nd,
        "p_grp_3rd_adv":    p_grp_3rd_adv,
        "p_grp_4th":        p_grp_4th,
        "p_grp_3rd_exit":   p_grp_3rd_exit,
    }


# ── EV calculation ────────────────────────────────────────────────────────────

def calculate_ev(p: Dict[str, float]) -> Dict[str, float]:
    ev_group = (
        p["p_grp_1st"]      * GROUP_PTS[1]
        + p["p_grp_2nd"]    * GROUP_PTS[2]
        + p["p_grp_4th"]    * GROUP_PTS[4]
        # 3rd-place (advanced or not) earns 0 group pts
    )

    ev_tournament = (
        p["p_win"]          * TOURNAMENT_PTS["1st"]
        + p["p_2nd"]        * TOURNAMENT_PTS["2nd"]
        + p["p_3rd"]        * TOURNAMENT_PTS["3rd"]
        + p["p_4th"]        * TOURNAMENT_PTS["4th"]
        + p["p_qf_exit"]    * TOURNAMENT_PTS["qf_exit"]
        + p["p_r16_exit"]   * TOURNAMENT_PTS["r16_exit"]
        + p["p_r32_exit"]   * TOURNAMENT_PTS["r32_exit"]
        + p["p_group_exit"] * TOURNAMENT_PTS["group_exit"]
        + p["p_wooden_spoon"] * TOURNAMENT_PTS["wooden_spoon"]
    )

    # Uniform 1/48 prior pre-tournament; every team has equal chance pre-play
    ev_entertainment = (1.0 / N_TEAMS) * ENTERTAINMENT_BONUS

    ev_total = ev_group + ev_tournament + ev_entertainment
    return {
        "ev_total":         round(ev_total, 2),
        "ev_group":         round(ev_group, 2),
        "ev_tournament":    round(ev_tournament, 2),
        "ev_entertainment": round(ev_entertainment, 2),
    }


# ── Main ──────────────────────────────────────────────────────────────────────

def detect_state() -> str:
    now = datetime.now(timezone.utc)
    if now < datetime(2026, 6, 11, tzinfo=timezone.utc):
        return "pre_tournament"
    if now < datetime(2026, 7, 2, tzinfo=timezone.utc):
        return "group_stage"
    return "knockout"


def main() -> None:
    print("[INFO] Fetching Polymarket WC 2026 markets...", file=sys.stderr)
    win_probs = fetch_win_probs()
    print(f"[INFO] Got P(win) for {len(win_probs)} / {N_TEAMS} teams", file=sys.stderr)

    # Small fallback prior for teams without Polymarket data
    known_total  = sum(win_probs.values())
    n_unknown    = N_TEAMS - len(win_probs)
    remaining    = max(0.0, 1.0 - known_total)
    prior_p_win  = (remaining / n_unknown) if n_unknown > 0 else 0.001

    results = []
    for key, (name, flag, group) in TEAMS.items():
        has_data = key in win_probs
        p_win    = win_probs.get(key, prior_p_win)
        probs    = derive_probs(p_win)
        ev       = calculate_ev(probs)

        results.append({
            "name":         name,
            "key":          key,
            "flag":         flag,
            "group":        group,
            "data_quality": "full" if has_data else "prior",
            **ev,
            "probs": {
                "p_win":       round(probs["p_win"], 4),
                "p_final":     round(probs["p_final"], 4),
                "p_sf":        round(probs["p_sf"], 4),
                "p_qf":        round(probs["p_qf"], 4),
                "p_advance":   round(probs["p_advance"], 4),
                "p_win_group": round(probs["p_win_group"], 4),
            },
            "breakdown": {
                "p_1st":        round(probs["p_win"], 4),
                "p_2nd":        round(probs["p_2nd"], 4),
                "p_3rd":        round(probs["p_3rd"], 4),
                "p_4th":        round(probs["p_4th"], 4),
                "p_qf_exit":    round(probs["p_qf_exit"], 4),
                "p_r16_exit":   round(probs["p_r16_exit"], 4),
                "p_r32_exit":   round(probs["p_r32_exit"], 4),
                "p_group_exit": round(probs["p_group_exit"], 4),
                "p_grp_1st":    round(probs["p_grp_1st"], 4),
                "p_grp_2nd":    round(probs["p_grp_2nd"], 4),
                "p_grp_4th":    round(probs["p_grp_4th"], 4),
            },
        })

    results.sort(key=lambda r: r["ev_total"], reverse=True)

    if len(results) < 10:
        print("[ERROR] Too few teams resolved — aborting", file=sys.stderr)
        sys.exit(1)

    payload = {
        "last_updated":     datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "tournament_state": detect_state(),
        "polymarket_teams": len(win_probs),
        "teams":            results,
    }

    out = Path(__file__).parent / "data" / "teams.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(payload, indent=2, ensure_ascii=False))
    print(f"[INFO] Wrote {len(results)} teams → {out}", file=sys.stderr)


if __name__ == "__main__":
    main()
