# polymarketresearch

Open dataset + collectors for comparing **prediction-market** probabilities
(Polymarket) with **option-implied** risk-neutral densities (Deribit BTC/ETH).
All sources are free and public — no API keys, no paid subscriptions.

## Data sources

| Script | Source | What it captures | Cadence |
|---|---|---|---|
| `crawl_polymarket.py` | Polymarket Gamma + CLOB | "above ___ on \<date\>" strike ladders → risk-neutral CDF + realized 0/1 outcomes | daily |
| `tardis_options.py` | Tardis free 1st-of-month | full Deribit BTC/ETH option smile (mark/bid/ask IV + greeks) | monthly |
| `deribit_snapshot.py` | Deribit live API | full live option surface (finer-than-monthly history) | hourly |
| `spot_collect.py` | Deribit perpetual TV-chart | BTC/ETH hourly spot — moneyness/IV anchor for smile analysis | hourly |

Notes from building the collectors:
- The Polymarket daily-ladder product began ~Sep 2025.
- Polymarket CLOB history needs an explicit `startTs/endTs` window (not `interval=max`).
- Tardis gives away the 1st of each month for free; one daily file is the full
  tick-level chain (~2 GB gzip), streamed and downsampled to hourly marks.

## Outputs (in `data/`)

- `btc_ladder.csv`, `eth_ladder.csv` — `event_id, expiry_ts, strike, token_id, settled_yes, t, p`
- `deribit_smile_history.csv` — monthly Deribit smiles (Tardis)
- `collected/deribit/snap_<ts>.csv` — immutable hourly self-collected surfaces
- `spot_history.csv` — hourly BTC/ETH spot (`t, asset, close`)
- `manifest.json` — Polymarket coverage summary

## Running locally

```bash
PY=python3.12   # needs pandas for the crawl/tardis scripts
$PY crawl_polymarket.py --start 2025-08-01     # Polymarket backfill
$PY tardis_options.py   --start 2024-04        # Deribit monthly smiles backfill
$PY deribit_snapshot.py --once                 # one live snapshot
```

## Automated collection (GitHub Actions)

Three scheduled workflows in `.github/workflows/` keep the dataset current and
commit straight to `main`:

- `collect-deribit` — hourly
- `collect-spot` — hourly
- `collect-polymarket` — daily (`--since-days 3 --merge`)
- `collect-tardis` — monthly (2nd of month)

They share one `concurrency: data-collection` group so commits never race.
Public repo → unlimited Actions minutes. To run on demand, use
**Actions → \<workflow\> → Run workflow** (`workflow_dispatch`).
