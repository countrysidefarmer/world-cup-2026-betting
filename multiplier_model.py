#!/usr/bin/env python3
"""
WC 2026 match-multiplier model.

Payoff = Goals × (Yellow Cards + 2 × Red Cards) × Corners
         — combined for both teams, settled at 90 minutes.

Calibration anchors (WC 2022 group stage averages):
  Goals/match:          2.69
  Yellow cards/match:   3.41
  Red cards/match:      0.14
  Weighted cards:       3.69
  Corners/match:        9.5  (approx)
  Independence baseline E[G × C × Co] ≈ 90
  With positive correlation:             ≈ 108
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Dict, Optional, Tuple

import numpy as np

# ── Referee stats (avg per match, both teams combined) ────────────────────────
# Source: FootRef / TransferMarkt / UEFA match data, WC + major tournament games.
# avg_yellows: mean yellow cards per 90-min match across career (both teams).
# avg_reds:    mean red cards per 90-min match.
# WC 2022 group-stage average = 3.41 yellows/match — used as the baseline (1.0).
# ref_factor = ref.avg_yellows / WC_AVG_YELLOWS_PER_MATCH

WC_AVG_YELLOWS_PER_MATCH = 3.41   # WC 2022 calibration anchor — used for ref factor normalisation only

def _load_tournament_card_factor() -> float:
    stats_path = Path(__file__).parent / "data" / "wc2026_stats.json"
    try:
        with open(stats_path) as f:
            d = json.load(f)
        m = d.get("total_matches", 0)
        y = d.get("total_yellows", 0)
        if m > 0:
            return y / (m * WC_AVG_YELLOWS_PER_MATCH)
    except Exception:
        pass
    return 0.744  # fallback: Week 1 value (33 yellows / 13 matches)

TOURNAMENT_CARD_FACTOR = _load_tournament_card_factor()

REF_STATS: Dict[str, Dict[str, float]] = {
    # ── Strict / card-happy (factor > 1.2) ───────────────────────────────────
    # Source: StatsHub / FootyMetrics career averages (all competitions)
    "Kevin Ortega":             {"avg_yellows": 5.73, "avg_reds": 0.15},
    "Istvan Kovacs":            {"avg_yellows": 5.39, "avg_reds": 0.10},
    "Wilton Sampaio":           {"avg_yellows": 5.08, "avg_reds": 0.12},
    "Yael Falcon Perez":        {"avg_yellows": 5.00, "avg_reds": 0.15},
    "Felix Zwayer":             {"avg_yellows": 4.63, "avg_reds": 0.09},
    "Raphael Claus":            {"avg_yellows": 4.41, "avg_reds": 0.10},
    "Szymon Marciniak":         {"avg_yellows": 4.29, "avg_reds": 0.08},
    "Jesus Valenzuela":         {"avg_yellows": 4.20, "avg_reds": 0.12},
    "Slavko Vincic":            {"avg_yellows": 4.10, "avg_reds": 0.09},
    # ── Average (factor 1.0–1.2) ─────────────────────────────────────────────
    "Jalal Jayed":              {"avg_yellows": 3.86, "avg_reds": 0.10},
    "Mustapha Ghorbal":         {"avg_yellows": 3.80, "avg_reds": 0.13},
    "Anthony Taylor":           {"avg_yellows": 3.78, "avg_reds": 0.08},
    "Francois Letexier":        {"avg_yellows": 3.77, "avg_reds": 0.07},
    "Alireza Faghani":          {"avg_yellows": 3.61, "avg_reds": 0.08},
    "Ismail Elfath":            {"avg_yellows": 3.58, "avg_reds": 0.07},
    "Facundo Tello":            {"avg_yellows": 3.50, "avg_reds": 0.13},
    "Abdulrahman Al-Jassim":    {"avg_yellows": 3.40, "avg_reds": 0.10},
    "Danny Makkelie":           {"avg_yellows": 3.40, "avg_reds": 0.07},
    "Michael Oliver":           {"avg_yellows": 3.49, "avg_reds": 0.06},
    "Cesar Ramos":              {"avg_yellows": 3.45, "avg_reds": 0.11},
    # ── Lenient (factor < 1.0) ───────────────────────────────────────────────
    "Clement Turpin":           {"avg_yellows": 3.36, "avg_reds": 0.06},
    "Glenn Nyberg":             {"avg_yellows": 3.21, "avg_reds": 0.05},
    "Ma Ning":                  {"avg_yellows": 2.80, "avg_reds": 0.09},
    "Bakary Gassama":           {"avg_yellows": 2.50, "avg_reds": 0.08},
}

# Last-name aliases for quick lookup (e.g. "Vincic" → "Slavko Vincic")
_REF_ALIASES: Dict[str, str] = {k.split()[-1].lower(): k for k in REF_STATS}
# Extra accent-insensitive aliases
_REF_ALIASES.update({
    "vinčić": "Slavko Vincic",
    "marciniak": "Szymon Marciniak",
    "letexier": "Francois Letexier",
    "falcón": "Yael Falcon Perez",
    "falcon": "Yael Falcon Perez",
    "jayed": "Jalal Jayed",
    "jiyed": "Jalal Jayed",
})


def get_ref_factor(ref_name: str, wc_games: int = 0) -> float:
    """Multiplicative factor on yellow card rate vs WC 2022 average.

    Shrinkage toward 1.0 decreases as the referee accumulates WC 2026 assignments:
      wc_games=0  → 80% shrink (no in-tournament signal yet)
      wc_games=8  → 50% shrink (meaningful in-tournament track record)
    Capped at 60% weight on career data to retain tournament-level conservatism.
    """
    stats = REF_STATS.get(ref_name)
    if not stats:
        stats = REF_STATS.get(_REF_ALIASES.get(ref_name.lower(), ""))
    if not stats:
        return 1.0
    career_rf = stats["avg_yellows"] / WC_AVG_YELLOWS_PER_MATCH
    career_weight = min(0.20 + 0.05 * wc_games, 0.60)
    return career_weight * career_rf + (1 - career_weight) * 1.0

# ── Per-team stats (per 90 min WC match) ─────────────────────────────────────
# card_rate   : expected yellow cards per game (for this team alone)
# red_rate    : expected red cards per game
# corner_rate : expected corners won per game
# attack_mult : scales the team's share of total match goals (1.0 = average)
#
# Sources: WC 2022 / 2018 / 2014 group stage data, known team styles.
# Confederation defaults used for teams with <4 WC games in last 3 tournaments.

TEAM_STATS: Dict[str, Dict[str, float]] = {
    # ── UEFA ──────────────────────────────────────────────────────────────────
    "England":                {"card_rate": 1.5, "red_rate": 0.06, "corner_rate": 6.2, "attack_mult": 1.05},
    "Spain":                  {"card_rate": 1.9, "red_rate": 0.07, "corner_rate": 6.8, "attack_mult": 1.10},
    "France":                 {"card_rate": 1.7, "red_rate": 0.07, "corner_rate": 5.4, "attack_mult": 1.00},
    "Germany":                {"card_rate": 1.8, "red_rate": 0.07, "corner_rate": 6.3, "attack_mult": 1.05},
    "Netherlands":            {"card_rate": 1.9, "red_rate": 0.08, "corner_rate": 5.6, "attack_mult": 1.00},
    "Portugal":               {"card_rate": 2.0, "red_rate": 0.08, "corner_rate": 5.3, "attack_mult": 1.05},
    "Belgium":                {"card_rate": 1.8, "red_rate": 0.07, "corner_rate": 5.2, "attack_mult": 1.00},
    "Norway":                 {"card_rate": 1.8, "red_rate": 0.07, "corner_rate": 5.5, "attack_mult": 1.05},
    "Croatia":                {"card_rate": 1.7, "red_rate": 0.07, "corner_rate": 5.0, "attack_mult": 0.95},
    "Switzerland":            {"card_rate": 1.8, "red_rate": 0.07, "corner_rate": 4.8, "attack_mult": 0.95},
    "Austria":                {"card_rate": 1.9, "red_rate": 0.08, "corner_rate": 5.0, "attack_mult": 0.95},
    "Scotland":               {"card_rate": 2.0, "red_rate": 0.08, "corner_rate": 4.8, "attack_mult": 0.90},
    "Sweden":                 {"card_rate": 1.7, "red_rate": 0.07, "corner_rate": 5.2, "attack_mult": 0.95},
    "Czechia":                {"card_rate": 2.0, "red_rate": 0.09, "corner_rate": 4.8, "attack_mult": 0.90},
    "Bosnia and Herzegovina": {"card_rate": 2.1, "red_rate": 0.09, "corner_rate": 4.5, "attack_mult": 0.88},
    # ── CONMEBOL ─────────────────────────────────────────────────────────────
    "Argentina":              {"card_rate": 2.2, "red_rate": 0.10, "corner_rate": 5.0, "attack_mult": 1.02},
    "Brazil":                 {"card_rate": 2.0, "red_rate": 0.08, "corner_rate": 5.8, "attack_mult": 1.05},
    "Colombia":               {"card_rate": 2.7, "red_rate": 0.13, "corner_rate": 5.2, "attack_mult": 0.95},
    "Uruguay":                {"card_rate": 2.3, "red_rate": 0.10, "corner_rate": 4.8, "attack_mult": 0.92},
    "Ecuador":                {"card_rate": 2.0, "red_rate": 0.09, "corner_rate": 4.5, "attack_mult": 0.90},
    "Paraguay":               {"card_rate": 2.2, "red_rate": 0.10, "corner_rate": 4.3, "attack_mult": 0.85},
    # ── CONCACAF ─────────────────────────────────────────────────────────────
    "USA":                    {"card_rate": 1.8, "red_rate": 0.07, "corner_rate": 5.0, "attack_mult": 0.92},
    "Mexico":                 {"card_rate": 2.5, "red_rate": 0.11, "corner_rate": 5.3, "attack_mult": 0.90},
    "Canada":                 {"card_rate": 1.8, "red_rate": 0.08, "corner_rate": 4.5, "attack_mult": 0.88},
    "Panama":                 {"card_rate": 2.3, "red_rate": 0.11, "corner_rate": 3.8, "attack_mult": 0.80},
    "Haiti":                  {"card_rate": 2.0, "red_rate": 0.09, "corner_rate": 3.5, "attack_mult": 0.78},
    "Curacao":                {"card_rate": 2.0, "red_rate": 0.09, "corner_rate": 3.5, "attack_mult": 0.78},
    # ── UEFA (continued) ─────────────────────────────────────────────────────
    "Turkiye":                {"card_rate": 2.3, "red_rate": 0.10, "corner_rate": 5.0, "attack_mult": 0.92},
    # ── CAF ──────────────────────────────────────────────────────────────────
    "Morocco":                {"card_rate": 1.8, "red_rate": 0.08, "corner_rate": 4.5, "attack_mult": 0.88},
    "Senegal":                {"card_rate": 2.3, "red_rate": 0.10, "corner_rate": 4.8, "attack_mult": 0.90},
    "Egypt":                  {"card_rate": 2.2, "red_rate": 0.10, "corner_rate": 4.2, "attack_mult": 0.85},
    "Ivory Coast":            {"card_rate": 2.2, "red_rate": 0.10, "corner_rate": 4.5, "attack_mult": 0.88},
    "Ghana":                  {"card_rate": 2.1, "red_rate": 0.10, "corner_rate": 4.3, "attack_mult": 0.83},
    "Tunisia":                {"card_rate": 2.0, "red_rate": 0.09, "corner_rate": 4.0, "attack_mult": 0.82},
    "Algeria":                {"card_rate": 2.1, "red_rate": 0.09, "corner_rate": 4.2, "attack_mult": 0.82},
    "DR Congo":               {"card_rate": 2.1, "red_rate": 0.09, "corner_rate": 4.0, "attack_mult": 0.80},
    "South Africa":           {"card_rate": 2.0, "red_rate": 0.09, "corner_rate": 4.0, "attack_mult": 0.80},
    "Cape Verde":             {"card_rate": 2.1, "red_rate": 0.09, "corner_rate": 3.8, "attack_mult": 0.78},
    # ── AFC ──────────────────────────────────────────────────────────────────
    "Japan":                  {"card_rate": 1.3, "red_rate": 0.05, "corner_rate": 4.8, "attack_mult": 0.88},
    "South Korea":            {"card_rate": 1.8, "red_rate": 0.08, "corner_rate": 4.5, "attack_mult": 0.88},
    "Australia":              {"card_rate": 1.8, "red_rate": 0.08, "corner_rate": 4.5, "attack_mult": 0.85},
    "Iran":                   {"card_rate": 2.2, "red_rate": 0.10, "corner_rate": 3.8, "attack_mult": 0.75},
    "Saudi Arabia":           {"card_rate": 2.3, "red_rate": 0.10, "corner_rate": 3.8, "attack_mult": 0.75},
    "Qatar":                  {"card_rate": 2.0, "red_rate": 0.09, "corner_rate": 3.5, "attack_mult": 0.72},
    "Uzbekistan":             {"card_rate": 2.0, "red_rate": 0.09, "corner_rate": 3.8, "attack_mult": 0.75},
    "Jordan":                 {"card_rate": 2.0, "red_rate": 0.09, "corner_rate": 3.5, "attack_mult": 0.72},
    "Iraq":                   {"card_rate": 2.1, "red_rate": 0.09, "corner_rate": 3.5, "attack_mult": 0.72},
    # ── OFC ──────────────────────────────────────────────────────────────────
    "New Zealand":            {"card_rate": 1.8, "red_rate": 0.08, "corner_rate": 3.8, "attack_mult": 0.78},
    # ── Default (used if team not found) ─────────────────────────────────────
    "_DEFAULT":               {"card_rate": 1.8, "red_rate": 0.08, "corner_rate": 4.5, "attack_mult": 0.90},
}

# Correlation matrix for [goals, cards, corners] intensity factors
# Positive correlation because all three increase with match "openness"
_CORR = np.array([
    [1.00, 0.15, 0.25],
    [0.15, 1.00, 0.10],
    [0.25, 0.10, 1.00],
])
_CHOL = np.linalg.cholesky(_CORR)
_SIGMA = 0.25   # log-normal volatility for each intensity factor

# WC average total goals per match (calibration anchor)
_WC_GOALS_PER_MATCH = 2.65


def _team_stats(team: str) -> Dict[str, float]:
    return TEAM_STATS.get(team, TEAM_STATS["_DEFAULT"])


def compute_multiplier_theo(
    team_a: str,
    team_b: str,
    p_win_a: float,
    p_win_b: float,
    ref_factor: float = 1.0,
    lam_goals_override: Optional[float] = None,
    lam_corners_override: Optional[float] = None,
    n_sims: int = 200_000,
    seed: Optional[int] = None,
) -> Dict[str, float]:
    """
    Monte Carlo estimate of E[Goals × WeightedCards × Corners] for a match.

    Args:
        team_a, team_b        : canonical team names (matching TEAMS keys in fetch_data.py)
        p_win_a, p_win_b      : Polymarket tournament win probabilities (used as strength proxy)
        lam_goals_override    : market-implied total expected goals (overrides win-prob model)
        lam_corners_override  : market-implied total expected corners (overrides team stats)
        n_sims                : number of Monte Carlo draws
        seed                  : optional RNG seed for reproducibility

    Returns dict with 'theo' (fair price) plus distribution info and component expectations.
    """
    rng = np.random.default_rng(seed)

    sa = _team_stats(team_a)
    sb = _team_stats(team_b)

    # ── Goals ──────────────────────────────────────────────────────────────
    if lam_goals_override is not None:
        lam_goals = lam_goals_override
    else:
        s_a = max(p_win_a, 1e-4)
        s_b = max(p_win_b, 1e-4)
        share_a = s_a / (s_a + s_b)
        share_b = 1.0 - share_a
        lam_goals_a = _WC_GOALS_PER_MATCH * share_a * sa["attack_mult"]
        lam_goals_b = _WC_GOALS_PER_MATCH * share_b * sb["attack_mult"]
        lam_goals   = lam_goals_a + lam_goals_b

    # ── Cards ───────────────────────────────────────────────────────────────
    # TOURNAMENT_CARD_FACTOR calibrates team rates to live WC 2026 observed average.
    lam_yellow = (sa["card_rate"] + sb["card_rate"]) * ref_factor * TOURNAMENT_CARD_FACTOR
    lam_red    = (sa["red_rate"]  + sb["red_rate"])  * TOURNAMENT_CARD_FACTOR

    # ── Corners ─────────────────────────────────────────────────────────────
    lam_corners = lam_corners_override if lam_corners_override is not None else (sa["corner_rate"] + sb["corner_rate"])

    # ── Correlated intensity factors via Cholesky decomposition ─────────────
    z  = rng.standard_normal((3, n_sims))
    zc = _CHOL @ z                    # correlated standard normals
    alpha = np.exp(_SIGMA * zc)       # log-normal: shape [3, n_sims]
    # alpha[0] scales goals, alpha[1] scales yellows, alpha[2] scales corners

    # ── Simulate components ──────────────────────────────────────────────────
    goals   = rng.poisson(lam_goals   * alpha[0])
    yellows = rng.poisson(lam_yellow  * alpha[1])
    reds    = rng.poisson(lam_red     * np.ones(n_sims))  # independent (rare)
    corners = rng.poisson(lam_corners * alpha[2])

    weighted_cards = yellows + 2 * reds
    multiplier = goals * weighted_cards * corners

    pcts = np.percentile(multiplier, [10, 25, 50, 75, 90])

    return {
        "theo":      float(multiplier.mean()),
        "p10":       float(pcts[0]),
        "p25":       float(pcts[1]),
        "median":    float(pcts[2]),
        "p75":       float(pcts[3]),
        "p90":       float(pcts[4]),
        "p_zero":    float((multiplier == 0).mean()),
        "e_goals":   float(goals.mean()),
        "e_cards":   float(weighted_cards.mean()),
        "e_corners": float(corners.mean()),
    }


def parse_match_teams(
    title: str,
    metadata: Optional[Dict] = None,
) -> Optional[Tuple[str, str]]:
    """
    Extract (home_team, away_team) canonical names from a multiplier contract.

    Prefers metadata homeName/awayName fields if present, falls back to title parsing.
    Returns None if either team can't be matched.
    """
    # Prefer explicit metadata fields
    if metadata:
        home = metadata.get("homeName")
        away = metadata.get("awayName")
        if home and away:
            from tychemkt_scanner import _team_from_title
            h = _team_from_title(home)
            a = _team_from_title(away)
            if h and a:
                return h, a

    # Fall back to title parsing
    try:
        from tychemkt_scanner import _team_from_title
    except ImportError:
        return None

    for sep in (" vs ", " × ", " - ", " v "):
        if sep.lower() in title.lower():
            parts = title.split(sep) if sep in title else title.lower().split(sep.lower())
            if sep in title:
                a_str, b_str = title.split(sep, 1)
            else:
                # case-insensitive split
                idx = title.lower().index(sep.lower())
                a_str = title[:idx]
                b_str = title[idx + len(sep):]
            # strip suffix like "— Multiplier"
            for suffix in [" — Multiplier", "— Multiplier", " Multiplier"]:
                b_str = b_str.replace(suffix, "")
            h = _team_from_title(a_str.strip())
            a = _team_from_title(b_str.strip())
            if h and a:
                return h, a

    return None


if __name__ == "__main__":
    # Quick sanity check
    import json, sys
    sys.path.insert(0, ".")

    print("Multiplier model — sanity check")
    print(f"{'═'*70}")

    test_matches = [
        ("Spain",       "Saudi Arabia",  0.169, 0.001),  # heavy mismatch, Spain high corners
        ("Netherlands", "Japan",         0.049, 0.021),  # balanced-ish, Japan low cards
        ("Brazil",      "Morocco",       0.083, 0.015),  # Brazil high corners, Morocco solid
        ("Colombia",    "Senegal",       0.017, 0.007),  # high-card both sides
        ("Qatar",       "Switzerland",   0.001, 0.014),  # defensive vs attacking
    ]

    for ta, tb, pa, pb in test_matches:
        r = compute_multiplier_theo(ta, tb, pa, pb, n_sims=200_000)
        print(f"  {ta+' vs '+tb:<30}  theo={r['theo']:>6.1f}  "
              f"p50={r['median']:>5.0f}  p90={r['p90']:>6.0f}  "
              f"G={r['e_goals']:.2f} C={r['e_cards']:.2f} Co={r['e_corners']:.1f}  "
              f"p_zero={r['p_zero']:.1%}")

    print(f"{'═'*70}")
