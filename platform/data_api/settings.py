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


def default_metrics_db_path(catalog_path: str) -> str:
    """
    Where the ranking engine writes metrics.db when `METRICS_DB_PATH` is unset. It must equal the
    writer's own default (`ranking/__main__.py`'s `settings_from_env`), or data_api would read an
    empty file next to the real one; `data_api/tests/test_data_api.py` pins the two together.
    """
    return str(Path(catalog_path).parent / "metrics" / "metrics.db")


# The ranking engine's metrics.db, read by both the legacy `/metrics/*` and the `/api/metrics/*`
# routes. A directory mount in compose: SQLite WAL's -wal/-shm sidecars sit next to the file.
METRICS_DB_PATH: str = os.environ.get("METRICS_DB_PATH", default_metrics_db_path(CATALOG_PATH))

# Derived SQLite candle stores (`candles.infrastructure.sqlite_store`): each venue's collector
# writes its own
# file in this directory, the UI routes read them. A directory for the same WAL-sidecar reason as
# metrics.db.
CANDLES_DB_DIR: str = os.environ.get("CANDLES_DB_DIR", str(Path(CATALOG_PATH).parent / "candles"))

# The Redis both buses subscribe to (`data_api.buses`): `rankings:live` and `snapshots:raw`. Same
# variable and default as the pre-Story-24.2 `data_api/redis_bus.py` (AD-D12: env vars are frozen).
REDIS_URL: str = os.environ.get("REDIS_URL", "redis://127.0.0.1:6379")

# Durable per-service error ledger (`observability.error_ledger`, story 23.3): every service that
# calls `error_ledger.start()` writes its own `<service>.jsonl` here; `/api/errors` reads every
# service's file back through this same shared directory (docker-compose.yml's `errors_dir` mount).
ERROR_LEDGER_DIR: str = os.environ.get(
    "ERROR_LEDGER_DIR", str(Path(CATALOG_PATH).parent / "errors")
)

# The one directory holding the UI preference files (Story 32.5): `chart_indicators.toml`,
# `screener_columns.toml`, `chart_drawings.toml`, `chart_layouts.toml` and (Story 33.7)
# `screener_filter_presets.toml`, mounted as a directory
# (`./data/preferences/`) so a new preference file needs no new mount. The two per-file path
# variables it replaced would be silently ignored by a stale compose file (the files would then be
# written inside the container and lost on the next recreate), so they refuse to start instead.
_REMOVED_PATH_VARS = {
    "CHART_INDICATOR_CONFIG_PATH": "chart_indicators.toml",
    "SCREENER_COLUMNS_CONFIG_PATH": "screener_columns.toml",
}
for _var, _name in _REMOVED_PATH_VARS.items():
    if _var in os.environ:
        raise RuntimeError(
            f"{_var} was removed: set CHART_PREFERENCES_DIR to the directory holding {_name}, "
            "chart_indicators.toml, screener_columns.toml and chart_drawings.toml "
            "(docker-compose.yml mounts ./data/preferences/ at /app/preferences)"
        )

CHART_PREFERENCES_DIR: str = os.environ.get("CHART_PREFERENCES_DIR", "/app/preferences")
CHART_INDICATOR_CONFIG_PATH: str = str(Path(CHART_PREFERENCES_DIR) / "chart_indicators.toml")
SCREENER_COLUMNS_CONFIG_PATH: str = str(Path(CHART_PREFERENCES_DIR) / "screener_columns.toml")
CHART_DRAWINGS_PATH: str = str(Path(CHART_PREFERENCES_DIR) / "chart_drawings.toml")
CHART_LAYOUTS_PATH: str = str(Path(CHART_PREFERENCES_DIR) / "chart_layouts.toml")
# Story 33.7: the Rankings page's named filter presets.
SCREENER_FILTER_PRESETS_PATH: str = str(
    Path(CHART_PREFERENCES_DIR) / "screener_filter_presets.toml"
)
# Story 33.12: the chart page's pinned instruments (the watchlist rail).
CHART_WATCHLIST_PATH: str = str(Path(CHART_PREFERENCES_DIR) / "chart_watchlist.toml")


def candles_db_path(venue: str) -> str:
    """One store file per venue (each venue's collector is its single writer)."""
    return str(Path(CANDLES_DB_DIR) / f"candles_{venue.lower()}.db")
