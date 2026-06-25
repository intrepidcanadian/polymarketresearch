"""Reassemble combined frames from the partitioned data files.

Collection writes small per-month / per-run files (to stay under GitHub's file
limits and keep git history clean). Experiments want unified tables, so this
helper concatenates the partitions on demand. Nothing here is committed — call
the loaders from analysis code, or run as a script to write combined CSVs to
data/_combined/ locally (gitignored).

    import concat
    ladders = concat.load_ladders()        # all Polymarket ladders (btc+eth)
    smiles  = concat.load_smiles()         # all Tardis monthly Deribit smiles
    snaps   = concat.load_snapshots()      # all hourly self-collected surfaces
    spot    = concat.load_spot()           # hourly BTC/ETH spot
"""

import glob
import os.path as osp

import pandas as pd

HERE = osp.dirname(osp.abspath(__file__))
DATA = osp.join(HERE, "data")


def _concat(pattern):
    files = sorted(glob.glob(osp.join(DATA, pattern)))
    if not files:
        return pd.DataFrame()
    return pd.concat((pd.read_csv(f) for f in files), ignore_index=True)


def load_ladders():
    # asset isn't a column in the partition files -> derive it from the directory
    parts = []
    for asset in ("btc", "eth"):
        a = _concat(f"ladders/{asset}/*.csv")
        if not a.empty:
            a["asset"] = asset
            parts.append(a)
    return pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()


def load_smiles():
    return _concat("smiles/*.csv")


def load_snapshots():
    return _concat("collected/deribit/**/snap_*.csv")


def load_spot():
    fp = osp.join(DATA, "spot_history.csv")
    return pd.read_csv(fp) if osp.exists(fp) else pd.DataFrame()


if __name__ == "__main__":
    import os
    out = osp.join(DATA, "_combined")
    os.makedirs(out, exist_ok=True)
    for name, df in [("ladders", load_ladders()), ("smiles", load_smiles()),
                     ("snapshots", load_snapshots()), ("spot", load_spot())]:
        if not df.empty:
            df.to_csv(osp.join(out, f"{name}.csv"), index=False)
        print(f"{name}: {len(df)} rows")
