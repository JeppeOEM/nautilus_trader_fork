
INSERT OR REPLACE INTO candles(instrument_id, bar_seconds, t, o, h, l, c, v, seconds_observed,
    buy_v, sell_v, buy_n, sell_n, pv, liq_long_v, liq_short_v, liq_n,
    price_precision, size_precision)
VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
