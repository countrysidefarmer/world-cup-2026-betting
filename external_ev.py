#!/usr/bin/env python3
"""
WC 2026 EV scanner — two tables:
  1. Finish-value (bracket) contracts: Polymarket EV + XGBoost EV side by side
  2. Multiplier contracts

Data sources:
  - data/tychemkt_opps.json  (written by tychemkt_scanner.py — live books + multipliers)
  - data/teams.json           (Polymarket theos, written by fetch_data.py)
  - DagsHub MLflow            (XGBoost probs, updated bi-hourly)

Usage: /usr/bin/python3 external_ev.py
"""

from __future__ import annotations

import json
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Optional, Tuple

_ROOT = Path(__file__).parent
_DATA = _ROOT / "data"

DAGSHUB_BASE    = "https://dagshub.com/tomppa999/MLOps_FootballWCPredictor.mlflow"
MLFLOW_RUNS_URL = f"{DAGSHUB_BASE}/api/2.0/mlflow/runs/search"
EXP_ID          = "667e431847fd43a882314ab4a5fb472b"
ARTIFACT_URL    = (
    f"{DAGSHUB_BASE}/api/2.0/mlflow-artifacts/artifacts"
    f"/{EXP_ID}/{{run_id}}/artifacts/{{filename}}"
)
FALLBACK_RUN_IDS = [
    "5b78732f5fcd4dfabcd1dc72bcdc3fe7",
    "1f2a1fc4fd324d2eab2df3739e05bdb2",
]
GITHUB_FALLBACK = (
    "https://raw.githubusercontent.com/tomppa999/MLOps_FootballWCPredictor"
    "/main/src/dashboard/_offline_cache"
)

GROUP_PTS = {1: 20, 2: 10, 3: 0, 4: 5}
TOURNAMENT_PTS = {
    "1st": 90, "2nd": 70, "3rd": 55, "4th": 40,
    "qf_exit": 30, "r16_exit": 15, "r32_exit": 5,
    "group_exit": 0, "wooden_spoon": 5,
}
ENTERTAINMENT_BONUS = 15

EXT_TO_INTERNAL: Dict[str, str] = {
    "United States": "USA",
    "Turkey": "Türkiye",
    "Ivory Coast": "Ivory Coast",
    "South Korea": "South Korea",
    "DR Congo": "DR Congo",
    "Bosnia and Herzegovina": "Bosnia and Herzegovina",
}


# ── DagsHub fetch ─────────────────────────────────────────────────────────────

def _fetch_url(url: str, timeout: int = 10) -> Optional[bytes]:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "wc-ev/1.0"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read()
    except Exception:
        return None


def _fetch_latest_run() -> Tuple[str, str]:
    payload = json.dumps({
        "experiment_ids": ["1"], "max_results": 3, "order_by": ["start_time DESC"],
    }).encode()
    req = urllib.request.Request(
        MLFLOW_RUNS_URL, data=payload,
        headers={"Content-Type": "application/json", "User-Agent": "wc-ev/1.0"},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            runs = json.load(r).get("runs", [])
        if runs:
            info = runs[0]["info"]
            ts = datetime.fromtimestamp(int(info["start_time"]) / 1000, tz=timezone.utc)
            return info["run_id"], ts.strftime("%Y-%m-%dT%H:%MZ")
    except Exception as e:
        print(f"[WARN] DagsHub query failed ({e}), using fallback", file=sys.stderr)
    return FALLBACK_RUN_IDS[0], "unknown"


def _read_csv(data: bytes) -> list[dict]:
    lines = data.decode("utf-8").splitlines()
    if not lines:
        return []
    headers = [h.strip() for h in lines[0].split(",")]
    return [
        {headers[i]: (v.split(",")[i].strip() if i < len(v.split(",")) else "")
         for i in range(len(headers))}
        for v in lines[1:] if v.strip()
    ]


def _fetch_csv(run_id: str, filename: str) -> Optional[list[dict]]:
    for url in [
        ARTIFACT_URL.format(run_id=run_id, filename=filename),
        *[ARTIFACT_URL.format(run_id=fid, filename=filename) for fid in FALLBACK_RUN_IDS if fid != run_id],
        f"{GITHUB_FALLBACK}/{filename}",
    ]:
        data = _fetch_url(url)
        if data:
            rows = _read_csv(data)
            if rows:
                if "github" in url:
                    print("[WARN] Using GitHub offline cache (may be pre-tournament)", file=sys.stderr)
                return rows
    return None


# ── XGBoost EV computation ────────────────────────────────────────────────────

def _xgb_ev_total(t: dict, g: dict, ent_ev: float) -> float:
    p_win    = float(t.get("p_winner", 0))
    p_final  = float(t.get("p_final",  0))
    p_sf     = float(t.get("p_sf",     0))
    p_qf     = float(t.get("p_qf",     0))
    p_r16    = float(t.get("p_r16",    0))
    p_r32    = float(t.get("p_r32",    0))

    p_2nd      = max(p_final - p_win, 0)
    p_3rd      = max((p_sf - p_final) / 2, 0)
    p_4th      = max((p_sf - p_final) / 2, 0)
    p_qf_exit  = max(p_qf  - p_sf,  0)
    p_r16_exit = max(p_r16 - p_qf,  0)
    p_r32_exit = max(p_r32 - p_r16, 0)
    p_group_exit = max(1.0 - p_r32, 0)

    ev_t = (
        p_win * 90 + p_2nd * 70 + p_3rd * 55 + p_4th * 40
        + p_qf_exit * 30 + p_r16_exit * 15 + p_r32_exit * 5
        + (p_group_exit / 16) * 5
    )
    ev_g = (
        float(g.get("p_1st", 0)) * 20
        + float(g.get("p_2nd", 0)) * 10
        + float(g.get("p_4th", 0)) * 5
    )
    return ev_t + ev_g + ent_ev


# ── Data loaders ──────────────────────────────────────────────────────────────

def _load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    with open(path) as f:
        return json.load(f)


def _tradeable_edge(ev: float, bid: Optional[float], ask: Optional[float]) -> float:
    """Signed tradeable edge: positive = buy opportunity, negative = sell opportunity."""
    if bid is None or ask is None:
        return 0.0
    buy  = ev - ask   # positive if EV > ask (we buy cheap)
    sell = bid - ev   # positive if bid > EV (we sell dear)
    if buy > 0:
        return buy
    if sell > 0:
        return -sell   # negative signals sell
    return 0.0


# ── Table 1: Finish-value (bracket) ──────────────────────────────────────────

def _print_bracket_table(
    opps: list[dict],
    poly_evs: Dict[str, float],
    xgb_map: Dict[str, float],
    xgb_ts: str,
    poly_ts: str,
    scanned_at: str,
) -> None:
    rows = []
    for opp in opps:
        team     = opp["team"]
        bid      = opp.get("best_bid")
        ask      = opp.get("best_ask")
        bid_qty  = opp.get("best_bid_qty")
        ask_qty  = opp.get("best_ask_qty")
        poly_ev  = poly_evs.get(team, opp.get("ev_total", 0))
        xgb_ev   = xgb_map.get(team)

        poly_edge = _tradeable_edge(poly_ev, bid, ask)
        xgb_edge  = _tradeable_edge(xgb_ev, bid, ask) if xgb_ev is not None else None

        # Filter: either model has tradeable edge > 1
        if abs(poly_edge) <= 1 and (xgb_edge is None or abs(xgb_edge) <= 1):
            continue

        action_str = opp.get("action", "")
        price_str  = f"{opp.get('order_price', 0):.0f}" if opp.get("order_price") else ""
        pos        = opp.get("pos", 0)

        rows.append({
            "team":       team,
            "poly_ev":    round(poly_ev, 1),
            "xgb_ev":     round(xgb_ev, 1) if xgb_ev is not None else None,
            "bid":        bid,
            "ask":        ask,
            "bid_qty":    bid_qty,
            "ask_qty":    ask_qty,
            "poly_edge":  round(poly_edge, 1),
            "xgb_edge":   round(xgb_edge, 1) if xgb_edge is not None else None,
            "action":     f"{action_str} {price_str}".strip(),
            "pos":        pos,
        })

    # Sort by Poly edge magnitude: positive (BUY) first, most negative (SELL) last
    rows.sort(key=lambda r: -r["poly_edge"])

    W = 96
    print()
    print("═" * W)
    print(f"  Bracket contracts  |  Poly: {poly_ts}  (book: {scanned_at[:16]}Z)  |  XGB: {xgb_ts}")
    print(f"  Poly Edge / XGB Edge = tradeable edge vs ask (buy) or bid (sell)  [ + = buy,  − = sell ]")
    print()
    hdr = (
        f"  {'#':<3}  {'Team':<22}  {'PolyEV':>7}  {'XGB EV':>7}  "
        f"{'Book (B×Q / A×Q)':<22}  {'PolyEdge':>9}  {'XGB Edge':>9}  {'Action'}"
    )
    print(hdr)
    print("  " + "─" * (W - 2))

    for i, r in enumerate(rows, 1):
        bid_s  = f"{r['bid']:.0f}×{r['bid_qty']:.0f}" if r["bid"] is not None else "—"
        ask_s  = f"{r['ask']:.0f}×{r['ask_qty']:.0f}" if r["ask"] is not None else "—"
        book_s = f"{bid_s} / {ask_s}"
        xgb_s  = f"{r['xgb_ev']:>7.1f}" if r["xgb_ev"] is not None else "     —"

        pe = r["poly_edge"]
        xe = r["xgb_edge"]
        poly_e_s = f"{pe:>+.1f}" if pe != 0 else "    —"
        xgb_e_s  = (f"{xe:>+.1f}" if xe is not None and xe != 0 else "    —")

        print(
            f"  {i:<3}  {r['team']:<22}  {r['poly_ev']:>7.1f}  {xgb_s}  "
            f"{book_s:<22}  {poly_e_s:>9}  {xgb_e_s:>9}  {r['action']}"
        )

    print("═" * W)


# ── Table 2: Multipliers ──────────────────────────────────────────────────────

def _print_multiplier_table(mults: list[dict]) -> None:
    if not mults:
        return

    W = 108
    print()
    print("═" * W)
    print(f"  Multiplier contracts  (Goals × WeightedCards × Corners)")
    print()
    hdr = (
        f"  {'#':<3}  {'Match':<28}  {'Kickoff':<12}  {'Theo':>6}  {'EV':>5}  "
        f"{'Action':<14}  {'Book (B / A)':<20}  G / C / CK"
    )
    print(hdr)
    print("  " + "─" * (W - 2))

    for i, r in enumerate(mults, 1):
        kickoff = r.get("kickoff", "")[:10] if r.get("kickoff") else "—"
        theo    = r.get("theo")
        ev      = r.get("ev")
        action  = r.get("action", "")
        price   = r.get("order_price")
        bid     = r.get("best_bid")
        ask     = r.get("best_ask")
        bq      = r.get("best_bid_qty")
        aq      = r.get("best_ask_qty")
        eg      = r.get("e_goals",   0)
        ec      = r.get("e_cards",   0)
        eck     = r.get("e_corners", 0)
        mkt_tag = ("[MC]" if r.get("mkt_goals") and r.get("mkt_corners")
                   else "[M]" if r.get("mkt_goals")
                   else "[C]" if r.get("mkt_corners") else "")

        bid_s   = f"{bid:.0f}×{bq:.0f}" if bid is not None else "—"
        ask_s   = f"{ask:.0f}×{aq:.0f}" if ask is not None else "—"
        book_s  = f"{bid_s} / {ask_s}"
        theo_s  = f"{theo:.1f}{mkt_tag}" if theo is not None else "—"
        ev_s    = f"{ev:+.1f}" if ev is not None else "—"
        action_s = f"{action} {price:.0f}" if price is not None else action

        print(
            f"  {i:<3}  {r.get('match',''):<28}  {kickoff:<12}  {theo_s:>9}  {ev_s:>5}  "
            f"{action_s:<14}  {book_s:<20}  {eg:.1f} / {ec:.1f} / {eck:.1f}"
        )

    print("═" * W)
    print()


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    print("[INFO] Fetching latest XGBoost run from DagsHub...", file=sys.stderr)
    run_id, xgb_ts = _fetch_latest_run()
    print(f"[INFO] Run {run_id[:12]}  ts={xgb_ts}", file=sys.stderr)

    t_rows = _fetch_csv(run_id, "tournament_probabilities.csv")
    g_rows = _fetch_csv(run_id, "group_positions.csv")
    if not t_rows or not g_rows:
        print("[ERROR] Could not fetch XGBoost CSVs", file=sys.stderr)
        sys.exit(1)

    t_map = {r["team"]: r for r in t_rows if r.get("model_name") == "xgboost"}
    g_map = {r["team"]: r for r in g_rows if r.get("model_name") == "xgboost"}

    # Entertainment weights from teams.json
    teams_data = _load_json(_DATA / "teams.json")
    ent_map  = {t["key"]: t["ev_entertainment"] for t in teams_data.get("teams", [])}
    poly_evs = {t["key"]: t["ev_total"]         for t in teams_data.get("teams", [])}
    poly_ts  = teams_data.get("last_updated", "?")[:16].replace("T", " ") + "Z"

    # Build XGB EV map keyed by internal team name
    xgb_map: Dict[str, float] = {}
    for ext_name, t_row in t_map.items():
        int_name = EXT_TO_INTERNAL.get(ext_name, ext_name)
        g_row    = g_map.get(ext_name, {})
        ent_ev   = ent_map.get(int_name, ENTERTAINMENT_BONUS / 48)
        xgb_map[int_name] = _xgb_ev_total(t_row, g_row, ent_ev)

    # Load cached TycheMkt data
    tyche = _load_json(_DATA / "tychemkt_opps.json")
    opps       = tyche.get("opportunities", [])
    mults      = tyche.get("multipliers", [])
    scanned_at = tyche.get("scanned_at", "?")

    print(f"[INFO] {len(opps)} finish-value opps, {len(mults)} multipliers from cache ({scanned_at})", file=sys.stderr)

    _print_bracket_table(opps, poly_evs, xgb_map, xgb_ts, poly_ts, scanned_at)
    _print_multiplier_table(mults)


if __name__ == "__main__":
    main()
