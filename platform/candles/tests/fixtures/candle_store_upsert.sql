
INSERT INTO candles(instrument_id, bar_seconds, t, o, h, l, c, v, seconds_observed)
VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT(instrument_id, bar_seconds, t) DO UPDATE SET
    o = COALESCE(o, excluded.o),
    h = CASE WHEN excluded.h IS NULL THEN h WHEN h IS NULL THEN excluded.h ELSE max(h, excluded.h) END,
    l = CASE WHEN excluded.l IS NULL THEN l WHEN l IS NULL THEN excluded.l ELSE min(l, excluded.l) END,
    c = COALESCE(excluded.c, c),
    v = v + excluded.v,
    seconds_observed = seconds_observed + excluded.seconds_observed
