
CREATE TABLE IF NOT EXISTS candles (
    instrument_id    TEXT    NOT NULL,
    bar_seconds      INTEGER NOT NULL,
    t                INTEGER NOT NULL,
    o REAL, h REAL, l REAL, c REAL,
    v                REAL    NOT NULL,
    seconds_observed INTEGER NOT NULL,
    buy_v INTEGER, sell_v INTEGER, buy_n INTEGER, sell_n INTEGER, pv INTEGER,
    liq_long_v INTEGER, liq_short_v INTEGER, liq_n INTEGER,
    price_precision INTEGER, size_precision INTEGER,
    PRIMARY KEY (instrument_id, bar_seconds, t)
) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS built_through (
    instrument_id TEXT PRIMARY KEY,
    through_ns    INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS verified_days (
    instrument_id TEXT    NOT NULL,
    day           TEXT    NOT NULL,
    status        TEXT    NOT NULL,
    checked_at    INTEGER NOT NULL,
    mismatches    INTEGER NOT NULL,
    PRIMARY KEY (instrument_id, day)
) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS liquidations_applied (
    instrument_id  TEXT    NOT NULL,
    venue_event_id TEXT    NOT NULL,
    ts_event       INTEGER NOT NULL,
    PRIMARY KEY (instrument_id, venue_event_id)
) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS liquidation_feed_since (
    instrument_id TEXT    PRIMARY KEY,
    since_ns      INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS candles_by_bar_seconds_t ON candles(bar_seconds, t);
