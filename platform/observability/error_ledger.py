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
One place where "our code malfunctioned and we carried on" is made impossible to miss (DATA-07).

Every site that must continue past a failure (a dropped message, a skipped coin, an unreadable
file) calls `record()` instead of a bare log line: it logs at ERROR with the traceback AND counts
the site, so `GET /api/errors` (and the frontend's error bar) can show that something is wrong
without anyone reading Dozzle. The in-memory counts are per-process and reset on restart -- they
say "this process has seen N failures at this site", never "the failure went away".

Story 23.3 adds the durable half. When `ERROR_LEDGER_DIR` is set and the process has called
`start()`, every `record()` also appends one JSON line to
`<ERROR_LEDGER_DIR>/<ERROR_LEDGER_SERVICE>.jsonl`, flushed before `record()` returns, so a day's
failures survive container restarts, image rebuilds and Redis loss (Redis has no volume and
`appendonly no` here on purpose -- this story does not change that). Fields:

    {"ts_ns", "service", "pid", "site", "detail", "exc_type", "suppressed"}

`start()` (called once by each process entrypoint -- `collector_core.collector.run_forever`,
`ranking_engine.engine`'s `__main__`, `data_api.app`'s lifespan, `live_paper.node.main`,
`bot_tui.app.main`) writes a `{"site": "process_start", ...}` line carrying the pid and, when
`ERROR_LEDGER_REVISION` is exported, the code revision, so a reader can tell a zero-error window
from a restarted one even when nothing ever failed. `detail` is truncated to `_MAX_DETAIL_CHARS`
in the file only (the ERROR log line keeps it whole).

Storm bound: at most `ERROR_LEDGER_MAX_LINES_PER_SITE_PER_MIN` (default 60) lines per site per
UTC minute are written; records past the cap are counted and the next written line for that site
carries the exact number in `suppressed`, so `lines + sum(suppressed)` is the true record count.
Files rotate by size (`<service>.jsonl`, `.1` .. `.N`; `ERROR_LEDGER_MAX_BYTES` default 20 MB,
`ERROR_LEDGER_BACKUP_COUNT` default 10, mirroring the compose `x-logging` policy).

Known limit: rotation is size-based, so a sustained storm can age a day's lines out of the
`20 MB x 10` window (at the 60/site/min cap that takes hundreds of sites erroring flat out for a
whole day); the suppressed carry keeps totals exact inside the window but not across a dropped
file. Upgrade path: raise the two env vars, or ship the files to object storage alongside the
catalog backup (story 22.11's rclone remote).

Known limit: a site's pending suppressed count only reaches disk on that site's *next* write --
`close()` (called by `reset()` in tests) does not flush it, and nothing calls `close()` in
production (Docker sends SIGTERM/SIGKILL, not a clean shutdown hook), so the last bucket's
suppressed count for a site that goes quiet right before a crash or redeploy is lost rather than
written. Upgrade path: a periodic time-based flush of every site's pending carry, not only the
size-based rotation this sink has today.

Standard library only (spine AD-D16: `observability/` imports nothing else; `tests/
test_boundaries.py` enforces it). Module state is the one sanctioned mutable ledger (AD-D10;
DESIGN-01's named invariant): `_counts`/`_last` and the sink are both guarded by `_lock` (the
sink also has its own lock around its file/rotation state), and the invariant every `record()`
call preserves is that it increments exactly one in-memory count and yields exactly one durable
file line or one suppressed increment -- never both a line and a suppressed increment, and never
neither.
"""

import json
import logging
import os
import sys
import threading
import time
from collections import Counter
from collections.abc import Iterable
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from typing import TextIO


logger = logging.getLogger(__name__)

PROCESS_START_SITE = "process_start"
WRITE_FAILED_SITE = "observability.ledger_write"

_MAX_DETAIL_CHARS = 2000
_DEFAULT_MAX_LINES_PER_SITE_PER_MIN = 60
_DEFAULT_MAX_BYTES = 20 * 1024 * 1024
_DEFAULT_BACKUP_COUNT = 10
_MINUTE_NS = 60 * 1_000_000_000

_lock = threading.Lock()
_counts: Counter[str] = Counter()
_last: dict[str, str] = {}


class _FileSink:
    """
    Append-only JSON-lines writer with size rotation and a per-site per-minute cap.

    Not a `logging.handlers.RotatingFileHandler`: that swallows write failures into
    `handleError` (stderr), while here a failed write must be counted at `WRITE_FAILED_SITE`
    and logged like any other tolerated failure (DATA-07).
    """

    def __init__(
        self,
        path: Path,
        service: str,
        *,
        max_lines_per_site_per_min: int,
        max_bytes: int,
        backup_count: int,
    ) -> None:
        self.path = path
        self.service = service
        self.max_lines_per_site_per_min = max_lines_per_site_per_min
        self.max_bytes = max_bytes
        self.backup_count = backup_count
        self._lock = threading.Lock()
        self._file: TextIO | None = None
        self.last_error: BaseException | None = None
        # site -> (minute bucket, lines written in that bucket, suppressed since the last line)
        self._buckets: dict[str, tuple[int, int, int]] = {}

    def write(self, site: str, detail: str, exc: BaseException | None, ts_ns: int) -> bool:
        """Return False when the write failed (the caller counts it); never raises."""
        with self._lock:
            suppressed = self._admit(site, ts_ns)
            if suppressed is None:
                return True
            line = json.dumps(
                {
                    "ts_ns": ts_ns,
                    "service": self.service,
                    "pid": os.getpid(),
                    "site": site,
                    "detail": detail[:_MAX_DETAIL_CHARS],
                    "exc_type": type(exc).__name__ if exc is not None else None,
                    "suppressed": suppressed,
                },
                separators=(",", ":"),
            )
            try:
                self._emit(line)
            except (OSError, UnicodeError) as exc_write:
                # The line is lost; put its count (and the slot _admit already spent) back so
                # the next line reports it exactly, and a write outage doesn't also exhaust the
                # per-minute cap for real errors that arrive once the outage clears.
                self.last_error = exc_write
                bucket, written, pending = self._buckets[site]
                self._buckets[site] = (bucket, max(written - 1, 0), pending + suppressed + 1)
                return False
            return True

    def write_extra(self, site: str, fields: dict[str, Any], ts_ns: int) -> bool:
        """Write a one-off line with extra fields (the `process_start` marker); uncapped."""
        with self._lock:
            line = json.dumps(
                {
                    "ts_ns": ts_ns,
                    "service": self.service,
                    "pid": os.getpid(),
                    "site": site,
                    "detail": "",
                    "exc_type": None,
                    "suppressed": 0,
                    **fields,
                },
                separators=(",", ":"),
            )
            try:
                self._emit(line)
            except (OSError, UnicodeError) as exc:
                self.last_error = exc
                return False
            return True

    def close(self) -> None:
        with self._lock:
            if self._file is not None:
                self._file.close()
                self._file = None

    def _admit(self, site: str, ts_ns: int) -> int | None:
        """Return the `suppressed` carry for a line written now, or None when it is capped."""
        minute = ts_ns // _MINUTE_NS
        bucket, written, pending = self._buckets.get(site, (minute, 0, 0))
        if bucket != minute:
            bucket, written = minute, 0
        if written >= self.max_lines_per_site_per_min:
            self._buckets[site] = (bucket, written, pending + 1)
            return None
        self._buckets[site] = (bucket, written + 1, 0)
        return pending

    def _emit(self, line: str) -> None:
        fh = self._file
        if fh is None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            fh = self._file = open(self.path, "a", encoding="utf-8")  # noqa: SIM115
        # `open(..., "a")` positions at end of file, so `tell()` is the current size.
        position = fh.tell()
        if position > 0 and position + len(line) + 1 > self.max_bytes:
            fh = self._rotate(fh)
        fh.write(line + "\n")
        fh.flush()

    def _rotate(self, fh: TextIO) -> TextIO:
        """Close `fh`, shift `.N` .. `.1` down one and reopen; returns the new open file."""
        fh.close()
        self._file = None
        for n in range(self.backup_count - 1, 0, -1):
            older = self.path.with_name(f"{self.path.name}.{n}")
            if older.exists():
                os.replace(older, self.path.with_name(f"{self.path.name}.{n + 1}"))
        if self.backup_count > 0:
            os.replace(self.path, self.path.with_name(f"{self.path.name}.1"))
        else:
            self.path.unlink()
        self._file = open(self.path, "a", encoding="utf-8")  # noqa: SIM115
        return self._file


_sink: _FileSink | None = None


def _env_int(name: str, default: int) -> int:
    """
    Parse `name` as an int, falling back to `default` on a malformed value.

    A typo here must never crash `start()` -- every process calls it near the top of its
    entrypoint (DATA-07: a misconfigured env var is not license for the whole service to
    fail to boot).
    """
    raw = os.environ.get(name)
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        logger.warning("error ledger: %s=%r is not an int, using default %d", name, raw, default)
        return default


def start(service: str | None = None) -> bool:
    """
    Open the durable sink from the environment and write the `process_start` line.

    Called once per process by the entrypoint. Returns False (and stays in-memory only) when
    `ERROR_LEDGER_DIR` is unset; a second call is a no-op. `service` defaults to
    `ERROR_LEDGER_SERVICE`, then the process name.
    """
    global _sink
    directory = os.environ.get("ERROR_LEDGER_DIR")
    if not directory:
        return False
    with _lock:
        if _sink is not None:
            return True
        name = service or os.environ.get("ERROR_LEDGER_SERVICE") or Path(sys.argv[0]).stem
        _sink = _FileSink(
            Path(directory) / f"{name}.jsonl",
            name,
            max_lines_per_site_per_min=_env_int(
                "ERROR_LEDGER_MAX_LINES_PER_SITE_PER_MIN",
                _DEFAULT_MAX_LINES_PER_SITE_PER_MIN,
            ),
            max_bytes=_env_int("ERROR_LEDGER_MAX_BYTES", _DEFAULT_MAX_BYTES),
            backup_count=_env_int("ERROR_LEDGER_BACKUP_COUNT", _DEFAULT_BACKUP_COUNT),
        )
        sink = _sink
    fields: dict[str, Any] = {"revision": os.environ.get("ERROR_LEDGER_REVISION") or None}
    if not sink.write_extra(PROCESS_START_SITE, fields, time.time_ns()):
        _count_write_failure(sink)
    logger.info("error ledger: durable sink at %s", sink.path)
    return True


def record(site: str, detail: str = "", exc: BaseException | None = None) -> None:
    """Log at ERROR (with traceback when `exc` is given or an exception is being handled) and count."""
    with _lock:
        _counts[site] += 1
        _last[site] = detail or (repr(exc) if exc else "")
        sink = _sink
    logger.error("[%s] %s", site, detail, exc_info=exc if exc is not None else True)
    if sink is not None and not sink.write(site, detail, exc, time.time_ns()):
        _count_write_failure(sink)


def _count_write_failure(sink: _FileSink) -> None:
    """Count and log a lost line: it is itself a tolerated failure, never a silent one."""
    with _lock:
        _counts[WRITE_FAILED_SITE] += 1
        _last[WRITE_FAILED_SITE] = f"could not append to {sink.path}: {sink.last_error!r}"
    logger.error(
        "[%s] could not append to %s",
        WRITE_FAILED_SITE,
        sink.path,
        exc_info=sink.last_error,
    )


def counts() -> dict[str, int]:
    with _lock:
        return dict(_counts)


def last_details() -> dict[str, str]:
    with _lock:
        return dict(_last)


def reset() -> None:
    """Clear every count and detail, and close the sink so `start()` can run again (tests only)."""
    global _sink
    with _lock:
        _counts.clear()
        _last.clear()
        sink, _sink = _sink, None
    if sink is not None:
        sink.close()


# --- Readers -------------------------------------------------------------------------------------
# `data_api`'s `/api/errors` and `collector_core.crosscheck_errors` read the files back. A reader
# never assumes a line is well formed (a rotation or a crash mid-write can truncate the last one).


def ledger_files(directory: str | Path, service: str) -> list[Path]:
    """Return the service's files, oldest first: `<service>.jsonl.N` .. `.1`, then `.jsonl`."""
    base = Path(directory) / f"{service}.jsonl"
    if not base.parent.is_dir():
        return []
    rotated = sorted(
        (p for p in base.parent.glob(f"{base.name}.*") if p.suffix[1:].isdigit()),
        key=lambda p: int(p.suffix[1:]),
        reverse=True,
    )
    return [*rotated, base] if base.exists() else rotated


def services(directory: str | Path) -> list[str]:
    """Every service with a ledger file in the directory."""
    root = Path(directory)
    if not root.is_dir():
        return []
    return sorted({p.name[: -len(".jsonl")] for p in root.glob("*.jsonl")})


def iter_records(
    directory: str | Path,
    service: str,
    *,
    since_ns: int | None = None,
    until_ns: int | None = None,
) -> Iterator[dict[str, Any]]:
    """Every parseable line of the service in the window, oldest first (rotated files included)."""
    for path in ledger_files(directory, service):
        try:
            fh = open(path, encoding="utf-8", errors="replace")  # noqa: SIM115
        except FileNotFoundError:
            # The writer rotated this file out from under us between the listing above and
            # this open -- its lines are still in a `.N` sibling `ledger_files` will pick up
            # on the next call; skip rather than raise.
            continue
        with fh:
            for raw in fh:
                rec = _parse_line(raw)
                if rec is None:
                    continue
                ts = rec["ts_ns"]
                if (since_ns is not None and ts < since_ns) or (
                    until_ns is not None and ts > until_ns
                ):
                    continue
                yield rec


def _parse_line(raw: str) -> dict[str, Any] | None:
    try:
        rec = json.loads(raw)
    except ValueError:
        return None
    if not isinstance(rec, dict) or not isinstance(rec.get("ts_ns"), int) or "site" not in rec:
        return None
    return rec


def site_counts(records: Iterable[dict[str, Any]]) -> dict[str, int]:
    """Per-site totals with the suppressed carry folded in; `process_start` lines excluded."""
    totals: Counter[str] = Counter()
    for rec in records:
        if rec["site"] == PROCESS_START_SITE:
            continue
        totals[rec["site"]] += 1 + int(rec.get("suppressed") or 0)
    return dict(totals)


def service_summary(
    directory: str | Path, service: str, since_ns: int | None = None
) -> dict[str, Any]:
    """
    `/api/errors`' per-service block: `last_start_ns`, per-site counts `since_start` (after the
    last `process_start` line) and `since` (from `since_ns`; None when no bound was given).

    Suppressed carries are folded in. Known limit: every call re-reads the service's whole file
    set, so the cost grows with the rotation window rather than with the poll interval; upgrade
    path is an mtime/offset cache if this poll ever shows up in `data_api`'s CPU.
    """
    last_start: int | None = None
    since_start: Counter[str] = Counter()
    since: Counter[str] = Counter()
    for rec in iter_records(directory, service):
        if rec["site"] == PROCESS_START_SITE:
            last_start = rec["ts_ns"]
            since_start.clear()
            continue
        n = 1 + int(rec.get("suppressed") or 0)
        since_start[rec["site"]] += n
        if since_ns is not None and rec["ts_ns"] >= since_ns:
            since[rec["site"]] += n
    return {
        "last_start_ns": last_start,
        "since_start": dict(since_start),
        "since": dict(since) if since_ns is not None else None,
    }
