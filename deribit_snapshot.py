"""Forward-collect our own hourly Deribit BTC/ETH option surface (free, no auth).

Tardis free tier only gives the 1st of each month. To get finer-than-monthly
history we snapshot the LIVE chain ourselves: one call to
`get_book_summary_by_currency` per currency returns every option instrument with
mark_iv / mark_price / bid / ask / underlying_price / open_interest. Append a row
per instrument per snapshot. Run hourly (via launchd, see install-launchd) and a
dense surface accumulates going forward, matched to the Polymarket ladders.

Modes:
  --once   take a single snapshot, append to data/deribit_snapshots.csv, exit
           (use this from launchd / cron with StartInterval=3600)
  --loop   take a snapshot every --interval seconds forever (foreground/nohup)
  --install-launchd   print a ready-to-load launchd plist for hourly --once

Output: data/deribit_snapshots.csv (append-only), columns
  snap_ts, asset, expiry_ms, strike, opt_type, mark_iv, mark_price,
  bid, ask, underlying_price, open_interest
Stdlib only.
"""

import argparse
import csv
import os
import os.path as osp
import time
from datetime import datetime, timezone
from urllib.request import Request, urlopen

HERE = osp.dirname(osp.abspath(__file__))
DATA = osp.join(HERE, "data")
OUT = osp.join(DATA, "deribit_snapshots.csv")
B = "https://www.deribit.com/api/v2"
UA = {"User-Agent": "ai-scientist-deribit-snapshot/1.0"}
COLS = ["snap_ts", "asset", "expiry_ms", "strike", "opt_type", "mark_iv",
        "mark_price", "bid", "ask", "underlying_price", "open_interest"]
# Deribit expiry codes -> month number
MON = {m: i + 1 for i, m in enumerate(
    ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"])}


def _get(url, tries=4):
    import json
    for i in range(tries):
        try:
            with urlopen(Request(url, headers=UA), timeout=30) as r:
                return json.load(r)
        except Exception:  # noqa: BLE001
            if i == tries - 1:
                raise
            time.sleep(1.5 ** i)


def parse_instrument(name):
    """'BTC-27DEC24-60000-C' -> (asset, expiry_ms, strike, opt_type). None if odd."""
    p = name.split("-")
    if len(p) != 4:
        return None
    asset, exp, strike, kind = p
    try:
        d = int(exp[:-5]); mon = MON[exp[-5:-2]]; yr = 2000 + int(exp[-2:])
        expiry = datetime(yr, mon, d, 8, 0, tzinfo=timezone.utc)  # Deribit expires 08:00 UTC
        return asset.lower(), int(expiry.timestamp() * 1000), float(strike), \
            "call" if kind == "C" else "put"
    except (ValueError, KeyError):
        return None


def snapshot(out=None, log=print):
    """Take one full BTC+ETH option snapshot.

    out=None  -> append to the single data/deribit_snapshots.csv (local default).
    out=<dir> -> write an IMMUTABLE per-run file <dir>/snap_<ts>.csv with its own
                 header. Used by the hourly GitHub Action so each run is additive
                 (no rewrite of a growing file -> clean git history).
    """
    snap_ts = int(time.time())
    rows = []
    for cur in ("BTC", "ETH"):
        res = _get(f"{B}/public/get_book_summary_by_currency?currency={cur}&kind=option")["result"]
        for it in res:
            pi = parse_instrument(it["instrument_name"])
            if pi is None:
                continue
            asset, expiry_ms, strike, kind = pi
            rows.append({
                "snap_ts": snap_ts, "asset": asset, "expiry_ms": expiry_ms,
                "strike": strike, "opt_type": kind,
                "mark_iv": it.get("mark_iv"), "mark_price": it.get("mark_price"),
                "bid": it.get("bid_price"), "ask": it.get("ask_price"),
                "underlying_price": it.get("underlying_price"),
                "open_interest": it.get("open_interest"),
            })
    if out:
        os.makedirs(out, exist_ok=True)
        fp = osp.join(out, f"snap_{snap_ts}.csv")
        with open(fp, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=COLS)
            w.writeheader()
            w.writerows(rows)
    else:
        fp = OUT
        new = not osp.exists(OUT)
        with open(OUT, "a", newline="") as f:
            w = csv.DictWriter(f, fieldnames=COLS)
            if new:
                w.writeheader()
            w.writerows(rows)
    log(f"[{datetime.now(timezone.utc):%Y-%m-%d %H:%M}Z] wrote {len(rows)} rows -> {fp}")
    return len(rows)


LAUNCHD = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>com.aiscientist.deribit-snapshot</string>
  <key>ProgramArguments</key>
  <array>
    <string>{py}</string>
    <string>{script}</string>
    <string>--once</string>
  </array>
  <key>StartInterval</key><integer>3600</integer>
  <key>RunAtLoad</key><true/>
  <key>StandardOutPath</key><string>{data}/snapshot.out.log</string>
  <key>StandardErrorPath</key><string>{data}/snapshot.err.log</string>
</dict></plist>"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--loop", action="store_true")
    ap.add_argument("--interval", type=int, default=3600)
    ap.add_argument("--install-launchd", action="store_true")
    ap.add_argument("--collected", action="store_true",
                    help="write immutable per-run file under data/collected/deribit/ (CI mode)")
    args = ap.parse_args()
    os.makedirs(DATA, exist_ok=True)
    out = osp.join(DATA, "collected", "deribit") if args.collected else None

    if args.install_launchd:
        import sys
        print(LAUNCHD.format(py=sys.executable, script=osp.abspath(__file__), data=DATA))
        return
    if args.loop:
        print(f"[deribit] hourly loop every {args.interval}s; Ctrl-C to stop")
        while True:
            try:
                snapshot(out=out)
            except Exception as e:  # noqa: BLE001
                print(f"  snapshot failed: {e}")
            time.sleep(args.interval)
    else:  # default: once
        snapshot(out=out)


if __name__ == "__main__":
    main()
