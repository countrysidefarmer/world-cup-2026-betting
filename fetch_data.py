#!/usr/bin/env python3
"""
Fetch WC 2026 probabilities from Polymarket and calculate expected values.
Uses 5 market types: winner, group winner (x12), R16, QF, SF.
Writes data/teams.json consumed by index.html.
"""

import json
import math
import re
import sys
import time
from datetime import datetime, timezone
from difflib import get_close_matches
from itertools import permutations
from pathlib import Path
from typing import Dict, List, Optional, Any

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

# ── All 48 WC 2026 teams ──────────────────────────────────────────────────────

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


def fetch_group_winner_probs() -> Dict[str, Dict[str, float]]:
    """
    P(win group) for each team via slug-based lookup.
    Returns {group_letter: {team_key: normalized_prob}}.
    Prices are normalized within each group to sum to 1.
    """
    result: Dict[str, Dict[str, float]] = {}
    for g in "ABCDEFGHIJKL":
        data = _get("/events", {"slug": f"world-cup-group-{g.lower()}-winner"})
        if not data or not isinstance(data, list) or not data:
            continue
        raw: Dict[str, float] = {}
        for m in data[0].get("markets", []):
            if _volume(m) < VOLUME_MIN:
                continue
            match = _WIN_GRP_RE.match(m.get("question", ""))
            if not match:
                continue
            key = _normalize_name(match.group(1))
            if key and key in TEAMS:
                p = _yes_price(m)
                if p is not None:
                    raw[key] = p
        if raw:
            total = sum(raw.values())
            result[g] = {k: v / total for k, v in raw.items()}
    n = sum(len(v) for v in result.values())
    print(f"[INFO] Group winner markets: {n} teams across {len(result)} groups", file=sys.stderr)
    return result


def _fetch_stage(slug: str, label: str) -> Dict[str, float]:
    """Generic fetch for binary 'reach stage X' markets."""
    probs: Dict[str, float] = {}
    data = _get("/events", {"slug": slug})
    if not data or not isinstance(data, list) or not data:
        print(f"[WARN] No data for {label} ({slug})", file=sys.stderr)
        return probs
    for m in data[0].get("markets", []):
        if _volume(m) < VOLUME_MIN:
            continue
        match = _REACH_RE.match(m.get("question", ""))
        if not match:
            continue
        key = _normalize_name(match.group(1))
        if key and key in TEAMS:
            p = _yes_price(m)
            if p is not None:
                probs[key] = p
    print(f"[INFO] {label} markets: {len(probs)} teams", file=sys.stderr)
    return probs


def fetch_r16_probs() -> Dict[str, float]:
    return _fetch_stage("world-cup-nation-to-reach-round-of-16", "R16")


def fetch_qf_probs() -> Dict[str, float]:
    return _fetch_stage("world-cup-nation-to-reach-quarterfinals", "QF")


def fetch_sf_probs() -> Dict[str, float]:
    return _fetch_stage("world-cup-nation-to-reach-semifinals", "SF")


# ── Entertainment bonus weights ───────────────────────────────────────────────

def compute_entertainment_weights(
    win_probs: Dict[str, float],
    groups: Dict[str, List[str]],
    mu_base: float = 2.8,
    kappa: float = 0.2,
) -> Dict[str, float]:
    """
    P(entertainment bonus) per team, based on expected total goals across 3 group games.
    Mismatched games (large log-strength difference) produce more extreme scorelines,
    so teams at either extreme (dominant or minnow) in lopsided groups score higher.

    λ_game = mu_base × (1 + kappa × |log(s_i / s_j)|)
    weight_i = sum over 3 opponents of λ_game
    Returns weights normalised to sum to 1 across all 48 teams.
    """
    weights: Dict[str, float] = {}
    for g, team_keys in groups.items():
        strengths = {k: max(win_probs.get(k, 0.001), 0.001) for k in team_keys}
        for i_key in team_keys:
            s_i = strengths[i_key]
            total = 0.0
            for j_key in team_keys:
                if j_key == i_key:
                    continue
                mismatch = abs(math.log(s_i / strengths[j_key]))
                total += mu_base * (1.0 + kappa * mismatch)
            weights[i_key] = total
    total_w = sum(weights.values())
    if total_w <= 0:
        return {k: 1.0 / N_TEAMS for k in weights}
    return {k: v / total_w for k, v in weights.items()}


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


# ── Probability derivation ────────────────────────────────────────────────────

def derive_probs(
    p_win: float,
    group_finish: Optional[Dict[str, float]] = None,
    p_r16_direct: Optional[float] = None,
    p_qf_direct: Optional[float] = None,
    p_sf_direct: Optional[float] = None,
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
        # 8 of 12 3rd-place finishers advance as wildcards
        p_advance = min(p_grp_1st + p_grp_2nd + p_grp_3rd * (8.0 / 12.0), 0.97)
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
    p_final = min(p_win * 2.0, 0.82)

    # ── Monotonicity (top-down) ───────────────────────────────────────────────
    # If R16 market exceeds Harville advance, trust the more liquid R16 market
    p_advance = max(p_advance, p_r16)
    p_r16   = min(p_r16,   p_advance)
    p_qf    = min(p_qf,    p_r16)
    p_sf    = min(p_sf,    p_qf)
    p_final = min(p_final, p_sf)
    p_win   = min(p_win,   p_final)

    # ── Stage-exit probabilities ──────────────────────────────────────────────
    p_2nd          = max(0.0, p_final - p_win)
    p_sf_exit      = max(0.0, p_sf - p_final)
    p_3rd          = p_sf_exit / 2.0
    p_4th          = p_sf_exit / 2.0
    p_qf_exit      = max(0.0, p_qf  - p_sf)
    p_r16_exit     = max(0.0, p_r16 - p_qf)
    p_r32_exit     = max(0.0, p_advance - p_r16)
    p_group_exit   = max(0.0, 1.0 - p_advance)
    p_wooden_spoon = p_group_exit / 16.0   # 1 of 16 group exits is wooden spoon

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
        "p_wooden_spoon":  p_wooden_spoon,
        "p_grp_1st":       p_grp_1st,
        "p_grp_2nd":       p_grp_2nd,
        "p_grp_3rd":       p_grp_3rd,
        "p_grp_4th":       p_grp_4th,
    }


# ── EV calculation ────────────────────────────────────────────────────────────

def calculate_ev(p: Dict[str, float], ent_weight: float) -> Dict[str, float]:
    ev_group = (
        p["p_grp_1st"] * GROUP_PTS[1]
        + p["p_grp_2nd"] * GROUP_PTS[2]
        + p["p_grp_4th"] * GROUP_PTS[4]
        # 3rd-place earns 0 group pts regardless of whether they advance
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

    win_probs   = fetch_win_probs()
    group_probs = fetch_group_winner_probs()
    r16_probs   = fetch_r16_probs()
    qf_probs    = fetch_qf_probs()
    sf_probs    = fetch_sf_probs()

    # Pre-compute Harville group finish distributions
    group_finish_all: Dict[str, Dict[str, float]] = {}
    for g, team_keys in GROUPS.items():
        raw = group_probs.get(g, {})
        if len(raw) >= 2:
            finish = harville_group_finish(raw)
            group_finish_all.update(finish)

    print(f"[INFO] Harville computed for {len(group_finish_all)} / {N_TEAMS} teams", file=sys.stderr)

    # Fallback prior for teams not found in winner markets
    known_total = sum(win_probs.values())
    n_unknown   = N_TEAMS - len(win_probs)
    remaining   = max(0.0, 1.0 - known_total)
    prior_p_win = (remaining / n_unknown) if n_unknown > 0 else 0.001

    ent_weights = compute_entertainment_weights(win_probs, GROUPS)

    results = []
    for key, (name, flag, group) in TEAMS.items():
        has_winner = key in win_probs
        has_group  = key in group_finish_all
        has_r16    = key in r16_probs
        has_qf     = key in qf_probs
        has_sf     = key in sf_probs

        stage_count = sum([has_r16, has_qf, has_sf, has_group])
        if has_winner and stage_count >= 4:
            quality = "full"
        elif has_winner and stage_count >= 1:
            quality = "partial"
        else:
            quality = "winner_only"

        p_win = win_probs.get(key, prior_p_win)
        probs = derive_probs(
            p_win,
            group_finish=group_finish_all.get(key),
            p_r16_direct=r16_probs.get(key),
            p_qf_direct=qf_probs.get(key),
            p_sf_direct=sf_probs.get(key),
        )
        ev = calculate_ev(probs, ent_weights.get(key, 1.0 / N_TEAMS))

        results.append({
            "name":         name,
            "key":          key,
            "flag":         flag,
            "group":        group,
            "data_quality": quality,
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
                "p_grp_4th":    round(probs["p_grp_4th"], 4),
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
