import subprocess
import sys

_CHECK = """
import data_api.app as app, data_api.settings as s
from data_api.routes import candles, snapshots, indicators, indicator_series, rankings, metrics
assert all(m.CATALOG_PATH == s.CATALOG_PATH for m in (app, candles, snapshots, indicator_series, rankings, metrics))  # indicators reads candles via candles.candle_page
assert candles.CANDLES_DB_PATH == s.CANDLES_DB_PATH
"""


def test_data_api_imports_cleanly_and_shares_one_catalog_path() -> None:
    # Fresh interpreter: a circular import only shows up on a cold import, never in a warm pytest process.
    subprocess.run([sys.executable, "-c", _CHECK], check=True)
