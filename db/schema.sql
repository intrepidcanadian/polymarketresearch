-- Postgres schema for the queryable analytical layer.
-- The partitioned CSVs in data/ remain the reproducible source of truth; this DB
-- is a derived query layer (joins for the matcher/analysis). Idempotent loads
-- rely on the primary keys below (ON CONFLICT DO NOTHING).

-- Polymarket strike ladders: hourly P(S>K) per strike + realized outcome.
CREATE TABLE IF NOT EXISTS ladders (
    asset         text             NOT NULL,
    event_id      text             NOT NULL,
    expiry_ts     bigint           NOT NULL,   -- unix seconds (16:00 UTC resolve)
    strike        double precision NOT NULL,
    token_id      text,
    settled_yes   double precision,            -- realized 0/1 outcome
    t             bigint           NOT NULL,    -- unix seconds (hourly obs)
    p             double precision,             -- market prob P(S>strike)
    PRIMARY KEY (event_id, strike, t)
);
CREATE INDEX IF NOT EXISTS idx_ladders_asset_t  ON ladders (asset, t);
CREATE INDEX IF NOT EXISTS idx_ladders_expiry   ON ladders (asset, expiry_ts);

-- Deribit option smiles (Tardis monthly cross-sections): IV per strike.
CREATE TABLE IF NOT EXISTS smiles (
    ts_us            bigint           NOT NULL,  -- unix microseconds (obs)
    asset            text             NOT NULL,
    expiry_us        bigint           NOT NULL,  -- unix microseconds (08:00 UTC)
    strike           double precision NOT NULL,
    opt_type         text             NOT NULL,
    mark_iv          double precision,
    bid_iv           double precision,
    ask_iv           double precision,
    mark_price       double precision,
    underlying_price double precision,           -- forward
    delta            double precision,
    open_interest    double precision,
    PRIMARY KEY (ts_us, asset, expiry_us, strike, opt_type)
);
CREATE INDEX IF NOT EXISTS idx_smiles_asset_ts     ON smiles (asset, ts_us);
CREATE INDEX IF NOT EXISTS idx_smiles_asset_expiry ON smiles (asset, expiry_us);

-- Hourly BTC/ETH spot (moneyness/IV anchor).
CREATE TABLE IF NOT EXISTS spot (
    t      bigint           NOT NULL,            -- unix seconds
    asset  text             NOT NULL,
    close  double precision,
    PRIMARY KEY (asset, t)
);
