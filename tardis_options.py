"""Tardis free-tier historical Deribit option smiles (BTC/ETH).

Tardis gives away the FIRST DAY of every month for all datasets, no auth. The
`options_chain` dataset for Deribit is the full chain with per-strike IV and
greeks. Each daily file is ~2.2 GB gzipped tick-level, so we STREAM-decompress,
keep only BTC-/ETH- (USD-margined) option rows, and downsample to one row per
(instrument, hour) -- the last quote in each hour. That turns 2.2 GB into ~MBs.

Result: one free full-smile cross-section per month (24 hourly snapshots within
that 1st-of-month day), back to whenever Deribit options start on Tardis. Densify
the recent period separately with deribit_snapshot.py (our own hourly forward
collection).

Output: data/deribit_smile_history.csv with columns
  ts_us, asset, expiry_ms, strike, opt_type, mark_iv, bid_iv, ask_iv,
  mark_price, underlying_price, delta, open_interest
Resumable: each month cached under data/_cache/tardis_<YYYY>_<MM>.csv.
Stdlib only (urllib + zlib); pandas only for the final concat.
"""

import argparse
import csv
import io
import os
import os.path as osp
import zlib
from datetime import datetime, timezone
from urllib.request import Request, urlopen
from urllib.error import HTTPError

import pandas as pd

HERE = osp.dirname(osp.abspath(__file__))
DATA = osp.join(HERE, "data")
CACHE = osp.join(DATA, "_cache")
UA = {"User-Agent": "ai-scientist-tardis-fetch/1.0"}
BASE = "https://datasets.tardis.dev/v1/deribit/options_chain/{y}/{m:02d}/01/OPTIONS.csv.gz"
# symbol is the 2nd CSV field, so match on the full "deribit,<SYM>" line prefix.
# "deribit,BTC-"/"deribit,ETH-" = USD-margined inverse options (USD strikes that
# match Polymarket); this excludes "deribit,BTC_USDC-" linear options by design.
KEEP = ("deribit,BTC-", "deribit,ETH-")
HOUR_US = 3_600_000_000  # tardis timestamps are microseconds


def months(start, end):
    y, m = start
    while (y, m) <= end:
        yield y, m
        m += 1
        if m > 12:
            y, m = y + 1, 1


def fetch_month(y, m, log=print):
    """Stream one 1st-of-month file; return downsampled rows (or None if absent)."""
    cache_fp = osp.join(CACHE, f"tardis_{y}_{m:02d}.csv")
    if osp.exists(cache_fp):
        with open(cache_fp) as f:
            return list(csv.DictReader(f))

    url = BASE.format(y=y, m=m)
    try:
        resp = urlopen(Request(url, headers=UA), timeout=120)
    except HTTPError as e:
        log(f"  {y}-{m:02d}: not available ({e.code})")
        return None

    dec = zlib.decompressobj(zlib.MAX_WBITS | 16)
    header, hidx = None, {}
    # last row per (symbol, hour) -> tuple of kept fields
    snap = {}
    tail = ""  # partial line carried across chunks
    nbytes = 0
    while True:
        chunk = resp.read(1 << 20)
        if not chunk:
            break
        nbytes += len(chunk)
        text = tail + dec.decompress(chunk).decode("utf-8", "replace")
        lines = text.split("\n")
        tail = lines.pop()  # last is partial
        for ln in lines:
            if not ln:
                continue
            if header is None:
                header = ln.split(",")
                hidx = {c: i for i, c in enumerate(header)}
                continue
            # cheap prefix filter before splitting the whole line
            if not ln.startswith(KEEP):
                continue
            f = ln.split(",")
            try:
                sym = f[hidx["symbol"]]
                ts = int(f[hidx["timestamp"]])
                hour = ts // HOUR_US
                snap[(sym, hour)] = (
                    ts,
                    sym[:3].rstrip("-").lower(),  # BTC/ETH
                    f[hidx["expiration"]],
                    f[hidx["strike_price"]],
                    f[hidx["type"]],
                    f[hidx["mark_iv"]],
                    f[hidx["bid_iv"]],
                    f[hidx["ask_iv"]],
                    f[hidx["mark_price"]],
                    f[hidx["underlying_price"]],
                    f[hidx["delta"]],
                    f[hidx["open_interest"]],
                )
            except (KeyError, IndexError, ValueError):
                continue
    resp.close()

    cols = ["ts_us", "asset", "expiry_us", "strike", "opt_type", "mark_iv",
            "bid_iv", "ask_iv", "mark_price", "underlying_price", "delta", "open_interest"]
    rows = [dict(zip(cols, v)) for v in snap.values()]
    with open(cache_fp, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows(rows)
    log(f"  {y}-{m:02d}: streamed {nbytes/1e9:.1f} GB -> {len(rows)} hourly BTC/ETH rows")
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2024-04", help="YYYY-MM")
    ap.add_argument("--end", default=datetime.now(timezone.utc).strftime("%Y-%m"))
    ap.add_argument("--one", action="store_true", help="fetch only the start month (validate)")
    ap.add_argument("--merge", action="store_true", help="(no-op; kept for back-compat)")
    args = ap.parse_args()
    os.makedirs(CACHE, exist_ok=True)
    smiles_dir = osp.join(DATA, "smiles")
    os.makedirs(smiles_dir, exist_ok=True)

    sy, sm = map(int, args.start.split("-"))
    ey, em = map(int, args.end.split("-"))
    mlist = list(months((sy, sm), (ey, em)))
    if args.one:
        mlist = mlist[:1]

    # Partitioned output: one file per month (data/smiles/<YYYY-MM>.csv). Each is
    # small and immutable once written, so the monthly CI run only ADDS a file and
    # never rewrites a growing combined CSV (which would blow past GitHub's 100 MB
    # file limit). Use concat.py to assemble a combined frame for experiments.
    print(f"[tardis] fetching {len(mlist)} monthly cross-sections {args.start}..{args.end}")
    written = 0
    for (y, m) in mlist:
        r = fetch_month(y, m)
        if not r:
            continue
        fp = osp.join(smiles_dir, f"{y}-{m:02d}.csv")
        pd.DataFrame(r).to_csv(fp, index=False)
        written += 1
        print(f"[tardis] {y}-{m:02d}: {len(r)} rows -> {osp.relpath(fp, DATA)}")
    print(f"[tardis] wrote {written} monthly smile files under data/smiles/")


if __name__ == "__main__":
    main()
