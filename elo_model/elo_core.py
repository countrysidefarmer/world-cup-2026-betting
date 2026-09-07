#!/usr/bin/env python3
"""
Elo rating core: seeding from eloratings.net, live updating, adjustment loading.
"""

from __future__ import annotations

import csv
import json
import sys
from pathlib import Path
from typing import Dict, Optional, Tuple

import requests

SEED_URL = "https://www.eloratings.net/World.tsv"

# eloratings.net 2-letter code → canonical WC 2026 team key
# Codes verified against TSV (col 3) + eloratings.net site.
ELO_CODE_TO_TEAM: Dict[str, str] = {
    "ES": "Spain",
    "AR": "Argentina",
    "FR": "France",
    "EN": "England",
    "CO": "Colombia",
    "BR": "Brazil",
    "PT": "Portugal",
    "NL": "Netherlands",
    "DE": "Germany",
    "NO": "Norway",
    "JP": "Japan",
    "EC": "Ecuador",
    "HR": "Croatia",
    "MX": "Mexico",
    "BE": "Belgium",
    "UY": "Uruguay",
    "CH": "Switzerland",
    "AT": "Austria",
    "TR": "Turkiye",
    "MA": "Morocco",
    "AU": "Australia",
    "SN": "Senegal",
    "SQ": "Scotland",
    "KR": "South Korea",
    "PY": "Paraguay",
    "US": "USA",
    "CA": "Canada",
    "DZ": "Algeria",
    "IR": "Iran",
    "SE": "Sweden",
    "CI": "Ivory Coast",
    "UZ": "Uzbekistan",
    "CZ": "Czechia",
    "EG": "Egypt",
    "JO": "Jordan",
    "ZA": "South Africa",
    "GH": "Ghana",
    "TN": "Tunisia",
    "NZ": "New Zealand",
    "HT": "Haiti",
    "BA": "Bosnia and Herzegovina",
    "CD": "DR Congo",
    "CV": "Cape Verde",
    "SA": "Saudi Arabia",
    "IQ": "Iraq",
    "QA": "Qatar",
    "CW": "Curacao",
    "PA": "Panama",
}

# Reverse: team key → eloratings code (for display)
TEAM_TO_ELO_CODE: Dict[str, str] = {v: k for k, v in ELO_CODE_TO_TEAM.items()}

# All 48 WC 2026 teams (needed to detect missing mappings)
WC_TEAMS = set(ELO_CODE_TO_TEAM.values())


def fetch_seed_ratings() -> Dict[str, float]:
    """Download Elo ratings from eloratings.net/World.tsv and return {team_key: elo}."""
    try:
        r = requests.get(SEED_URL, timeout=15)
        r.raise_for_status()
        text = r.text
    except Exception as exc:
        print(f"[WARN] Could not fetch {SEED_URL}: {exc}", file=sys.stderr)
        return {}

    ratings: Dict[str, float] = {}
    for line in text.splitlines():
        parts = line.strip().split("\t")
        # Format: rank rank code rating ...
        if len(parts) < 4:
            continue
        code = parts[2].strip()
        team = ELO_CODE_TO_TEAM.get(code)
        if not team:
            continue
        try:
            elo = float(parts[3])
        except ValueError:
            continue
        ratings[team] = elo

    found = len(ratings)
    missing = WC_TEAMS - set(ratings)
    print(f"[INFO] Elo seed: {found} WC teams from eloratings.net", file=sys.stderr)
    if missing:
        print(f"[WARN] Missing seed ratings for: {sorted(missing)}", file=sys.stderr)
    return ratings


def load_ratings(cache_path: Path) -> Dict[str, float]:
    """Load Elo ratings from cache; fall back to fresh seed if cache missing."""
    if cache_path.exists():
        try:
            with open(cache_path) as f:
                data = json.load(f)
            ratings = data.get("ratings", {})
            if ratings:
                print(
                    f"[INFO] Elo ratings loaded from cache "
                    f"(seeded {data.get('seeded_at','?')}, "
                    f"updated through {data.get('updated_through','?')})",
                    file=sys.stderr,
                )
                return ratings
        except Exception:
            pass
    print("[INFO] No Elo cache found — seeding from eloratings.net", file=sys.stderr)
    return fetch_seed_ratings()


def save_ratings(
    ratings: Dict[str, float],
    cache_path: Path,
    seeded_at: str = "",
    updated_through: str = "",
) -> None:
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    existing: dict = {}
    if cache_path.exists():
        try:
            with open(cache_path) as f:
                existing = json.load(f)
        except Exception:
            pass
    existing["ratings"] = ratings
    if seeded_at:
        existing["seeded_at"] = seeded_at
    if updated_through:
        existing["updated_through"] = updated_through
    with open(cache_path, "w") as f:
        json.dump(existing, f, indent=2)


def elo_win_prob(rating_a: float, rating_b: float) -> float:
    """P(team A beats team B) from Elo ratings. No home advantage (neutral venue)."""
    return 1.0 / (1.0 + 10.0 ** ((rating_b - rating_a) / 400.0))


def expected_score(rating_a: float, rating_b: float) -> float:
    """Elo expected score for team A (same as win_prob for binary outcome)."""
    return elo_win_prob(rating_a, rating_b)


def update_elo(
    rating_a: float,
    rating_b: float,
    goals_a: int,
    goals_b: int,
    k: float = 20.0,
) -> Tuple[float, float]:
    """
    Update Elo ratings from match result. Returns (new_ra, new_rb).

    Score encoding: win=1, draw=0.5, loss=0. WC format has no extra weight here;
    goal difference is ignored (Elo only tracks outcome, not margin).
    """
    if goals_a > goals_b:
        score_a = 1.0
    elif goals_a == goals_b:
        score_a = 0.5
    else:
        score_a = 0.0
    score_b = 1.0 - score_a

    e_a = expected_score(rating_a, rating_b)
    e_b = 1.0 - e_a

    new_ra = rating_a + k * (score_a - e_a)
    new_rb = rating_b + k * (score_b - e_b)
    return new_ra, new_rb


def load_adjustments(adj_path: Path) -> Dict[str, Dict]:
    """
    Load manual adjustments from CSV. Returns {team_key: {elo_delta, strength_mult, note}}.
    CSV columns: team, elo_delta, strength_mult, note
    """
    adjustments: Dict[str, Dict] = {}
    if not adj_path.exists():
        return adjustments
    with open(adj_path, newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            team = row.get("team", "").strip()
            if not team or team.startswith("#"):
                continue
            try:
                elo_delta = float(row.get("elo_delta", 0) or 0)
            except ValueError:
                elo_delta = 0.0
            try:
                strength_mult = float(row.get("strength_mult", 1) or 1)
            except ValueError:
                strength_mult = 1.0
            adjustments[team] = {
                "elo_delta":     elo_delta,
                "strength_mult": strength_mult,
                "note":          row.get("note", "").strip(),
            }
    return adjustments


def apply_adjustments(
    ratings: Dict[str, float],
    adjustments: Dict[str, Dict],
) -> Dict[str, float]:
    """Return a new ratings dict with manual adjustments applied."""
    adjusted = dict(ratings)
    for team, adj in adjustments.items():
        if team in adjusted:
            adjusted[team] = adjusted[team] + adj["elo_delta"]
        # strength_mult is handled downstream in dixon_coles.py via effective Elo shift
    return adjusted
