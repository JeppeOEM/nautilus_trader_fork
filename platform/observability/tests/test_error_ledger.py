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
import json
import logging
from pathlib import Path

import pytest

from observability import error_ledger
from observability.error_ledger import _FileSink


_MIN = 60 * 1_000_000_000


def test_record_logs_error_with_traceback_and_counts(caplog: pytest.LogCaptureFixture) -> None:
    error_ledger.reset()
    try:
        raise ValueError("bad row")
    except ValueError as exc:
        with caplog.at_level(logging.ERROR):
            error_ledger.record("site.a", "row DROPPED", exc)
    error_ledger.record("site.a", "again")
    assert error_ledger.counts() == {"site.a": 2}
    assert error_ledger.last_details() == {"site.a": "again"}
    assert caplog.records[0].levelno == logging.ERROR
    assert caplog.records[0].exc_info is not None
    error_ledger.reset()


# --- durable sink (story 23.3) -------------------------------------------------------------------


def _lines(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines()]


def _sink(path: Path, cap: int = 60, max_bytes: int = 20 << 20, backups: int = 10) -> _FileSink:
    return _FileSink(
        path, "svc", max_lines_per_site_per_min=cap, max_bytes=max_bytes, backup_count=backups
    )


def test_start_writes_process_start_then_records_flushed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    error_ledger.reset()
    monkeypatch.setenv("ERROR_LEDGER_DIR", str(tmp_path))
    monkeypatch.setenv("ERROR_LEDGER_SERVICE", "collector")
    monkeypatch.setenv("ERROR_LEDGER_REVISION", "abc123")
    assert error_ledger.start() is True
    assert error_ledger.start() is True  # idempotent
    try:
        raise KeyError("k")
    except KeyError as exc:
        error_ledger.record("collector.x", "detail " + "y" * 3000, exc)
    error_ledger.record("collector.y")
    # Read while the process still holds the file open: every line is flushed before return.
    lines = _lines(tmp_path / "collector.jsonl")
    assert [line["site"] for line in lines] == ["process_start", "collector.x", "collector.y"]
    assert lines[0]["revision"] == "abc123"
    assert lines[0]["pid"] > 0
    assert set(lines[1]) == {"ts_ns", "service", "pid", "site", "detail", "exc_type", "suppressed"}
    assert lines[1]["service"] == "collector"
    assert lines[1]["exc_type"] == "KeyError"
    assert len(lines[1]["detail"]) == 2000
    assert lines[1]["suppressed"] == 0
    assert lines[2]["exc_type"] is None
    assert error_ledger.counts() == {"collector.x": 1, "collector.y": 1}
    error_ledger.reset()


def test_unset_dir_is_in_memory_only(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    error_ledger.reset()
    monkeypatch.delenv("ERROR_LEDGER_DIR", raising=False)
    assert error_ledger.start() is False
    error_ledger.record("site.a", "x")
    assert error_ledger.counts() == {"site.a": 1}
    assert list(tmp_path.iterdir()) == []
    error_ledger.reset()


def test_malformed_env_int_falls_back_to_the_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A typo in an env var must never stop a service booting (`_env_int`)."""
    error_ledger.reset()
    monkeypatch.setenv("ERROR_LEDGER_DIR", str(tmp_path))
    monkeypatch.setenv("ERROR_LEDGER_SERVICE", "collector")
    monkeypatch.setenv("ERROR_LEDGER_MAX_BYTES", "twenty megabytes")
    assert error_ledger.start() is True
    assert (tmp_path / "collector.jsonl").exists()
    error_ledger.reset()


def test_cap_per_site_per_minute_with_exact_suppressed_carry(tmp_path: Path) -> None:
    sink = _sink(tmp_path / "svc.jsonl", cap=2)
    t0 = 1_000 * _MIN
    for i in range(5):  # minute 0: 2 written, 3 suppressed
        assert sink.write("a", f"{i}", None, t0 + i)
    assert sink.write("b", "other site unaffected", None, t0 + 10)
    assert sink.write("a", "first of minute 1", None, t0 + _MIN)  # carries 3
    assert sink.write("a", "second of minute 1", None, t0 + _MIN + 1)  # carries 0
    lines = _lines(tmp_path / "svc.jsonl")
    site_a = [line for line in lines if line["site"] == "a"]
    assert [line["suppressed"] for line in site_a] == [0, 0, 3, 0]
    assert len(site_a) + sum(line["suppressed"] for line in site_a) == 7
    assert [line["site"] for line in lines].count("b") == 1
    sink.close()


def test_rotation_by_size_keeps_bounded_backups(tmp_path: Path) -> None:
    path = tmp_path / "svc.jsonl"
    sink = _sink(path, max_bytes=300, backups=2)
    for i in range(20):
        assert sink.write("a", "x" * 50, None, i)
    sink.close()
    names = sorted(p.name for p in tmp_path.iterdir())
    assert names == ["svc.jsonl", "svc.jsonl.1", "svc.jsonl.2"]
    assert all(p.stat().st_size <= 300 for p in tmp_path.iterdir())
    # Oldest first when read back, and every surviving line intact.
    files = error_ledger.ledger_files(tmp_path, "svc")
    assert [p.name for p in files] == ["svc.jsonl.2", "svc.jsonl.1", "svc.jsonl"]
    ts = [r["ts_ns"] for r in error_ledger.iter_records(tmp_path, "svc")]
    assert ts == sorted(ts)
    assert ts[-1] == 19


def test_write_failure_is_counted_and_logged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    error_ledger.reset()
    # The sink path resolves to a directory, so every append fails with an OSError.
    (tmp_path / "collector.jsonl").mkdir()
    monkeypatch.setenv("ERROR_LEDGER_DIR", str(tmp_path))
    monkeypatch.setenv("ERROR_LEDGER_SERVICE", "collector")
    with caplog.at_level(logging.ERROR):
        error_ledger.start()  # the process_start line fails too
        error_ledger.record("site.a", "x")
    counts = error_ledger.counts()
    assert counts["site.a"] == 1
    assert counts[error_ledger.WRITE_FAILED_SITE] == 2
    assert "could not append" in error_ledger.last_details()[error_ledger.WRITE_FAILED_SITE]
    failures = [r for r in caplog.records if error_ledger.WRITE_FAILED_SITE in r.getMessage()]
    assert len(failures) == 2
    assert failures[0].exc_info is not None
    error_ledger.reset()


def test_write_failure_returns_the_spent_cap_slot_and_the_lost_line(tmp_path: Path) -> None:
    """A write outage neither loses a count nor burns the minute's cap (I/O matrix, row 6)."""
    path = tmp_path / "svc.jsonl"
    path.mkdir()  # every append fails
    sink = _sink(path, cap=2)
    assert sink.write("a", "lost", None, 0) is False
    path.rmdir()  # the outage clears
    assert sink.write("a", "first after outage", None, 1) is True
    assert sink.write("a", "second after outage", None, 2) is True
    sink.close()
    lines = _lines(path)
    # Both post-outage writes were admitted (the failed one gave its slot back), and the lost
    # line is reported exactly once in the next line's `suppressed`.
    assert [line["suppressed"] for line in lines] == [1, 0]
    assert len(lines) + sum(line["suppressed"] for line in lines) == 3


# --- readers --------------------------------------------------------------------------------------


def _write(path: Path, records: list[dict]) -> None:
    path.write_text("".join(json.dumps(r) + "\n" for r in records))


def test_readers_skip_malformed_lines_and_fold_suppressed(tmp_path: Path) -> None:
    path = tmp_path / "collector.jsonl"
    _write(
        path,
        [
            {"ts_ns": 10, "site": "process_start", "suppressed": 0},
            {"ts_ns": 20, "site": "a", "suppressed": 4},
            {"ts_ns": 30, "site": "b", "suppressed": 0},
        ],
    )
    with path.open("a") as fh:
        fh.write('{"ts_ns": 40, "site": "a", "supp')  # truncated by a crash mid-write
    assert error_ledger.services(tmp_path) == ["collector"]
    records = list(error_ledger.iter_records(tmp_path, "collector"))
    assert [r["ts_ns"] for r in records] == [10, 20, 30]
    assert error_ledger.site_counts(records) == {"a": 5, "b": 1}
    assert [r["ts_ns"] for r in error_ledger.iter_records(tmp_path, "collector", since_ns=25)] == [
        30
    ]
    assert error_ledger.services(tmp_path / "missing") == []
    assert error_ledger.ledger_files(tmp_path / "missing", "collector") == []


def test_service_summary_counts_since_last_start_and_since_bound(tmp_path: Path) -> None:
    _write(
        tmp_path / "collector.jsonl",
        [
            {"ts_ns": 10, "site": "process_start", "suppressed": 0},
            {"ts_ns": 20, "site": "a", "suppressed": 0},
            {"ts_ns": 30, "site": "process_start", "suppressed": 0},
            {"ts_ns": 40, "site": "a", "suppressed": 2},
            {"ts_ns": 50, "site": "b", "suppressed": 0},
        ],
    )
    summary = error_ledger.service_summary(tmp_path, "collector")
    assert summary == {"last_start_ns": 30, "since_start": {"a": 3, "b": 1}, "since": None}
    summary = error_ledger.service_summary(tmp_path, "collector", since_ns=45)
    assert summary["since"] == {"b": 1}
    assert summary["since_start"] == {"a": 3, "b": 1}
