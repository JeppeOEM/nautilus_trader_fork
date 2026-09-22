"""
Env-derived settings shared by `app.py` and every `routes/*.py` (Story 19.2). A leaf module
-- imports nothing from `app.py` or `routes/` -- so routes can share one `CATALOG_PATH`
without the `app.py` <-> routes circular import that motivated the old per-file copies.

Consumers do `from data_api.settings import CATALOG_PATH` (a module-level name of their own)
so tests keep monkeypatching `<module>.CATALOG_PATH` per route module.
"""

import os
from pathlib import Path


# One catalog root for every collector (dYdX, Bybit, ...): the catalog partitions by
# instrument_id, so no venue -> path registry is needed.
CATALOG_PATH: str = os.environ.get("CATALOG_PATH", "platform/data/catalog")

# Derived SQLite candle stores (`ml_signals.candle_store`): each venue's collector writes its own
# file in this directory, the UI routes read them. A directory for the same WAL-sidecar reason as metrics.db.
CANDLES_DB_DIR: str = os.environ.get("CANDLES_DB_DIR", str(Path(CATALOG_PATH).parent / "candles"))

# Durable per-service error ledger (`observability.error_ledger`, story 23.3): every service that
# calls `error_ledger.start()` writes its own `<service>.jsonl` here; `/api/errors` reads every
# service's file back through this same shared directory (docker-compose.yml's `errors_dir` mount).
ERROR_LEDGER_DIR: str = os.environ.get(
    "ERROR_LEDGER_DIR", str(Path(CATALOG_PATH).parent / "errors")
)


def candles_db_path(venue: str) -> str:
    """One store file per venue (each venue's collector is its single writer)."""
    return str(Path(CANDLES_DB_DIR) / f"candles_{venue.lower()}.db")
