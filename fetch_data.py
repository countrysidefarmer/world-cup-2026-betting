#!/usr/bin/env python3
"""
Fetch WC 2026 probabilities from Polymarket and calculate expected values.
Uses 5 market types: winner, group winner (x12), R16, QF, SF.
Writes data/teams.json consumed by index.html.
"""

import functools
import json
import math
import random
import re
import sys
import time
from datetime import date, datetime, timedelta, timezone
from difflib import get_close_matches
from itertools import combinations, permutations
from pathlib import Path
from typing import Dict, List, Optional, Any, Tuple

import requests

# ── Scoring constants ─────────────────────────────────────────────────────────

GROUP_PTS = {1: 20, 2: 10, 3: 0, 4: 5}

TOURNAMENT_PTS = {
    "1st":          90,
    "2nd":          70,
    "3rd":          55,
    "4th":          40,
    "qf_exit":      30,   # 5th–8th
    "r16_exit":     15,   # 9th–16th
    "r32_exit":      5,   # 17th–32nd
    "group_exit":    0,   # 33rd–47th
    "wooden_spoon":  5,   # 48th
}

ENTERTAINMENT_BONUS = 15
N_TEAMS = 48
VOLUME_MIN = 50  # USD — exclude zero-volume automated quotes only

ESPN_SCOREBOARD_URL = (
    "https://site.api.espn.com/apis/site/v2/sports/soccer/fifa.world/scoreboard"
)
WC_START_DATE = date(2026, 6, 11)  # first day of group stage fixtures

# ── All 48 WC 2026 teams ──────────────────────────────────────────────────────

# FIFA world rankings (approximate, used as wooden spoon 3rd tiebreaker)
FIFA_RANKING: Dict[str, int] = {
    "Argentina":              1,
    "France":                 2,
    "Spain":                  3,
    "England":                4,
    "Brazil":                 5,
    "Portugal":               6,
    "Belgium":                7,
    "Netherlands":            8,
    "Germany":                9,
    "Colombia":              10,
    "Morocco":               11,
    "Croatia":               12,
    "Uruguay":               13,
    "USA":                   14,
    "Switzerland":           15,
    "Japan":                 16,
    "Mexico":                17,
    "Senegal":               18,
    "Turkiye":               19,
    "South Korea":           20,
    "Ecuador":               22,
    "Austria":               23,
    "Norway":                24,
    "Canada":                25,
    "Australia":             26,
    "Sweden":                27,
    "Algeria":               28,
    "Iran":                  29,
    "Scotland":              30,
    "Egypt":                 34,
    "Tunisia":               36,
    "Czechia":               37,
    "Paraguay":              40,
    "Iraq":                  45,
    "Ivory Coast":           46,
    "Saudi Arabia":          53,
    "Bosnia and Herzegovina":55,
    "DR Congo":              57,
    "Qatar":                 58,
    "South Africa":          60,
    "Ghana":                 61,
    "Uzbekistan":            62,
    "Cape Verde":            64,
    "Jordan":                70,
    "New Zealand":           79,
    "Panama":                80,
    "Curacao":               82,
    "Haiti":                 97,
}

TEAMS: Dict[str, tuple] = {
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

GROUPS: Dict[str, List[str]] = {
    "A": ["Mexico", "South Korea", "South Africa", "Czechia"],
    "B": ["Canada", "Qatar", "Switzerland", "Bosnia and Herzegovina"],
    "C": ["Brazil", "Morocco", "Scotland", "Haiti"],
    "D": ["USA", "Australia", "Paraguay", "Turkiye"],
    "E": ["Germany", "Ivory Coast", "Ecuador", "Curacao"],
    "F": ["Japan", "Netherlands", "Sweden", "Tunisia"],
    "G": ["Belgium", "Iran", "Egypt", "New Zealand"],
    "H": ["Spain", "Uruguay", "Cape Verde", "Saudi Arabia"],
    "I": ["France", "Norway", "Senegal", "Iraq"],
    "J": ["Argentina", "Algeria", "Austria", "Jordan"],
    "K": ["Portugal", "Colombia", "Uzbekistan", "DR Congo"],
    "L": ["England", "Croatia", "Ghana", "Panama"],
}

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
    "Congo DR":                               "DR Congo",
    "Congo, DR":                              "DR Congo",
    "DR Congo":                               "DR Congo",
    "Democratic Republic of Congo":           "DR Congo",
    "Democratic Republic of the Congo":       "DR Congo",
    "the Democratic Republic of Congo":       "DR Congo",
    "the Democratic Republic of the Congo":   "DR Congo",
    "IR Iran":                                "Iran",
    **{k: k for k in TEAMS},
}

# ── Polymarket API ────────────────────────────────────────────────────────────

GAMMA_BASE = "https://gamma-api.polymarket.com"
_WINNER_RE   = re.compile(r"^Will\s+(.+?)\s+win\s+the\s+2026\s+FIFA\s+World\s+Cup", re.IGNORECASE)
_REACH_RE    = re.compile(r"^Will\s+(.+?)\s+(?:reach|make)\s+the", re.IGNORECASE)
_WIN_GRP_RE  = re.compile(r"^Will\s+(.+?)\s+win\s+Group", re.IGNORECASE)


def _get(path: str, params: dict) -> Optional[Any]:
    url = f"{GAMMA_BASE}{path}"
    for attempt in range(3):
        try:
            r = requests.get(url, params=params, timeout=20)
            if r.status_code == 429:
                time.sleep(int(r.headers.get("Retry-After", 5)))
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


def _yes_price(market: dict) -> Optional[float]:
    try:
        prices = json.loads(market.get("outcomePrices", "[]"))
        p = float(prices[0])
        return p if 0.0 < p < 1.0 else None
    except (IndexError, ValueError, json.JSONDecodeError):
        return None


def _volume(market: dict) -> float:
    try:
        return float(market.get("volume", 0))
    except (TypeError, ValueError):
        return 0.0


# ── Live group standings (ESPN public API) ────────────────────────────────────

def fetch_group_standings() -> Tuple[Dict[str, Dict], Dict[str, List[Tuple[str, str]]], List[Tuple[str, str, str, str, str]], Dict[Tuple[str, str], Tuple[int, int]]]:
    """
    Build group stage standings from ESPN's scoreboard API by accumulating
    all completed match results over the group stage date window.

    Returns:
      standings:         {team_key: {"played", "pts", "gf", "ga", "gd"}}
      played_pairs:      {group_letter: [(team_a_key, team_b_key), ...]} for MC simulation
      upcoming_fixtures: [(home_key, away_key, home_abbr, away_abbr, date)] for game-odds fetch
      game_scores:       {(home_key, away_key): (home_goals, away_goals)} for H2H tiebreaker
    """
    # Fetch one day at a time: ESPN scoreboard ignores date ranges and returns
    # only the current day's events when a range is passed.
    all_events: list = []
    fetch_date = WC_START_DATE
    today = date.today()
    while fetch_date <= today:
        try:
            r = requests.get(
                ESPN_SCOREBOARD_URL,
                params={"dates": fetch_date.strftime("%Y%m%d"), "limit": 50},
                timeout=10,
            )
            r.raise_for_status()
            all_events.extend(r.json().get("events", []))
        except Exception as exc:
            print(f"[WARN] ESPN scoreboard {fetch_date}: {exc}", file=sys.stderr)
        fetch_date += timedelta(days=1)
    # Deduplicate by event id, preferring STATUS_FULL_TIME over earlier SCHEDULED/live
    # versions (ESPN returns upcoming games in the prior day's feed).
    best: dict = {}
    for ev in all_events:
        eid = ev.get("id")
        if eid is None:
            continue
        status = ev.get("competitions", [{}])[0].get("status", {}).get("type", {}).get("name", "")
        if eid not in best or status == "STATUS_FULL_TIME":
            best[eid] = ev
    events = list(best.values())
    if not events:
        print("[WARN] ESPN scoreboard returned no events", file=sys.stderr)
        return {}, {g: [] for g in "ABCDEFGHIJKL"}, [], {}

    raw_stats: Dict[str, Dict] = {}
    espn_pairs: List[Tuple[str, str]] = []        # (home_espn_name, away_espn_name)
    upcoming_raw: List[Tuple[str, str, str, str, str]] = []  # (home_key, away_key, home_abbr, away_abbr, date)
    game_scores: Dict[Tuple[str, str], Tuple[int, int]] = {}  # completed game results for H2H

    for event in events:
        date_str = event.get("date", "")[:10]
        for comp in event.get("competitions", []):
            status = comp.get("status", {}).get("type", {}).get("name", "")
            competitors = comp.get("competitors", [])
            if len(competitors) != 2:
                continue

            home_obj  = competitors[0].get("team", {})
            away_obj  = competitors[1].get("team", {})
            home_name = home_obj.get("displayName", "")
            away_name = away_obj.get("displayName", "")

            if status != "STATUS_FULL_TIME":
                # Capture upcoming group-stage fixtures for Polymarket game-market lookup
                hk = _normalize_name(home_name)
                ak = _normalize_name(away_name)
                if hk and ak and hk in TEAMS and ak in TEAMS:
                    upcoming_raw.append((
                        hk, ak,
                        home_obj.get("abbreviation", "").lower(),
                        away_obj.get("abbreviation", "").lower(),
                        date_str,
                    ))
                continue

            home_g = int(competitors[0].get("score", 0))
            away_g = int(competitors[1].get("score", 0))

            if home_name and away_name:
                espn_pairs.append((home_name, away_name))
                hk = _normalize_name(home_name)
                ak = _normalize_name(away_name)
                if hk and ak and hk in TEAMS and ak in TEAMS:
                    game_scores[(hk, ak)] = (home_g, away_g)

            for team_name, gf, ga in [
                (home_name, home_g, away_g),
                (away_name, away_g, home_g),
            ]:
                if not team_name:
                    continue
                if team_name not in raw_stats:
                    raw_stats[team_name] = {"played": 0, "gf": 0, "ga": 0, "pts": 0}
                raw_stats[team_name]["played"] += 1
                raw_stats[team_name]["gf"]     += gf
                raw_stats[team_name]["ga"]     += ga
                raw_stats[team_name]["pts"]    += (3 if gf > ga else 1 if gf == ga else 0)

    # Normalise ESPN team names to our canonical keys; also build ESPN→key mapping
    results: Dict[str, Dict] = {}
    name_to_key: Dict[str, str] = {}
    for espn_name, s in raw_stats.items():
        key = _normalize_name(espn_name)
        if not key:
            continue
        name_to_key[espn_name] = key
        results[key] = {
            "played": s["played"],
            "pts":    s["pts"],
            "gf":     s["gf"],
            "ga":     s["ga"],
            "gd":     s["gf"] - s["ga"],
        }

    # Build played pairs grouped by group letter — used by Monte Carlo simulation
    played_pairs: Dict[str, List[Tuple[str, str]]] = {g: [] for g in "ABCDEFGHIJKL"}
    for home_espn, away_espn in espn_pairs:
        hk = name_to_key.get(home_espn) or _normalize_name(home_espn)
        ak = name_to_key.get(away_espn) or _normalize_name(away_espn)
        if not hk or not ak or hk not in TEAMS or ak not in TEAMS:
            continue
        g = TEAMS[hk][2]
        if TEAMS[ak][2] == g:
            played_pairs[g].append((hk, ak))

    n_games = sum(v["played"] for v in results.values()) // 2
    if results:
        print(f"[INFO] ESPN: {n_games} completed game(s), {len(results)} team(s) with data", file=sys.stderr)
    else:
        print("[INFO] ESPN: no completed games yet", file=sys.stderr)
    print(f"[INFO] ESPN: {len(upcoming_raw)} upcoming group-stage fixture(s) identified", file=sys.stderr)
    return results, played_pairs, upcoming_raw, game_scores


_GAME_WIN_RE  = re.compile(r"Will\s+(.+?)\s+win\s+on\s+\d{4}-\d{2}-\d{2}", re.IGNORECASE)
_GAME_DRAW_RE = re.compile(r"end in a draw", re.IGNORECASE)
_FIFWC_SERIES_ID = "11433"


def _fetch_wc_game_events() -> Dict[Tuple[str, str], Dict]:
    """
    Batch-fetch all FIFA WC 2026 per-game events from the Gamma API in one call.

    Uses series_id=11433 (soccer-fifwc) which returns all group-stage (and later
    knockout) game markets. Keyed by (home_team_key, away_team_key) using the
    teams[].name field — no abbreviation or date guessing needed.
    """
    data = _get("/events", {"series_id": _FIFWC_SERIES_ID, "limit": 500})
    if not data:
        return {}
    events: Dict[Tuple[str, str], Dict] = {}
    for ev in data:
        teams = ev.get("teams", [])
        if len(teams) < 2:
            continue
        hk = _normalize_name(teams[0].get("name", ""))
        ak = _normalize_name(teams[1].get("name", ""))
        if hk and ak:
            events[(hk, ak)] = ev
    return events


def fetch_match_odds(
    upcoming_fixtures: List[Tuple[str, str, str, str, str]],
) -> Dict[Tuple[str, str], Dict[str, float]]:
    """
    Fetch Polymarket per-game W/D/L odds for upcoming group-stage fixtures.

    Batch-fetches all WC game events from the soccer-fifwc series, then matches
    each upcoming fixture by normalized team name — no slug/abbreviation/date
    guessing required.

    Returns {(home_key, away_key): {"p_win", "p_draw", "p_lose"}} with values
    normalised to sum to 1.0.
    """
    game_events = _fetch_wc_game_events()
    print(
        f"[INFO] Fetched {len(game_events)} WC game events from Polymarket series",
        file=sys.stderr,
    )

    result: Dict[Tuple[str, str], Dict[str, float]] = {}

    for home_key, away_key, home_abbr, away_abbr, date_str in upcoming_fixtures:
        ev = game_events.get((home_key, away_key))
        if ev is None:
            ev = game_events.get((away_key, home_key))
            if ev:
                home_key, away_key = away_key, home_key
        if ev is None:
            continue

        markets = ev.get("markets", [])
        p_home_win = p_draw_val = p_away_win = None

        for m in markets:
            q = m.get("question", "")
            p = _yes_price(m)
            if p is None:
                continue
            if _GAME_DRAW_RE.search(q):
                p_draw_val = p
                continue
            win_m = _GAME_WIN_RE.search(q)
            if not win_m:
                continue
            key = _normalize_name(win_m.group(1).strip())
            if key == home_key:
                p_home_win = p
            elif key == away_key:
                p_away_win = p

        if None not in (p_home_win, p_draw_val, p_away_win):
            total = p_home_win + p_draw_val + p_away_win
            if total > 0:
                result[(home_key, away_key)] = {
                    "p_win":  p_home_win / total,
                    "p_draw": p_draw_val / total,
                    "p_lose": p_away_win / total,
                }

    print(
        f"[INFO] Game markets: {len(result)}/{len(upcoming_fixtures)} fixtures covered",
        file=sys.stderr,
    )
    return result


ESPN_SUMMARY_URL = (
    "https://site.api.espn.com/apis/site/v2/sports/soccer/fifa.world/summary"
)
_STATS_FILE = Path(__file__).parent / "data" / "wc2026_stats.json"


def fetch_tournament_stats() -> None:
    """
    Fetch yellow/red card counts for every completed WC 2026 match via ESPN summary API.
    Results are cached in data/wc2026_stats.json — only new matches are fetched each run.
    """
    # Load existing cache
    cache: Dict[str, Any] = {"total_matches": 0, "total_yellows": 0, "total_reds": 0, "per_match": {}}
    if _STATS_FILE.exists():
        try:
            with open(_STATS_FILE) as f:
                cache = json.load(f)
        except Exception:
            pass
    per_match: Dict[str, Any] = cache.get("per_match", {})

    # Get completed event IDs from scoreboard (per-day loop — same as fetch_group_standings)
    all_stat_events: list = []
    fetch_date = WC_START_DATE
    today = date.today()
    while fetch_date <= today:
        try:
            r = requests.get(
                ESPN_SCOREBOARD_URL,
                params={"dates": fetch_date.strftime("%Y%m%d"), "limit": 50},
                timeout=10,
            )
            r.raise_for_status()
            all_stat_events.extend(r.json().get("events", []))
        except Exception as exc:
            print(f"[WARN] ESPN stats {fetch_date}: {exc}", file=sys.stderr)
        fetch_date += timedelta(days=1)
    best_s: dict = {}
    for ev in all_stat_events:
        eid = ev.get("id")
        if eid is None:
            continue
        status = ev.get("competitions", [{}])[0].get("status", {}).get("type", {}).get("name", "")
        if eid not in best_s or status == "STATUS_FULL_TIME":
            best_s[eid] = ev
    events = list(best_s.values())
    if not events:
        print("[WARN] ESPN stats scoreboard returned no events", file=sys.stderr)
        return

    new_matches = 0
    for event in events:
        event_id = str(event.get("id", ""))
        # Re-fetch if missing or if older entry lacks per-team breakdown
        if not event_id or (event_id in per_match and "teams" in per_match[event_id]):
            continue
        for comp in event.get("competitions", []):
            if comp.get("status", {}).get("type", {}).get("name") != "STATUS_FULL_TIME":
                continue
            # Fetch match summary for card stats
            try:
                sr = requests.get(ESPN_SUMMARY_URL, params={"event": event_id}, timeout=10)
                sr.raise_for_status()
                sd = sr.json()
            except Exception as exc:
                print(f"[WARN] ESPN summary {event_id}: {exc}", file=sys.stderr)
                break
            teams_data = []
            for team_obj in sd.get("boxscore", {}).get("teams", []):
                ty = tr = 0
                for stat in team_obj.get("statistics", []):
                    try:
                        v = int(stat.get("displayValue", "0"))
                    except (ValueError, TypeError):
                        v = 0
                    if stat["name"] == "yellowCards":
                        ty += v
                    elif stat["name"] == "redCards":
                        tr += v
                teams_data.append({
                    "team": team_obj.get("team", {}).get("displayName", ""),
                    "yellows": ty,
                    "reds": tr,
                })
            yellows = sum(t["yellows"] for t in teams_data)
            reds    = sum(t["reds"]    for t in teams_data)
            per_match[event_id] = {"yellows": yellows, "reds": reds, "teams": teams_data}
            new_matches += 1
            break  # one competition per event

    # Recompute totals from all cached matches
    total_matches = len(per_match)
    total_yellows = sum(v["yellows"] for v in per_match.values())
    total_reds    = sum(v["reds"]    for v in per_match.values())

    cache = {
        "last_updated":   datetime.now(timezone.utc).isoformat(),
        "total_matches":  total_matches,
        "total_yellows":  total_yellows,
        "total_reds":     total_reds,
        "per_match":      per_match,
    }
    _STATS_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(_STATS_FILE, "w") as f:
        json.dump(cache, f, indent=2)

    factor = total_yellows / (total_matches * 3.41) if total_matches > 0 else 0.744
    print(
        f"[INFO] Tournament stats: {total_matches} matches, {total_yellows}Y {total_reds}R  "
        f"→ card factor = {factor:.3f}  ({new_matches} new)",
        file=sys.stderr,
    )


# ── Fetch functions ───────────────────────────────────────────────────────────

def fetch_win_probs() -> Dict[str, float]:
    """P(win WC) from binary winner markets."""
    probs: Dict[str, float] = {}
    data = _get("/markets", {"search": "World Cup", "limit": 200, "active": "true", "closed": "false"})
    if not data:
        return probs
    if isinstance(data, dict):
        data = data.get("results", [])
    for m in data:
        if _volume(m) < VOLUME_MIN:
            continue
        match = _WINNER_RE.match(m.get("question", ""))
        if not match:
            continue
        key = _normalize_name(match.group(1))
        if key and key in TEAMS:
            p = _yes_price(m)
            if p is not None:
                probs[key] = p
    print(f"[INFO] Winner markets: {len(probs)} teams", file=sys.stderr)
    return probs


def _settlement_price(market: dict) -> Optional[float]:
    """Return YES price including 0.0 and 1.0 — for reading resolved market settlements."""
    try:
        prices = json.loads(market.get("outcomePrices", "[]"))
        return float(prices[0])
    except (IndexError, ValueError, json.JSONDecodeError):
        return None


def fetch_group_winner_probs() -> Dict[str, Dict[str, float]]:
    """
    P(win group) for each team via slug-based lookup.
    Returns {group_letter: {team_key: normalized_prob}}.
    Prices are normalized within each group to sum to 1.

    For resolved/closed markets, uses settlement prices (0.0 or 1.0) directly —
    these are ground truth (one team at 1.0, all others at 0.0).
    """
    result: Dict[str, Dict[str, float]] = {}
    for g in "ABCDEFGHIJKL":
        data = _get("/events", {"slug": f"world-cup-group-{g.lower()}-winner"})
        if not data or not isinstance(data, list) or not data:
            continue
        ev = data[0]
        raw: Dict[str, float] = {}
        for m in ev.get("markets", []):
            if _volume(m) < VOLUME_MIN:
                continue
            match = _WIN_GRP_RE.match(m.get("question", ""))
            if not match:
                continue
            key = _normalize_name(match.group(1))
            if not key or key not in TEAMS:
                continue
            # Use settlement price for individually-closed markets (resolved YES/NO at 1.0/0.0).
            # Event-level `closed` lags behind individual market closures in the API.
            p = _settlement_price(m) if m.get("closed", False) else _yes_price(m)
            if p is not None:
                raw[key] = p
        if raw:
            # If one market settled at 1.0, the group is decided — use that as certainty
            # and ignore any residual live prices (e.g. Australia's 0.0005 straggler).
            settled = [k for k, v in raw.items() if v >= 1.0]
            if settled:
                result[g] = {settled[0]: 1.0}
                continue
            total = sum(raw.values())
            if total > 0:
                result[g] = {k: v / total for k, v in raw.items()}
    n = sum(len(v) for v in result.values())
    n_resolved = sum(
        1 for v in result.values() if any(p >= 0.99 for p in v.values())
    )
    print(
        f"[INFO] Group winner markets: {n} teams across {len(result)} groups "
        f"({n_resolved} resolved)",
        file=sys.stderr,
    )
    return result


def _fetch_stage_event(slug: str, label: str) -> Dict[str, float]:
    """Fetch 'nation to reach X' multi-market event. Returns raw prices for all teams found.

    No volume filter — we want every team including low-volume markets.
    Handles resolved markets: tries _yes_price first; if None (price is exactly 0 or 1),
    falls back to _settlement_price so eliminated teams get p=0.0 rather than being skipped.
    """
    probs: Dict[str, float] = {}
    data = _get("/events", {"slug": slug})
    if not data or not isinstance(data, list) or not data:
        print(f"[WARN] No data for {label} ({slug})", file=sys.stderr)
        return probs
    for m in data[0].get("markets", []):
        match = _REACH_RE.match(m.get("question", ""))
        if not match:
            continue
        key = _normalize_name(match.group(1))
        if not key or key not in TEAMS:
            continue
        p = _yes_price(m)
        if p is None:
            p = _settlement_price(m)  # picks up resolved markets at 0 or 1
        if p is not None:
            probs[key] = p
    n_resolved = sum(1 for v in probs.values() if v == 0.0 or v >= 0.99)
    print(f"[INFO] {label} markets: {len(probs)} teams ({n_resolved} resolved)", file=sys.stderr)
    return probs


def fetch_r16_probs() -> Dict[str, float]:
    return _fetch_stage_event("world-cup-nation-to-reach-round-of-16", "R16")


def fetch_qf_probs() -> Dict[str, float]:
    return _fetch_stage_event("world-cup-nation-to-reach-quarterfinals", "QF")


def fetch_sf_probs() -> Dict[str, float]:
    return _fetch_stage_event("world-cup-nation-to-reach-semifinals", "SF")


def fetch_final_probs() -> Dict[str, float]:
    return _fetch_stage_event("world-cup-nation-to-reach-final", "Final")


# ── Group finish markets (2nd place + last place) ────────────────────────────

_FINISH_2ND_RE = re.compile(
    r"Will\s+(.+?)\s+finish second in Group ([A-L]).*2026 FIFA World Cup",
    re.IGNORECASE,
)
_FINISH_4TH_RE = re.compile(
    r"Will\s+(.+?)\s+finish last in Group ([A-L]).*2026 FIFA World Cup",
    re.IGNORECASE,
)

# Hardcoded event slugs — discovered via wc-group-futures tag on 2026-06-18.
# 2nd-place slugs created ~2026-06-05 14:00–15:30 UTC; last-place ~2026-06-04 23:30 UTC.
_GROUP_FINISH_SLUGS: Dict[str, Dict[str, str]] = {
    "A": {"2nd": "world-cup-group-a-second-place-20260605143908410", "last": "world-cup-group-a-last-place-20260604232412189"},
    "B": {"2nd": "world-cup-group-b-second-place-20260605145934706", "last": "world-cup-group-b-last-place-20260604232643540"},
    "C": {"2nd": "world-cup-group-c-second-place-20260605150541408", "last": "world-cup-group-c-last-place-20260604235104332"},
    "D": {"2nd": "world-cup-group-d-second-place-20260605150925317", "last": "world-cup-group-d-last-place-20260604235452724"},
    "E": {"2nd": "world-cup-group-e-second-place-20260605151546476", "last": "world-cup-group-e-last-place-20260604235658873"},
    "F": {"2nd": "world-cup-group-f-second-place-20260605151832850", "last": "world-cup-group-f-last-place-20260604235829189"},
    "G": {"2nd": "world-cup-group-g-second-place-20260605152042794", "last": "world-cup-group-g-last-place-20260605000104252"},
    "H": {"2nd": "world-cup-group-h-second-place-20260605152236065", "last": "world-cup-group-h-last-place-20260605000238709"},
    "I": {"2nd": "world-cup-group-i-second-place-20260605152635447", "last": "world-cup-group-i-last-place-20260605000400141"},
    "J": {"2nd": "world-cup-group-j-second-place-20260605152843566", "last": "world-cup-group-j-last-place-20260605000527700"},
    "K": {"2nd": "world-cup-group-k-second-place-20260605153109381", "last": "world-cup-group-k-last-place-20260605000716522"},
    "L": {"2nd": "world-cup-group-l-second-place-20260605153257625", "last": "world-cup-group-l-last-place-20260605000843479"},
}


def fetch_group_finish_probs() -> Dict[str, Dict[str, Dict[str, float]]]:
    """
    Fetch Polymarket 2nd-place and last-place markets for all 12 groups via hardcoded slugs.
    Returns {group_letter: {team_key: {"p_2nd": float, "p_4th": float}}}.
    Prices are raw (unnormalised); normalisation happens in build_group_finish_all().
    'Country E' / 'another country' placeholder markets are automatically filtered out.
    """
    result: Dict[str, Dict[str, Dict[str, float]]] = {}
    total_entries = 0

    for g, slugs in _GROUP_FINISH_SLUGS.items():
        grp_data: Dict[str, Dict[str, float]] = {}

        for p_key, regex, slug in [
            ("p_2nd", _FINISH_2ND_RE, slugs["2nd"]),
            ("p_4th", _FINISH_4TH_RE, slugs["last"]),
        ]:
            data = _get("/events", {"slug": slug})
            if not data or not isinstance(data, list) or not data:
                continue
            for m in data[0].get("markets", []):
                q = m.get("question", "")
                match = regex.search(q)
                if not match or match.group(2).upper() != g:
                    continue
                key = _normalize_name(match.group(1))
                if not key or key not in TEAMS:
                    continue  # filters out "Country E" / "another country" placeholders
                if _volume(m) < VOLUME_MIN:
                    continue
                p = _yes_price(m)
                if p is not None:
                    grp_data.setdefault(key, {})[p_key] = p
                    total_entries += 1

        if grp_data:
            result[g] = grp_data

    print(
        f"[INFO] Group 2nd/last markets: {total_entries} price(s) across {len(result)} group(s)",
        file=sys.stderr,
    )
    return result


def _ev_group(probs: Dict[str, float]) -> float:
    return probs["1st"] * 20 + probs["2nd"] * 10 + probs["4th"] * 5


def build_group_finish_all(
    groups: Dict[str, List[str]],
    group_probs: Dict[str, Dict[str, float]],
    group_2nd_4th: Dict[str, Dict[str, Dict[str, float]]],
    group_results: Dict[str, Dict],
    played_pairs: Dict[str, List[Tuple[str, str]]],
    win_probs: Dict[str, float],
    prior_p_win: float,
    match_odds: Optional[Dict[Tuple[str, str], Dict[str, float]]] = None,
    game_scores: Optional[Dict[Tuple[str, str], Tuple[int, int]]] = None,
) -> Dict[str, Dict[str, float]]:
    """
    Build group finish distributions {team_key: {"1st","2nd","3rd","4th"}}.

    Per group:
      1. P(1st): Polymarket group-winner prices (anchored to market).
      2. P(2nd), P(3rd), P(4th): from MC simulation (match-odds when Polymarket game
         markets cover remaining fixtures; Bradley-Terry Poisson fallback otherwise).
         Positions 2-4 are scaled proportionally so they sum with P(1st) to 1.0.
      3. MC uses proper FIFA H2H tiebreaker seeded from known completed game scores.
    """
    finish_all: Dict[str, Dict[str, float]] = {}
    n_mo = n_bt = 0
    comparison_rows: list = []   # (g, key, pm_finish, bt_finish)

    for g, team_keys in groups.items():
        grp_poly = group_probs.get(g, {})

        all_pairs  = list(combinations(team_keys, 2))
        played_set = {frozenset(p) for p in played_pairs.get(g, [])}
        remaining  = [(a, b) for a, b in all_pairs if frozenset((a, b)) not in played_set]

        # Choose MC engine: use Polymarket game odds when available, BT otherwise
        covered = match_odds and any(
            (a, b) in match_odds or (b, a) in match_odds for a, b in remaining
        )
        if covered:
            mc = simulate_group_from_match_odds(
                team_keys, group_results, remaining, match_odds,  # type: ignore[arg-type]
                win_probs, prior_p_win, known_game_scores=game_scores,
            )
            mc_bt = simulate_group_finish(
                team_keys, group_results, remaining, win_probs, prior_p_win,
                known_game_scores=game_scores,
            )
            for k in team_keys:
                comparison_rows.append((g, k, mc[k], mc_bt[k]))
            n_mo += 1
        else:
            mc = simulate_group_finish(
                team_keys, group_results, remaining, win_probs, prior_p_win,
                known_game_scores=game_scores,
            )
            n_bt += 1

        # If Polymarket settled the group (one team at 1.0), zero out p1 for all others
        # so the MC fallback doesn't inflate the column sum past 1.
        grp_settled_winner = next((k for k, v in grp_poly.items() if v >= 1.0), None)

        for k in team_keys:
            if grp_settled_winner is not None:
                p1 = 1.0 if k == grp_settled_winner else 0.0
            else:
                p1 = grp_poly.get(k, mc[k]["1st"])
            mc_rest = mc[k]["2nd"] + mc[k]["3rd"] + mc[k]["4th"]
            if mc_rest > 0:
                scale = max(0.0, 1.0 - p1) / mc_rest
                p2 = mc[k]["2nd"] * scale
                p3 = mc[k]["3rd"] * scale
                p4 = mc[k]["4th"] * scale
            else:
                p2 = p3 = p4 = 0.0
            total = p1 + p2 + p3 + p4
            if total <= 0:
                total = 1.0
            finish_all[k] = {
                "1st": p1 / total,
                "2nd": p2 / total,
                "3rd": p3 / total,
                "4th": p4 / total,
            }

        # Column-normalize within each group: exactly 1 team finishes in each position.
        # Without this, groups where Poly 1st-place coverage < 4 teams have an additive
        # fallback to MC p_grp_1st, making column sums > 1.
        col_sums = {
            pos: sum(finish_all[k][pos] for k in team_keys)
            for pos in ("1st", "2nd", "3rd", "4th")
        }
        for k in team_keys:
            finish_all[k] = {
                pos: (finish_all[k][pos] / col_sums[pos] if col_sums[pos] > 0 else 0.0)
                for pos in ("1st", "2nd", "3rd", "4th")
            }

    print(
        f"[INFO] Group finish: {n_mo} match-odds MC, {n_bt} BT-only",
        file=sys.stderr,
    )

    if comparison_rows:
        W = 82
        print(f"\n{'═'*W}")
        print(f"  Group Finish: Polymarket Game Odds (PM) vs Bradley-Terry (BT)  [H2H tiebreaker]")
        print(f"  ΔEV = PM ev_grp − BT ev_grp  (group-stage points only)")
        print(f"  {'Grp':<5}{'Team':<22}{'PM 1st':>7}{'BT 1st':>7}{'PM 2nd':>7}{'BT 2nd':>7}{'PM 4th':>7}{'BT 4th':>7}{'ΔEV':>7}")
        print(f"  {'─'*78}")
        for g, key, pm, bt in comparison_rows:
            dev = _ev_group(pm) - _ev_group(bt)
            print(
                f"  {g:<5}{key:<22}"
                f"{pm['1st']:>7.0%}{bt['1st']:>7.0%}"
                f"{pm['2nd']:>7.0%}{bt['2nd']:>7.0%}"
                f"{pm['4th']:>7.0%}{bt['4th']:>7.0%}"
                f"{dev:>+7.1f}"
            )
        print(f"{'═'*W}\n")

    return finish_all


# ── Entertainment bonus weights ───────────────────────────────────────────────

def _poisson_sample(lam: float) -> int:
    """Sample from Poisson(lam) via exponential inter-arrivals (exact, no numpy needed)."""
    if lam <= 0:
        return 0
    L = math.exp(-lam)
    k, p = 0, 1.0
    while p > L:
        p *= random.random()
        k += 1
    return k - 1


def _is_group_stage_complete(
    played_pairs: Dict[str, List[Tuple[str, str]]],
    groups: Dict[str, List[str]],
) -> bool:
    for g, teams in groups.items():
        n_expected = len(teams) * (len(teams) - 1) // 2  # C(4,2) = 6
        if len(played_pairs.get(g, [])) < n_expected:
            return False
    return True


def compute_entertainment_win_probs(
    win_probs: Dict[str, float],
    groups: Dict[str, List[str]],
    group_results: Optional[Dict[str, Dict]] = None,
    played_pairs: Optional[Dict[str, List[Tuple[str, str]]]] = None,
    mu_base: float = 2.8,
    kappa: float = 0.2,
    n_sim: int = 20_000,
) -> Dict[str, float]:
    """
    P(win entertainment bonus) per team via Monte Carlo simulation.

    Rule: single team with highest combined goals (GF+GA) in the group stage wins 15 pts.
    Actual goals from played games are locked in; remaining fixtures are simulated as
    Poisson(λ_game = mu_base × (1 + κ × |log(si/sj)|)) total goals per game.

    Groups at different matchday stages are handled correctly: remaining fixtures =
    all C(4,2)=6 pairings minus those already in played_pairs[g].
    """
    results  = group_results or {}
    pp       = played_pairs  or {}

    # Group stage complete — winner is deterministic, no simulation needed.
    if _is_group_stage_complete(pp, groups):
        combined = {k: int(results.get(k, {}).get("gf", 0)) + int(results.get(k, {}).get("ga", 0))
                    for keys in groups.values() for k in keys}
        gf_map   = {k: int(results.get(k, {}).get("gf", 0)) for keys in groups.values() for k in keys}
        gd_map   = {k: int(results.get(k, {}).get("gd", 0)) for keys in groups.values() for k in keys}
        winner   = max(combined, key=lambda k: (combined[k], gf_map[k], gd_map[k], -FIFA_RANKING.get(k, 80)))
        print(f"[INFO] Entertainment bonus settled: {winner} wins (combined={combined[winner]})", file=sys.stderr)
        return {k: (1.0 if k == winner else 0.0) for k in combined}

    # Locked-in combined goals, goals-scored, and GD (for tiebreakers) from actual results
    actual_combined: Dict[str, int] = {}
    actual_gf:       Dict[str, int] = {}
    actual_gd:       Dict[str, int] = {}
    for g, team_keys in groups.items():
        for k in team_keys:
            res = results.get(k, {})
            actual_combined[k] = int(res.get("gf", 0)) + int(res.get("ga", 0))
            actual_gf[k]       = int(res.get("gf", 0))
            actual_gd[k]       = int(res.get("gd", 0))

    # Pre-compute per-group remaining fixtures and team strengths
    group_fixtures: Dict[str, List[Tuple[str, str]]] = {}
    group_strengths: Dict[str, Dict[str, float]] = {}
    for g, team_keys in groups.items():
        played_set = {frozenset(p) for p in pp.get(g, [])}
        remaining  = [
            (i, j) for i, j in combinations(team_keys, 2)
            if frozenset((i, j)) not in played_set
        ]
        group_fixtures[g]  = remaining
        group_strengths[g] = {k: max(win_probs.get(k, 1e-5), 1e-5) ** STRENGTH_EXP for k in team_keys}

    wins: Dict[str, int] = {k: 0 for keys in groups.values() for k in keys}

    for _ in range(n_sim):
        combined = dict(actual_combined)
        gf_sim   = dict(actual_gf)
        gd_sim   = dict(actual_gd)

        for g, fixtures in group_fixtures.items():
            strengths = group_strengths[g]
            for i_key, j_key in fixtures:
                lam   = mu_base * (1.0 + kappa * abs(math.log(strengths[i_key] / strengths[j_key])))
                total = _poisson_sample(lam)
                combined[i_key] += total
                combined[j_key] += total
                # Split for goals-scored and GD tiebreakers
                if total > 0:
                    p_i     = strengths[i_key] / (strengths[i_key] + strengths[j_key])
                    goals_i = sum(1 for _ in range(total) if random.random() < p_i)
                    goals_j = total - goals_i
                    gf_sim[i_key] += goals_i
                    gf_sim[j_key] += goals_j
                    gd_sim[i_key] += goals_i - goals_j
                    gd_sim[j_key] += goals_j - goals_i

        # Tiebreaker: most combined goals → most GF → best GD → best FIFA ranking (lower number = better)
        winner = max(wins, key=lambda k: (combined[k], gf_sim[k], gd_sim[k], -FIFA_RANKING.get(k, 80)))
        wins[winner] += 1

    total_wins = sum(wins.values())
    if total_wins <= 0:
        return {k: 1.0 / N_TEAMS for k in wins}
    return {k: v / total_wins for k, v in wins.items()}


def compute_third_place_advance_probs(
    win_probs: Dict[str, float],
    groups: Dict[str, List[str]],
    group_results: Optional[Dict[str, Dict]] = None,
    played_pairs: Optional[Dict[str, List[Tuple[str, str]]]] = None,
    mu_base: float = 2.8,
    kappa: float = 0.2,
    n_sim: int = 20_000,
) -> Dict[str, float]:
    """
    P(advance to R32 | finish 3rd in group) per team, via cross-group Monte Carlo.

    Simulates all 12 groups simultaneously. In each iteration the remaining
    fixtures in every group are resolved via Poisson total goals + proportional
    binomial split (same model as compute_entertainment_win_probs). The 3rd-place
    finisher per group is determined by pts → GD → GF; all 12 are then ranked
    cross-group and the top 8 advance.

    Returns conditional P(advance | finish 3rd). Multiply by Polymarket p_grp_3rd
    in derive_probs() to get the joint advancement probability.
    """
    results = group_results or {}
    pp      = played_pairs  or {}

    actual: Dict[str, Dict[str, int]] = {}
    for g, team_keys in groups.items():
        for k in team_keys:
            res = results.get(k, {})
            actual[k] = {
                "pts": int(res.get("pts", 0)),
                "gd":  int(res.get("gd",  0)),
                "gf":  int(res.get("gf",  0)),
            }

    group_fixtures: Dict[str, List[Tuple[str, str]]] = {}
    group_strengths: Dict[str, Dict[str, float]] = {}
    for g, team_keys in groups.items():
        played_set = {frozenset(p) for p in pp.get(g, [])}
        group_fixtures[g] = [
            (i, j) for i, j in combinations(team_keys, 2)
            if frozenset((i, j)) not in played_set
        ]
        group_strengths[g] = {
            k: max(win_probs.get(k, 1e-5), 1e-5) ** STRENGTH_EXP
            for k in team_keys
        }

    finish_3rd_count:        Dict[str, int] = {k: 0 for g in groups.values() for k in g}
    advance_given_3rd_count: Dict[str, int] = {k: 0 for g in groups.values() for k in g}

    for _ in range(n_sim):
        all_thirds: List[Tuple[int, int, int, str]] = []

        for g, team_keys in groups.items():
            strengths = group_strengths[g]
            sim_pts = {k: actual[k]["pts"] for k in team_keys}
            sim_gd  = {k: actual[k]["gd"]  for k in team_keys}
            sim_gf  = {k: actual[k]["gf"]  for k in team_keys}

            for i, j in group_fixtures[g]:
                si, sj  = strengths[i], strengths[j]
                lam     = mu_base * (1.0 + kappa * abs(math.log(si / sj)))
                total   = _poisson_sample(lam)
                if total > 0:
                    p_i     = si / (si + sj)
                    goals_i = sum(1 for _ in range(total) if random.random() < p_i)
                else:
                    goals_i = 0
                goals_j = total - goals_i

                if goals_i > goals_j:
                    sim_pts[i] += 3
                elif goals_i == goals_j:
                    sim_pts[i] += 1
                    sim_pts[j] += 1
                else:
                    sim_pts[j] += 3
                sim_gd[i] += goals_i - goals_j
                sim_gd[j] += goals_j - goals_i
                sim_gf[i] += goals_i
                sim_gf[j] += goals_j

            ranking = sorted(
                team_keys,
                key=lambda k: (sim_pts[k], sim_gd[k], sim_gf[k]),
                reverse=True,
            )
            third_key = ranking[2]
            finish_3rd_count[third_key] += 1
            all_thirds.append((sim_pts[third_key], sim_gd[third_key], sim_gf[third_key], third_key))

        all_thirds.sort(key=lambda x: (x[0], x[1], x[2]), reverse=True)
        for _, _, _, team_key in all_thirds[:8]:
            advance_given_3rd_count[team_key] += 1

    result: Dict[str, float] = {}
    for k in finish_3rd_count:
        n = finish_3rd_count[k]
        result[k] = (advance_given_3rd_count[k] / n) if n > 0 else (8.0 / 12.0)
    return result


# ── Geometric conditional advance model ──────────────────────────────────────

def _geo_cond(p_win: float, p_at_stage: float, n_remaining: int) -> float:
    """
    P(advance to next stage | at this stage) under constant match-win-prob model.
    q = (p_win / p_at_stage)^(1/n_remaining), where n_remaining = matches left to win WC.
    Stronger teams get q > 0.5; weaker teams get q < 0.5.
    """
    if p_at_stage <= 0 or p_win <= 0 or p_win >= p_at_stage:
        return 0.5
    return (p_win / p_at_stage) ** (1.0 / n_remaining)


# ── Harville model ────────────────────────────────────────────────────────────

def harville_group_finish(
    group_probs: Dict[str, float],
) -> Dict[str, Dict[str, float]]:
    """
    Harville (1973) model: compute P(finish 1st/2nd/3rd/4th) for each team.
    Enumerates all 4! = 24 orderings; each ordering's probability is the product
    of Harville conditional terms.  Correct: P(team i is 1st) = s_i / sum(s).
    """
    teams = list(group_probs.keys())
    S = sum(group_probs.values())
    pos = ["1st", "2nd", "3rd", "4th"]
    finish: Dict[str, Dict[str, float]] = {t: {k: 0.0 for k in pos} for t in teams}
    if S <= 0 or len(teams) < 2:
        return finish

    for perm in permutations(teams):
        remaining = S
        p_perm = 1.0
        for team in perm:
            s = group_probs[team]
            if remaining <= 0:
                break
            p_perm *= s / remaining
            remaining -= s
        for i, team in enumerate(perm):
            if i < 4:
                finish[team][pos[i]] += p_perm

    return finish


# ── Monte Carlo group simulation ──────────────────────────────────────────────

STRENGTH_EXP    = 0.3    # compress tournament win_probs to realistic head-to-head ratios
MEAN_GOALS_GAME = 2.8    # historical WC group-stage goals per game
N_MC_SIMS       = 10_000 # simulations per group


def _poisson_sample(lam: float) -> int:
    """Knuth's algorithm for Poisson sampling."""
    if lam <= 0.0:
        return 0
    L = math.exp(-min(lam, 30.0))
    k, p = 0, 1.0
    while p > L:
        p *= random.random()
        k += 1
    return k - 1


def _h2h_stats(
    team: str,
    tied: List[str],
    game_results: Dict[Tuple[str, str], Tuple[int, int]],
) -> Tuple[int, int, int]:
    """H2H points, GD, GF for team against the given subset of tied teams."""
    pts = gd = gf = 0
    for opp in tied:
        if opp == team:
            continue
        r = game_results.get((team, opp))
        if r:
            ga, gb = r
        else:
            r = game_results.get((opp, team))
            if not r:
                continue
            gb, ga = r
        pts += 3 if ga > gb else (1 if ga == gb else 0)
        gd += ga - gb
        gf += ga
    return pts, gd, gf


def _sort_group_h2h(
    group_keys: List[str],
    sim_pts: Dict[str, int],
    sim_gd: Dict[str, int],
    sim_gf: Dict[str, int],
    game_results: Dict[Tuple[str, str], Tuple[int, int]],
) -> List[str]:
    """Sort group teams using FIFA WC 2026 tiebreaker: pts → H2H pts/GD/GF → overall GD/GF → lots."""
    rand_seed = {k: random.random() for k in group_keys}

    def cmp(a: str, b: str) -> int:
        if sim_pts[a] != sim_pts[b]:
            return sim_pts[b] - sim_pts[a]
        tied = [k for k in group_keys if sim_pts[k] == sim_pts[a]]
        ha = _h2h_stats(a, tied, game_results)
        hb = _h2h_stats(b, tied, game_results)
        for i in range(3):
            if ha[i] != hb[i]:
                return hb[i] - ha[i]
        if sim_gd[a] != sim_gd[b]:
            return sim_gd[b] - sim_gd[a]
        if sim_gf[a] != sim_gf[b]:
            return sim_gf[b] - sim_gf[a]
        r = rand_seed[a] - rand_seed[b]
        return -1 if r < 0 else (1 if r > 0 else 0)

    return sorted(group_keys, key=functools.cmp_to_key(cmp))


def simulate_group_finish(
    group_keys: List[str],
    current_standings: Dict[str, Dict],
    remaining_fixtures: List[Tuple[str, str]],
    win_probs: Dict[str, float],
    prior_p_win: float,
    n_sims: int = N_MC_SIMS,
    known_game_scores: Optional[Dict[Tuple[str, str], Tuple[int, int]]] = None,
) -> Dict[str, Dict[str, float]]:
    """
    Monte Carlo group finish simulation starting from current ESPN standings.

    For each remaining fixture, draws goals from independent Poisson distributions
    parameterised by Bradley-Terry strengths derived from tournament win_probs
    (compressed by STRENGTH_EXP to give realistic ~65-75% win rates for strong teams).

    Standings resolved using FIFA WC 2026 tiebreaker: pts → H2H pts/GD/GF → overall GD/GF → lots.
    Returns {team_key: {"1st", "2nd", "3rd", "4th"}} with frequencies as probabilities.
    """
    pos_labels = ["1st", "2nd", "3rd", "4th"]
    counts = {k: {pos: 0 for pos in pos_labels} for k in group_keys}
    base_scores: Dict[Tuple[str, str], Tuple[int, int]] = dict(known_game_scores or {})

    strengths: Dict[str, float] = {
        k: max(win_probs.get(k, prior_p_win), 1e-5) ** STRENGTH_EXP
        for k in group_keys
    }

    for _ in range(n_sims):
        sim_pts = {k: current_standings.get(k, {}).get("pts", 0) for k in group_keys}
        sim_gd  = {k: current_standings.get(k, {}).get("gd",  0) for k in group_keys}
        sim_gf  = {k: current_standings.get(k, {}).get("gf",  0) for k in group_keys}
        game_results: Dict[Tuple[str, str], Tuple[int, int]] = dict(base_scores)

        for a, b in remaining_fixtures:
            sa, sb = strengths[a], strengths[b]
            lam_a = MEAN_GOALS_GAME * sa / (sa + sb)
            lam_b = MEAN_GOALS_GAME * sb / (sa + sb)
            ga = _poisson_sample(lam_a)
            gb = _poisson_sample(lam_b)

            if ga > gb:
                sim_pts[a] += 3
            elif gb > ga:
                sim_pts[b] += 3
            else:
                sim_pts[a] += 1
                sim_pts[b] += 1

            sim_gd[a] += ga - gb
            sim_gd[b] += gb - ga
            sim_gf[a] += ga
            sim_gf[b] += gb
            game_results[(a, b)] = (ga, gb)

        order = _sort_group_h2h(group_keys, sim_pts, sim_gd, sim_gf, game_results)
        for i, k in enumerate(order):
            counts[k][pos_labels[i]] += 1

    return {
        k: {pos: counts[k][pos] / n_sims for pos in pos_labels}
        for k in group_keys
    }


def simulate_group_from_match_odds(
    group_keys: List[str],
    current_standings: Dict[str, Dict],
    remaining_fixtures: List[Tuple[str, str]],
    match_odds: Dict[Tuple[str, str], Dict[str, float]],
    win_probs: Dict[str, float],
    prior_p_win: float,
    n_sims: int = N_MC_SIMS,
    known_game_scores: Optional[Dict[Tuple[str, str], Tuple[int, int]]] = None,
) -> Dict[str, Dict[str, float]]:
    """
    Monte Carlo group simulation using Polymarket per-game W/D/L odds.

    For covered fixtures, samples the result directly from market probabilities
    (more accurate than the Bradley-Terry proxy). Goals are approximated from
    Poisson for tiebreaker tracking only; the W/D/L outcome is market-driven.
    Uncovered fixtures fall back to the existing Poisson/Bradley-Terry model.
    Standings resolved using FIFA WC 2026 H2H tiebreaker.
    Returns same shape as simulate_group_finish().
    """
    pos_labels = ["1st", "2nd", "3rd", "4th"]
    counts = {k: {pos: 0 for pos in pos_labels} for k in group_keys}
    HALF = MEAN_GOALS_GAME / 2.0
    base_scores: Dict[Tuple[str, str], Tuple[int, int]] = dict(known_game_scores or {})

    strengths: Dict[str, float] = {
        k: max(win_probs.get(k, prior_p_win), 1e-5) ** STRENGTH_EXP
        for k in group_keys
    }

    for _ in range(n_sims):
        sim_pts = {k: current_standings.get(k, {}).get("pts", 0) for k in group_keys}
        sim_gd  = {k: current_standings.get(k, {}).get("gd",  0) for k in group_keys}
        sim_gf  = {k: current_standings.get(k, {}).get("gf",  0) for k in group_keys}
        game_results: Dict[Tuple[str, str], Tuple[int, int]] = dict(base_scores)

        for a, b in remaining_fixtures:
            odds = match_odds.get((a, b)) or match_odds.get((b, a))
            if odds is not None:
                if (a, b) in match_odds:
                    p_a_win, p_draw, p_b_win = odds["p_win"], odds["p_draw"], odds["p_lose"]
                else:
                    p_a_win, p_draw, p_b_win = odds["p_lose"], odds["p_draw"], odds["p_win"]

                r = random.random()
                if r < p_a_win:
                    sim_pts[a] += 3
                    ga = max(1, _poisson_sample(HALF * 1.3))
                    gb = _poisson_sample(HALF * 0.7)
                    if gb >= ga:
                        gb = ga - 1
                elif r < p_a_win + p_draw:
                    sim_pts[a] += 1
                    sim_pts[b] += 1
                    ga = _poisson_sample(HALF)
                    gb = ga
                else:
                    sim_pts[b] += 3
                    gb = max(1, _poisson_sample(HALF * 1.3))
                    ga = _poisson_sample(HALF * 0.7)
                    if ga >= gb:
                        ga = gb - 1
            else:
                # Fallback: Bradley-Terry Poisson
                sa, sb = strengths[a], strengths[b]
                lam_a = MEAN_GOALS_GAME * sa / (sa + sb)
                lam_b = MEAN_GOALS_GAME * sb / (sa + sb)
                ga = _poisson_sample(lam_a)
                gb = _poisson_sample(lam_b)

                if ga > gb:
                    sim_pts[a] += 3
                elif gb > ga:
                    sim_pts[b] += 3
                else:
                    sim_pts[a] += 1
                    sim_pts[b] += 1

            sim_gd[a] += ga - gb
            sim_gd[b] += gb - ga
            sim_gf[a] += ga
            sim_gf[b] += gb
            game_results[(a, b)] = (ga, gb)

        order = _sort_group_h2h(group_keys, sim_pts, sim_gd, sim_gf, game_results)
        for i, k in enumerate(order):
            counts[k][pos_labels[i]] += 1

    return {
        k: {pos: counts[k][pos] / n_sims for pos in pos_labels}
        for k in group_keys
    }


# ── Probability derivation ────────────────────────────────────────────────────

def derive_probs(
    p_win: float,
    group_finish: Optional[Dict[str, float]] = None,
    p_advance_given_3rd: Optional[float] = None,
    p_r16_direct: Optional[float] = None,
    p_qf_direct: Optional[float] = None,
    p_sf_direct: Optional[float] = None,
    p_final_direct: Optional[float] = None,
) -> Dict[str, float]:
    """
    Assemble the full probability distribution from available Polymarket data.

    group_finish: {"1st","2nd","3rd","4th"} from Harville — gives p_advance directly
    p_r16_direct: P(position <= 16), i.e. team wins their R32 game (from Polymarket)
    p_qf_direct:  P(position <= 8), i.e. team wins their R16 game (from Polymarket)
    p_sf_direct:  P(position <= 4), i.e. team wins their QF game (from Polymarket)

    Falls back to scaling from p_win when market data isn't available.
    """
    # ── Group finish distribution ─────────────────────────────────────────────
    if group_finish is not None:
        p_grp_1st = group_finish.get("1st", 0.0)
        p_grp_2nd = group_finish.get("2nd", 0.0)
        p_grp_3rd = group_finish.get("3rd", 0.0)
        p_grp_4th = group_finish.get("4th", 0.0)
        # MC-derived conditional P(advance | finish 3rd); fall back to flat 8/12
        p3rd_cond = p_advance_given_3rd if p_advance_given_3rd is not None else (8.0 / 12.0)
        p_advance = min(p_grp_1st + p_grp_2nd + p_grp_3rd * p3rd_cond, 1.0)
    else:
        p_advance = min(p_win * 32.0, 0.97)
        p_grp_1st = min(p_win * 6.0, p_advance, 0.88)
        p_adv_rest = max(0.0, p_advance - p_grp_1st)
        p_grp_2nd  = p_adv_rest * (12.0 / 20.0)
        p_grp_3rd  = p_adv_rest * (8.0 / 20.0)
        p_grp_4th  = max(0.0, 1.0 - p_advance) * (12.0 / 16.0)

    # ── Knockout stage ────────────────────────────────────────────────────────
    # Direct market prices used where available; geometric fallback otherwise.
    # n_remaining = matches still needed to win WC from each stage.
    p_r16 = p_r16_direct if p_r16_direct is not None else p_advance * _geo_cond(p_win, p_advance, 5)
    p_qf  = p_qf_direct  if p_qf_direct  is not None else p_r16    * _geo_cond(p_win, p_r16,    4)
    p_sf  = p_sf_direct  if p_sf_direct  is not None else p_qf     * _geo_cond(p_win, p_qf,     3)
    p_final = p_final_direct if p_final_direct is not None else p_sf * _geo_cond(p_win, p_sf, 2)

    # If a market price exceeds the stage above it (inconsistent markets / thin liquidity),
    # use the geometric fallback so exit probabilities remain meaningful.
    if p_sf_direct is not None and p_sf > p_qf:
        p_sf = p_qf * _geo_cond(p_win, p_qf, 3)
    if p_final_direct is not None and p_final > p_sf:
        p_final = p_sf * _geo_cond(p_win, p_sf, 2)

    # ── Monotonicity (top-down) ───────────────────────────────────────────────
    # If R16 market exceeds Harville advance, trust the more liquid R16 market
    p_advance = max(p_advance, p_r16)
    p_r16   = min(p_r16,   p_advance)
    p_qf    = min(p_qf,    p_r16)
    p_sf    = min(p_sf,    p_qf)
    p_final = min(p_final, p_sf)
    p_win   = min(p_win,   p_final)

    # ── Stage-exit probabilities ──────────────────────────────────────────────
    p_2nd     = max(0.0, p_final - p_win)
    p_sf_exit = max(0.0, p_sf - p_final)
    # Quality-adjusted 3rd/4th: stronger teams (higher p_win relative to p_sf) are
    # more likely to win the 3rd-place playoff. Harville-style ratio:
    #   q_T = p_win / p_sf  (conditional P(win WC | reach SF))
    #   q_avg = 1/4         (sum(p_win)=1, sum(p_sf)=4 → average conditional = 0.25)
    #   P(win 3rd-place match) ≈ q_T / (q_T + q_avg)
    q_T    = (p_win / p_sf) if p_sf > 0 else 0.25
    q_avg  = 0.25
    p_win_3rd = q_T / (q_T + q_avg)
    p_3rd  = p_sf_exit * p_win_3rd
    p_4th  = p_sf_exit * (1.0 - p_win_3rd)
    p_qf_exit      = max(0.0, p_qf  - p_sf)
    p_r16_exit     = max(0.0, p_r16 - p_qf)
    p_r32_exit     = max(0.0, p_advance - p_r16)
    p_group_exit   = max(0.0, 1.0 - p_advance)

    return {
        "p_win":           p_win,
        "p_final":         p_final,
        "p_sf":            p_sf,
        "p_qf":            p_qf,
        "p_r16":           p_r16,
        "p_advance":       p_advance,
        "p_win_group":     p_grp_1st,
        "p_2nd":           p_2nd,
        "p_3rd":           p_3rd,
        "p_4th":           p_4th,
        "p_qf_exit":       p_qf_exit,
        "p_r16_exit":      p_r16_exit,
        "p_r32_exit":      p_r32_exit,
        "p_group_exit":    p_group_exit,
        "p_wooden_spoon":  0.0,   # placeholder; overridden by compute_wooden_spoon_probs()
        "p_grp_1st":       p_grp_1st,
        "p_grp_2nd":       p_grp_2nd,
        "p_grp_3rd":       p_grp_3rd,
        "p_grp_4th":       p_grp_4th,
    }


# ── Wooden spoon probabilities (global, two-pass) ────────────────────────────

def compute_wooden_spoon_probs(
    team_keys: List[str],
    all_probs: Dict[str, Dict[str, float]],
    group_results: Dict[str, Dict],
    stats_cache: Optional[Dict[str, Any]] = None,
) -> Dict[str, float]:
    """
    P(wooden spoon) for each team, updating as group stage results accumulate.

    Wooden spoon = worst 4th-place finisher (pool = 12 fourth-place teams only).
    Official tiebreaker order: fewest group-stage points → fewest GF → worst GD → worst FIFA ranking.

    weight_i = p_grp_4th_i × badness_i

    badness from actual record (if games played):
      exp(-pts × 0.5) × exp(-gf × 0.1) × exp(-gd × 0.05) × exp(fifa_rank × 0.008)
    Points carry the largest weight (primary tiebreaker); FIFA rank is the final tiebreaker.

    Pre-tournament or no data: badness = 1 (uniform, reduces to p_grp_4th/12 after norm).
    Normalised so weights sum to 1 (exactly one team gets the wooden spoon).
    """
    # Group stage complete — wooden spoon is deterministic.
    fourth_place = [k for k in team_keys if all_probs.get(k, {}).get("p_grp_4th", 0.0) > 0.99]
    if len(fourth_place) == 12:
        def _ws_badness(k: str) -> tuple:
            r = group_results.get(k, {})
            return (r.get("pts", 0), r.get("gf", 0), r.get("gd", 0), -FIFA_RANKING.get(k, 80))
        wooden_spoon = min(fourth_place, key=_ws_badness)
        print(f"[INFO] Wooden spoon settled: {wooden_spoon} {_ws_badness(wooden_spoon)}", file=sys.stderr)
        return {k: (1.0 if k == wooden_spoon else 0.0) for k in team_keys}

    raw: Dict[str, float] = {}

    for k in team_keys:
        p_4th = all_probs.get(k, {}).get("p_grp_4th", 0.0)
        if p_4th < 1e-6:
            raw[k] = 0.0
            continue

        res    = group_results.get(k, {})
        played = res.get("played", 0)

        if played == 0:
            raw[k] = p_4th          # no data yet — neutral badness
        else:
            pts = res.get("pts", None)
            gd  = res.get("gd", None)
            gf  = res.get("gf", None)

            if gf is None:
                raw[k] = p_4th
            else:
                # Fewer pts (primary) / fewer GF / more negative GD / higher FIFA rank → more likely wooden spoon
                pts_factor  = math.exp(-(pts if pts is not None else 0) * 0.5)
                gf_factor   = math.exp(-(gf if gf is not None else 2) * 0.1)
                gd_factor   = math.exp(-(gd if gd is not None else 0) * 0.05)
                fifa_factor = math.exp(FIFA_RANKING.get(k, 80) * 0.008)
                raw[k] = p_4th * pts_factor * gf_factor * gd_factor * fifa_factor

    total = sum(raw.values())
    if total <= 0:
        return {k: all_probs.get(k, {}).get("p_grp_4th", 0.0) / 12.0 for k in team_keys}
    return {k: v / total for k, v in raw.items()}


# ── EV calculation ────────────────────────────────────────────────────────────

def calculate_ev(p: Dict[str, float], ent_weight: float) -> Dict[str, float]:
    ev_group = (
        p["p_grp_1st"] * GROUP_PTS[1]
        + p["p_grp_2nd"] * GROUP_PTS[2]
        + p["p_grp_4th"] * GROUP_PTS[4]
        # All 4th-place teams earn GROUP_PTS[4]; wooden spoon gets an additional TOURNAMENT_PTS bonus
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

    ev_entertainment = ent_weight * ENTERTAINMENT_BONUS
    ev_total = ev_group + ev_tournament + ev_entertainment

    return {
        "ev_total":         round(ev_total, 2),
        "ev_group":         round(ev_group, 2),
        "ev_tournament":    round(ev_tournament, 2),
        "ev_entertainment": round(ev_entertainment, 2),
    }


# ── Group outcome detection ───────────────────────────────────────────────────

def _detect_group_outcomes(
    groups: Dict[str, List[str]],
    group_results: Dict[str, Dict],
    group_finish_all: Dict[str, Dict[str, float]],
) -> Dict[str, str]:
    """
    Detect eliminated/clinched teams from live group results.
    Modifies group_finish_all in-place for fully completed groups (replaces Harville
    estimates with exact finish positions).

    Returns {team_key: 'eliminated' | 'clinched' | 'alive'}.
      eliminated: cannot mathematically reach the R32.
      clinched:   guaranteed to advance (finished 1st or 2nd with all games played).
      alive:      undetermined — includes 3rd-place wildcards and in-progress groups.
    """
    outcomes: Dict[str, str] = {}
    n_eliminated = n_clinched = 0

    for g, team_keys in groups.items():
        standings = []
        for k in team_keys:
            res    = group_results.get(k, {})
            played = res.get("played", 0)
            pts    = res.get("pts",    0)
            gd     = res.get("gd",     0)
            gf     = res.get("gf",     0)
            standings.append({
                "key": k, "played": played, "pts": pts,
                "gd": gd, "gf": gf,
                "max_pts": pts + (3 - played) * 3,
            })
        standings.sort(key=lambda x: (-x["pts"], -x["gd"], -x["gf"]))

        all_played = all(s["played"] >= 3 for s in standings)
        any_played = any(s["played"] > 0  for s in standings)

        if all_played and any_played:
            # Group stage complete: override Harville estimates with exact finish positions
            pos_labels = ["1st", "2nd", "3rd", "4th"]
            for i, s in enumerate(standings):
                k = s["key"]
                group_finish_all[k] = {lbl: (1.0 if j == i else 0.0)
                                        for j, lbl in enumerate(pos_labels)}
                if i < 2:
                    outcomes[k] = "clinched"
                    n_clinched += 1
                elif i == 2:
                    outcomes[k] = "alive"       # wildcard spot: still uncertain
                else:
                    outcomes[k] = "eliminated"
                    n_eliminated += 1
        elif any_played:
            # In progress: conservative elimination — only when max_pts < current 3rd pts.
            # Current 3rd-place pts cannot decrease even with remaining games, so this
            # bound is safe without requiring their remaining games to be known.
            third_pts = standings[2]["pts"] if len(standings) >= 3 else 0
            for s in standings:
                if s["max_pts"] < third_pts:
                    outcomes[s["key"]] = "eliminated"
                    n_eliminated += 1
                else:
                    outcomes[s["key"]] = "alive"
        else:
            for s in standings:
                outcomes[s["key"]] = "alive"

    if n_eliminated or n_clinched:
        print(
            f"[INFO] Live group outcomes: {n_clinched} clinched, {n_eliminated} eliminated",
            file=sys.stderr,
        )
    return outcomes


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

    win_probs      = fetch_win_probs()
    group_probs    = fetch_group_winner_probs()
    r16_probs      = fetch_r16_probs()
    qf_probs       = fetch_qf_probs()
    sf_probs       = fetch_sf_probs()
    final_probs    = fetch_final_probs()
    group_2nd_4th  = fetch_group_finish_probs()

    print("[INFO] Fetching live group standings + tournament card stats from ESPN...", file=sys.stderr)
    group_results, played_pairs, upcoming_fixtures, game_scores = fetch_group_standings()
    fetch_tournament_stats()

    print("[INFO] Fetching Polymarket per-game W/D/L odds...", file=sys.stderr)
    match_odds = fetch_match_odds(upcoming_fixtures) if upcoming_fixtures else {}

    # Normalise stage probs so they sum to the correct team counts.
    # Polymarket binary markets are independent and accumulate vig, so their raw
    # sum exceeds the true count (e.g. SF prices sum to ~4.6 instead of 4).
    # We expand each stage dict to all 48 teams first (absent / eliminated teams → 0),
    # then normalise the full set to exactly the target. Teams with p=0 stay at 0.
    def _normalise(probs: Dict[str, float], target: float) -> Dict[str, float]:
        """Normalise probs to sum to exactly target. Expects all relevant teams in dict."""
        if not probs:
            return probs
        total = sum(probs.values())
        if total <= 0:
            return probs
        return {k: v * target / total for k, v in probs.items()}

    all_keys = set(TEAMS.keys())
    sf_probs    = {k: sf_probs.get(k,    0.0) for k in all_keys}
    qf_probs    = {k: qf_probs.get(k,    0.0) for k in all_keys}
    r16_probs   = {k: r16_probs.get(k,   0.0) for k in all_keys}
    final_probs = {k: final_probs.get(k, 0.0) for k in all_keys}

    win_probs   = _normalise(win_probs,   1)   # exactly 1 WC winner
    sf_probs    = _normalise(sf_probs,    4)   # exactly 4 SF teams
    qf_probs    = _normalise(qf_probs,    8)   # exactly 8 QF teams
    r16_probs   = _normalise(r16_probs,  16)   # exactly 16 R16 teams
    final_probs = _normalise(final_probs,  2)  # exactly 2 finalists
    # group_probs already normalised per-group in fetch_group_winner_probs()

    sums = {
        "win":   round(sum(win_probs.values()),   3),
        "final": round(sum(final_probs.values()), 3),
        "sf":    round(sum(sf_probs.values()),    3),
        "qf":    round(sum(qf_probs.values()),    3),
        "r16":   round(sum(r16_probs.values()),   3),
    }
    nonzero = {
        "win":   sum(1 for v in win_probs.values()   if v > 0),
        "final": sum(1 for v in final_probs.values() if v > 0),
        "sf":    sum(1 for v in sf_probs.values()    if v > 0),
        "qf":    sum(1 for v in qf_probs.values()    if v > 0),
        "r16":   sum(1 for v in r16_probs.values()   if v > 0),
    }
    print(f"[INFO] Stage prob sums after normalisation: {sums}  (teams >0: {nonzero})", file=sys.stderr)

    # Fallback prior for teams not quoted in winner markets
    known_total = sum(win_probs.values())
    n_unknown   = N_TEAMS - len(win_probs)
    remaining   = max(0.0, 1.0 - known_total)
    prior_p_win = (remaining / n_unknown) if n_unknown > 0 else 0.001

    # Build group finish distributions anchored to Polymarket group-specific prices.
    # P(1st) = Polymarket group-winner price; P(2nd)/P(4th) from thin Polymarket markets
    # where available (normalised, missing teams filled with results-adjusted Harville prior).
    # Full Harville fallback used when no 2nd/4th market exists for a group.
    group_finish_all = build_group_finish_all(
        GROUPS, group_probs, group_2nd_4th, group_results, played_pairs, win_probs, prior_p_win,
        match_odds=match_odds,
        game_scores=game_scores,
    )

    # Override for completed groups: use exact ESPN standings instead of model estimates.
    group_outcomes = _detect_group_outcomes(GROUPS, group_results, group_finish_all)

    # Entertainment weights: blend actual goals (from ESPN) with mismatch model
    ent_weights = compute_entertainment_win_probs(win_probs, GROUPS, group_results, played_pairs)

    # Cross-group 3rd-place advancement probabilities (MC simulation)
    p3rd_advance_mc = compute_third_place_advance_probs(
        win_probs, GROUPS, group_results, played_pairs
    )
    # Normalize so that Σ(p_grp_3rd × p_adv_given_3rd) = 8 exactly.
    # Polymarket group-finish probs and MC-implied third-place frequencies can
    # diverge slightly, causing a small systematic deficit in total p_advance.
    _raw_3rd_sum = sum(
        group_finish_all.get(k, {}).get("3rd", 0.0) * p3rd_advance_mc.get(k, 8.0 / 12.0)
        for k in TEAMS
    )
    if _raw_3rd_sum > 0:
        _norm_factor = 8.0 / _raw_3rd_sum
        p3rd_advance_mc = {k: min(v * _norm_factor, 1.0) for k, v in p3rd_advance_mc.items()}

    # ── Pass 1: compute per-team probability distributions ────────────────────
    all_team_probs: Dict[str, Dict[str, float]] = {}
    qualities:      Dict[str, str]              = {}

    for key, (name, flag, group) in TEAMS.items():
        has_winner = key in win_probs
        has_group  = key in group_finish_all
        has_r16    = key in r16_probs
        has_qf     = key in qf_probs
        has_sf     = key in sf_probs

        stage_count = sum([has_r16, has_qf, has_sf, has_group])
        if has_winner and stage_count >= 4:
            qualities[key] = "full"
        elif has_winner and stage_count >= 1:
            qualities[key] = "partial"
        else:
            qualities[key] = "winner_only"

        p_win = win_probs.get(key, prior_p_win)
        all_team_probs[key] = derive_probs(
            p_win,
            group_finish=group_finish_all.get(key),
            p_advance_given_3rd=p3rd_advance_mc.get(key),
            p_r16_direct=r16_probs.get(key),
            p_qf_direct=qf_probs.get(key),
            p_sf_direct=sf_probs.get(key),
            p_final_direct=final_probs.get(key),
        )

    # Hard-set probabilities for teams with determined group outcomes.
    # Eliminated teams: p_advance=0 so EV is not inflated by phantom advance odds.
    # Clinched teams: p_advance=1 so no probability leaks to p_group_exit.
    for key, outcome in group_outcomes.items():
        if key not in all_team_probs:
            continue
        p = all_team_probs[key]
        if outcome == "eliminated":
            all_team_probs[key].update({
                "p_advance": 0.0, "p_win": 0.0, "p_final": 0.0,
                "p_sf": 0.0, "p_qf": 0.0, "p_r16": 0.0,
                "p_2nd": 0.0, "p_3rd": 0.0, "p_4th": 0.0,
                "p_qf_exit": 0.0, "p_r16_exit": 0.0, "p_r32_exit": 0.0,
                "p_group_exit": 1.0,
            })
        elif outcome == "clinched":
            delta = max(0.0, 1.0 - p["p_advance"])
            all_team_probs[key].update({
                "p_advance":    1.0,
                "p_group_exit": 0.0,
                "p_r32_exit":   p["p_r32_exit"] + delta,
            })

    # Renorm tournament 3rd/4th so each sums to exactly 1 across 48 teams.
    # The quality-adjusted split (q_T ratio) doesn't guarantee column sums = 1.
    _p3_sum = sum(p["p_3rd"] for p in all_team_probs.values())
    _p4_sum = sum(p["p_4th"] for p in all_team_probs.values())
    if _p3_sum > 0:
        for p in all_team_probs.values():
            p["p_3rd"] /= _p3_sum
    if _p4_sum > 0:
        for p in all_team_probs.values():
            p["p_4th"] /= _p4_sum

    # ── Pass 2: compute global wooden spoon distribution, then EVs ───────────
    _stats_cache: Dict[str, Any] = {}
    if _STATS_FILE.exists():
        try:
            with open(_STATS_FILE) as _sf:
                _stats_cache = json.load(_sf).get("per_match", {})
        except Exception:
            pass
    ws_probs = compute_wooden_spoon_probs(list(TEAMS.keys()), all_team_probs, group_results, stats_cache=_stats_cache)

    results = []
    for key, (name, flag, group) in TEAMS.items():
        probs = all_team_probs[key]
        probs["p_wooden_spoon"] = ws_probs.get(key, 0.0)  # override placeholder
        ev = calculate_ev(probs, ent_weights.get(key, 1.0 / N_TEAMS))

        results.append({
            "name":         name,
            "key":          key,
            "flag":         flag,
            "group":        group,
            "data_quality": qualities[key],
            **ev,
            "probs": {
                "p_win":       round(probs["p_win"], 4),
                "p_final":     round(probs["p_final"], 4),
                "p_sf":        round(probs["p_sf"], 4),
                "p_qf":        round(probs["p_qf"], 4),
                "p_r16":       round(probs["p_r16"], 4),
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
                "p_grp_3rd":    round(probs["p_grp_3rd"], 4),
                "p_grp_4th":      round(probs["p_grp_4th"], 4),
                "p_wooden_spoon": round(probs["p_wooden_spoon"], 5),
            },
        })

    results.sort(key=lambda r: r["ev_total"], reverse=True)

    if len(results) < 10:
        print("[ERROR] Too few teams resolved — aborting", file=sys.stderr)
        sys.exit(1)

    full_ct    = sum(1 for r in results if r["data_quality"] == "full")
    partial_ct = sum(1 for r in results if r["data_quality"] == "partial")
    print(
        f"[INFO] Quality: {full_ct} full, {partial_ct} partial, "
        f"{len(results) - full_ct - partial_ct} winner_only",
        file=sys.stderr,
    )

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
