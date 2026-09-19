"""
One place where "our code malfunctioned and we carried on" is made impossible to miss (DATA-07).

Every site that must continue past a failure (a dropped message, a skipped coin, an unreadable
file) calls `record()` instead of a bare log line: it logs at ERROR with the traceback AND counts
the site, so `GET /api/errors` (and the frontend's error bar) can show that something is wrong
without anyone reading Dozzle. Counts are per-process and reset on restart -- they say "this
process has seen N failures at this site", never "the failure went away".
"""

import logging
import threading
from collections import Counter


logger = logging.getLogger(__name__)

_lock = threading.Lock()
_counts: Counter[str] = Counter()
_last: dict[str, str] = {}


def record(site: str, detail: str = "", exc: BaseException | None = None) -> None:
    """Log at ERROR (with traceback when `exc` is given or an exception is being handled) and count."""
    with _lock:
        _counts[site] += 1
        _last[site] = detail or (repr(exc) if exc else "")
    logger.error("[%s] %s", site, detail, exc_info=exc if exc is not None else True)


def counts() -> dict[str, int]:
    with _lock:
        return dict(_counts)


def last_details() -> dict[str, str]:
    with _lock:
        return dict(_last)


def reset() -> None:
    """Tests only."""
    with _lock:
        _counts.clear()
        _last.clear()
