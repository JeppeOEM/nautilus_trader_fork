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
added `chart_drawings.toml`, Story 32.6 `chart_layouts.toml`, Story 33.7
`screener_filter_presets.toml`, Story 33.12 `chart_watchlist.toml`). All six live in one directory
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
  `trendline`, `fib`, `position`, `anchored_vp`, `anchored_vwap`; Story 33.10 added `ray`,
  `extended`, `vline`, `rect`, `channel`, `text`, `arrow`, `fib_extension`, `price_range` and
  `date_range`, plus the optional `locked`/`hidden` on every kind and `line_width`/`line_style` on
  the line-like kinds, absent = false / 1 px solid) --
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
  while the PUT validation and the `[default]` table stay strict. Drawings are never part of it;
  only the optional `drawings_hidden` (bool, default false; Story 33.10) hides them all. Story 33.12
  added the optional `time_zone` (one of `TIME_ZONES`, default `utc`; display only, a bar's `t`
  stays UTC), `session_breaks` (bool, default false), `bar_countdown` (bool, default true) and
  `last_price` (a `{line, label}` table of two required booleans, default both true).
- `screener_columns.toml`: the screener-wide Technicals column
  selection (Story 17.5), one flat top-level `columns` array of
  `{name, params, category, bar_seconds}` tables in display order, applied to every row of the
  Rankings table -- `load_screener_columns`/`save_screener_columns`.
- `screener_filter_presets.toml`: the Rankings page's named filter presets (Story 33.7), `v = 1`
  and a `[[presets]]` array of `{name, conditions}` tables, each condition a `[[presets.conditions]]`
  table of `field`, `op` (one of `FILTER_OPERATORS`) and `value` (a finite number, or a string with
  `=` only) -- `load_filter_presets`/`save_filter_presets`. Every preset is checked by
  `validate_filter_presets`, which names the offending field (`FilterPresetError`); a condition's
  display precision is never stored (the page re-derives it from its field list on recall).
- `chart_watchlist.toml`: the chart page's pinned instruments (Story 33.12), `v = 1` and one flat
  `instruments` array of distinct instrument ids in the operator's order, at most `MAX_WATCHLIST`
  -- `load_watchlist`/`save_watchlist`, checked by `validate_watchlist` (`WatchlistError` names the
  entry, `instruments[3]`). A UI-only list: neither `research/watchlist.py`'s research watchlist
  nor the collection plan or the coin ranking (the DDD glossary forbids "watchlist" for those).

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
from collections.abc import Callable
from dataclasses import dataclass
from dataclasses import field
from pathlib import Path
from typing import Any

import tomli_w
from kernel.venues import MalformedInstrumentId
from kernel.venues import venue_of


DEFAULT_BAR_SECONDS = 3600

# `chart_drawings.toml` (Story 32.5): the table layout version and the closed set of item kinds.
# Story 33.10 appends the ten kinds of the second drawing set; mirrors the frontend's
# `DRAWING_KIND_NAMES` (`lib/drawings.ts`; `test_the_closed_sets_mirror_the_frontend`).
DRAWINGS_VERSION = 1
DRAWING_KINDS = (
    "hline",
    "trendline",
    "fib",
    "position",
    "anchored_vp",
    "anchored_vwap",
    "ray",
    "extended",
    "vline",
    "rect",
    "channel",
    "text",
    "arrow",
    "fib_extension",
    "price_range",
    "date_range",
)
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
# Story 33.10: the optional `line_width`/`line_style` of the line-like kinds and the text drawing's
# bounds. `LINE_STYLES` is also the derivatives' (`DERIVATIVE_LINE_STYLES` aliases it). Mirror the
# frontend's `LINE_STYLES` (`lib/indicatorStyle.ts`, re-exported by
# `lib/drawings.ts`), `MAX_TEXT_LENGTH`, `MIN_FONT_SIZE` and `MAX_FONT_SIZE` (`lib/drawings.ts`);
# `test_line_styles_mirror_the_frontend` and `test_text_and_font_bounds_mirror_the_frontend` pin them.
LINE_STYLES = ("solid", "dashed", "dotted")
MAX_DRAWING_TEXT_LENGTH = 500
MIN_DRAWING_FONT_SIZE = 8
MAX_DRAWING_FONT_SIZE = 72

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


def _check_point(point: Any, field_name: str) -> None:
    """Check one `{time, price}` point: UTC seconds (an integer) and a finite price."""
    if not isinstance(point, dict) or set(point) != {"time", "price"}:
        raise DrawingError(field_name, "each point must be exactly {time, price}")
    _check_time(point["time"], field_name)
    if not _is_number(point["price"]):
        raise DrawingError(field_name, "price must be a finite number")


def _check_anchors(item: dict[str, Any], count: int = 2) -> None:
    """`anchors` is exactly `count` `{time, price}` points (two, or a Fib extension's three)."""
    anchors = item.get("anchors")
    if not isinstance(anchors, list) or len(anchors) != count:
        raise DrawingError("anchors", f"must be a list of exactly {count} {{time, price}} points")
    for anchor in anchors:
        _check_point(anchor, "anchors")


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


def _check_two_anchors(item: dict[str, Any]) -> None:
    _check_anchors(item, 2)


def _check_hline(item: dict[str, Any]) -> None:
    _require_number(item, "price", positive=True)


def _check_fib(item: dict[str, Any]) -> None:
    _check_anchors(item, 2)
    _check_fib_options(item)


def _check_fib_extension(item: dict[str, Any]) -> None:
    """Check a trend-based Fibonacci extension: A, B, C, each level at `C + (B - A) * ratio`."""
    _check_anchors(item, 3)
    _check_fib_options(item)


def _check_vline(item: dict[str, Any]) -> None:
    _check_time(item.get("time"), "time")


def _check_rect(item: dict[str, Any]) -> None:
    """Check a rectangle: two opposite corners and the fill's opacity, a number in [0, 1]."""
    _check_anchors(item, 2)
    _require_number(item, "fill_opacity")
    if not 0 <= item["fill_opacity"] <= 1:
        raise DrawingError("fill_opacity", "must be between 0 and 1")


def _check_channel(item: dict[str, Any]) -> None:
    """Check a channel: the base line A-B and the parallel's price offset (any sign, finite)."""
    _check_anchors(item, 2)
    _require_number(item, "offset")


def _check_text(item: dict[str, Any]) -> None:
    """Check a text: one `{time, price}` anchor, a non-blank bounded text and a font size in px."""
    if "anchor" not in item:
        raise DrawingError("anchor", "is required")
    _check_point(item["anchor"], "anchor")
    text = item.get("text")
    if not isinstance(text, str) or not text.strip():
        raise DrawingError("text", "must be a non-empty string")
    if len(text) > MAX_DRAWING_TEXT_LENGTH:
        raise DrawingError("text", f"must be at most {MAX_DRAWING_TEXT_LENGTH} characters")
    # A lone UTF-16 surrogate (a half-pasted emoji, sent as a `\udXXX` JSON escape) parses into a
    # str the TOML file cannot encode: refused here, never a 500 from the save.
    try:
        text.encode("utf-8")
    except UnicodeEncodeError:
        raise DrawingError("text", "must be valid Unicode (no lone surrogate)") from None
    _require_int(item, "font_size", MIN_DRAWING_FONT_SIZE, MAX_DRAWING_FONT_SIZE)


def _check_flags(item: dict[str, Any]) -> None:
    """Check the optional `locked`/`hidden` of every kind (absent = false): booleans if present."""
    for key in ("locked", "hidden"):
        if key in item and not isinstance(item[key], bool):
            raise DrawingError(key, "must be a boolean")


def _check_line_look(item: dict[str, Any]) -> None:
    """Check a line-like kind's optional `line_width` (1..4) and `line_style` (absent: 1, solid)."""
    if "line_width" in item:
        _require_int(item, "line_width", 1, MAX_DRAWING_LINE_WIDTH)
    if "line_style" in item and item["line_style"] not in LINE_STYLES:
        raise DrawingError("line_style", f"must be one of {list(LINE_STYLES)}")


_FIB_KEYS = frozenset({"anchors", "levels", "extend_right", "label_side", "line_width"})
# The kinds drawn as lines, which may carry the optional `line_width`/`line_style`.
_LINE_LIKE_KINDS = frozenset(
    {
        "hline",
        "trendline",
        "ray",
        "extended",
        "vline",
        "rect",
        "channel",
        "arrow",
        "price_range",
        "date_range",
    }
)
_LINE_LOOK_KEYS = frozenset({"line_width", "line_style"})
# Per kind: the keys beyond `kind`/`id`/`color`/`locked`/`hidden` it may carry (the line-like kinds
# also `_LINE_LOOK_KEYS`); any other key is refused.
_DRAWING_KEYS: dict[str, frozenset[str]] = {
    "hline": frozenset({"price"}),
    "trendline": frozenset({"anchors"}),
    "fib": _FIB_KEYS,
    "position": frozenset(
        {"side", "time", "entry", "stop", "target", "width_bars", "account", "risk_pct"}
    ),
    "anchored_vp": frozenset({"time", "rows", "value_area_pct", "up_color", "down_color"}),
    "anchored_vwap": frozenset({"time", "source", "bands", "band_color"}),
    "ray": frozenset({"anchors"}),
    "extended": frozenset({"anchors"}),
    "vline": frozenset({"time"}),
    "rect": frozenset({"anchors", "fill_opacity"}),
    "channel": frozenset({"anchors", "offset"}),
    "text": frozenset({"anchor", "text", "font_size"}),
    "arrow": frozenset({"anchors"}),
    "fib_extension": _FIB_KEYS,
    "price_range": frozenset({"anchors"}),
    "date_range": frozenset({"anchors"}),
}
# Per kind: the check of its own fields (the shared ones are checked by `validate_drawing`).
_DRAWING_CHECKS: dict[str, Callable[[dict[str, Any]], None]] = {
    "hline": _check_hline,
    "trendline": _check_two_anchors,
    "fib": _check_fib,
    "position": _check_position,
    "anchored_vp": _check_anchored_vp,
    "anchored_vwap": _check_anchored_vwap,
    "ray": _check_two_anchors,
    "extended": _check_two_anchors,
    "vline": _check_vline,
    "rect": _check_rect,
    "channel": _check_channel,
    "text": _check_text,
    "arrow": _check_two_anchors,
    "fib_extension": _check_fib_extension,
    "price_range": _check_two_anchors,
    "date_range": _check_two_anchors,
}


def _allowed_keys(kind: str) -> frozenset[str]:
    shared = frozenset({"kind", "id", "color", "locked", "hidden"})
    look = _LINE_LOOK_KEYS if kind in _LINE_LIKE_KINDS else frozenset()
    return shared | look | _DRAWING_KEYS[kind]


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
    unknown = set(item) - _allowed_keys(kind)
    if unknown:
        raise DrawingError(sorted(unknown)[0], f"is not a field of a {kind}")
    _check_flags(item)
    if kind in _LINE_LIKE_KINDS:
        _check_line_look(item)
    _DRAWING_CHECKS[kind](item)
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
# The one line-style set (Story 33.10's `LINE_STYLES`, the drawings' too): an alias, never a copy.
DERIVATIVE_LINE_STYLES = LINE_STYLES
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

# Story 33.9: the optional `chart_type`, `price_scale` and `compare` keys, how the price pane draws
# the main series, its right scale's mode and up to three compare symbols (plus the cross-venue
# Spread pane). Mirror the frontend's `lib/chartTypes.ts` (`CHART_TYPES`, `PRICE_SCALE_MODES`,
# `MAX_COMPARE_SYMBOLS`) and `lib/chartLayout.ts` (`DEFAULT_PRICE_SCALE`, `DEFAULT_COMPARE`);
# `test_chart_type_and_scale_settings_mirror_the_frontend` pins the pairs. A layout saved before
# them loads with `candles`, `PRICE_SCALE_DEFAULTS` and `COMPARE_DEFAULTS`.
CHART_TYPES = ("candles", "hollow", "bars", "line", "area", "baseline", "heikin_ashi")
PRICE_SCALE_MODES = ("normal", "log", "percent", "indexed")
MAX_COMPARE_SYMBOLS = 3
# A compare symbol is an instrument id (`SYMBOL.VENUE`, venue ids run to ~30 characters); no kernel
# bound exists, so this cap only bounds a hostile value. Mirrors the frontend's
# `MAX_INSTRUMENT_ID_LENGTH` (`lib/compare.ts`; `test_instrument_id_length_mirrors_the_frontend`).
MAX_INSTRUMENT_ID_LENGTH = 512
PRICE_SCALE_DEFAULTS: dict[str, Any] = {"mode": "normal", "auto_scale": True, "invert": False}
COMPARE_DEFAULTS: dict[str, Any] = {"symbols": [], "spread": False}
# Story 33.10: the optional `drawings_hidden` key, the tool rail's "Hide all drawings" (every drawing
# of the coin neither drawn nor hit-tested, and the drawing tools off). A layout saved before it
# loads with `False`; the drawings themselves are never part of a layout.
# Story 33.12: the optional `time_zone` (how the chart page prints a time: `utc`, the viewer's
# `local` zone or the `exchange`'s; formatting only, a bar's `t` stays UTC, audit D-218),
# `session_breaks` (a dashed line at the first bar of each UTC day), `bar_countdown` (the time to
# the last bar's close under the last-price label, from the viewer's clock, audit D-219) and
# `last_price` (`{line, label}`: the main series' last-price line and its axis label). Mirror the
# frontend's `TimeZoneSetting` (`lib/time.ts`) and `BUILT_IN_LAYOUT` (`lib/chartLayout.ts`);
# `test_time_zone_and_last_price_settings_mirror_the_frontend` pins them. A layout saved before them
# loads with `utc`, no session breaks, the countdown on and both last-price parts shown.
TIME_ZONES = ("utc", "local", "exchange")
LAST_PRICE_DEFAULTS: dict[str, bool] = {"line": True, "label": True}
_OPTIONAL_LAYOUT_KEYS = frozenset(
    {
        "footprint",
        "derivatives",
        "volume_color_by",
        "chart_type",
        "price_scale",
        "compare",
        "drawings_hidden",
        "time_zone",
        "session_breaks",
        "bar_countdown",
        "last_price",
    }
)

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
    "chart_type": CHART_TYPES[0],
    "price_scale": dict(PRICE_SCALE_DEFAULTS),
    "compare": copy.deepcopy(COMPARE_DEFAULTS),
    "drawings_hidden": False,
    "time_zone": TIME_ZONES[0],
    "session_breaks": False,
    "bar_countdown": True,
    "last_price": dict(LAST_PRICE_DEFAULTS),
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
    `volume_color_by`, `direction` when absent; Story 33.9's `chart_type`, `price_scale` and
    `compare`, `candles`, `PRICE_SCALE_DEFAULTS` and `COMPARE_DEFAULTS` when absent; Story 33.10's
    `drawings_hidden`, `False` when absent; Story 33.12's `time_zone`, `session_breaks`,
    `bar_countdown` and `last_price`, `utc`, `False`, `True` and `LAST_PRICE_DEFAULTS` when absent),
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
    _check_keys(layout, _LAYOUT_KEYS, _LAYOUT_KEYS | _OPTIONAL_LAYOUT_KEYS)
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
        "chart_type": _validate_chart_type(layout.get("chart_type", CHART_TYPES[0])),
        "price_scale": _validate_price_scale(layout.get("price_scale", PRICE_SCALE_DEFAULTS)),
        "compare": _validate_compare(layout.get("compare", COMPARE_DEFAULTS)),
        "drawings_hidden": _validate_flag("drawings_hidden", layout.get("drawings_hidden", False)),
        "time_zone": _validate_time_zone(layout.get("time_zone", TIME_ZONES[0])),
        "session_breaks": _validate_flag("session_breaks", layout.get("session_breaks", False)),
        "bar_countdown": _validate_flag("bar_countdown", layout.get("bar_countdown", True)),
        "last_price": _validate_last_price(layout.get("last_price", LAST_PRICE_DEFAULTS)),
    }


def _validate_volume_color_by(mode: Any) -> str:
    """
    Return the Volume colour mode (the caller passes `direction` when absent), else raise naming
    the key: an explicit null is a wrong value, refused like any other (strict by design).
    """
    if mode not in VOLUME_COLOR_MODES:
        raise LayoutError("volume_color_by", f"must be one of {list(VOLUME_COLOR_MODES)}")
    return str(mode)


def _validate_time_zone(zone: Any) -> str:
    """Return the display time zone (`utc` passed when absent), else raise: a null is refused."""
    if zone not in TIME_ZONES:
        raise LayoutError("time_zone", f"must be one of {list(TIME_ZONES)}")
    return str(zone)


def _validate_flag(key: str, flag: Any) -> bool:
    """
    Return an optional boolean key (its default passed when absent), else raise naming `key`: a
    null is refused.
    """
    if not isinstance(flag, bool):
        raise LayoutError(key, "must be a boolean")
    return flag


def _validate_last_price(last_price: Any) -> dict[str, bool]:
    """
    Return the last-price settings (the caller passes `LAST_PRICE_DEFAULTS` when the table is
    absent), else raise `LayoutError` naming `last_price.<key>`: a present table carries both `line`
    and `label`, booleans, and an explicit null is a wrong value, refused (strict by design).
    """
    if not isinstance(last_price, dict):
        raise LayoutError("last_price", "must be an object")
    keys = frozenset(LAST_PRICE_DEFAULTS)
    _check_keys(last_price, keys, keys, "last_price.")
    return {
        key: _validate_flag(f"last_price.{key}", last_price[key]) for key in LAST_PRICE_DEFAULTS
    }


def _validate_chart_type(chart_type: Any) -> str:
    """Return the main series' chart type (`candles` passed when absent), else raise naming it."""
    if chart_type not in CHART_TYPES:
        raise LayoutError("chart_type", f"must be one of {list(CHART_TYPES)}")
    return str(chart_type)


def _validate_price_scale(scale: Any) -> dict[str, Any]:
    """
    Return the right price scale's settings (the caller passes `PRICE_SCALE_DEFAULTS` when the
    table is absent), else raise `LayoutError` naming `price_scale.<key>`: a present table carries
    all three keys, and an explicit null is a wrong value, refused (strict by design).
    """
    if not isinstance(scale, dict):
        raise LayoutError("price_scale", "must be an object")
    keys = frozenset(PRICE_SCALE_DEFAULTS)
    _check_keys(scale, keys, keys, "price_scale.")
    if scale["mode"] not in PRICE_SCALE_MODES:
        raise LayoutError("price_scale.mode", f"must be one of {list(PRICE_SCALE_MODES)}")
    for key in ("auto_scale", "invert"):
        if not isinstance(scale[key], bool):
            raise LayoutError(f"price_scale.{key}", "must be a boolean")
    return {key: scale[key] for key in PRICE_SCALE_DEFAULTS}


def _check_compare_symbol(index: int, symbol: Any) -> None:
    name = f"compare.symbols.{index}"
    if not isinstance(symbol, str) or not 1 <= len(symbol) <= MAX_INSTRUMENT_ID_LENGTH:
        raise LayoutError(name, f"must be a string of 1..{MAX_INSTRUMENT_ID_LENGTH} characters")
    try:
        venue_of(symbol)
    except MalformedInstrumentId as exc:
        raise LayoutError(name, "must be an instrument id with a .VENUE suffix") from exc


def _validate_compare(compare: Any) -> dict[str, Any]:
    """
    Return the compare settings (the caller passes `COMPARE_DEFAULTS` when the table is absent),
    else raise `LayoutError` naming `compare.<key>`: at most `MAX_COMPARE_SYMBOLS` distinct
    instrument ids and a boolean `spread`. A duplicate is refused, never deduplicated (DATA-07), and
    an explicit null is a wrong value, refused.
    """
    if not isinstance(compare, dict):
        raise LayoutError("compare", "must be an object")
    keys = frozenset(COMPARE_DEFAULTS)
    _check_keys(compare, keys, keys, "compare.")
    symbols = compare["symbols"]
    if not isinstance(symbols, list) or len(symbols) > MAX_COMPARE_SYMBOLS:
        raise LayoutError("compare.symbols", f"must be a list of at most {MAX_COMPARE_SYMBOLS} ids")
    for index, symbol in enumerate(symbols):
        _check_compare_symbol(index, symbol)
    if len(set(symbols)) != len(symbols):
        raise LayoutError("compare.symbols", "must not repeat an instrument id")
    if not isinstance(compare["spread"], bool):
        raise LayoutError("compare.spread", "must be a boolean")
    return {"symbols": list(symbols), "spread": compare["spread"]}


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


# -- screener filter presets ------------------------------------------------------------------------

# `screener_filter_presets.toml` (Story 33.7). `FILTER_OPERATORS` mirrors the frontend's
# `FILTER_OPERATORS` (`pages/filters.ts`; `views/tests/test_filter_presets.py` pins the pair).
FILTER_PRESETS_VERSION = 1
FILTER_OPERATORS = (">", "<", ">=", "<=", "=")
MAX_FILTER_PRESETS = 100
MAX_PRESET_CONDITIONS = 50
MAX_PRESET_NAME_LENGTH = 64
MAX_FILTER_FIELD_LENGTH = 512
MAX_FILTER_TEXT_LENGTH = 512
_PRESET_KEYS = frozenset({"name", "conditions"})
_CONDITION_KEYS = frozenset({"field", "op", "value"})


class FilterPresetError(ValueError):
    """A preset (or the preset list) that is not storable; `field` names what is wrong."""

    def __init__(self, field_name: str, message: str) -> None:
        super().__init__(f"{field_name}: {message}")
        self.field = field_name


@dataclass(frozen=True)
class FilterPresetCondition:
    """One stored condition. Its display precision is not stored: the page re-derives it on recall."""

    field: str
    op: str
    value: float | int | str


@dataclass(frozen=True)
class FilterPreset:
    name: str
    conditions: tuple[FilterPresetCondition, ...]


def _check_keys_exactly(where: str, item: Any, keys: frozenset[str]) -> dict[str, Any]:
    if not isinstance(item, dict):
        raise FilterPresetError(where, "must be an object")
    missing = sorted(keys - set(item))
    if missing:
        raise FilterPresetError(f"{where}.{missing[0]}", "is required")
    unknown = sorted(set(item) - keys)
    if unknown:
        raise FilterPresetError(f"{where}.{unknown[0]}", "is not a field")
    return item


def _validate_condition(where: str, item: Any) -> FilterPresetCondition:
    condition = _check_keys_exactly(where, item, _CONDITION_KEYS)
    field_name, op, value = condition["field"], condition["op"], condition["value"]
    if not isinstance(field_name, str) or not 1 <= len(field_name) <= MAX_FILTER_FIELD_LENGTH:
        raise FilterPresetError(
            f"{where}.field", f"must be a string of 1..{MAX_FILTER_FIELD_LENGTH} characters"
        )
    if op not in FILTER_OPERATORS:
        raise FilterPresetError(f"{where}.op", f"must be one of {list(FILTER_OPERATORS)}")
    if isinstance(value, str):
        if op != "=":
            raise FilterPresetError(f"{where}.value", "a text value takes only the `=` operator")
        if len(value) > MAX_FILTER_TEXT_LENGTH:
            raise FilterPresetError(
                f"{where}.value", f"must be at most {MAX_FILTER_TEXT_LENGTH} characters"
            )
    elif isinstance(value, int) and not isinstance(value, bool) and not _exact_in_a_double(value):
        raise FilterPresetError(
            f"{where}.value", "an integer must be exact in a browser number (a 64-bit double)"
        )
    elif not _is_number(value):
        raise FilterPresetError(f"{where}.value", "must be a finite number or a string")
    return FilterPresetCondition(field=field_name, op=op, value=value)


def _exact_in_a_double(value: int) -> bool:
    """
    Whether a browser's number (a double) holds `value` exactly: one it does not would be silently
    rounded by `JSON.parse` on the next GET, so the preset read back would not be the one saved. An
    integer too large for any double (`10**400`) is not, rather than an `OverflowError`.
    """
    try:
        return int(float(value)) == value
    except OverflowError:
        return False


def _validate_preset(where: str, item: Any) -> FilterPreset:
    preset = _check_keys_exactly(where, item, _PRESET_KEYS)
    name = preset["name"]
    if not isinstance(name, str) or not name.strip():
        raise FilterPresetError(f"{where}.name", "must be a non-empty string")
    if len(name.strip()) > MAX_PRESET_NAME_LENGTH:
        raise FilterPresetError(
            f"{where}.name", f"must be at most {MAX_PRESET_NAME_LENGTH} characters"
        )
    conditions = preset["conditions"]
    if not isinstance(conditions, list) or not 1 <= len(conditions) <= MAX_PRESET_CONDITIONS:
        raise FilterPresetError(
            f"{where}.conditions", f"must be a list of 1..{MAX_PRESET_CONDITIONS} conditions"
        )
    return FilterPreset(
        name=name.strip(),
        conditions=tuple(
            _validate_condition(f"{where}.conditions[{j}]", c) for j, c in enumerate(conditions)
        ),
    )


def validate_filter_presets(body: Any) -> list[FilterPreset]:
    """
    Return the presets of a `{"presets": [...]}` body, else raise `FilterPresetError` naming the
    field (`presets[1].conditions[0].value`). Strict (DATA-07): an unknown key, a wrong type, a
    duplicate name or one over a bound is refused, never dropped. A name is stored stripped, and
    two names equal once stripped are duplicates.
    """
    if not isinstance(body, dict) or set(body) != {"presets"}:
        raise FilterPresetError("presets", 'the body must be exactly {"presets": [...]}')
    preset_list = body["presets"]
    if not isinstance(preset_list, list):
        raise FilterPresetError("presets", "must be a list of presets")
    if len(preset_list) > MAX_FILTER_PRESETS:
        raise FilterPresetError("presets", f"must hold at most {MAX_FILTER_PRESETS} presets")
    presets: list[FilterPreset] = []
    for index, item in enumerate(preset_list):
        preset = _validate_preset(f"presets[{index}]", item)
        if any(p.name == preset.name for p in presets):
            raise FilterPresetError(f"presets[{index}].name", f"{preset.name!r} appears twice")
        presets.append(preset)
    return presets


def load_filter_presets(path: Path) -> list[FilterPreset]:
    """
    Load the stored presets. A missing file (none saved yet) is `[]`; a file of another version, a
    stray top-level key or a malformed preset raises (`FilterPresetError`, or `TOMLDecodeError`) --
    the file is hand-editable and a preset is never silently skipped.
    """
    if not path.exists():
        return []
    with path.open("rb") as f:
        raw = tomllib.load(f)
    version = raw.get("v")
    if (
        type(version) is not int
        or version != FILTER_PRESETS_VERSION
        or not set(raw) <= {"v", "presets"}
    ):
        raise FilterPresetError(
            "v", f"is not a v = {FILTER_PRESETS_VERSION} presets file (only `v` and `presets`)"
        )
    return validate_filter_presets({"presets": raw.get("presets", [])})


def _preset_table(preset: FilterPreset) -> dict[str, Any]:
    return {
        "name": preset.name,
        "conditions": [{"field": c.field, "op": c.op, "value": c.value} for c in preset.conditions],
    }


def save_filter_presets(presets: list[FilterPreset], path: Path) -> None:
    """
    Persist the whole preset list (`v = 1`, `[[presets]]` with `[[presets.conditions]]`), a full
    rewrite: validated and serialized before the file is touched, then published atomically
    (`_write_atomic`). Callers serialize concurrent writers (data_api's `PREFERENCES_LOCK`).
    """
    raw = {"v": FILTER_PRESETS_VERSION, "presets": [_preset_table(p) for p in presets]}
    validate_filter_presets({"presets": raw["presets"]})
    _write_atomic(path, tomli_w.dumps(raw).encode())


# -- chart watchlist --------------------------------------------------------------------------------

# `chart_watchlist.toml` (Story 33.12): the chart page's pinned instruments, one list for the whole
# UI. An id is checked only for its `.VENUE` suffix and length (`_check_compare_symbol`'s rule), not
# against the live market list: a market delisted or a venue down later must not make the file
# unloadable, and the rail shows such an id with `—` values.
WATCHLIST_VERSION = 1
MAX_WATCHLIST = 200


class WatchlistError(ValueError):
    """A watchlist (or the watchlist file) that is not storable; `field` names what is wrong."""

    def __init__(self, field_name: str, message: str) -> None:
        super().__init__(f"{field_name}: {message}")
        self.field = field_name


def _check_watchlist_id(where: str, instrument_id: Any) -> str:
    if (
        not isinstance(instrument_id, str)
        or not 1 <= len(instrument_id) <= MAX_INSTRUMENT_ID_LENGTH
    ):
        raise WatchlistError(where, f"must be a string of 1..{MAX_INSTRUMENT_ID_LENGTH} characters")
    try:
        venue_of(instrument_id)
    except MalformedInstrumentId as exc:
        raise WatchlistError(where, "must be an instrument id with a .VENUE suffix") from exc
    return instrument_id


def validate_watchlist(body: Any) -> list[str]:
    """
    Return the ids of an `{"instruments": [...]}` body in order, else raise `WatchlistError` naming
    the entry (`instruments[3]`). Strict (DATA-07): an unknown key, a wrong type, a malformed or
    over-long id, a duplicate or a list over `MAX_WATCHLIST` is refused, never dropped or
    deduplicated.
    """
    if not isinstance(body, dict) or set(body) != {"instruments"}:
        raise WatchlistError("instruments", 'the body must be exactly {"instruments": [...]}')
    entries = body["instruments"]
    if not isinstance(entries, list):
        raise WatchlistError("instruments", "must be a list of instrument ids")
    if len(entries) > MAX_WATCHLIST:
        raise WatchlistError("instruments", f"must hold at most {MAX_WATCHLIST} instruments")
    ids: list[str] = []
    for index, entry in enumerate(entries):
        instrument_id = _check_watchlist_id(f"instruments[{index}]", entry)
        if instrument_id in ids:
            raise WatchlistError(f"instruments[{index}]", f"{instrument_id!r} appears twice")
        ids.append(instrument_id)
    return ids


def load_watchlist(path: Path) -> list[str]:
    """
    Load the pinned ids. A missing file (none pinned yet) is `[]`; a file of another version, a
    stray top-level key or a malformed entry raises (`WatchlistError`, or `TOMLDecodeError`) -- the
    file is hand-editable and a pinned id is never silently skipped.
    """
    if not path.exists():
        return []
    with path.open("rb") as f:
        raw = tomllib.load(f)
    version = raw.get("v")
    if (
        type(version) is not int
        or version != WATCHLIST_VERSION
        or not set(raw) <= {"v", "instruments"}
    ):
        raise WatchlistError(
            "v", f"is not a v = {WATCHLIST_VERSION} watchlist file (only `v` and `instruments`)"
        )
    return validate_watchlist({"instruments": raw.get("instruments", [])})


def save_watchlist(instrument_ids: list[str], path: Path) -> None:
    """
    Persist the whole list (`v = 1`, `instruments = [...]`), a full rewrite: validated and
    serialized before the file is touched, then published atomically (`_write_atomic`). Callers
    serialize concurrent writers (data_api's `PREFERENCES_LOCK`).
    """
    ids = validate_watchlist({"instruments": instrument_ids})
    raw = {"v": WATCHLIST_VERSION, "instruments": ids}
    _write_atomic(path, tomli_w.dumps(raw).encode())
