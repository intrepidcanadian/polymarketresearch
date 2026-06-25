"""Hourly BTC/ETH spot (index proxy) collector — anchors the moneyness/IV axis.

Smile analysis needs a spot price S at every timestamp to (a) put Polymarket
ladders on a moneyness axis and (b) invert P(S>K) into an implied vol comparable
to the Deribit smile. The option files only carry `underlying_price` where Deribit
data exists (monthly historically, hourly forward); this fills the gap with a
continuous hourly series back to the Polymarket product's start.

Source: Deribit `get_tradingview_chart_data` for BTC-PERPETUAL / ETH-PERPETUAL
(resolution=60 -> hourly close). Same venue as the options, so it lines up with
the smile's underlying. Free, no auth, stdlib-only.

Output (merged, deduped): data/spot_history.csv  ->  t,asset,close   (t = unix sec)

Modes:
  (default)        fetch the last --hours hours and merge (forward/CI mode)
  --start YYYY-MM-DD   backfill from that date to now (monthly windows), merge
"""

import argparse
import csv
import json
import os
import os.path as osp
import time
from datetime import datetime, timedelta, timezone
from urllib.request import Request, urlopen

HERE = osp.dirname(osp.abspath(__file__))
DATA = osp.join(HERE, "data")
OUT = osp.join(DATA, "spot_history.csv")
B = "https://www.deribit.com/api/v2"
UA = {"User-Agent": "polymarketresearch-spot/1.0"}
INSTR = {"btc": "BTC-PERPETUAL", "eth": "ETH-PERPETUAL"}


def _get(url, tries=4):
    for i in range(tries):
        try:
            with urlopen(Request(url, headers=UA), timeout=45) as r:
                return json.load(r)
        except Exception:  # noqa: BLE001
            if i == tries - 1:
                raise
            time.sleep(1.5 ** i)


def fetch_window(asset, start_ms, end_ms):
    """Hourly closes for one asset in [start_ms, end_ms]; list of (t_sec, asset, close)."""
    url = (f"{B}/public/get_tradingview_chart_data?instrument_name={INSTR[asset]}"
           f"&start_timestamp={start_ms}&end_timestamp={end_ms}&resolution=60")
    res = (_get(url) or {}).get("result", {})
    if res.get("status") == "no_data" or not res.get("ticks"):
        return []
    return [(t // 1000, asset, c) for t, c in zip(res["ticks"], res["close"])]


def month_windows(start_dt, end_dt):
    cur = start_dt
    while cur < end_dt:
        nxt = (cur.replace(day=1) + timedelta(days=32)).replace(day=1)
        yield cur, min(nxt, end_dt)
        cur = nxt


def load_existing():
    rows = {}
    if osp.exists(OUT):
        with open(OUT) as f:
            for r in csv.DictReader(f):
                rows[(r["asset"], int(r["t"]))] = float(r["close"])
    return rows


def write_merged(rows, log=print):
    os.makedirs(DATA, exist_ok=True)
    with open(OUT, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["t", "asset", "close"])
        for (asset, t), close in sorted(rows.items(), key=lambda kv: (kv[0][1], kv[0][0])):
            w.writerow([t, asset, close])
    log(f"[spot] wrote {len(rows)} rows -> {OUT}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", help="backfill from YYYY-MM-DD (else fetch last --hours)")
    ap.add_argument("--hours", type=int, default=72, help="forward/CI window size")
    args = ap.parse_args()

    now = datetime.now(timezone.utc)
    rows = load_existing()
    before = len(rows)

    if args.start:
        start_dt = datetime.fromisoformat(args.start).replace(tzinfo=timezone.utc)
        print(f"[spot] backfilling {args.start} -> now in monthly windows ...")
        for asset in INSTR:
            for w0, w1 in month_windows(start_dt, now):
                got = fetch_window(asset, int(w0.timestamp() * 1000), int(w1.timestamp() * 1000))
                for t, a, c in got:
                    rows[(a, t)] = c
                time.sleep(0.15)
            print(f"   {asset}: {sum(1 for k in rows if k[0]==asset)} hourly points")
    else:
        start_ms = int((now.timestamp() - args.hours * 3600) * 1000)
        end_ms = int(now.timestamp() * 1000)
        for asset in INSTR:
            for t, a, c in fetch_window(asset, start_ms, end_ms):
                rows[(a, t)] = c

    write_merged(rows)
    print(f"[spot] +{len(rows) - before} new rows (total {len(rows)})")


if __name__ == "__main__":
    main()
