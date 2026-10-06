# -------------------------------------------------------------------------------------------------
#  Copyright (C) 2015-2026 Nautech Systems Pty Ltd. All rights reserved.
#  https://nautechsystems.io
#
#  Licensed under the GNU Lesser General Public License Version 3.0 (the "License");
#  You may not use this file except in compliance with the License.
#  You may obtain a copy of the License at https://www.gnu.org/licenses/lgpl-3.0.en.html
#
#  Unless required by applicable law or agreed to in writing, software
#  distributed under the License is distributed on an "AS IS" BASIS,
#  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#  See the License for the specific language governing permissions and
#  limitations under the License.
# -------------------------------------------------------------------------------------------------
"""
The UI preference files, and their one loader/saver each (Story 24.2 merged
the `chart_indicator_config` and `screener_columns_config` modules here, bodies verbatim; Story 32.5
added `chart_drawings.toml`, Story 32.6 `chart_layouts.toml`). All four live in one directory
(`CHART_PREFERENCES_DIR`, Story 32.5):

- `chart_indicators.toml`: per-instrument chart indicator
  selections (Story 10.5), a table keyed by instrument_id, each holding a list of
  `{name, params, category}` entries -- `load_chart_indicators`/`save_chart_indicators`. Story 32.3
  added three optional keys to an entry: `source` (the price a close-fed indicator reads, default
  `"close"`), `hidden` (the legend's eye, default `false`) and `style` (a table per output label of
  `color`/`line_width`/`line_style`, default empty = the pane palette). They are written only when
  not default, so a file saved before them loads unchanged and saves back byte-identical. `id` is
  never persisted: it is a client-side sequence counter for the multi-instance picker UI,
  regenerated fresh on every load.
- `chart_drawings.toml`: per-instrument chart drawings (Story 32.5), a table per instrument id
  holding `v = 1` and an `items` array of tables, each tagged with a `kind` (`hline`,
  `trendline`, `fib`, `position`, `anchored_vp`, `anchored_vwap`) --
  `load_chart_drawings`/`save_chart_drawings`. Each item is
  checked by `validate_drawing`, which names the offending field (`DrawingError`): a malformed
  item is refused, never dropped, so a saved drawing is never silently lost on a round trip.
- `chart_layouts.toml`: per-instrument chart layout (Story 32.6), a table per instrument id holding
  `v = 1` and the fields below, plus one reserved `[default]` table (the template a coin opened for
  the first time is seeded from) with the same fields and a `default_indicators` array of indicator
  entry tables (the `chart_indicators.toml` shape) -- `load_chart_layouts`/`save_chart_layouts`.
  `default` can never be an instrument id (every id is `SYMBOL.VENUE`). Fields (`validate_layout`,
  which names the offending key, `LayoutError`): `bar_seconds` (one of `LAYOUT_BAR_SECONDS`, the
  frontend's TIMEFRAMES), `mode` (`candles`|`lines`), `volume` and `crosshair` (bool),
  `pane_heights` (pane id, at most `MAX_PANE_ID_LENGTH` characters -> height, an integer of
  1..10000 px; written on a divider drag, for the panes on screen then),
  `visible_bars` (a finite number in (0, 100000], the zoom; never a scroll position) and
  `volume_profile`, a table of `kind` (`off`|`visible`|`fixed`|`session`|`auto`|`tpo`: `auto` and
  `tpo` are session-type, so exclusive with `session` -- the frontend holds one session-type
  profile), `rows` (integer 2..500),
  `value_area_pct` (number in (0, 100]), `session` (one of `PROFILE_SESSIONS`, the frontend's
  SESSION_PERIODS), `hd` (bool), the fixed range's anchors `start`/`end` (integers, UTC
  seconds, both required and in order when `kind = "fixed"`; omitted on disk when unset, `None`
  in memory) and OPTIONAL keys that default when absent (a file saved before them loads
  unchanged; always written back): since Story 32.7 `anchor` (one of `PROFILE_ANCHORS`, the
  frontend's AUTO_ANCHOR_PRESETS; default `auto`), `ib_minutes` (the TPO's initial balance, integer
  1..`MAX_IB_MINUTES`; default 60 = 2 x 30m) and `letters` (bool, default false); since DW-151/153
  the saved kind's `sessions` (integer 1..`MAX_PROFILE_SESSIONS`, default 5), `up_color`/
  `down_color` (`#rrggbb`, defaults the frontend's DEFAULT_VOLUME_PROFILE_SETTINGS) and `show_poc`/
  `show_value_area` (bool, default true). Unknown keys are refused, never dropped. Coin tables are
  loaded tolerantly for `bar_seconds` and `mode` only (a value outside the supported set is returned as stored, so a
  timeframe retired later never fails the GET; the frontend falls back with one `console.error`),
  while the PUT validation and the `[default]` table stay strict. Drawings are never part of it.
- `screener_columns.toml`: the screener-wide Technicals column
  selection (Story 17.5), one flat top-level `columns` array of
  `{name, params, category, bar_seconds}` tables in display order, applied to every row of the
  Rankings table -- `load_screener_columns`/`save_screener_columns`.

All are tomllib to read, tomli_w to write, and a full rewrite (not a patch). Key sets frozen
(AD-D12): these files are bind-mounted and hand-editable, so a renamed or dropped key would silently
lose a saved selection on the next deploy; `views/tests/test_preferences.py` pins the written text.
The freeze forbids renaming or dropping a key, not adding an optional one with a default: every
existing file stays loadable. The screener's `columns` entries never carry the three new keys
(`save_screener_columns` writes its own four).
The paths themselves are the interface's (env vars read in `data_api`), passed in.
"""

import copy
import logging
import math
import os
import re
import tomllib
from dataclasses import dataclass
from dataclasses import field
from pathlib import Path
from typing import Any

import tomli_w


DEFAULT_BAR_SECONDS = 3600

# `chart_drawings.toml` (Story 32.5): the table layout version and the closed set of item kinds.
DRAWINGS_VERSION = 1
DRAWING_KINDS = ("hline", "trendline", "fib", "position", "anchored_vp", "anchored_vwap")
# Story 32.7: mirrors of the frontend's `VWAP_SOURCES` (`lib/anchoredVwap.ts`); the Anchored VP's
# row bounds are the layout's `MIN_PROFILE_ROWS`..`MAX_PROFILE_ROWS` (one engine, one row limit).
# `test_*_mirror_the_frontend` in `views/tests/test_chart_drawings.py` pins the pair.
VWAP_SOURCES = ("hlc3", "close", "ohlc4")
# Story 33.6: the Anchored VWAP drawing alone also takes `stored` (the bars' exact stored `pv` and
# volume, served by `views.indicator_picker`'s unlisted `AnchoredStoredVWAP`); the Anchored VP keeps
# `VWAP_SOURCES`. Mirrors the frontend's `ANCHORED_VWAP_SOURCES` (`lib/anchoredVwap.ts`).
ANCHORED_VWAP_SOURCES = (*VWAP_SOURCES, "stored")
FIB_LABEL_SIDES = ("left", "right")
POSITION_SIDES = ("long", "short")
MAX_DRAWING_LINE_WIDTH = 4

_log = logging.getLogger(__name__)


@dataclass(frozen=True)
class IndicatorEntry:
    name: str
    params: dict[str, Any]
    category: str
    # Keyword-only: `ColumnEntry`'s fourth positional field stays `bar_seconds`.
    source: str = field(default="close", kw_only=True)
    hidden: bool = field(default=False, kw_only=True)
    # Output label -> {color, line_width, line_style, ...}; empty = the pane palette default.
    style: dict[str, dict[str, Any]] = field(default_factory=dict, kw_only=True)


@dataclass(frozen=True)
class ColumnEntry(IndicatorEntry):
    """
    A column is an indicator plus the bar size it is computed on -- a field of its own, not a
    param: params feed the indicator constructor and its series id.
    """

    bar_seconds: int = DEFAULT_BAR_SECONDS


def load_chart_indicators(path: Path) -> dict[str, list[IndicatorEntry]]:
    """
    Load persisted per-instrument indicator selections.

    A missing file (nothing saved yet) returns an empty dict, not an error -- same
    "nothing saved yet" treatment the rest of this codebase gives an absent data
    source, so a fresh install or a coin with no saved config just sees no entries.
    """
    if not path.exists():
        return {}
    with path.open("rb") as f:
        raw = tomllib.load(f)
    return {
        instrument_id: [_load_indicator_entry(instrument_id, e) for e in entries]
        for instrument_id, entries in raw.items()
    }


def is_valid_style(style: Any) -> bool:
    """
    Return True for a `style` value both the file and the API accept: a table per output label of
    string, integer, boolean or finite float leaves. One rule for the loader and the PUT route, so
    an entry the loader keeps is always one the client can save back (a nested table or a `nan`
    is neither).
    """
    if not isinstance(style, dict):
        return False
    for output_style in style.values():
        if not isinstance(output_style, dict):
            return False
        for value in output_style.values():
            if not isinstance(value, str | int | float | bool):
                return False
            if isinstance(value, float) and not math.isfinite(value):
                return False
    return True


def _typed_or_default(instrument_id: str, e: dict[str, Any], key: str, default: Any) -> Any:
    """
    `e[key]` if it has the type of `default` (a dict must hold only dicts), else `default` with one
    warning: the file is hand-editable, a wrong type must not reach the replay or the client.
    """
    value = e.get(key, default)
    ok = isinstance(value, type(default))
    if ok and key == "style":
        ok = is_valid_style(value)
    if not ok:
        _log.warning(
            "chart_indicators.toml: %s entry %r has a non-%s %r; using the default %r",
            instrument_id,
            e.get("name"),
            type(default).__name__,
            key,
            default,
        )
        return default
    return value


def _load_indicator_entry(instrument_id: str, e: dict[str, Any]) -> IndicatorEntry:
    return IndicatorEntry(
        name=e["name"],
        params=e.get("params", {}),
        category=e["category"],
        source=_typed_or_default(instrument_id, e, "source", "close"),
        hidden=_typed_or_default(instrument_id, e, "hidden", False),
        style=_typed_or_default(instrument_id, e, "style", {}),
    )


def _indicator_table(entry: IndicatorEntry) -> dict[str, Any]:
    """
    One entry as its TOML table: the three Story 32.3 keys only when not default, so a
    pre-story file round-trips byte-identical (AD-D12) and a default entry stays as small as before.
    """
    table: dict[str, Any] = {
        "name": entry.name,
        "params": entry.params,
        "category": entry.category,
    }
    if entry.source != "close":
        table["source"] = entry.source
    if entry.hidden:
        table["hidden"] = True
    if entry.style:
        table["style"] = entry.style
    return table


def save_chart_indicators(config: dict[str, list[IndicatorEntry]], path: Path) -> None:
    """
    Persist `config` back to `path` as TOML.

    Full rewrite, not a patch -- `tomli_w` has no comment-preservation support, so any
    hand-written comments in the file are lost on a Save-button-triggered write. Same
    accepted, deliberate tradeoff as `collection_control`'s `TomlPlanStore.save`
    (see its docstring); revisit only if it becomes a real complaint.
    """
    raw = {
        instrument_id: [_indicator_table(e) for e in entries]
        for instrument_id, entries in config.items()
    }
    _write_atomic(path, tomli_w.dumps(raw).encode())


def load_screener_columns(path: Path) -> list[ColumnEntry]:
    """Load the column list; a missing or empty file (nothing configured yet) is `[]`, not an error."""
    if not path.exists():
        return []
    with path.open("rb") as f:
        raw = tomllib.load(f)
    return [
        ColumnEntry(
            name=e["name"],
            params=e.get("params", {}),
            category=e["category"],
            bar_seconds=e.get("bar_seconds", DEFAULT_BAR_SECONDS),
        )
        for e in raw.get("columns", [])
    ]


def save_screener_columns(entries: list[ColumnEntry], path: Path) -> None:
    raw = {
        "columns": [
            {
                "name": e.name,
                "params": e.params,
                "category": e.category,
                "bar_seconds": e.bar_seconds,
            }
            for e in entries
        ]
    }
    _write_atomic(path, tomli_w.dumps(raw).encode())


def _write_atomic(path: Path, data: bytes) -> None:
    """
    Publish `data` as `path`'s whole content: a sibling temp file, fsynced, renamed over the target
    (the preference files share one mounted directory since Story 32.5, so a rename works where
    the old single-file bind mounts could not). A crash or a failed write never leaves a truncated
    file, and the caller serializes before calling, so a bad value fails before anything is written.
    """
    temp = path.with_name(f".{path.name}.tmp")
    try:
        with temp.open("wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())  # the bytes are on disk before the rename publishes them
        os.replace(temp, path)
    except BaseException:
        temp.unlink(missing_ok=True)
        raise


# -- chart drawings ---------------------------------------------------------------------------------


# Sane bounds: a position wider than this is not a drawing; a time past this is not UTC seconds
# (year 2286 is 10**10; 2**62 would still fit a signed 64-bit, so this is well inside it).
MAX_DRAWING_WIDTH_BARS = 10_000
MAX_DRAWING_TIME = 10**11


class DrawingError(ValueError):
    """A drawing item (or the item list) that is not storable; `field` names what is wrong."""

    def __init__(self, field_name: str, message: str) -> None:
        super().__init__(f"{field_name}: {message}")
        self.field = field_name


def _is_number(value: Any) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool) and math.isfinite(value)


def _require_number(item: dict[str, Any], key: str, *, positive: bool = False) -> None:
    if key not in item:
        raise DrawingError(key, "is required")
    if not _is_number(item[key]):
        raise DrawingError(key, "must be a finite number")
    if positive and item[key] <= 0:
        raise DrawingError(key, "must be greater than zero")


def _require_int(item: dict[str, Any], key: str, low: int, high: int | None = None) -> None:
    value = item.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        raise DrawingError(key, "must be an integer")
    if value < low or (high is not None and value > high):
        raise DrawingError(
            key, f"must be at least {low}" + (f" and at most {high}" if high is not None else "")
        )


def _check_time(value: Any, field_name: str) -> None:
    """Check a UTC-seconds time: an integer in `[0, MAX_DRAWING_TIME]` (far below int64)."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise DrawingError(field_name, "time must be an integer (UTC seconds)")
    if value < 0 or value > MAX_DRAWING_TIME:
        raise DrawingError(field_name, f"time must be between 0 and {MAX_DRAWING_TIME}")


def _check_anchors(item: dict[str, Any]) -> None:
    """`anchors` is exactly two `{time, price}` points: UTC seconds (an integer) and a price."""
    anchors = item.get("anchors")
    if not isinstance(anchors, list) or len(anchors) != 2:
        raise DrawingError("anchors", "must be a list of exactly two {time, price} points")
    for anchor in anchors:
        if not isinstance(anchor, dict) or set(anchor) != {"time", "price"}:
            raise DrawingError("anchors", "each point must be exactly {time, price}")
        _check_time(anchor["time"], "anchors")
        if not _is_number(anchor["price"]):
            raise DrawingError("anchors", "price must be a finite number")


def _check_fib_options(item: dict[str, Any]) -> None:
    """Every fib option is required (the client types them required): nothing is defaulted."""
    for key in ("levels", "extend_right", "label_side", "line_width"):
        if key not in item:
            raise DrawingError(key, "is required")
    _check_fib_levels(item["levels"])
    if not isinstance(item["extend_right"], bool):
        raise DrawingError("extend_right", "must be a boolean")
    if item["label_side"] not in FIB_LABEL_SIDES:
        raise DrawingError("label_side", f"must be one of {list(FIB_LABEL_SIDES)}")
    _require_int(item, "line_width", 1, MAX_DRAWING_LINE_WIDTH)


def _check_fib_levels(levels: Any) -> None:
    """Check a fib's levels: `{ratio, enabled, color}` each, a ratio of at least 0, none twice."""
    if not isinstance(levels, list):
        raise DrawingError("levels", "must be a list of {ratio, enabled, color}")
    ratios: set[float] = set()
    for level in levels:
        if not isinstance(level, dict) or set(level) != {"ratio", "enabled", "color"}:
            raise DrawingError("levels", "each level must be exactly {ratio, enabled, color}")
        if not _is_number(level["ratio"]) or level["ratio"] < 0:
            raise DrawingError("levels", "ratio must be a finite number of at least 0")
        if not isinstance(level["enabled"], bool) or not isinstance(level["color"], str):
            raise DrawingError("levels", "enabled must be a boolean and color a string")
        if level["ratio"] in ratios:
            raise DrawingError("levels", f"ratio {level['ratio']} appears twice")
        ratios.add(level["ratio"])


def _check_position(item: dict[str, Any]) -> None:
    """
    Check a position: entry, stop and target are ordered by its side, a long has `stop < entry <
    target`, a short `target < entry < stop`, so a target or stop dragged past the entry is never
    stored. `account` and `risk_pct` come as a pair or not at all (the optional size).
    """
    if item.get("side") not in POSITION_SIDES:
        raise DrawingError("side", f"must be one of {list(POSITION_SIDES)}")
    _check_time(item.get("time"), "time")
    for key in ("entry", "stop", "target"):
        _require_number(item, key, positive=True)
    _require_int(item, "width_bars", 1, MAX_DRAWING_WIDTH_BARS)
    entry, stop, target = item["entry"], item["stop"], item["target"]
    ordered = stop < entry < target if item["side"] == "long" else target < entry < stop
    if not ordered:
        raise DrawingError("entry", f"a {item['side']} needs its stop and target on opposite sides")
    for key in ("account", "risk_pct"):
        if key in item:
            _require_number(item, key, positive=True)
    if ("account" in item) != ("risk_pct" in item):
        raise DrawingError("risk_pct", "account and risk_pct are set together or not at all")


def _check_anchored_vp(item: dict[str, Any]) -> None:
    """Check an Anchored VP: the anchor bar, the row count and value area, the two colours."""
    _check_time(item.get("time"), "time")
    _require_int(item, "rows", MIN_PROFILE_ROWS, MAX_PROFILE_ROWS)
    _require_number(item, "value_area_pct")
    if not 0 < item["value_area_pct"] <= 100:
        raise DrawingError("value_area_pct", "must be above 0 and at most 100")
    for key in ("up_color", "down_color"):
        if not isinstance(item.get(key), str):
            raise DrawingError(key, "must be a string")


def _check_anchored_vwap(item: dict[str, Any]) -> None:
    """Check an Anchored VWAP: the anchor bar, the source, the bands switch and the band colour."""
    _check_time(item.get("time"), "time")
    if item.get("source") not in ANCHORED_VWAP_SOURCES:
        raise DrawingError("source", f"must be one of {list(ANCHORED_VWAP_SOURCES)}")
    if not isinstance(item.get("bands"), bool):
        raise DrawingError("bands", "must be a boolean")
    if not isinstance(item.get("band_color"), str):
        raise DrawingError("band_color", "must be a string")


# Per kind: the keys beyond `kind`/`id`/`color` it may carry; any other key is refused.
_DRAWING_KEYS: dict[str, frozenset[str]] = {
    "hline": frozenset({"price"}),
    "trendline": frozenset({"anchors"}),
    "fib": frozenset({"anchors", "levels", "extend_right", "label_side", "line_width"}),
    "position": frozenset(
        {"side", "time", "entry", "stop", "target", "width_bars", "account", "risk_pct"}
    ),
    "anchored_vp": frozenset({"time", "rows", "value_area_pct", "up_color", "down_color"}),
    "anchored_vwap": frozenset({"time", "source", "bands", "band_color"}),
}


def validate_drawing(item: Any) -> dict[str, Any]:
    """
    Return `item` unchanged when it is a storable drawing, else raise `DrawingError` naming the
    field. Strict by design (DATA-07): an unknown key or a wrong type is refused rather than
    dropped, so what the client saved is exactly what a reload returns.
    """
    if not isinstance(item, dict):
        raise DrawingError("items", "each drawing must be an object")
    kind = item.get("kind")
    if kind not in DRAWING_KINDS:
        raise DrawingError("kind", f"must be one of {list(DRAWING_KINDS)}")
    if not isinstance(item.get("id"), str) or not item["id"]:
        raise DrawingError("id", "must be a non-empty string")
    if "color" in item and not isinstance(item["color"], str):
        raise DrawingError("color", "must be a string")
    unknown = set(item) - {"kind", "id", "color"} - _DRAWING_KEYS[kind]
    if unknown:
        raise DrawingError(sorted(unknown)[0], f"is not a field of a {kind}")
    if kind == "hline":
        _require_number(item, "price", positive=True)
    elif kind == "trendline":
        _check_anchors(item)
    elif kind == "fib":
        _check_anchors(item)
        _check_fib_options(item)
    elif kind == "position":
        _check_position(item)
    elif kind == "anchored_vp":
        _check_anchored_vp(item)
    else:
        _check_anchored_vwap(item)
    return item


def validate_drawings(items: Any) -> list[dict[str, Any]]:
    """Validate a whole item list (`DrawingError` naming `items` or the item's field)."""
    if not isinstance(items, list):
        raise DrawingError("items", "must be a list of drawings")
    seen: set[str] = set()
    for index, item in enumerate(items):
        try:
            validate_drawing(item)
            if item["id"] in seen:
                raise DrawingError("id", f"{item['id']!r} appears twice")
        except DrawingError as exc:
            raise DrawingError(f"items[{index}].{exc.field}", str(exc).split(": ", 1)[1]) from exc
        seen.add(item["id"])
    return items


def load_chart_drawings(path: Path) -> dict[str, list[dict[str, Any]]]:
    """
    Load every instrument's drawings. A missing or empty file (nothing drawn yet) is `{}`; a table
    of another version or a malformed item raises `DrawingError` -- the file is hand-editable and
    a drawing is never silently skipped.
    """
    if not path.exists():
        return {}
    with path.open("rb") as f:
        raw = tomllib.load(f)
    out: dict[str, list[dict[str, Any]]] = {}
    for instrument_id, table in raw.items():
        # Exactly `v` and `items` (a save never writes an instrument without drawings): a missing
        # list or a stray key is refused, never defaulted or dropped by the next rewrite.
        if (
            not isinstance(table, dict)
            or table.get("v") != DRAWINGS_VERSION
            or set(table) != {"v", "items"}
        ):
            raise DrawingError(
                instrument_id,
                f"is not a v = {DRAWINGS_VERSION} drawings table (exactly `v` and `items`)",
            )
        out[instrument_id] = validate_drawings(table["items"])
    return out


def save_chart_drawings(config: dict[str, list[dict[str, Any]]], path: Path) -> None:
    """
    Persist `config` as TOML, a full rewrite. Validates first and serializes before touching the
    file, then writes a sibling temp file and renames it over the target, so a crash or a bad value
    never leaves a truncated file. An instrument with no drawings left is dropped from the file.
    """
    raw = {
        instrument_id: {"v": DRAWINGS_VERSION, "items": validate_drawings(items)}
        for instrument_id, items in config.items()
        if items
    }
    _write_atomic(path, tomli_w.dumps(raw).encode())


# -- chart layouts ----------------------------------------------------------------------------------

# `chart_layouts.toml` (Story 32.6). `LAYOUT_BAR_SECONDS` mirrors the frontend's `timeframes.ts`
# TIMEFRAMES (1m 5m 15m 1H 4H 1D 1W; `test_bar_seconds_mirror_the_frontend` pins the pair); the
# candles route accepts any size, so this is the layout's own closed set.
LAYOUT_VERSION = 1
LAYOUT_DEFAULT_KEY = "default"
LAYOUT_BAR_SECONDS = (60, 300, 900, 3600, 14400, 86400, 604800)
LAYOUT_MODES = ("candles", "lines")
PROFILE_KINDS = ("off", "visible", "fixed", "session", "auto", "tpo")
MAX_PANE_HEIGHT_PX = 10_000
# A pane id is an indicator instance id (`indicator_id`: the catalog name plus every parameter, and
# `:source`), which reaches ~160 characters for `CandlePattern`; the cap only bounds a hostile key.
MAX_PANE_ID_LENGTH = 512
MAX_VISIBLE_BARS = 100_000
MIN_PROFILE_ROWS = 2
MAX_PROFILE_ROWS = 500
# Required in every layout; `footprint` (Story 32.8), `derivatives` (Story 33.5) and
# `volume_color_by` (Story 33.6) are optional, see `FOOTPRINT_DEFAULTS`, `DERIVATIVES_DEFAULTS` and
# `VOLUME_COLOR_MODES`.
_LAYOUT_KEYS = frozenset(
    {"bar_seconds", "mode", "volume", "crosshair", "pane_heights", "visible_bars", "volume_profile"}
)
_PROFILE_KEYS = frozenset(
    {
        "kind",
        "rows",
        "value_area_pct",
        "session",
        "hd",
        "anchor",
        "ib_minutes",
        "letters",
        "start",
        "end",
    }
)
_PROFILE_ANCHORS = ("start", "end")
# Optional keys, absent from a file saved before them, so filled with defaults (AD-D12 allows adding
# a key with a default, never renaming or dropping one): Story 32.7's anchor/TPO keys (members of
# `_PROFILE_KEYS`, checked by `_check_profile_scalars`) and DW-151/153's display options (checked by
# `_check_profile_options`). `MAX_PROFILE_SESSIONS` and the colours mirror the frontend's
# `MAX_SESSIONS`, `DEFAULT_SESSION_COUNT` and `DEFAULT_VOLUME_PROFILE_SETTINGS`
# (`test_profile_defaults_mirror_the_frontend` pins them).
MAX_PROFILE_SESSIONS = 10
_PROFILE_OPTIONAL_DEFAULTS: dict[str, Any] = {
    "anchor": "auto",
    "ib_minutes": 60,
    "letters": False,
    "sessions": 5,
    "up_color": "#55ff55",
    "down_color": "#ff5555",
    "show_poc": True,
    "show_value_area": True,
}
_HEX_COLOR = re.compile(r"#[0-9a-fA-F]{6}")

# Mirrors the frontend's `SESSION_PERIODS` (the volume profile's session length); a change there
# must change this tuple too (`test_session_periods_mirror_the_frontend` pins the pair). Story 32.7
# adds auto-anchored and TPO settings to the `volume_profile` table.
PROFILE_SESSIONS = ("4h", "daily", "weekly", "monthly")
# Mirrors the frontend's `AUTO_ANCHOR_PRESETS` (`lib/autoAnchor.ts`);
# `test_profile_anchors_mirror_the_frontend` pins the pair.
PROFILE_ANCHORS = ("session", "week", "month", "highest_high", "lowest_low", "auto")
MIN_IB_MINUTES = 1
MAX_IB_MINUTES = 1440

# Story 32.8: the optional `footprint` table (the volume footprint primitive's settings). Mirrors the
# frontend's `lib/chartLayout.ts` (`FOOTPRINT_MODES`, `FOOTPRINT_DEFAULT_IMBALANCE_RATIO`,
# `MAX_FOOTPRINT_ROW_TICKS`); `test_footprint_settings_mirror_the_frontend` pins the pairs. A layout
# saved before it has no table and loads with `FOOTPRINT_DEFAULTS` (Footprint off).
FOOTPRINT_MODES = ("bid_ask", "delta", "volume")
FOOTPRINT_DEFAULT_IMBALANCE_RATIO = 3
# `row_ticks` 0 = auto (the read model's smallest size giving at most 24 rows per bar). The cap only
# bounds a hostile value: a million ticks is already wider than any bar of a collected instrument.
MAX_FOOTPRINT_ROW_TICKS = 1_000_000
FOOTPRINT_DEFAULTS: dict[str, Any] = {
    "on": False,
    "row_ticks": 0,
    "mode": "bid_ask",
    "imbalance_ratio": FOOTPRINT_DEFAULT_IMBALANCE_RATIO,
    "text": True,
}
# Absent = the chart's `--chart-up`/`--chart-down` token; never written as null.
_FOOTPRINT_COLOR_KEYS = frozenset({"buy_color", "sell_color"})
_FOOTPRINT_KEYS = frozenset(FOOTPRINT_DEFAULTS) | _FOOTPRINT_COLOR_KEYS

# Story 33.5: the optional `derivatives` table (the chart's Derivatives group: Open Interest,
# Funding, Basis, Mark / Index and Liquidations). Mirrors the frontend's `lib/chartLayout.ts`
# (`DERIVATIVE_KEYS`, `DERIVATIVE_OUTPUTS`, `LIQUIDATION_MEASURES`, `DERIVATIVE_LINE_STYLES`;
# `test_derivatives_settings_mirror_the_frontend` pins them). A layout saved before it has no table
# and loads with `DERIVATIVES_DEFAULTS` (every entry off); a present table carries every entry and
# each entry its `on` (Liquidations also `measure` and `markers`), with an optional `style` table
# per output label (absent = the chart's token colours and the library's line defaults).
DERIVATIVE_KEYS = ("oi", "funding", "basis", "mark_index", "liquidations")
DERIVATIVE_OUTPUTS: dict[str, tuple[str, ...]] = {
    "oi": ("oi",),
    "funding": ("rate",),
    "basis": ("mark_index", "mark_last"),
    "mark_index": ("mark", "index"),
    "liquidations": ("liquidations",),
}
LIQUIDATION_MEASURES = ("size", "notional")
DERIVATIVE_LINE_STYLES = ("solid", "dashed", "dotted")
MAX_DERIVATIVE_LINE_WIDTH = 4
_DERIVATIVE_STYLE_COLORS = frozenset({"color", "up_color", "down_color"})
_DERIVATIVE_STYLE_KEYS = _DERIVATIVE_STYLE_COLORS | {"line_width", "line_style"}
DERIVATIVES_DEFAULTS: dict[str, dict[str, Any]] = {
    "oi": {"on": False},
    "funding": {"on": False},
    "basis": {"on": False},
    "mark_index": {"on": False},
    "liquidations": {"on": False, "measure": "size", "markers": True},
}

# Story 33.6: the optional `volume_color_by` key, how the Volume pane colours its bars: `direction`
# (up when the close is at or above the open) or `delta` (the sign of the bar's `buy_v - sell_v`).
# Mirrors the frontend's `VOLUME_COLOR_MODES` (`lib/chartLayout.ts`;
# `test_volume_color_modes_mirror_the_frontend` pins them). Absent loads as the first, `direction`.
VOLUME_COLOR_MODES = ("direction", "delta")

BUILTIN_DEFAULT_LAYOUT: dict[str, Any] = {
    "bar_seconds": 60,
    "mode": "candles",
    "volume": True,
    "crosshair": True,
    "pane_heights": {},
    "visible_bars": 120,
    "volume_profile": {
        "kind": "off",
        "rows": 24,
        "value_area_pct": 70,
        "session": "daily",
        "hd": False,
        "start": None,
        "end": None,
        **_PROFILE_OPTIONAL_DEFAULTS,
    },
    "footprint": dict(FOOTPRINT_DEFAULTS),
    "derivatives": copy.deepcopy(DERIVATIVES_DEFAULTS),
    "volume_color_by": VOLUME_COLOR_MODES[0],
}


class LayoutError(ValueError):
    """A layout (or the layouts file) that is not storable; `key` names what is wrong."""

    def __init__(self, key: str, message: str) -> None:
        super().__init__(f"{key}: {message}")
        self.key = key
        self.reason = message


@dataclass
class ChartLayouts:
    """The whole `chart_layouts.toml`: a layout per instrument id plus the optional template."""

    layouts: dict[str, dict[str, Any]] = field(default_factory=dict)
    # `None` = nothing saved as default yet (the built-in layout applies, no indicators).
    default: dict[str, Any] | None = None
    default_indicators: list[IndicatorEntry] = field(default_factory=list)


def _layout_int(key: str, value: Any, low: int, high: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int):
        raise LayoutError(key, "must be an integer")
    if value < low or value > high:
        raise LayoutError(key, f"must be between {low} and {high}")


def _check_profile_scalars(profile: dict[str, Any]) -> None:
    if profile["kind"] not in PROFILE_KINDS:
        raise LayoutError("volume_profile.kind", f"must be one of {list(PROFILE_KINDS)}")
    _layout_int("volume_profile.rows", profile["rows"], MIN_PROFILE_ROWS, MAX_PROFILE_ROWS)
    pct = profile["value_area_pct"]
    if not _is_number(pct) or not 0 < pct <= 100:
        raise LayoutError("volume_profile.value_area_pct", "must be a number above 0 up to 100")
    if profile["session"] not in PROFILE_SESSIONS:
        raise LayoutError("volume_profile.session", f"must be one of {list(PROFILE_SESSIONS)}")
    if not isinstance(profile["hd"], bool):
        raise LayoutError("volume_profile.hd", "must be a boolean")
    if profile["anchor"] not in PROFILE_ANCHORS:
        raise LayoutError("volume_profile.anchor", f"must be one of {list(PROFILE_ANCHORS)}")
    _layout_int("volume_profile.ib_minutes", profile["ib_minutes"], MIN_IB_MINUTES, MAX_IB_MINUTES)
    if not isinstance(profile["letters"], bool):
        raise LayoutError("volume_profile.letters", "must be a boolean")


def _check_profile_options(options: dict[str, Any]) -> None:
    _layout_int("volume_profile.sessions", options["sessions"], 1, MAX_PROFILE_SESSIONS)
    for key in ("up_color", "down_color"):
        value = options[key]
        if not isinstance(value, str) or not _HEX_COLOR.fullmatch(value):
            raise LayoutError(f"volume_profile.{key}", "must be a #rrggbb colour")
    for key in ("show_poc", "show_value_area"):
        if not isinstance(options[key], bool):
            raise LayoutError(f"volume_profile.{key}", "must be a boolean")


def _check_profile_anchor(key: str, value: Any) -> None:
    if value is None:
        return
    if isinstance(value, bool) or not isinstance(value, int):
        raise LayoutError(f"volume_profile.{key}", "must be an integer (UTC seconds) or null")
    if value < 0 or value > MAX_DRAWING_TIME:
        raise LayoutError(f"volume_profile.{key}", f"must be between 0 and {MAX_DRAWING_TIME}")


def _validate_profile(profile: Any) -> dict[str, Any]:
    if not isinstance(profile, dict):
        raise LayoutError("volume_profile", "must be an object")
    required = _PROFILE_KEYS - set(_PROFILE_ANCHORS) - set(_PROFILE_OPTIONAL_DEFAULTS)
    allowed = _PROFILE_KEYS | set(_PROFILE_OPTIONAL_DEFAULTS)
    _check_keys(profile, required, frozenset(allowed), "volume_profile.")
    profile = {**_PROFILE_OPTIONAL_DEFAULTS, **profile}
    _check_profile_scalars(profile)
    out = {key: profile[key] for key in _PROFILE_KEYS - set(_PROFILE_ANCHORS)}
    options = {key: profile[key] for key in _PROFILE_OPTIONAL_DEFAULTS}
    _check_profile_options(options)
    out.update(options)
    for key in _PROFILE_ANCHORS:
        _check_profile_anchor(key, profile.get(key))
        out[key] = profile.get(key)
    # A fixed range needs both anchors, in order: the client would otherwise draw nothing and fall
    # back on every open (the server is the authority, never more permissive than the reader).
    if out["kind"] == "fixed" and (
        out["start"] is None or out["end"] is None or out["start"] > out["end"]
    ):
        raise LayoutError("volume_profile.start", "a fixed range needs start <= end, both set")
    return out


def _validate_footprint(footprint: Any) -> dict[str, Any]:
    """
    Return the footprint settings (`FOOTPRINT_DEFAULTS` when the table is absent), else raise
    `LayoutError` naming `footprint.<key>`: a present table carries every setting, the two colours
    are optional strings, any other key is refused.
    """
    if footprint is None:
        return dict(FOOTPRINT_DEFAULTS)
    if not isinstance(footprint, dict):
        raise LayoutError("footprint", "must be an object")
    _check_keys(footprint, frozenset(FOOTPRINT_DEFAULTS), _FOOTPRINT_KEYS, "footprint.")
    for key in ("on", "text"):
        if not isinstance(footprint[key], bool):
            raise LayoutError(f"footprint.{key}", "must be a boolean")
    _layout_int("footprint.row_ticks", footprint["row_ticks"], 0, MAX_FOOTPRINT_ROW_TICKS)
    if footprint["mode"] not in FOOTPRINT_MODES:
        raise LayoutError("footprint.mode", f"must be one of {list(FOOTPRINT_MODES)}")
    ratio = footprint["imbalance_ratio"]
    if not _is_number(ratio) or ratio < 1:
        raise LayoutError("footprint.imbalance_ratio", "must be a number of at least 1")
    for key in _FOOTPRINT_COLOR_KEYS & set(footprint):
        if not isinstance(footprint[key], str) or not footprint[key]:
            raise LayoutError(f"footprint.{key}", "must be a non-empty string")
    return dict(footprint)


def _check_derivative_style(prefix: str, style: dict[str, Any]) -> None:
    for key, value in style.items():
        name = f"{prefix}.{key}"
        if key in _DERIVATIVE_STYLE_COLORS and (not isinstance(value, str) or not value):
            raise LayoutError(name, "must be a non-empty string")
        if key == "line_width":
            _layout_int(name, value, 1, MAX_DERIVATIVE_LINE_WIDTH)
        if key == "line_style" and value not in DERIVATIVE_LINE_STYLES:
            raise LayoutError(name, f"must be one of {list(DERIVATIVE_LINE_STYLES)}")


def _validate_derivative_styles(prefix: str, key: str, styles: Any) -> dict[str, Any]:
    if not isinstance(styles, dict):
        raise LayoutError(prefix, "must be an object of output -> style")
    _check_keys(styles, frozenset(), frozenset(DERIVATIVE_OUTPUTS[key]), f"{prefix}.")
    out: dict[str, Any] = {}
    for output, style in styles.items():
        if not isinstance(style, dict):
            raise LayoutError(f"{prefix}.{output}", "must be an object")
        _check_keys(style, frozenset(), _DERIVATIVE_STYLE_KEYS, f"{prefix}.{output}.")
        _check_derivative_style(f"{prefix}.{output}", style)
        out[output] = dict(style)
    return out


def _validate_derivative(key: str, entry: Any) -> dict[str, Any]:
    prefix = f"derivatives.{key}"
    if not isinstance(entry, dict):
        raise LayoutError(prefix, "must be an object")
    required = frozenset(DERIVATIVES_DEFAULTS[key])
    _check_keys(entry, required, required | {"style"}, f"{prefix}.")
    for flag in ("on", "markers"):
        if flag in required and not isinstance(entry[flag], bool):
            raise LayoutError(f"{prefix}.{flag}", "must be a boolean")
    if "measure" in required and entry["measure"] not in LIQUIDATION_MEASURES:
        raise LayoutError(f"{prefix}.measure", f"must be one of {list(LIQUIDATION_MEASURES)}")
    out = {name: entry[name] for name in required}
    if "style" in entry:
        out["style"] = _validate_derivative_styles(f"{prefix}.style", key, entry["style"])
    return out


def _validate_derivatives(derivatives: Any) -> dict[str, Any]:
    """
    Return the derivatives settings (`DERIVATIVES_DEFAULTS`, every entry off, when the table is
    absent), else raise `LayoutError` naming the dotted key (`derivatives.oi.on`): a present table
    carries all five entries, each its required keys, and an unknown key, a wrong type or a style
    outside `color`/`line_width`/`line_style`/`up_color`/`down_color` is refused, never dropped.
    """
    if derivatives is None:
        return copy.deepcopy(DERIVATIVES_DEFAULTS)
    if not isinstance(derivatives, dict):
        raise LayoutError("derivatives", "must be an object")
    keys = frozenset(DERIVATIVE_KEYS)
    _check_keys(derivatives, keys, keys, "derivatives.")
    return {key: _validate_derivative(key, derivatives[key]) for key in DERIVATIVE_KEYS}


def _check_keys(
    table: dict[str, Any],
    required: frozenset[str] | set[str],
    allowed: frozenset[str],
    prefix: str = "",
) -> None:
    """Refuse the first key outside `allowed`, then the first of `required` that is missing."""
    unknown = min(set(table) - allowed, default=None)
    if unknown is not None:
        raise LayoutError(f"{prefix}{unknown}", "is not a field here")
    missing = min(set(required) - set(table), default=None)
    if missing is not None:
        raise LayoutError(f"{prefix}{missing}", "is required")


def _check_timeframe_and_mode(layout: dict[str, Any], *, tolerant: bool) -> None:
    bar_seconds = layout["bar_seconds"]
    if tolerant:
        _layout_int("bar_seconds", bar_seconds, 1, 10**9)
        if not isinstance(layout["mode"], str):
            raise LayoutError("mode", "must be a string")
        return
    if isinstance(bar_seconds, bool) or bar_seconds not in LAYOUT_BAR_SECONDS:
        raise LayoutError("bar_seconds", f"must be one of {list(LAYOUT_BAR_SECONDS)}")
    if layout["mode"] not in LAYOUT_MODES:
        raise LayoutError("mode", f"must be one of {list(LAYOUT_MODES)}")


def _check_pane_heights(heights: Any) -> None:
    if not isinstance(heights, dict):
        raise LayoutError("pane_heights", "must be an object of pane id -> pixels")
    for pane, px in heights.items():
        if not isinstance(pane, str) or not 1 <= len(pane) <= MAX_PANE_ID_LENGTH:
            raise LayoutError(
                "pane_heights", f"a pane id must be a string of 1..{MAX_PANE_ID_LENGTH} characters"
            )
        _layout_int(f"pane_heights.{pane}", px, 1, MAX_PANE_HEIGHT_PX)


def validate_layout(layout: Any, *, tolerant: bool = False) -> dict[str, Any]:
    """
    Return a normalized copy of `layout` (the optional fixed-range anchors always present, `None`
    when unset; the optional `footprint` table always present, `FOOTPRINT_DEFAULTS` when absent,
    likewise the optional `derivatives` table, `DERIVATIVES_DEFAULTS` when absent, and the optional
    `volume_color_by`, `direction` when absent),
    else raise `LayoutError` naming the key. Strict by design (DATA-07): an unknown or missing key
    or a wrong type is refused rather than dropped or defaulted.

    `tolerant=True` is for coin tables read back from disk: a `bar_seconds` outside
    `LAYOUT_BAR_SECONDS` (any positive integer) or a `mode` outside `LAYOUT_MODES` (any string) is
    kept as stored, so a timeframe retired after the save never makes the GET fail; the client falls
    back to its built-in value for that field and says so. The PUT and the `[default]` table are
    always strict.
    """
    if not isinstance(layout, dict):
        raise LayoutError("layout", "must be an object")
    _check_keys(
        layout, _LAYOUT_KEYS, _LAYOUT_KEYS | {"footprint", "derivatives", "volume_color_by"}
    )
    _check_timeframe_and_mode(layout, tolerant=tolerant)
    for key in ("volume", "crosshair"):
        if not isinstance(layout[key], bool):
            raise LayoutError(key, "must be a boolean")
    _check_pane_heights(layout["pane_heights"])
    bars = layout["visible_bars"]
    if not _is_number(bars) or not 0 < bars <= MAX_VISIBLE_BARS:
        raise LayoutError("visible_bars", f"must be a number above 0 up to {MAX_VISIBLE_BARS}")
    return {
        "bar_seconds": layout["bar_seconds"],
        "mode": layout["mode"],
        "volume": layout["volume"],
        "crosshair": layout["crosshair"],
        "pane_heights": dict(layout["pane_heights"]),
        "visible_bars": bars,
        "volume_profile": _validate_profile(layout["volume_profile"]),
        "footprint": _validate_footprint(layout.get("footprint")),
        "derivatives": _validate_derivatives(layout.get("derivatives")),
        "volume_color_by": _validate_volume_color_by(
            layout.get("volume_color_by", VOLUME_COLOR_MODES[0])
        ),
    }


def _validate_volume_color_by(mode: Any) -> str:
    """
    Return the Volume colour mode (the caller passes `direction` when absent), else raise naming
    the key: an explicit null is a wrong value, refused like any other (strict by design).
    """
    if mode not in VOLUME_COLOR_MODES:
        raise LayoutError("volume_color_by", f"must be one of {list(VOLUME_COLOR_MODES)}")
    return str(mode)


def _layout_table(layout: dict[str, Any]) -> dict[str, Any]:
    """Return a validated layout as its TOML table: `v` first, unset anchors omitted (no null)."""
    table: dict[str, Any] = {"v": LAYOUT_VERSION, **layout}
    table["volume_profile"] = {k: v for k, v in layout["volume_profile"].items() if v is not None}
    return table


def _read_layout_table(name: str, table: Any, *, tolerant: bool, extra: frozenset[str]) -> Any:
    if not isinstance(table, dict) or table.get("v") != LAYOUT_VERSION:
        raise LayoutError(name, f"is not a v = {LAYOUT_VERSION} layout table")
    body = {k: v for k, v in table.items() if k != "v" and k not in extra}
    try:
        return validate_layout(body, tolerant=tolerant)
    except LayoutError as exc:
        raise LayoutError(f"{name}.{exc.key}", exc.reason) from exc


def load_chart_layouts(path: Path) -> ChartLayouts:
    """
    Load every coin's layout and the `[default]` template. A missing or empty file is an empty
    `ChartLayouts`; an empty `[default]` table means no template. A malformed table raises
    `LayoutError` (the file is hand-editable and a layout is never silently reset); coin tables are
    tolerant of a stale `bar_seconds`/`mode`, see `validate_layout`.
    """
    if not path.exists():
        return ChartLayouts()
    with path.open("rb") as f:
        raw = tomllib.load(f)
    out = ChartLayouts()
    for name, table in raw.items():
        if name != LAYOUT_DEFAULT_KEY:
            out.layouts[name] = _read_layout_table(name, table, tolerant=True, extra=frozenset())
            continue
        if not isinstance(table, dict):
            raise LayoutError(name, "must be a table")
        if not table:
            continue
        out.default = _read_layout_table(
            name, table, tolerant=False, extra=frozenset({"default_indicators"})
        )
        entries = table.get("default_indicators", [])
        if not isinstance(entries, list) or not all(
            isinstance(e, dict) and {"name", "category"} <= set(e) for e in entries
        ):
            raise LayoutError(
                "default.default_indicators", "must be a list of {name, category, ...} tables"
            )
        out.default_indicators = [_load_indicator_entry(name, e) for e in entries]
    return out


def save_chart_layouts(config: ChartLayouts, path: Path) -> None:
    """
    Persist `config` as TOML, a full rewrite, validated and serialized before the file is touched
    and published atomically (`_write_atomic`). `default` is not a legal instrument id. Without a
    default, no `[default]` table is written (its indicators are then dropped, there is no template
    for them to belong to).
    """
    raw: dict[str, Any] = {}
    for instrument_id, layout in config.layouts.items():
        if instrument_id == LAYOUT_DEFAULT_KEY:
            raise LayoutError(instrument_id, "is reserved for the default template")
        raw[instrument_id] = _layout_table(validate_layout(layout, tolerant=True))
    if config.default is not None:
        table = _layout_table(validate_layout(config.default))
        table["default_indicators"] = [_indicator_table(e) for e in config.default_indicators]
        raw[LAYOUT_DEFAULT_KEY] = table
    _write_atomic(path, tomli_w.dumps(raw).encode())
