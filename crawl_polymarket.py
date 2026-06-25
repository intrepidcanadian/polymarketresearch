"""Crawler: Polymarket BTC/ETH "above ___ on <date>" strike-ladder markets.

Each daily event ("Bitcoin above ___ on June 25?") is a set of binary markets at
different strikes that all resolve at the same expiry. Together they sample the
risk-neutral CDF  P(S_T > K)  at ~5-20 strikes, and each strike has an hourly
price history over its (~1 week) life. This script assembles the full panel so a
risk-neutral density can be reconstructed at every hour and scored against the
realized 0/1 outcomes.

Design notes (see the live probing that motivated them):
  * Gamma offset pagination is unreliable (HTTP 422 past offset ~2500) and the
    events endpoint caps each response at 100 rows regardless of `limit`. So we
    DISCOVER by day-windowing `end_date_min/max` (one UTC day per query) under
    the crypto tag (tag_id=21); each day returns a handful of events, well under
    the cap.
  * Hourly price history comes from the CLOB `prices-history` endpoint with
    interval=max & fidelity=60 (minutes).
  * Resumable: every event's assembled rows are cached as JSON under
    data/_cache/<event_id>.json, so re-running only fetches what's missing.

Output (slimmed, long format):
  data/btc_ladder.csv, data/eth_ladder.csv with columns
    event_id, expiry_ts, strike, token_id, settled_yes, t, p
  data/manifest.json  -- coverage summary (n events, strikes/event, date range, rows)

CPU-only, no API key required (public read endpoints). Stdlib + pandas only.
"""

import argparse
import json
import os
import os.path as osp
import re
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

import pandas as pd

GAMMA = "https://gamma-api.polymarket.com"
CLOB = "https://clob.polymarket.com"
CRYPTO_TAG = 21
HERE = osp.dirname(osp.abspath(__file__))
DATA = osp.join(HERE, "data")
CACHE = osp.join(DATA, "_cache")
START_DATE = "2025-08-01"  # daily "above ___ on" ladder product began ~Sep 2025
UA = {"User-Agent": "ai-scientist-polymarket-crawler/1.0"}

# "Will the price of Bitcoin be above $54,000 on June 25?" -> asset, strike
ABOVE_RE = re.compile(r"\babove\b.*\bon\b", re.I)
STRIKE_RE = re.compile(r"\$\s*([\d,]+(?:\.\d+)?)")


def _get(url, tries=5, backoff=1.5):
    """GET json with retry/backoff; returns parsed JSON or raises."""
    last = None
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=45) as r:
                return json.load(r)
        except Exception as e:  # noqa: BLE001 - network is flaky, retry broadly
            last = e
            code = getattr(e, "code", None)
            if code in (404,):  # nothing there; don't hammer
                return None
            time.sleep(backoff ** i)
    raise RuntimeError(f"GET failed after {tries}: {url} :: {last}")


def asset_of(title):
    t = (title or "").lower()
    if "bitcoin" in t or "btc" in t:
        return "btc"
    if "ethereum" in t or "eth" in t:
        return "eth"
    return None


def discover_events(start_date, end_date, sleep=0.2, log=print):
    """Day-window the events endpoint; return list of above-on BTC/ETH events."""
    d0 = datetime.fromisoformat(start_date).replace(tzinfo=timezone.utc)
    d1 = datetime.fromisoformat(end_date).replace(tzinfo=timezone.utc)
    out, seen = [], set()
    day = d0
    while day <= d1:
        lo = day.strftime("%Y-%m-%dT00:00:00Z")
        hi = day.strftime("%Y-%m-%dT23:59:59Z")
        qs = urllib.parse.urlencode(
            {
                "tag_id": CRYPTO_TAG,
                "closed": "true",
                "limit": 100,
                "end_date_min": lo,
                "end_date_max": hi,
            }
        )
        try:
            evs = _get(f"{GAMMA}/events?{qs}") or []
        except RuntimeError as e:
            log(f"  ! discover {day.date()} failed: {e}")
            evs = []
        for e in evs:
            title = e.get("title") or ""
            if not ABOVE_RE.search(title):
                continue
            a = asset_of(title)
            if a is None:
                continue
            eid = str(e.get("id"))
            if eid in seen:
                continue
            seen.add(eid)
            out.append({"event_id": eid, "asset": a, "title": title,
                        "endDate": e.get("endDate"), "markets": e.get("markets", [])})
        if day.day == 1:
            log(f"  discover .. {day.date()}  (events so far: {len(out)})")
        day += timedelta(days=1)
        time.sleep(sleep)
    return out


def _expiry_ts(iso):
    if not iso:
        return None
    return int(datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp())


def fetch_event_rows(ev, sleep=0.15, log=print):
    """For one event, return list of row dicts across all strike tokens."""
    cache_fp = osp.join(CACHE, f"{ev['event_id']}.json")
    if osp.exists(cache_fp):
        with open(cache_fp) as f:
            return json.load(f)

    rows = []
    expiry = _expiry_ts(ev.get("endDate"))
    for m in ev.get("markets", []):
        q = m.get("question") or ""
        sm = STRIKE_RE.search(q)
        if not sm:
            continue
        strike = float(sm.group(1).replace(",", ""))
        try:
            tokens = json.loads(m.get("clobTokenIds") or "[]")
            prices = json.loads(m.get("outcomePrices") or "[]")
        except (json.JSONDecodeError, TypeError):
            continue
        if not tokens:
            continue
        yes_token = tokens[0]
        settled_yes = float(prices[0]) if prices else None
        # IMPORTANT: interval=max returns 0 points for older resolved markets;
        # an explicit startTs/endTs window retrieves the full history. Ladders
        # live ~1wk, so a 14-day pre-expiry window covers their life.
        start_ts = (expiry - 14 * 86400) if expiry else None
        end_ts = (expiry + 86400) if expiry else None
        params = {"market": yes_token, "fidelity": 60}
        if start_ts and end_ts:
            params.update({"startTs": start_ts, "endTs": end_ts})
        else:
            params["interval"] = "max"
        qs = urllib.parse.urlencode(params)
        hist = _get(f"{CLOB}/prices-history?{qs}")
        time.sleep(sleep)
        pts = (hist or {}).get("history", []) if isinstance(hist, dict) else []
        for pt in pts:
            rows.append({
                "event_id": ev["event_id"],
                "expiry_ts": expiry,
                "strike": strike,
                "token_id": yes_token,
                "settled_yes": settled_yes,
                "t": pt.get("t"),
                "p": pt.get("p"),
            })
    with open(cache_fp, "w") as f:
        json.dump(rows, f)
    return rows


COLS = ["event_id", "expiry_ts", "strike", "token_id", "settled_yes", "t", "p"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default=START_DATE)
    ap.add_argument("--end", default=datetime.now(timezone.utc).strftime("%Y-%m-%d"))
    ap.add_argument("--since-days", type=int, default=0,
                    help="incremental: only events with expiry in the last N days (CI daily mode)")
    ap.add_argument("--merge", action="store_true",
                    help="merge fetched rows into existing {asset}_ladder.csv instead of overwriting")
    ap.add_argument("--limit-events", type=int, default=0, help="cap events (smoke test)")
    args = ap.parse_args()

    start = args.start
    if args.since_days:
        start = (datetime.now(timezone.utc) - timedelta(days=args.since_days)).strftime("%Y-%m-%d")

    os.makedirs(CACHE, exist_ok=True)
    print(f"[1/3] discovering above-on events {start} -> {args.end} ...")
    events = discover_events(start, args.end)
    print(f"      found {len(events)} BTC/ETH ladder events")
    if args.limit_events:
        events = events[: args.limit_events]
        print(f"      (smoke test: limited to {len(events)})")

    print("[2/3] fetching hourly strike histories ...")
    all_rows = []
    for i, ev in enumerate(events, 1):
        rows = fetch_event_rows(ev)
        for r in rows:
            r["asset"] = ev["asset"]
        all_rows.extend(rows)
        if i % 25 == 0 or i == len(events):
            print(f"      {i}/{len(events)} events  ({len(all_rows)} rows)")

    print("[3/3] assembling slimmed panel ...")
    df = pd.DataFrame(all_rows)
    manifest = {"start": start, "end": args.end, "n_events": len(events)}
    if not df.empty:
        df = df.dropna(subset=["t", "p"]).sort_values(["asset", "expiry_ts", "strike", "t"])
        for asset, g in df.groupby("asset"):
            fp = osp.join(DATA, f"{asset}_ladder.csv")
            cols = COLS
            if args.merge and osp.exists(fp):
                prev = pd.read_csv(fp)
                g = (pd.concat([prev, g[cols]], ignore_index=True)
                     .drop_duplicates(subset=["event_id", "strike", "t"], keep="last")
                     .sort_values(["expiry_ts", "strike", "t"]))
            g[cols].to_csv(fp, index=False)
            spe = g.groupby("event_id")["strike"].nunique()
            manifest[asset] = {
                "rows": int(len(g)),
                "events": int(g["event_id"].nunique()),
                "strikes_per_event_min": int(spe.min()),
                "strikes_per_event_med": int(spe.median()),
                "strikes_per_event_max": int(spe.max()),
                "expiry_min": int(g["expiry_ts"].min()),
                "expiry_max": int(g["expiry_ts"].max()),
            }
            print(f"      {asset}: {manifest[asset]}")
    with open(osp.join(DATA, "manifest.json"), "w") as f:
        json.dump(manifest, f, indent=2)
    print("done. manifest -> data/manifest.json")


if __name__ == "__main__":
    main()
