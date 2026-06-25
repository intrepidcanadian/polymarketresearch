"""Load the partitioned CSVs into Postgres (the queryable analytical layer).

The CSVs in data/ stay the reproducible source of truth; this upserts ladders,
smiles, and spot into Postgres for fast SQL joins (the matcher). Hourly Deribit
snapshots are intentionally NOT loaded (they grow ~16M rows/yr and would blow a
free-tier DB) — they remain in git as partitioned CSVs.

Idempotent: COPY into a TEMP table, then INSERT ... ON CONFLICT DO NOTHING on the
primary keys, so re-running never duplicates.

    export DATABASE_URL=postgresql://user:pass@host/db
    python db_load.py --init            # create schema
    python db_load.py --all             # full backfill (ladders+smiles+spot)
    python db_load.py --recent 2        # only the latest 2 monthly partitions (CI)

Needs: psycopg[binary], pandas.
"""

import argparse
import glob
import io
import os
import os.path as osp

import pandas as pd
import psycopg

HERE = osp.dirname(osp.abspath(__file__))
DATA = osp.join(HERE, "data")

# table -> (full column list in schema order, primary-key tuple)
COLS = {
    "ladders": ["asset", "event_id", "expiry_ts", "strike", "token_id",
                "settled_yes", "t", "p"],
    "smiles":  ["ts_us", "asset", "expiry_us", "strike", "opt_type", "mark_iv",
                "bid_iv", "ask_iv", "mark_price", "underlying_price", "delta",
                "open_interest"],
    "spot":    ["t", "asset", "close"],
}


def _dsn():
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        raise SystemExit("Set DATABASE_URL (postgresql://user:pass@host/db) first.")
    return dsn


def _ladders_df(recent):
    parts = []
    for asset in ("btc", "eth"):
        files = sorted(glob.glob(osp.join(DATA, "ladders", asset, "*.csv")))
        if recent:
            files = files[-recent:]
        for f in files:
            d = pd.read_csv(f)
            d["asset"] = asset
            parts.append(d)
    return pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()


def _smiles_df(recent):
    files = sorted(glob.glob(osp.join(DATA, "smiles", "*.csv")))
    if recent:
        files = files[-recent:]
    return pd.concat((pd.read_csv(f) for f in files), ignore_index=True) if files else pd.DataFrame()


def _spot_df(_recent):
    fp = osp.join(DATA, "spot_history.csv")
    return pd.read_csv(fp) if osp.exists(fp) else pd.DataFrame()


LOADERS = {"ladders": _ladders_df, "smiles": _smiles_df, "spot": _spot_df}


def upsert(conn, table, df):
    if df.empty:
        print(f"  {table}: nothing to load")
        return
    cols = COLS[table]
    df = df[cols]
    with conn.cursor() as cur:
        cur.execute(f"CREATE TEMP TABLE _stg (LIKE {table} INCLUDING DEFAULTS) ON COMMIT DROP")
        buf = io.StringIO()
        df.to_csv(buf, index=False, header=False)
        buf.seek(0)
        with cur.copy(f"COPY _stg ({','.join(cols)}) FROM STDIN WITH (FORMAT csv)") as cp:
            while chunk := buf.read(1 << 16):
                cp.write(chunk)
        cur.execute(f"INSERT INTO {table} ({','.join(cols)}) "
                    f"SELECT {','.join(cols)} FROM _stg ON CONFLICT DO NOTHING")
        inserted = cur.rowcount
    conn.commit()
    print(f"  {table}: staged {len(df)} rows, inserted {inserted} new")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--init", action="store_true", help="create schema, then exit")
    ap.add_argument("--all", action="store_true", help="load full backfill")
    ap.add_argument("--recent", type=int, default=0, help="only the latest N monthly partitions")
    ap.add_argument("--tables", default="ladders,smiles,spot")
    args = ap.parse_args()

    with psycopg.connect(_dsn()) as conn:
        if args.init:
            with conn.cursor() as cur, open(osp.join(HERE, "db", "schema.sql")) as f:
                cur.execute(f.read())
            conn.commit()
            print("schema created.")
            return
        for table in args.tables.split(","):
            df = LOADERS[table](args.recent)
            upsert(conn, table, df)


if __name__ == "__main__":
    main()
