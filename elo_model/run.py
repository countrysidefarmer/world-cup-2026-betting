#!/usr/bin/env python3
"""
Fetches live XGBoost WC 2026 probabilities from DagsHub MLflow and writes
data/elo_teams.json in the schema expected by tychemkt_scanner.py.

Replaces the Elo + Dixon-Coles simulation. All CLI arguments are accepted for
backwards compatibility but ignored — no simulation runs here.

Usage: /usr/bin/python3 -m elo_model.run [ignored args]
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Optional, Tuple

_ROOT = Path(__file__).parent.parent
_DATA = _ROOT / "data"
_OUTPUT_JSON = _DATA / "elo_teams.json"
_TEAMS_JSON  = _DATA / "teams.json"

DAGSHUB_BASE  = "https://dagshub.com/tomppa999/MLOps_FootballWCPredictor.mlflow"
MLFLOW_RUNS_URL = f"{DAGSHUB_BASE}/api/2.0/mlflow/runs/search"
EXP_ID = "667e431847fd43a882314ab4a5fb472b"
ARTIFACT_URL = (
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
}


def _fetch_url(url: str, timeout: int = 10) -> Optional[bytes]:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "xgb-runner/1.0"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read()
    except Exception:
        return None


def _fetch_latest_run_id() -> Tuple[str, str]:
    payload = json.dumps({
        "experiment_ids": ["1"],
        "max_results": 3,
        "order_by": ["start_time DESC"],
    }).encode()
    req = urllib.request.Request(
        MLFLOW_RUNS_URL,
        data=payload,
        headers={"Content-Type": "application/json", "User-Agent": "xgb-runner/1.0"},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            data = json.load(r)
        runs = data.get("runs", [])
        if runs:
            info = runs[0]["info"]
            run_id = info["run_id"]
            ts_ms = int(info.get("start_time", 0))
            ts = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).strftime("%Y-%m-%dT%H:%MZ")
            return run_id, ts
    except Exception as e:
        print(f"[WARN] DagsHub run query failed ({e}), using fallback", file=sys.stderr)
    return FALLBACK_RUN_IDS[0], "unknown"


def _read_csv(data: bytes) -> list[dict]:
    lines = data.decode("utf-8").splitlines()
    if not lines:
        return []
    headers = [h.strip() for h in lines[0].split(",")]
    rows = []
    for line in lines[1:]:
        if not line.strip():
            continue
        vals = line.split(",")
        rows.append({headers[i]: vals[i].strip() if i < len(vals) else "" for i in range(len(headers))})
    return rows


def _fetch_csv(run_id: str, filename: str) -> Optional[list[dict]]:
    url = ARTIFACT_URL.format(run_id=run_id, filename=filename)
    data = _fetch_url(url)
    if data:
        rows = _read_csv(data)
        if rows:
            return rows
    print(f"[WARN] DagsHub artifact {filename} failed, trying GitHub cache", file=sys.stderr)
    data = _fetch_url(f"{GITHUB_FALLBACK}/{filename}")
    if data:
        rows = _read_csv(data)
        if rows:
            print("[WARN] Using GitHub offline cache (may be pre-tournament)", file=sys.stderr)
            return rows
    for fid in FALLBACK_RUN_IDS:
        if fid == run_id:
            continue
        data = _fetch_url(ARTIFACT_URL.format(run_id=fid, filename=filename))
        if data:
            rows = _read_csv(data)
            if rows:
                return rows
    return None


def _load_teams_meta() -> Dict[str, dict]:
    """Load team metadata (name, flag, group, ev_entertainment) from data/teams.json."""
    if not _TEAMS_JSON.exists():
        return {}
    with open(_TEAMS_JSON) as f:
        d = json.load(f)
    return {t["key"]: t for t in d.get("teams", [])}


def _compute_ev(t: dict, g: dict) -> dict:
    p_win    = float(t.get("p_winner", 0))
    p_final  = float(t.get("p_final", 0))
    p_sf     = float(t.get("p_sf", 0))
    p_qf     = float(t.get("p_qf", 0))
    p_r16    = float(t.get("p_r16", 0))
    p_r32    = float(t.get("p_r32", 0))

    p_2nd      = max(p_final - p_win, 0)
    p_3rd      = max((p_sf - p_final) / 2, 0)
    p_4th_ko   = max((p_sf - p_final) / 2, 0)
    p_qf_exit  = max(p_qf - p_sf, 0)
    p_r16_exit = max(p_r16 - p_qf, 0)
    p_r32_exit = max(p_r32 - p_r16, 0)
    p_group_exit = max(1.0 - p_r32, 0)
    p_wooden_spoon = p_group_exit / 16.0

    ev_tournament = (
        p_win          * TOURNAMENT_PTS["1st"]
        + p_2nd        * TOURNAMENT_PTS["2nd"]
        + p_3rd        * TOURNAMENT_PTS["3rd"]
        + p_4th_ko     * TOURNAMENT_PTS["4th"]
        + p_qf_exit    * TOURNAMENT_PTS["qf_exit"]
        + p_r16_exit   * TOURNAMENT_PTS["r16_exit"]
        + p_r32_exit   * TOURNAMENT_PTS["r32_exit"]
        + p_group_exit * TOURNAMENT_PTS["group_exit"]
        + p_wooden_spoon * TOURNAMENT_PTS["wooden_spoon"]
    )

    p_1st_grp = float(g.get("p_1st", 0))
    p_2nd_grp = float(g.get("p_2nd", 0))
    p_4th_grp = float(g.get("p_4th", 0))
    ev_group = p_1st_grp * GROUP_PTS[1] + p_2nd_grp * GROUP_PTS[2] + p_4th_grp * GROUP_PTS[4]

    return {
        "ev_tournament": ev_tournament,
        "ev_group": ev_group,
        "p_win": p_win,
        "p_final": p_final,
        "p_sf": p_sf,
        "p_qf": p_qf,
        "p_r16": p_r16,
        "p_r32": p_r32,
        "p_group_exit": p_group_exit,
        "p_2nd": p_2nd,
        "p_3rd": p_3rd,
        "p_4th_ko": p_4th_ko,
        "p_qf_exit": p_qf_exit,
        "p_r16_exit": p_r16_exit,
        "p_r32_exit": p_r32_exit,
        "p_wooden_spoon": p_wooden_spoon,
        "p_1st_grp": p_1st_grp,
        "p_2nd_grp": p_2nd_grp,
        "p_4th_grp": p_4th_grp,
    }


def main() -> None:
    # Accept all old args for backwards compatibility
    parser = argparse.ArgumentParser(description="XGBoost WC 2026 model (DagsHub)")
    parser.add_argument("--refresh",    action="store_true")
    parser.add_argument("--no-refresh", action="store_true")
    parser.add_argument("--n-sims",     type=int, default=50_000)
    parser.add_argument("--sigma-sims", type=int, default=5_000)
    parser.add_argument("--no-cal",     action="store_true")
    parser.add_argument("--compare-poly", action="store_true")
    parser.parse_args()

    print("[INFO] Fetching latest XGBoost run from DagsHub MLflow...", file=sys.stderr)
    run_id, ts = _fetch_latest_run_id()
    print(f"[INFO] Run: {run_id[:12]}...  inference_ts={ts}", file=sys.stderr)

    t_rows = _fetch_csv(run_id, "tournament_probabilities.csv")
    g_rows = _fetch_csv(run_id, "group_positions.csv")

    if not t_rows or not g_rows:
        print("[ERROR] Could not fetch XGBoost probability CSVs", file=sys.stderr)
        sys.exit(1)

    t_map = {r["team"]: r for r in t_rows if r.get("model_name") == "xgboost"}
    g_map = {r["team"]: r for r in g_rows if r.get("model_name") == "xgboost"}

    meta = _load_teams_meta()

    teams_output = []
    for ext_name, t_row in t_map.items():
        int_name = EXT_TO_INTERNAL.get(ext_name, ext_name)
        g_row = g_map.get(ext_name, {})
        m = meta.get(int_name, {})

        ev = _compute_ev(t_row, g_row)
        ent_ev = m.get("ev_entertainment", ENTERTAINMENT_BONUS / 48)
        ev_total = ev["ev_tournament"] + ev["ev_group"] + ent_ev

        teams_output.append({
            "name":             m.get("name", int_name),
            "key":              int_name,
            "flag":             m.get("flag", ""),
            "group":            m.get("group", "?"),
            "data_quality":     "xgboost",
            "ev_total":         round(ev_total, 2),
            "ev_group":         round(ev["ev_group"], 2),
            "ev_tournament":    round(ev["ev_tournament"], 2),
            "ev_entertainment": round(ent_ev, 2),
            "elo":              0,
            "elo_adjusted":     0,
            "probs": {
                "p_win":       round(ev["p_win"], 4),
                "p_final":     round(ev["p_final"], 4),
                "p_sf":        round(ev["p_sf"], 4),
                "p_qf":        round(ev["p_qf"], 4),
                "p_r16":       round(ev["p_r16"], 4),
                "p_advance":   round(ev["p_r32"], 4),
                "p_win_group": round(ev["p_1st_grp"], 4),
            },
            "breakdown": {
                "p_1st":          round(ev["p_win"], 4),
                "p_2nd":          round(ev["p_2nd"], 4),
                "p_3rd":          round(ev["p_3rd"], 4),
                "p_4th":          round(ev["p_4th_ko"], 4),
                "p_qf_exit":      round(ev["p_qf_exit"], 4),
                "p_r16_exit":     round(ev["p_r16_exit"], 4),
                "p_r32_exit":     round(ev["p_r32_exit"], 4),
                "p_group_exit":   round(ev["p_group_exit"], 4),
                "p_grp_1st":      round(ev["p_1st_grp"], 4),
                "p_grp_2nd":      round(ev["p_2nd_grp"], 4),
                "p_grp_3rd":      0.0,
                "p_grp_4th":      round(ev["p_4th_grp"], 4),
                "p_wooden_spoon": round(ev["p_wooden_spoon"], 5),
            },
        })

    teams_output.sort(key=lambda r: r["ev_total"], reverse=True)

    payload = {
        "last_updated":     datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "inference_ts":     ts,
        "tournament_state": "group_stage",
        "source":           "xgboost/tomppa999/MLOps_FootballWCPredictor",
        "elo_sigma":        "n/a",
        "sigma_meaning":    f"XGBoost (DagsHub MLflow, inference {ts})",
        "mlflow_run_id":    run_id,
        "teams":            teams_output,
    }
    _OUTPUT_JSON.write_text(json.dumps(payload, indent=2, ensure_ascii=False))
    print(f"[INFO] Wrote {len(teams_output)} teams → {_OUTPUT_JSON}", file=sys.stderr)

    print(f"\nTop 10 by EV total  (XGBoost, inference {ts}):")
    print(f"{'Rank':<5} {'Team':<28} {'EV':>6}  {'p_win':>7}")
    for i, t in enumerate(teams_output[:10], 1):
        print(f"{i:<5} {t['name']:<28} {t['ev_total']:>6.2f}  {t['probs']['p_win']:>7.2%}")


if __name__ == "__main__":
    main()
