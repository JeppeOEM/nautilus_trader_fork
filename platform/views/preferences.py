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
added `chart_drawings.toml`). All three live in one directory (`CHART_PREFERENCES_DIR`, Story 32.5):

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
  `trendline`, `fib`, `position`) -- `load_chart_drawings`/`save_chart_drawings`. Each item is
  checked by `validate_drawing`, which names the offending field (`DrawingError`): a malformed
  item is refused, never dropped, so a saved drawing is never silently lost on a round trip.
- `screener_columns.toml`: the screener-wide Technicals column
  selection (Story 17.5), one flat top-level `columns` array of
  `{name, params, category, bar_seconds}` tables in display order, applied to every row of the
  Rankings table -- `load_screener_columns`/`save_screener_columns`.

Both are tomllib to read, tomli_w to write, and a full rewrite (not a patch). Key sets frozen
(AD-D12): both files are bind-mounted and hand-editable, so a renamed or dropped key would silently
lose a saved selection on the next deploy; `views/tests/test_preferences.py` pins the written text.
The freeze forbids renaming or dropping a key, not adding an optional one with a default: every
existing file stays loadable. The screener's `columns` entries never carry the three new keys
(`save_screener_columns` writes its own four).
The paths themselves are the interface's (env vars read in `data_api`), passed in.
"""

import logging
import math
import os
import tomllib
from dataclasses import dataclass
from dataclasses import field
from pathlib import Path
from typing import Any

import tomli_w


DEFAULT_BAR_SECONDS = 3600

# `chart_drawings.toml` (Story 32.5): the table layout version and the closed set of item kinds.
DRAWINGS_VERSION = 1
DRAWING_KINDS = ("hline", "trendline", "fib", "position")
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


# Per kind: the keys beyond `kind`/`id`/`color` it may carry; any other key is refused.
_DRAWING_KEYS: dict[str, frozenset[str]] = {
    "hline": frozenset({"price"}),
    "trendline": frozenset({"anchors"}),
    "fib": frozenset({"anchors", "levels", "extend_right", "label_side", "line_width"}),
    "position": frozenset(
        {"side", "time", "entry", "stop", "target", "width_bars", "account", "risk_pct"}
    ),
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
    else:
        _check_position(item)
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
