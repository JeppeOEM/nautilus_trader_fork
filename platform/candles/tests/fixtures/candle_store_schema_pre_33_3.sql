
CREATE TABLE IF NOT EXISTS candles (
    instrument_id    TEXT    NOT NULL,
    bar_seconds      INTEGER NOT NULL,
    t                INTEGER NOT NULL,
    o REAL, h REAL, l REAL, c REAL,
    v                REAL    NOT NULL,
    seconds_observed INTEGER NOT NULL,
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
