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
Incident reports (Story 5.1, generalised in Story 23.1): any WARNING+ log line, from any logger in
the process, gets a permanent human-readable report snapshotting the relevant window of a raw
debug log. It turns "grep a 500 MB debug file by hand" into an automatic, standing capability.

Venue-free by construction (spine AD-D16): the instrument-id pattern, the report title, the raw
log's location and the evidence needle all come from the venue entrypoint as an `IncidentConfig`,
and the entrypoint also supplies the raw log's flush function to `raw_log_flush_loop`.

Classification is a best-effort heuristic over the already-formatted message: a structured JSON
message carrying `reason` is exact; otherwise the entrypoint's `IncidentRule`s are tried in order.
No logger call site needs changing, so any warning added later is covered for free.
"""

import asyncio
import concurrent.futures
import functools
import json
import logging
import re
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC
from datetime import datetime
from pathlib import Path

from observability import error_ledger


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class IncidentRule:
    """A message containing `needle` is an incident of `incident_type`."""

    needle: str
    incident_type: str
    # False for process-wide incidents (e.g. a late sampling tick): no instrument is attached
    # even when the message happens to name one.
    with_instrument: bool = True


@dataclass(frozen=True)
class IncidentConfig:
    """
    Everything venue-specific about incident reports.

    `iid_pattern` must define the named groups `iid` (the full instrument id) and `ticker` (the
    id the raw log's lines carry); `evidence_needle` is a `str.format` template receiving
    `ticker`. The raw log files are `<raw_log_dir>/<raw_log_name>*.log`: nautilus_trader's writer
    names the file `<name>.log` without rotation and, with size rotation, opens a new
    `<name>_<UTC timestamp>.log` and never renames the old one (`crates/common/src/logging/
    writer.rs` `create_log_file_path`/`rotate_file`); both shapes are scanned and pruned.
    """

    report_title: str
    iid_pattern: re.Pattern[str]
    evidence_needle: str
    raw_log_dir: Path
    raw_log_name: str
    report_dir: Path
    rules: tuple[IncidentRule, ...] = ()
    # One ongoing incident logs a fresh WARNING every tick while it persists; without the
    # debounce it would produce one report per tick.
    debounce_ns: int = 10_000_000_000
    lookback_ns: int = 10_000_000_000
    # Extraction waits this long after the trigger so the window also captures what resolves
    # the incident (e.g. the delete that un-crosses a book), not only its run-up.
    lookahead_s: float = 2.0
    # Bounded like the other log sinks; nothing else caps this directory.
    report_dir_max_bytes: int = 200_000_000

    def __post_init__(self) -> None:
        # Checked at construction, not at the first incident: `IncidentHandler.emit` swallows
        # every exception into `handleError`, so a config wrong in this way would silently never
        # write a report.
        missing = {"iid", "ticker"} - set(self.iid_pattern.groupindex)
        if missing:
            raise ValueError(f"iid_pattern must define the named groups {sorted(missing)}")
        if "{ticker}" not in self.evidence_needle:
            raise ValueError("evidence_needle must be a str.format template using {ticker}")


def classify_incident(config: IncidentConfig, message: str) -> tuple[str, str | None]:
    """Best-effort (incident_type, instrument_id) from an already-formatted log message."""
    try:
        payload = json.loads(message)
    except (json.JSONDecodeError, TypeError, RecursionError):  # RecursionError: nested JSON
        payload = None
    if isinstance(payload, dict) and "reason" in payload:
        # Structured CRITICAL escalations are exact; no heuristics needed.
        iid = payload.get("instrument_id")
        # Anything but a string (a number, a list) names no instrument: never a debounce key or a
        # fullmatch subject.
        return str(payload["reason"]), iid if isinstance(iid, str) else None

    match = config.iid_pattern.search(message)
    iid = match.group("iid") if match else None
    for rule in config.rules:
        if rule.needle in message:
            return rule.incident_type, iid if rule.with_instrument else None
    return "unclassified", iid


def _raw_log_glob(config: IncidentConfig) -> str:
    """Every raw log file, unrotated, current or rotated (see `IncidentConfig`)."""
    return f"{config.raw_log_name}*.log"


def ns_to_iso(ns: int) -> str:
    """
    Nanosecond-precision UTC ISO string in the raw log's line-prefix format (same width), so a
    plain string comparison is chronologically correct.
    """
    dt = datetime.fromtimestamp(ns // 1_000_000_000, tz=UTC)
    frac_ns = ns % 1_000_000_000
    return f"{dt.strftime('%Y-%m-%dT%H:%M:%S')}.{frac_ns:09d}Z"


_UNSAFE_NAME_CHARS = re.compile(r"[^A-Za-z0-9._-]")
_NAME_PART_MAX_CHARS = 64


def report_name(incident_type: str, iid: str | None, trigger_ns: int) -> str:
    """
    Return the report's file name. `incident_type` and `iid` are text a log line supplied (a structured
    message's `reason` and `instrument_id`, from any logger in the process), so a path separator,
    a NUL or a character the filesystem rejects would write outside `report_dir` or fail every
    debounce window. Anything outside `[A-Za-z0-9._-]` becomes `_` and each part is capped, so
    the name is always one plain component under the 255-byte limit.
    """
    parts = (
        _UNSAFE_NAME_CHARS.sub("_", part)[:_NAME_PART_MAX_CHARS]
        for part in (incident_type, iid or "system")
    )
    return "_".join(parts) + f"_{trigger_ns // 1_000_000_000}.log"


def ticker_of(config: IncidentConfig, iid: str | None) -> str | None:
    """Return the raw log's id for `iid`; None when `iid` is absent or not the venue's shape."""
    match = config.iid_pattern.fullmatch(iid) if iid else None
    return match.group("ticker") if match else None


def scan_raw_window(config: IncidentConfig, ticker: str, start_ns: int, end_ns: int) -> list[str]:
    """
    Blocking file I/O: call via `asyncio.to_thread`. Scans every rotated file present rather
    than tracking byte ranges: incidents are debounced and the raw log is a bounded rolling
    buffer, so this has never been a measured bottleneck.
    """
    start_ts, end_ts = ns_to_iso(start_ns), ns_to_iso(end_ns)
    needle = config.evidence_needle.format(ticker=ticker)
    matches: list[str] = []
    for path in sorted(config.raw_log_dir.glob(_raw_log_glob(config))):
        try:
            with path.open("r", errors="replace") as f:
                for line in f:
                    if needle in line and start_ts <= line.split(" ", 1)[0] <= end_ts:
                        matches.append(line)
        except OSError:
            continue  # rotated away between glob() and open(): its lines are gone either way
    return matches


def prune_stale_raw_logs(config: IncidentConfig) -> None:
    """
    Delete raw log files left behind by a previous process instance. Call before the raw log's
    writer starts.

    nautilus_trader's FileWriter tracks rotated backups in an in-memory queue
    (`crates/common/src/logging/writer.rs`'s `backup_files`) that is never seeded from files
    already on disk, so across a restart the prior run's files become permanently untracked and
    are never pruned (found: 43 orphaned files / 8 GB on nifelheim). FORK-01 keeps the fix here.
    """
    if not config.raw_log_dir.exists():
        return
    for path in config.raw_log_dir.glob(_raw_log_glob(config)):
        path.unlink(missing_ok=True)


async def raw_log_flush_loop(sync: Callable[[], None], interval_s: float = 2.0) -> None:
    """
    Flush the raw log every `interval_s`: a buffered file logger (Rust's `BufWriter`) only
    writes on an explicit sync, so without this the evidence would sit in memory forever.
    """
    while True:
        await asyncio.sleep(interval_s)
        sync()


class IncidentReportWriter:
    """
    Writes reports and keeps the report directory under its size cap.

    Invariant: prunes are serialised. Reports are written on `asyncio.to_thread` workers, so two
    incidents close together can prune at once; unserialised, one thread's `unlink()` races the
    other's `stat()` and raises `FileNotFoundError` (seen in production). Only this writer
    deletes report files, so one lock per writer (one writer per process) is sufficient.
    """

    def __init__(self, config: IncidentConfig) -> None:
        self._config = config
        self._prune_lock = threading.Lock()

    def write(
        self,
        incident_type: str,
        iid: str | None,
        level: str,
        logger_name: str,
        message: str,
        trigger_ns: int,
    ) -> str:
        """Blocking: call via `asyncio.to_thread`. Returns the report's path."""
        config = self._config
        config.report_dir.mkdir(parents=True, exist_ok=True)
        ticker = ticker_of(config, iid)
        end_ns = trigger_ns + int(config.lookahead_s * 1e9)
        lines = (
            scan_raw_window(config, ticker, trigger_ns - config.lookback_ns, end_ns)
            if ticker
            else []
        )
        path = config.report_dir / report_name(incident_type, iid, trigger_ns)
        with path.open("w") as f:
            f.write(f"=== {config.report_title} ===\n")
            f.write(f"Time: {ns_to_iso(trigger_ns)}\n")
            f.write(f"Level: {level}\n")
            f.write(f"Logger: {logger_name}\n")
            f.write(f"Type: {incident_type}\n")
            f.write(f"Instrument: {iid or '-'}\n")
            f.write(f"Message: {message}\n\n")
            if ticker:
                f.write(f"--- Raw WS evidence ({ticker}, {len(lines)} messages) ---\n")
                f.writelines(lines)
            else:
                f.write(
                    "--- No instrument identified in this message; no raw WS evidence attached ---\n"
                )
        self.prune()
        return str(path)

    def prune(self) -> None:
        """
        Delete the oldest reports until the directory is back under the size cap. A file that
        vanishes between the glob and its stat (an operator tidying the directory) is simply
        gone: not this writer's failure, and never a reason to ledger the report just written.
        """
        with self._prune_lock:
            found: dict[Path, tuple[float, int]] = {}
            for path in self._config.report_dir.glob("*.log"):
                try:
                    stat = path.stat()
                except FileNotFoundError:
                    continue
                found[path] = (stat.st_mtime, stat.st_size)
            total = sum(size for _, size in found.values())
            for path, (_, size) in sorted(found.items(), key=lambda item: item[1][0]):
                if total <= self._config.report_dir_max_bytes:
                    break
                total -= size
                path.unlink(missing_ok=True)


def _ledger_failed_report(
    incident_type: str, iid: str | None, future: concurrent.futures.Future[None]
) -> None:
    """
    Ledger a report that could not be written (disk full, permissions): never lost (DATA-07).

    The ledger's ERROR line reaches the handler again; its debounce bounds that to one retry per
    `debounce_ns` for as long as reports keep failing. A *cancelled* report is not a failure:
    nothing cancels these tasks but the loop's shutdown (`asyncio.run` cancels every pending
    task), where a warning in the last `lookahead_s` loses its evidence report by design. Logged
    at INFO, so it is visible but never re-enters this handler on a closing loop.
    """
    what = f"incident report {incident_type}/{iid or '-'}"
    if future.cancelled():
        logger.info("%s not written: cancelled at shutdown", what)
        return
    exc = future.exception()
    if exc is not None:
        error_ledger.record("observability.incidents.report", f"{what} not written", exc)


class IncidentHandler(logging.Handler):
    """
    Attach to the root logger: catches every current and future WARNING+ record in the process
    (every logger propagates to root by default) without a change at any call site.
    """

    def __init__(
        self, config: IncidentConfig, loop: asyncio.AbstractEventLoop | None = None
    ) -> None:
        super().__init__(level=logging.WARNING)
        self._config = config
        self.writer = IncidentReportWriter(config)
        # Captured once, not looked up in emit(): a record can be emitted from a plain worker
        # thread (e.g. a failed notification inside `asyncio.to_thread`) that has no running
        # loop, where asyncio.get_running_loop() would raise and drop the report.
        # run_coroutine_threadsafe works from the loop's own thread and from any other.
        self._loop = loop or asyncio.get_running_loop()
        self._last_report_ns: dict[tuple[str, str | None], int] = {}

    def emit(self, record: logging.LogRecord) -> None:
        try:
            message = record.getMessage()
            incident_type, iid = classify_incident(self._config, message)
            key = (incident_type, iid)
            now_ns = time.time_ns()
            if now_ns - self._last_report_ns.get(key, 0) < self._config.debounce_ns:
                return
            self._last_report_ns[key] = now_ns
            future = asyncio.run_coroutine_threadsafe(
                self.report(incident_type, iid, record.levelname, record.name, message, now_ns),
                self._loop,
            )
            future.add_done_callback(functools.partial(_ledger_failed_report, incident_type, iid))
        except Exception:
            # logging.Handler's documented convention: emit() never propagates -- a broken
            # report must not crash the process or its logging. handleError() reports to stderr.
            self.handleError(record)

    async def report(
        self,
        incident_type: str,
        iid: str | None,
        level: str,
        logger_name: str,
        message: str,
        trigger_ns: int,
    ) -> None:
        await asyncio.sleep(self._config.lookahead_s)
        path = await asyncio.to_thread(
            self.writer.write, incident_type, iid, level, logger_name, message, trigger_ns
        )
        logger.info("Incident report written [%s/%s]: %s", incident_type, iid or "-", path)
