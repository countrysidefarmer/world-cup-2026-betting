#!/usr/bin/env python3
"""
Dixon-Coles bivariate Poisson match simulator.

P(goals_A=i, goals_B=j) = Poisson(i; λ_A) × Poisson(j; λ_B) × τ(i,j,λ_A,λ_B,ρ)

τ corrects low-scoring scorelines for the observed negative correlation between
the two teams' goal counts (i.e. 0-0, 1-0, 0-1, 1-1 are more/less likely than
independent Poisson would predict).

λ_A = μ × q_A   where q_A = 10^(R_A/400) / (10^(R_A/400) + 10^(R_B/400))
This ensures λ_A + λ_B = μ and scales linearly with relative Elo strength.
"""

from __future__ import annotations

import math
from typing import Dict, Tuple

import numpy as np

# Match parameters
MU     = 2.65   # total expected goals per match (WC calibration)
RHO    = -0.10  # Dixon-Coles correlation correction


def _q(rating_a: float, rating_b: float) -> Tuple[float, float]:
    """Elo-derived strength shares. q_a + q_b = 1."""
    s_a = 10.0 ** (rating_a / 400.0)
    s_b = 10.0 ** (rating_b / 400.0)
    total = s_a + s_b
    return s_a / total, s_b / total


def elo_to_lambdas(
    rating_a: float,
    rating_b: float,
    mu: float = MU,
    strength_mult_a: float = 1.0,
    strength_mult_b: float = 1.0,
) -> Tuple[float, float]:
    """
    Map Elo ratings to per-team expected goals.
    strength_mult_* allows manual scaling on top of Elo (from adjustments CSV).
    """
    q_a, q_b = _q(rating_a, rating_b)
    # Apply multipliers, then re-normalise so total stays = mu
    w_a = q_a * strength_mult_a
    w_b = q_b * strength_mult_b
    total = w_a + w_b
    lam_a = mu * w_a / total
    lam_b = mu * w_b / total
    return lam_a, lam_b


def _dc_tau(i: int, j: int, lam_a: float, lam_b: float, rho: float) -> float:
    """Dixon-Coles correction factor τ for low-scoring scorelines."""
    if i == 0 and j == 0:
        return 1.0 - lam_a * lam_b * rho
    elif i == 0 and j == 1:
        return 1.0 + lam_a * rho
    elif i == 1 and j == 0:
        return 1.0 + lam_b * rho
    elif i == 1 and j == 1:
        return 1.0 - rho
    return 1.0


def _poisson_pmf(k: int, lam: float) -> float:
    if lam <= 0:
        return 1.0 if k == 0 else 0.0
    return math.exp(-lam) * (lam ** k) / math.factorial(k)


def dc_win_probs(
    rating_a: float,
    rating_b: float,
    rho: float = RHO,
    mu: float = MU,
    max_goals: int = 8,
    strength_mult_a: float = 1.0,
    strength_mult_b: float = 1.0,
) -> Dict[str, float]:
    """
    Analytical P(win_a), P(draw), P(win_b) from DC model.
    Also returns lam_a, lam_b for diagnostics.
    """
    lam_a, lam_b = elo_to_lambdas(
        rating_a, rating_b, mu, strength_mult_a, strength_mult_b
    )
    p_win_a = p_draw = p_win_b = 0.0
    for i in range(max_goals + 1):
        for j in range(max_goals + 1):
            p = (
                _poisson_pmf(i, lam_a)
                * _poisson_pmf(j, lam_b)
                * _dc_tau(i, j, lam_a, lam_b, rho)
            )
            if i > j:
                p_win_a += p
            elif i == j:
                p_draw += p
            else:
                p_win_b += p
    total = p_win_a + p_draw + p_win_b
    if total <= 0:
        total = 1.0
    return {
        "p_win_a": p_win_a / total,
        "p_draw":  p_draw  / total,
        "p_win_b": p_win_b / total,
        "lam_a":   lam_a,
        "lam_b":   lam_b,
    }


def _build_dc_pmf_matrix(
    lam_a: float,
    lam_b: float,
    rho: float = RHO,
    max_goals: int = 10,
) -> np.ndarray:
    """
    Build and return a (max_goals+1) × (max_goals+1) probability matrix
    where entry [i,j] = P(goals_A=i, goals_B=j).
    """
    mat = np.zeros((max_goals + 1, max_goals + 1))
    for i in range(max_goals + 1):
        for j in range(max_goals + 1):
            mat[i, j] = (
                _poisson_pmf(i, lam_a)
                * _poisson_pmf(j, lam_b)
                * _dc_tau(i, j, lam_a, lam_b, rho)
            )
    total = mat.sum()
    if total > 0:
        mat /= total
    return mat


def sample_scoreline(
    rating_a: float,
    rating_b: float,
    rng: np.random.Generator,
    rho: float = RHO,
    mu: float = MU,
    max_goals: int = 10,
    strength_mult_a: float = 1.0,
    strength_mult_b: float = 1.0,
) -> Tuple[int, int]:
    """
    Sample a scoreline (goals_a, goals_b) from the Dixon-Coles distribution.
    Uses vectorised lookup into the pre-computed PMF matrix.
    """
    lam_a, lam_b = elo_to_lambdas(
        rating_a, rating_b, mu, strength_mult_a, strength_mult_b
    )
    mat = _build_dc_pmf_matrix(lam_a, lam_b, rho, max_goals)
    flat = mat.ravel()
    flat = np.maximum(flat, 0)
    flat /= flat.sum()
    idx = rng.choice(len(flat), p=flat)
    i, j = divmod(idx, max_goals + 1)
    return int(i), int(j)


def sample_knockout_winner(
    rating_a: float,
    rating_b: float,
    rng: np.random.Generator,
    rho: float = RHO,
    mu: float = MU,
    strength_mult_a: float = 1.0,
    strength_mult_b: float = 1.0,
) -> Tuple[str, int, int]:
    """
    Sample knockout result: always produces a winner (draws go to penalties).
    Returns ('A' or 'B', goals_a, goals_b).
    Penalties modelled as 50/50 after draw (ignoring Elo — reasonable for rare events).
    """
    ga, gb = sample_scoreline(
        rating_a, rating_b, rng, rho, mu, strength_mult_a=strength_mult_a,
        strength_mult_b=strength_mult_b,
    )
    if ga > gb:
        return "A", ga, gb
    elif gb > ga:
        return "B", ga, gb
    else:
        # Penalty shootout — use Elo to bias slightly (75% base × p_win_a modifier)
        p_a = elo_win_prob_pen(rating_a, rating_b)
        winner = "A" if rng.random() < p_a else "B"
        return winner, ga, gb


def elo_win_prob_pen(rating_a: float, rating_b: float) -> float:
    """
    Penalty shootout win probability for A.
    Shrinks Elo advantage 50% toward 50/50 (skill matters less in shootouts).
    """
    from elo_model.elo_core import elo_win_prob
    p = elo_win_prob(rating_a, rating_b)
    return 0.5 + 0.5 * (p - 0.5)
