-- Example queries against the analytical layer.

-- 1) The matcher core: each Polymarket ladder point joined to spot at the same
--    hour. (Add the Deribit smile join once you pick a tenor-matching rule —
--    smiles.expiry_us is 08:00 UTC vs ladders.expiry_ts 16:00 UTC, so you'll
--    interpolate across expiries rather than equi-join on expiry.)
SELECT
    l.asset,
    to_timestamp(l.t)            AS obs_hour,
    to_timestamp(l.expiry_ts)    AS expiry,
    l.strike,
    l.p                          AS pm_prob,        -- Polymarket P(S>K)
    s.close                      AS spot,
    l.strike / s.close           AS moneyness,
    l.settled_yes
FROM ladders l
JOIN spot s
  ON s.asset = l.asset
 AND s.t     = (l.t / 3600) * 3600      -- align to the hourly spot grid
WHERE l.asset = 'btc'
ORDER BY obs_hour, l.strike
LIMIT 50;

-- 2) Coverage sanity: events and hourly rows per asset.
SELECT asset,
       count(DISTINCT event_id)                 AS events,
       count(*)                                  AS rows,
       to_timestamp(min(expiry_ts))::date       AS first_expiry,
       to_timestamp(max(expiry_ts))::date       AS last_expiry
FROM ladders
GROUP BY asset;

-- 3) A single Deribit smile cross-section (one asset/obs-hour/expiry).
SELECT strike, mark_iv, bid_iv, ask_iv, underlying_price
FROM smiles
WHERE asset = 'btc'
  AND opt_type = 'call'
  AND ts_us = (SELECT max(ts_us) FROM smiles WHERE asset = 'btc')
ORDER BY strike;
