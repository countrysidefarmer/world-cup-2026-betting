#!/usr/bin/env python3
"""
TycheMkt WC 2026 Scanner — ranks EV opportunities vs Polymarket theos.

Usage:
    python tychemkt_scanner.py [--refresh]

    --refresh   Re-run fetch_data.py to pull fresh Polymarket prices first.

Credentials (in priority order):
    1. TYCHEMKT_EMAIL / TYCHEMKT_PASSWORD environment variables
    2. .env file in the same directory
    3. Interactive prompt
"""

import getpass
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests

import multiplier_model as mm

# ── Config ──────────────────────────────────────────────────────────────────

BASE_URL      = "https://api.tychemkt.com"
_GAMMA_BASE   = "https://gamma-api.polymarket.com"
DATA_FILE     = Path(__file__).parent / "data" / "teams.json"
OUT_FILE      = Path(__file__).parent / "data" / "tychemkt_opps.json"
SNAPSHOT_FILE = Path(__file__).parent / "data" / "model_snapshots.json"

# ── Credentials ──────────────────────────────────────────────────────────────

def _load_dotenv() -> None:
    env_path = Path(__file__).parent / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

def get_credentials() -> Tuple[str, str]:
    _load_dotenv()
    email    = os.environ.get("TYCHEMKT_EMAIL", "fieldenthomas@gmail.com")
    password = os.environ.get("TYCHEMKT_PASSWORD", "")
    if not password:
        password = getpass.getpass(f"TycheMkt password for {email}: ")
    return email, password

# ── TycheMkt client ──────────────────────────────────────────────────────────

class TycheMktError(Exception):
    pass

class TycheMktClient:
    def __init__(self) -> None:
        self.session = requests.Session()
        self.session.headers.update({
            "Content-Type": "application/json",
            "Accept":       "application/json",
        })

    def _post(self, service: str, method: str, body: Optional[Dict] = None) -> Dict:
        url  = f"{BASE_URL}/tyche.v1.{service}/{method}"
        resp = self.session.post(url, json=body or {}, timeout=15)
        if resp.status_code not in (200, 201):
            try:
                detail = resp.json().get("message", resp.text[:300])
            except Exception:
                detail = resp.text[:300]
            raise TycheMktError(f"{service}/{method} → {resp.status_code}: {detail}")
        return resp.json()

    def _list_all(self, service: str, method: str, result_key: str,
                  body: Optional[Dict] = None) -> List[Dict]:
        items: List[Dict] = []
        page_token = ""
        while True:
            req = dict(body or {})
            if page_token:
                req["pageToken"] = page_token
            resp = self._post(service, method, req)
            items.extend(resp.get(result_key, []))
            page_token = resp.get("nextPageToken", "")
            if not page_token:
                break
        return items

    def login(self, email: str, password: str) -> None:
        self._post("AuthService", "Login", {"email": email, "password": password})

    def list_events(self) -> List[Dict]:
        return self._list_all("QueryService", "ListEvents", "events")

    def list_contracts(self, event_id: str) -> List[Dict]:
        return self._list_all("QueryService", "ListContracts", "contracts",
                              {"eventId": event_id})

    def get_order_book(self, contract_id: str) -> Dict:
        return self._post("QueryService", "GetOrderBook", {"contractId": contract_id})

# ── Order book parsing ────────────────────────────────────────────────────────

def _parse_decimal(v: Any) -> Optional[float]:
    if v is None:
        return None
    if isinstance(v, dict):
        try:
            return float(v["value"])
        except (KeyError, ValueError):
            return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None

def _parse_levels(levels: List[Dict]) -> List[Tuple[float, float]]:
    """Return list of (price, qty) from a bids or asks array."""
    result = []
    for lvl in levels:
        p = _parse_decimal(lvl.get("price"))
        q = _parse_decimal(lvl.get("quantity"))
        if p is not None and q is not None:
            result.append((p, q))
    return result

def _parse_book(ob: Dict) -> Tuple[List[Tuple[float, float]], List[Tuple[float, float]]]:
    """
    Returns (bids, asks) each as [(price, qty)] sorted:
      bids descending (best bid first), asks ascending (best ask first).
    """
    book = ob.get("orderBook", ob)
    bids = sorted(_parse_levels(book.get("bids", [])), key=lambda x: -x[0])
    asks = sorted(_parse_levels(book.get("asks", [])), key=lambda x:  x[0])
    return bids, asks

def _best_action(
    theo: float,
    bids: List[Tuple[float, float]],
    asks: List[Tuple[float, float]],
) -> Tuple[float, str, float]:
    """
    Return (ev, action_label, order_price) for the highest-EV actionable order.

    BUY actions (theo > price):
      LIFT       — take best ask immediately
      NEW BID    — post at best_bid + 1 (new best level, only if room below ask)
      JOIN BID   — post at best_bid (only if best bid qty < 10)

    SELL actions (price > theo):
      HIT        — hit best bid immediately
      NEW ASK    — post at best_ask - 1 (new best level, only if room above bid)
      JOIN ASK   — post at best_ask (only if best ask qty < 10)

    Returns (0.0, '', 0.0) if no actionable opportunity found.
    """
    candidates: List[Tuple[float, str, float]] = []

    best_bid_p = bids[0][0] if bids else None
    best_bid_q = bids[0][1] if bids else None
    best_ask_p = asks[0][0] if asks else None
    best_ask_q = asks[0][1] if asks else None

    # ── BUY side ──────────────────────────────────────────────────────────────
    if best_ask_p is not None:
        ev = theo - best_ask_p
        if ev > 0:
            candidates.append((ev, "LIFT", best_ask_p))

    if best_bid_p is not None:
        new_bid = best_bid_p + 1
        # New level only if it doesn't cross the ask
        if best_ask_p is None or new_bid < best_ask_p:
            ev = theo - new_bid
            if ev > 0:
                candidates.append((ev, "NEW BID", new_bid))

        # Join only if thin queue
        if best_bid_q is not None and best_bid_q < 10:
            ev = theo - best_bid_p
            if ev > 0:
                candidates.append((ev, "JOIN BID", best_bid_p))

    # ── SELL side ─────────────────────────────────────────────────────────────
    if best_bid_p is not None:
        ev = best_bid_p - theo
        if ev > 0:
            candidates.append((ev, "HIT", best_bid_p))

    if best_ask_p is not None:
        new_ask = best_ask_p - 1
        # New level only if it doesn't cross the bid
        if best_bid_p is None or new_ask > best_bid_p:
            ev = new_ask - theo
            if ev > 0:
                candidates.append((ev, "NEW ASK", new_ask))

        # Join only if thin queue
        if best_ask_q is not None and best_ask_q < 10:
            ev = best_ask_p - theo
            if ev > 0:
                candidates.append((ev, "JOIN ASK", best_ask_p))

    if not candidates:
        return 0.0, "", 0.0

    return max(candidates, key=lambda x: x[0])

# ── Team name aliases ─────────────────────────────────────────────────────────

_ALIASES: Dict[str, str] = {
    "united states":                "USA",
    "usa":                          "USA",
    "ivory coast":                  "Ivory Coast",
    "côte d'ivoire":                "Ivory Coast",
    "cote d ivoire":                "Ivory Coast",
    "south korea":                  "South Korea",
    "korea republic":               "South Korea",
    "dr congo":                     "DR Congo",
    "democratic republic of congo": "DR Congo",
    "drc":                          "DR Congo",
    "czech republic":               "Czechia",
    "czechia":                      "Czechia",
    "turkey":                       "Türkiye",
    "türkiye":                      "Türkiye",
    "cape verde islands":           "Cape Verde",
    "cape verde":                   "Cape Verde",
    "bosnia and herzegovina":       "Bosnia & Herzegovina",
    "bosnia & herzegovina":         "Bosnia & Herzegovina",
    "bosnia":                       "Bosnia & Herzegovina",
    "curacao":                      "Curaçao",
    "curaçao":                      "Curaçao",
    "south africa":                 "South Africa",
    "saudi arabia":                 "Saudi Arabia",
    "new zealand":                  "New Zealand",
}

_name_index: Dict[str, str] = {}  # normalised lower → canonical key

def _build_name_index(teams: List[Dict]) -> None:
    _name_index.clear()
    for t in teams:
        key = t["key"]
        _name_index[t["name"].lower()] = key
        _name_index[t["key"].lower()]  = key
    for alias, canonical in _ALIASES.items():
        _name_index[alias.lower()] = canonical

def _team_from_title(title: str) -> Optional[str]:
    """Match a bare team-name title (e.g. 'England') against the index."""
    return _name_index.get(title.strip().lower())

# ── Contract classification ───────────────────────────────────────────────────

def _classify(contract: Dict) -> str:
    """
    Returns one of:
      'finish_value'  — team finish-value contract; theo = ev_total
      'multiplier'    — match Goals×Cards×Corners exotic; no model yet
      'total'         — tournament total goals; skip
      'unknown'       — skip
    """
    meta = contract.get("metadata") or {}
    kind = meta.get("kind", "")

    if kind == "multiplier":
        return "multiplier"
    if kind == "total":
        return "total"

    # No kind field → finish value contract if title is a bare team name
    title = contract.get("title", "").strip()
    if _team_from_title(title):
        return "finish_value"

    return "unknown"

# ── Theos loading ─────────────────────────────────────────────────────────────

def load_theos(refresh: bool) -> Dict[str, Dict]:
    if refresh:
        print("Refreshing Polymarket theos...")
        script = Path(__file__).parent / "fetch_data.py"
        result = subprocess.run(
            [sys.executable, str(script)],
            capture_output=True, text=True
        )
        if result.returncode != 0:
            print(f"  Warning: fetch_data.py returned {result.returncode}:\n"
                  f"  {result.stderr.strip()[:300]}", file=sys.stderr)
        else:
            print("  Polymarket theos updated.")

    if not DATA_FILE.exists():
        sys.exit(f"Error: {DATA_FILE} not found. Run fetch_data.py first, or use --refresh.")

    with open(DATA_FILE) as f:
        data = json.load(f)

    teams    = data.get("teams", [])
    team_map = {t["key"]: t for t in teams}
    _build_name_index(teams)

    print(f"  Theos: {len(teams)} teams  (Polymarket snapshot: {data.get('last_updated','?')})")
    return team_map

# ── Scanner ───────────────────────────────────────────────────────────────────

def scan(
    client: TycheMktClient, team_map: Dict[str, Dict]
) -> Tuple[List[Dict], List[Dict], Dict[str, Optional[float]], Dict[str, str], Dict[str, Any]]:
    """Returns (finish_value_opportunities, multiplier_contracts_raw, mid_map, cid_to_team, book_map).

    mid_map    : team_key → live TycheMkt mid price (None if no book). All teams.
    cid_to_team: contract_id → team_key, for all finish-value contracts.
    book_map   : team_key → (bids, asks) for reuse by Elo evaluation.
    """
    print("\nFetching TycheMkt events...")
    events = client.list_events()
    print(f"  {len(events)} event(s) found")

    wc_events = [
        e for e in events
        if any(kw in (e.get("title", "") + " " + e.get("slug", "")).lower()
               for kw in ("world cup", "wc 2026", "wc2026", "fifa"))
    ] or events

    opportunities: List[Dict] = []
    multiplier_contracts: List[Dict] = []
    mid_map: Dict[str, Optional[float]] = {}
    cid_to_team: Dict[str, str] = {}
    book_map: Dict[str, Any] = {}
    no_book = 0

    for event in wc_events:
        contracts = client.list_contracts(event["id"])

        for c in contracts:
            if c.get("status") == "CONTRACT_STATUS_CLOSED":
                continue

            kind = _classify(c)
            title = c.get("title", "").strip()

            if kind == "multiplier":
                multiplier_contracts.append(c)
                continue
            if kind in ("total", "unknown"):
                continue
            if kind != "finish_value":
                continue

            team_key = _team_from_title(title)
            if not team_key or team_key not in team_map:
                continue

            cid_to_team[c["id"]] = team_key   # collect for position lookup

            t = team_map[team_key]
            theo = t.get("ev_total")
            if theo is None:
                continue

            try:
                ob = client.get_order_book(c["id"])
            except TycheMktError:
                continue

            bids, asks = _parse_book(ob)
            book_map[team_key] = (bids, asks)
            if not bids and not asks:
                no_book += 1
                mid_map[team_key] = None
                continue

            # Collect mid for position tracker (all teams, not just EV > 1)
            best_bid_p = bids[0][0] if bids else None
            best_ask_p = asks[0][0] if asks else None
            if best_bid_p is not None and best_ask_p is not None:
                mid_map[team_key] = (best_bid_p + best_ask_p) / 2.0
            elif best_bid_p is not None:
                mid_map[team_key] = float(best_bid_p)
            else:
                mid_map[team_key] = float(best_ask_p)  # type: ignore[arg-type]

            # Skip eliminated teams — their finish value is settled, no live market interest
            if t.get("probs", {}).get("p_advance", 0) == 0:
                continue

            ev, action, order_price = _best_action(theo, bids, asks)

            if ev <= 0.5:
                continue

            best_bid_q = bids[0][1] if bids else None
            best_ask_q = asks[0][1] if asks else None

            meta = c.get("metadata") or {}
            opportunities.append({
                "contract_id":  c["id"],
                "contract":     title,
                "team":         team_key,
                "group":        meta.get("group", t.get("group", "?")),
                "ev_total":     round(theo, 2),
                "p_win":        round(t.get("probs", {}).get("p_win", 0), 4),
                "best_bid":     best_bid_p,
                "best_bid_qty": best_bid_q,
                "best_ask":     best_ask_p,
                "best_ask_qty": best_ask_q,
                "ev":           round(ev, 2),
                "action":       action,
                "order_price":  order_price,
                "data_quality": t.get("data_quality", "?"),
            })

    print(f"  {len(multiplier_contracts)} multiplier contracts found (pricing below)")
    print(f"  {no_book} finish-value contracts with empty order book")

    return sorted(opportunities, key=lambda x: x["ev"], reverse=True), multiplier_contracts, mid_map, cid_to_team, book_map

# ── Position helpers ──────────────────────────────────────────────────────────

MY_USER_ID = "fb7acb9a-d949-481a-a211-d720dd20e13d"


def fetch_tyche_positions(
    client: TycheMktClient,
    cid_to_team: Dict[str, str],
) -> Dict[str, int]:
    """Fetch net positions per team from TycheMkt portfolio (ListPositions with userId filter)."""
    net: Dict[str, int] = {}
    resp = client._post("QueryService", "ListPositions", {"userId": MY_USER_ID})
    for p in resp.get("positions", []):
        if p.get("userId") != MY_USER_ID:
            continue
        cid  = p.get("contractId", "")
        team = cid_to_team.get(cid)
        if not team:
            continue
        qty = float((p.get("netQuantity") or {}).get("value", 0))
        if qty == 0:
            continue
        net[team] = int(qty)
    return net


def fetch_open_orders(
    client: TycheMktClient,
    cid_to_team: Dict[str, str],
) -> Dict[str, str]:
    """
    Returns {team: 'BUY'|'SELL'} for teams with a resting order.
    Silently returns {} if the endpoint doesn't exist or fails.
    """
    result: Dict[str, str] = {}
    try:
        resp = client._post("QueryService", "ListOrders", {"userId": MY_USER_ID})
        for o in resp.get("orders", []):
            if o.get("userId") != MY_USER_ID:
                continue
            cid  = o.get("contractId", "")
            team = cid_to_team.get(cid)
            if not team:
                continue
            side = (o.get("side") or "").upper()  # "BUY" or "SELL"
            if side in ("BUY", "SELL"):
                result[team] = side
    except Exception:
        pass
    return result


def build_combined_positions(tyche_net: Dict[str, int]) -> Dict[str, int]:
    return dict(tyche_net)


# ── Report ────────────────────────────────────────────────────────────────────

def print_report(opps: List[Dict], positions: Dict[str, int]) -> None:
    if not opps:
        print("\n  No opportunities (EV > 1) found.")
        return

    print(f"\n{'═'*90}")
    print(f"  {'#':>3}  {'Team':<22}  {'Theo':>5}  {'EV':>5}  {'Action':<14}  {'Book (B / A)':<22}  Pos")
    print(f"  {'─'*84}")

    for i, o in enumerate(opps, 1):
        bid_s = (f"{o['best_bid']:.0f}×{o['best_bid_qty']:.0f}"
                 if o["best_bid"] is not None else "—")
        ask_s = (f"{o['best_ask']:.0f}×{o['best_ask_qty']:.0f}"
                 if o["best_ask"] is not None else "—")
        book_s = f"{bid_s} / {ask_s}"
        action_s = f"{o['action']} {o['order_price']:.0f}"
        pos = positions.get(o["team"], 0)
        pos_s = f"{pos:+d}" if pos else "—"
        limit_flag = " !" if abs(pos) >= 40 else ""
        print(
            f"  {i:>3}  {o['team']:<22}  {o['ev_total']:>5.1f}  {o['ev']:>5.1f}  {action_s:<14}  {book_s:<22}  {pos_s}{limit_flag}"
        )

    print(f"{'═'*90}")


def print_combined_report(
    poly_opps: List[Dict],
    team_map: Dict[str, Dict],
    elo_map: Dict[str, Dict],
    book_map: Dict[str, Any],
    positions: Dict[str, int],
    open_orders: Dict[str, str],
) -> None:
    """Single bracket table: all teams where abs(Poly edge) > 1."""
    _BUY_ACTIONS  = {"LIFT", "JOIN BID", "NEW BID"}
    _SELL_ACTIONS = {"HIT", "JOIN ASK", "NEW ASK"}

    rows = []
    seen: set = set()

    for o in poly_opps:
        team = o["team"]
        seen.add(team)
        bids, asks = book_map.get(team, ([], []))
        bid0 = bids[0][0] if bids else None
        ask0 = asks[0][0] if asks else None
        mid  = (bid0 + ask0) / 2 if bid0 is not None and ask0 is not None else None
        xgb_t    = elo_map.get(team)
        xgb_theo = xgb_t["ev_total"] if xgb_t else None
        poly_edge = o["ev_total"] - mid if mid is not None else None
        if poly_edge is None or abs(poly_edge) <= 1:
            continue
        rows.append({
            "team":       team,
            "poly_theo":  o["ev_total"],
            "xgb_theo":   xgb_theo,
            "poly_edge":  poly_edge,
            "xgb_edge":   xgb_theo - mid if xgb_theo is not None and mid is not None else None,
            "action":     o["action"],
            "order_price": o["order_price"],
            "best_bid":   o["best_bid"],
            "best_bid_qty": o["best_bid_qty"],
            "best_ask":   o["best_ask"],
            "best_ask_qty": o["best_ask_qty"],
        })

    rows.sort(key=lambda r: -abs(r["poly_edge"] or 0))

    if not rows:
        print("\n  No opportunities found.")
        return

    W = 114
    print(f"\n{'═'*W}")
    print(f"  {'#':>3}  {'Team':<22}  {'Poly':>6}  {'PolyEdge':>9}  {'Action':<14}  {'Book (B / A)':<22}  {'Pos':>4}  {'':>2}  {'XGB':>6}  {'XGBEdge':>9}")
    print(f"  {'─'*110}")
    for i, r in enumerate(rows, 1):
        xgb_s  = f"{r['xgb_theo']:>6.1f}" if r["xgb_theo"] is not None else "     —"
        pe     = r["poly_edge"]
        xe     = r["xgb_edge"]
        pe_s   = f"{pe:>+.1f}" if pe is not None else "      —"
        xe_s   = f"{xe:>+.1f}" if xe is not None else "      —"
        bid_s  = (f"{r['best_bid']:.0f}×{r['best_bid_qty']:.0f}"
                  if r["best_bid"] is not None else "—")
        ask_s  = (f"{r['best_ask']:.0f}×{r['best_ask_qty']:.0f}"
                  if r["best_ask"] is not None else "—")
        book_s    = f"{bid_s} / {ask_s}"
        action_s  = f"{r['action']} {r['order_price']:.0f}"
        pos        = positions.get(r["team"], 0)
        pos_s      = f"{pos:+d}" if pos else "—"
        at_limit   = abs(pos) >= 40
        # Tick: ✓ if resting order matches wanted direction, ⚠ if at position limit
        action_dir = r["action"].split()[0].upper()  # e.g. "LIFT", "HIT", "JOIN", "NEW"
        wanted_side = "BUY" if action_dir in ("LIFT", "JOIN", "NEW") and "ASK" not in r["action"].upper() else "SELL"
        has_order  = open_orders.get(r["team"]) == wanted_side
        tick = "⚠" if at_limit else ("✓" if has_order else "·")
        print(
            f"  {i:>3}  {r['team']:<22}  {r['poly_theo']:>6.1f}  "
            f"{pe_s:>9}  {action_s:<14}  {book_s:<22}  {pos_s:<6}  {tick}  {xgb_s}  {xe_s:>9}"
        )
    print(f"{'═'*W}")


# ── Position tracker ─────────────────────────────────────────────────────────

_REFEREES_FILE = Path(__file__).parent / "data" / "referees.json"


def _load_referees() -> Dict:
    if not _REFEREES_FILE.exists():
        return {}
    with open(_REFEREES_FILE) as f:
        return json.load(f)


# ── Polymarket market data for multiplier model ──────────────────────────────

_FIFA_CODES: Dict[str, str] = {
    "Algeria":                  "alg",
    "Argentina":                "arg",
    "Australia":                "aus",
    "Austria":                  "aut",
    "Belgium":                  "bel",
    "Bosnia and Herzegovina":   "bih",
    "Bosnia & Herzegovina":     "bih",
    "Brazil":                   "bra",
    "Cabo Verde":               "cvi",
    "Canada":                   "can",
    "Colombia":                 "col",
    "Croatia":                  "hrv",
    "Curacao":                  "kor",
    "Curaçao":                  "kor",
    "Czechia":                  "cze",
    "Czech Republic":           "cze",
    "DR Congo":                 "cdr",
    "Ecuador":                  "ecu",
    "Egypt":                    "egy",
    "England":                  "eng",
    "France":                   "fra",
    "Germany":                  "ger",
    "Ghana":                    "gha",
    "Haiti":                    "hai",
    "Iran":                     "irn",
    "Iraq":                     "irq",
    "Ivory Coast":              "civ",
    "Côte d'Ivoire":            "civ",
    "Japan":                    "jpn",
    "Jordan":                   "jor",
    "South Korea":              "kr",
    "Korea Republic":           "kr",
    "Mexico":                   "mex",
    "Morocco":                  "mar",
    "Netherlands":              "nld",
    "New Zealand":              "nzl",
    "Norway":                   "nor",
    "Panama":                   "pan",
    "Paraguay":                 "par",
    "Portugal":                 "prt",
    "Qatar":                    "qat",
    "Saudi Arabia":             "ksa",
    "Scotland":                 "sco",
    "Senegal":                  "sen",
    "South Africa":             "rsa",
    "Spain":                    "esp",
    "Sweden":                   "swe",
    "Switzerland":              "che",
    "Tunisia":                  "tun",
    "Turkiye":                  "tur",
    "Türkiye":                  "tur",
    "Turkey":                   "tur",
    "USA":                      "usa",
    "United States":            "usa",
    "Uruguay":                  "ury",
    "Uzbekistan":               "uzb",
}


def _poisson_over(lam: float, threshold: float) -> float:
    """P(X > threshold) for Poisson(lam) where threshold is a half-integer like 9.5."""
    import math
    k = int(threshold)  # e.g. 9 for threshold=9.5, so P(X >= 10) = 1 - P(X <= 9)
    return 1.0 - sum(math.exp(-lam) * (lam ** i) / math.factorial(i) for i in range(k + 1))


def _fit_poisson_lambda(
    ou_pts: List[Tuple[float, float, float]],
    lam_min: float = 0.5,
    lam_max: float = 20.0,
    step: float = 0.1,
) -> float:
    """Grid search for Poisson lambda minimising volume-weighted MSE vs O/U over-probabilities."""
    best_lam, best_err = (lam_min + lam_max) / 2, float("inf")
    raw_min = int(lam_min / step)
    raw_max = int(lam_max / step) + 1
    for raw in range(raw_min, raw_max):
        lam = raw * step
        err = sum(
            (_poisson_over(lam, t) - p) ** 2 * max(v, 1.0)
            for t, p, v in ou_pts
        )
        if err < best_err:
            best_err = err
            best_lam = lam
    return best_lam


def _goals_lambda_from_ou_markets(markets: list) -> Optional[float]:
    """Fit Poisson lambda to total goals O/U markets from the -more-markets event.

    Matches questions of the form "TeamA vs. TeamB: O/U X.5" — the colon-space-OU
    pattern without any team prefix or 'Half' qualifier identifies total goals only.
    Uses volume-weighted Poisson fit across all available thresholds.
    """
    ou_pts: List[Tuple[float, float, float]] = []
    for m in markets:
        q = m.get("question", "")
        if "Half" in q or "Spread" in q:
            continue
        mt = re.search(r": O/U (\d+\.5)$", q)
        if mt:
            threshold = float(mt.group(1))
            prices = json.loads(m.get("outcomePrices", "[0,1]"))
            over_p = float(prices[0])
            vol = float(m.get("volume", 0))
            if vol < 100:          # ignore automated quotes with no real money behind them
                continue
            if 0 < over_p < 1:
                ou_pts.append((threshold, over_p, vol))

    if not ou_pts:
        return None

    return _fit_poisson_lambda(ou_pts, lam_min=0.3, lam_max=9.0)


def _fit_corners_lambda(ou_pts: List[Tuple[float, float, float]]) -> float:
    return _fit_poisson_lambda(ou_pts, lam_min=4.0, lam_max=20.0)


def _fetch_match_lambdas(team_a: str, team_b: str, kickoff_str: str) -> Dict[str, float]:
    """Fetch Polymarket total goals + corners O/U markets and return market-implied lambdas.

    Goals: from the '-more-markets' event (total goals O/U, high volume, all matches).
    Corners: from the '-total-corners' event (where available).
    Returns dict with zero or more of: 'lam_goals', 'lam_corners'.
    Returns {} on any failure — callers fall back to model defaults.
    """
    code_a = _FIFA_CODES.get(team_a)
    code_b = _FIFA_CODES.get(team_b)
    if not code_a or not code_b or not kickoff_str:
        return {}
    date_str = kickoff_str[:10]
    base_slug = f"fifwc-{code_a}-{code_b}-{date_str}"
    result: Dict[str, float] = {}

    # ── Total goals O/U → Poisson lambda (from -more-markets) ────────────────
    try:
        r = requests.get(
            f"{_GAMMA_BASE}/events",
            params={"slug": f"{base_slug}-more-markets"},
            timeout=10,
        )
        if r.status_code == 200:
            data = r.json()
            if data:
                lam_goals = _goals_lambda_from_ou_markets(data[0].get("markets", []))
                if lam_goals is not None:
                    result["lam_goals"] = round(lam_goals, 2)
    except Exception:
        pass

    # ── Corners O/U → Poisson lambda (from -total-corners) ───────────────────
    try:
        r = requests.get(
            f"{_GAMMA_BASE}/events",
            params={"slug": f"{base_slug}-total-corners"},
            timeout=10,
        )
        if r.status_code == 200:
            data = r.json()
            if data:
                ou_pts: List[Tuple[float, float, float]] = []
                for m in data[0].get("markets", []):
                    q = m.get("question", "")
                    mt = re.search(r"O/U (\d+\.5) Total Corners", q)
                    if mt and "Half" not in q:
                        threshold = float(mt.group(1))
                        prices = json.loads(m.get("outcomePrices", "[0,1]"))
                        over_p = float(prices[0])
                        vol = float(m.get("volume", 0))
                        if vol < 100:      # ignore automated quotes with no real money behind them
                            continue
                        ou_pts.append((threshold, over_p, vol))
                if ou_pts:
                    result["lam_corners"] = round(_fit_corners_lambda(ou_pts), 1)
    except Exception:
        pass

    return result


# ── Multiplier scanner ────────────────────────────────────────────────────────

def scan_multipliers(
    client: TycheMktClient,
    team_map: Dict[str, Dict],
    multiplier_contracts: List[Dict],
) -> List[Dict]:
    """Price each multiplier contract and return all results (not just EV > 1)."""
    results: List[Dict] = []
    ref_data = _load_referees()
    now_utc  = datetime.now(timezone.utc)

    for c in multiplier_contracts:
        meta  = c.get("metadata") or {}
        title = c.get("title", "").strip()

        # Filter: skip if match has already kicked off
        kickoff_str = meta.get("kickoff", "")
        if kickoff_str:
            try:
                kickoff_dt = datetime.fromisoformat(kickoff_str.replace("Z", "+00:00"))
                if kickoff_dt <= now_utc:
                    continue
            except ValueError:
                pass

        # Parse teams from metadata (preferred) or title
        home_name = meta.get("homeName", "")
        away_name = meta.get("awayName", "")
        team_a = _team_from_title(home_name) if home_name else None
        team_b = _team_from_title(away_name) if away_name else None

        if not team_a or not team_b:
            parsed = mm.parse_match_teams(title, meta)
            if parsed:
                team_a, team_b = parsed
        if not team_a or not team_b:
            continue

        # Referee adjustment
        match_key  = f"{team_a} vs {team_b}"
        assignments = ref_data.get("assignments", {})
        ref_name    = assignments.get(match_key, "")
        wc_games    = sum(1 for v in assignments.values() if v == ref_name) if ref_name else 0
        ref_factor  = mm.get_ref_factor(ref_name, wc_games=wc_games)

        # Look up win probs
        ta_data = team_map.get(team_a, {})
        tb_data = team_map.get(team_b, {})
        p_win_a = ta_data.get("probs", {}).get("p_win", 0.001)
        p_win_b = tb_data.get("probs", {}).get("p_win", 0.001)

        # Fetch market-implied goals + corners from Polymarket exact-score / O/U markets
        mkt = _fetch_match_lambdas(team_a, team_b, kickoff_str)

        # Compute theo
        try:
            model = mm.compute_multiplier_theo(
                team_a, team_b, p_win_a, p_win_b,
                ref_factor=ref_factor,
                lam_goals_override=mkt.get("lam_goals"),
                lam_corners_override=mkt.get("lam_corners"),
            )
        except Exception:
            continue

        theo = model["theo"]

        # Fetch order book
        try:
            ob = client.get_order_book(c["id"])
        except TycheMktError:
            continue

        bids, asks = _parse_book(ob)
        best_bid_p = bids[0][0] if bids else None
        best_bid_q = bids[0][1] if bids else None
        best_ask_p = asks[0][0] if asks else None
        best_ask_q = asks[0][1] if asks else None

        # Mid (for display only)
        if best_bid_p is not None and best_ask_p is not None:
            mid = (best_bid_p + best_ask_p) / 2.0
        elif best_bid_p is not None:
            mid = float(best_bid_p)
        elif best_ask_p is not None:
            mid = float(best_ask_p)
        else:
            mid = None

        # Best action — EV is always vs actual order price (positive = good trade)
        # BUY: ev = theo - order_price   SELL: ev = order_price - theo
        action = ""
        order_price = None
        ev = None

        candidates = []
        # BUY: lift the ask if it's below theo (paying less than fair)
        if best_ask_p is not None and theo > best_ask_p:
            candidates.append((theo - best_ask_p, "LIFT", best_ask_p))
        # BUY: bid just below theo (1 tick below fair) to queue
        if best_bid_p is not None or best_ask_p is not None:
            new_bid = int(theo) - 1   # 1 below fair, round down
            if best_ask_p is None or new_bid < best_ask_p:
                if best_bid_p is None or new_bid >= best_bid_p:
                    candidates.append((theo - new_bid, "NEW BID", new_bid))
        # SELL: hit the bid if it's above theo (getting paid more than fair)
        if best_bid_p is not None and best_bid_p > theo:
            candidates.append((best_bid_p - theo, "HIT", best_bid_p))
        # SELL: ask just above theo (1 tick above fair) to queue
        if best_bid_p is not None or best_ask_p is not None:
            new_ask = int(theo) + 1
            if best_bid_p is None or new_ask > best_bid_p:
                if best_ask_p is None or new_ask <= best_ask_p:
                    candidates.append((new_ask - theo, "NEW ASK", new_ask))

        if candidates:
            best_trade_ev, action, order_price = max(candidates, key=lambda x: x[0])
            ev = round(best_trade_ev, 1)

        results.append({
            "contract_id":  c["id"],
            "match":        f"{team_a} vs {team_b}",
            "team_a":       team_a,
            "team_b":       team_b,
            "kickoff":      meta.get("kickoff", ""),
            "stage":        meta.get("stage", ""),
            "ref":          ref_name or "—",
            "ref_factor":   round(ref_factor, 2),
            "theo":         round(theo, 1),
            "mid":          mid,
            "ev":           ev,
            "action":       action,
            "order_price":  order_price,
            "best_bid":     best_bid_p,
            "best_bid_qty": best_bid_q,
            "best_ask":     best_ask_p,
            "best_ask_qty": best_ask_q,
            "median":       round(model["median"], 0),
            "p90":          round(model["p90"], 0),
            "p_zero":       round(model["p_zero"] * 100, 1),
            "e_goals":      round(model["e_goals"], 2),
            "e_cards":      round(model["e_cards"], 2),
            "e_corners":    round(model["e_corners"], 1),
            "mkt_goals":    "lam_goals" in mkt,
            "mkt_corners":  "lam_corners" in mkt,
        })

    return sorted(results, key=lambda x: abs(x["ev"] or 0), reverse=True)


def run_elo_model() -> None:
    print("\nRunning Elo MC model (--no-cal, 50k sims)...")
    result = subprocess.run(
        ["/usr/bin/python3", "-m", "elo_model.run",
         "--no-refresh", "--no-cal", "--n-sims", "50000"],
        capture_output=True, text=True,
        cwd=Path(__file__).parent,
    )
    if result.returncode != 0:
        print(f"  [WARN] Elo model error:\n{result.stderr[-400:]}", file=sys.stderr)
    else:
        for line in result.stderr.strip().splitlines()[-4:]:
            print(f"  {line}")


def load_elo_theos() -> Tuple[Dict[str, Dict], Dict]:
    elo_file = Path(__file__).parent / "data" / "elo_teams.json"
    if not elo_file.exists():
        return {}, {}
    with open(elo_file) as f:
        data = json.load(f)
    team_map = {t["key"]: t for t in data.get("teams", [])}
    meta = {k: v for k, v in data.items() if k != "teams"}
    return team_map, meta


def load_elo_adjustments() -> List[Dict]:
    import csv as _csv
    adj_file = Path(__file__).parent / "data" / "elo_adjustments.csv"
    if not adj_file.exists():
        return []
    rows = []
    with open(adj_file) as f:
        for row in _csv.DictReader(f):
            try:
                if int(row.get("elo_delta", 0)) != 0:
                    rows.append(row)
            except (ValueError, TypeError):
                pass
    return rows


def scan_elo(
    elo_map: Dict[str, Dict],
    book_map: Dict[str, Any],
    poly_theo_map: Dict[str, float],
) -> List[Dict]:
    results: List[Dict] = []
    for team_key, (bids, asks) in book_map.items():
        t = elo_map.get(team_key)
        if not t:
            continue
        theo = t.get("ev_total")
        if theo is None:
            continue
        ev, action, order_price = _best_action(theo, bids, asks)
        if ev <= 0:
            continue
        best_bid_p = bids[0][0] if bids else None
        best_bid_q = bids[0][1] if bids else None
        best_ask_p = asks[0][0] if asks else None
        best_ask_q = asks[0][1] if asks else None
        results.append({
            "team":         team_key,
            "elo_theo":     round(theo, 2),
            "poly_theo":    round(poly_theo_map.get(team_key, 0), 2),
            "ev":           round(ev, 2),
            "action":       action,
            "order_price":  order_price,
            "best_bid":     best_bid_p,
            "best_bid_qty": best_bid_q,
            "best_ask":     best_ask_p,
            "best_ask_qty": best_ask_q,
            "p_win":        round(t.get("probs", {}).get("p_win", 0), 4),
            "p_sf":         round(t.get("probs", {}).get("p_sf", 0), 4),
            "p_final":      round(t.get("probs", {}).get("p_final", 0), 4),
            "elo_adjusted": t.get("elo_adjusted", 0),
        })
    return sorted(results, key=lambda x: x["ev"], reverse=True)


def print_elo_report(
    opps: List[Dict],
    elo_meta: Dict,
    adjustments: List[Dict],
    positions: Dict[str, int],
) -> None:
    sigma = elo_meta.get("elo_sigma", "?")
    updated = elo_meta.get("last_updated", "?")
    print(f"\n{'═'*90}")
    print(f"  Elo + Dixon-Coles Model  (σ={sigma}, 50k sims, updated {updated})")

    if adjustments:
        print(f"\n  Active Elo Adjustments:")
        for row in adjustments:
            delta = int(row["elo_delta"])
            sign = "+" if delta > 0 else ""
            note = row.get("note", "")
            print(f"    {row['team']:<22}  {sign}{delta:>+4}   {note}")

    if not opps:
        print("\n  No Elo opportunities found.")
        print(f"{'═'*90}")
        return

    print(f"\n  {'#':>3}  {'Team':<22}  {'EloTheo':>7}  {'PolyTheo':>8}  {'EV':>5}  {'Action':<14}  {'Book (B / A)':<22}  {'p_win':>6}  Pos")
    print(f"  {'─'*96}")
    for i, o in enumerate(opps, 1):
        bid_s = (f"{o['best_bid']:.0f}×{o['best_bid_qty']:.0f}"
                 if o["best_bid"] is not None else "—")
        ask_s = (f"{o['best_ask']:.0f}×{o['best_ask_qty']:.0f}"
                 if o["best_ask"] is not None else "—")
        book_s   = f"{bid_s} / {ask_s}"
        action_s = f"{o['action']} {o['order_price']:.0f}"
        pos      = positions.get(o["team"], 0)
        pos_s    = f"{pos:+d}" if pos else "—"
        limit_flag = " !" if abs(pos) >= 40 else ""
        print(
            f"  {i:>3}  {o['team']:<22}  {o['elo_theo']:>7.1f}  {o['poly_theo']:>8.1f}"
            f"  {o['ev']:>5.1f}  {action_s:<14}  {book_s:<22}  {o['p_win']:>5.1%}  {pos_s}{limit_flag}"
        )
    print(f"{'═'*90}")


def print_multiplier_report(results: List[Dict]) -> None:
    if not results:
        print("\n  No multiplier contracts found or priced.")
        return

    print(f"\n{'═'*104}")
    print(f"  Multiplier Markets  (Goals × WeightedCards × Corners)")
    print(f"  {'#':>3}  {'Match':<30}  {'Ref':<18}  {'Theo':>6}  {'EV':>5}  {'Action':<14}  {'Book (B / A)'}")
    print(f"  {'─'*98}")

    for i, r in enumerate(results, 1):
        ev_s  = f"{r['ev']:+.1f}" if r["ev"] is not None else "  —"
        px_s  = f"{r['order_price']:.0f}" if r["order_price"] is not None else "—"
        bb_s  = (f"{r['best_bid']:.0f}×{r['best_bid_qty']:.0f}"
                 if r["best_bid"] is not None else "—")
        ba_s  = (f"{r['best_ask']:.0f}×{r['best_ask_qty']:.0f}"
                 if r["best_ask"] is not None else "—")
        book_s   = f"{bb_s} / {ba_s}"
        action_s = f"{r['action']} {px_s}"
        ref      = r.get("ref", "—")
        factor   = r.get("ref_factor", 1.0)
        ref_s    = f"{ref}({factor:.2f})" if ref != "—" else "—"
        # [M] = goals from exact-score market; [C] = corners from O/U market
        mkt_tag  = ("[MC]" if r.get("mkt_goals") and r.get("mkt_corners")
                    else "[M]" if r.get("mkt_goals")
                    else "[C]" if r.get("mkt_corners")
                    else "")
        theo_s   = f"{r['theo']:>6.1f}{mkt_tag}"
        print(
            f"  {i:>3}  {r['match']:<30}  {ref_s:<18}  {theo_s:<12}  {ev_s:>5}  {action_s:<14}  {book_s}"
        )
        g  = r.get("e_goals",   0)
        c  = r.get("e_cards",   0)
        ck = r.get("e_corners", 0)
        print(f"       G={g:.1f}  C={c:.1f}  CK={ck:.1f}")

    print(f"{'═'*104}")


# ── Main ──────────────────────────────────────────────────────────────────────

def _send_email(subject: str, body: str) -> None:
    import smtplib, ssl
    from email.mime.text import MIMEText
    from_addr = os.environ.get("EMAIL_FROM", "fieldenthomas@gmail.com")
    to_addr   = os.environ.get("EMAIL_TO",   "fieldenthomas@gmail.com")
    password  = os.environ.get("EMAIL_PASSWORD", "")
    smtp_host = os.environ.get("SMTP_HOST", "smtp.gmail.com")
    smtp_port = int(os.environ.get("SMTP_PORT", "465"))

    msg = MIMEText(body, "plain")
    msg["Subject"] = subject
    msg["From"]    = from_addr
    msg["To"]      = to_addr

    ctx = ssl.create_default_context()
    with smtplib.SMTP_SSL(smtp_host, smtp_port, context=ctx) as s:
        s.login(from_addr, password)
        s.sendmail(from_addr, to_addr, msg.as_string())


def _save_snapshots(mult_results: List[Dict]) -> None:
    """Append pre-kickoff model inputs to model_snapshots.json for later backtesting.

    Each entry is keyed by 'match + kickoff' so re-runs don't duplicate.
    Only saves contracts that haven't kicked off yet (lam_goals etc. are pre-match values).
    """
    try:
        existing: Dict[str, Dict] = {}
        if SNAPSHOT_FILE.exists():
            with open(SNAPSHOT_FILE) as f:
                existing = json.load(f)
        ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        for r in mult_results:
            key = f"{r['match']}|{r['kickoff']}"
            if key in existing:
                continue   # already have a snapshot for this match
            existing[key] = {
                "match":       r["match"],
                "kickoff":     r["kickoff"],
                "snapshotted": ts,
                "theo":        r["theo"],
                "lam_goals":   r.get("e_goals"),
                "lam_cards":   r.get("e_cards"),
                "lam_corners": r.get("e_corners"),
                "ref":         r.get("ref"),
                "ref_factor":  r.get("ref_factor"),
                "mkt_goals":   r.get("mkt_goals"),
                "mkt_corners": r.get("mkt_corners"),
            }
        SNAPSHOT_FILE.parent.mkdir(exist_ok=True)
        with open(SNAPSHOT_FILE, "w") as f:
            json.dump(existing, f, indent=2)
    except Exception as exc:
        print(f"  [snapshot] warning: {exc}")


def main() -> None:
    refresh     = "--refresh" in sys.argv
    send_email  = "--email"   in sys.argv

    # Capture stdout when emailing so the full report goes in the email body
    import io
    _real_stdout = sys.stdout
    if send_email:
        sys.stdout = io.StringIO()

    print("═" * 60)
    print("  TycheMkt WC 2026 EV Scanner")
    print("═" * 60)

    run_elo_model()
    elo_map, elo_meta = load_elo_theos()
    if elo_map:
        print(f"  Elo theos: {len(elo_map)} teams  (σ={elo_meta.get('elo_sigma','?')}, updated {elo_meta.get('last_updated','?')})")

    team_map = load_theos(refresh)

    email, password = get_credentials()
    client = TycheMktClient()
    try:
        client.login(email, password)
        print(f"  Authenticated as {email}")
    except TycheMktError as e:
        sys.exit(f"Login failed: {e}")

    try:
        opps, mult_contracts, mid_map, cid_to_team, book_map = scan(client, team_map)
    except TycheMktError as e:
        sys.exit(f"API error: {e}")

    tyche_net    = fetch_tyche_positions(client, cid_to_team)
    combined_pos = build_combined_positions(tyche_net)
    open_orders  = fetch_open_orders(client, cid_to_team)

    print_combined_report(opps, team_map, elo_map, book_map, combined_pos, open_orders)

    # Basket: compare model EV vs TycheMkt mid — advancing teams only
    advancing = {k: t for k, t in team_map.items() if t.get("probs", {}).get("p_advance", 0) > 0}
    sum_theo  = sum(t.get("ev_total", 0) for t in advancing.values())
    sum_mid   = sum(mid_map[k] for k in advancing if mid_map.get(k) is not None)
    n_mid     = sum(1 for k in advancing if mid_map.get(k) is not None)
    gap       = sum_theo - sum_mid
    print(f"\n  Basket ({len(advancing)} advancing teams): model Σ={sum_theo:.1f}  mkt Σ={sum_mid:.1f} ({n_mid}/{len(advancing)} with book)  gap={gap:+.1f}")

    print("\nPricing multiplier markets...")
    mult_results = scan_multipliers(client, team_map, mult_contracts)
    print_multiplier_report(mult_results)

    # Persist model inputs for backtesting — one entry per match per scan run.
    # validate_model.py joins these against ESPN settlement data.
    _save_snapshots(mult_results)

    OUT_FILE.parent.mkdir(exist_ok=True)
    with open(OUT_FILE, "w") as f:
        json.dump(
            {"scanned_at":    time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
             "opportunities": opps,
             "multipliers":   mult_results,
             "mids":          {k: v for k, v in mid_map.items() if v is not None}},
            f, indent=2
        )

    if send_email:
        body = sys.stdout.getvalue()
        sys.stdout = _real_stdout
        ts = time.strftime("%H:%M UTC", time.gmtime())
        try:
            _send_email(f"TycheMkt WC Scanner — {ts}", body)
            print(f"  Email sent ({ts})")
        except Exception as e:
            print(f"  Email failed: {e}")
            print(body)
    print(f"\n  Full results saved to {OUT_FILE.relative_to(Path(__file__).parent)}")


if __name__ == "__main__":
    main()
