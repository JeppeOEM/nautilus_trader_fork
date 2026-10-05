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
`observability.incidents` on a synthetic, venue-free config: classification, the raw-log window
scan, report writing, debounce, cross-thread emit, serialised pruning and stale-log cleanup.
The dYdX entrypoint's own config is tested in `capture/venues/dydx/tests/test_incident_config.py`.
"""

import asyncio
import concurrent.futures
import dataclasses
import json
import logging
import os
import re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import pytest

from observability import error_ledger
from observability import incidents
from observability.incidents import IncidentConfig
from observability.incidents import IncidentRule


_IID = "BTC-USD.SIM"
_T = 1_800_000_000_000_000_000  # trigger time, ns


def _config(tmp_path: Path, **overrides: Any) -> IncidentConfig:
    config = IncidentConfig(
        report_title="Sim Incident Report",
        iid_pattern=re.compile(r"\b(?P<iid>(?P<ticker>[A-Z0-9]+-USD)\.SIM)\b"),
        evidence_needle='"id":"{ticker}"',
        raw_log_dir=tmp_path / "raw",
        raw_log_name="raw_debug",
        report_dir=tmp_path / "reports",
        rules=(
            IncidentRule("Crossed book", "crossed_book"),
            IncidentRule("tick arrived", "loop_lag", with_instrument=False),
        ),
    )
    return dataclasses.replace(config, **overrides)


def _raw_line(ns: int, ticker: str) -> str:
    return incidents.ns_to_iso(ns) + f' [DEBUG] x: [WS_RAW] {{"id":"{ticker}","type":"data"}}\n'


def test_rule_match_carries_the_instrument() -> None:
    config = _config(Path("/nonexistent"))
    message = f"Crossed book for {_IID} (bid=2.26 >= ask=2.26) — skipping snapshot"
    assert incidents.classify_incident(config, message) == ("crossed_book", _IID)


def test_rule_without_instrument_drops_a_named_one() -> None:
    config = _config(Path("/nonexistent"))
    message = f"tick arrived 3.0s late ({_IID} was last)"
    assert incidents.classify_incident(config, message) == ("loop_lag", None)


def test_structured_json_reason_is_exact_not_heuristic() -> None:
    config = _config(Path("/nonexistent"))
    message = json.dumps({"instrument_id": _IID, "reason": "steady_state_crossed_book"})
    assert incidents.classify_incident(config, message) == ("steady_state_crossed_book", _IID)


def test_unmatched_message_is_unclassified() -> None:
    config = _config(Path("/nonexistent"))
    assert incidents.classify_incident(config, "Something unexpected") == ("unclassified", None)


def test_ticker_needs_the_venue_shape() -> None:
    config = _config(Path("/nonexistent"))
    assert incidents.ticker_of(config, _IID) == "BTC-USD"
    assert incidents.ticker_of(config, "BTCUSDT-LINEAR.OTHER") is None
    assert incidents.ticker_of(config, None) is None


def test_scan_filters_by_ticker_and_time(tmp_path: Path) -> None:
    config = _config(tmp_path)
    config.raw_log_dir.mkdir()
    (config.raw_log_dir / "raw_debug_1.log").write_text(
        _raw_line(_T - 1_000_000_000, "BTC-USD")
        + _raw_line(_T, "ETH-USD")  # wrong ticker
        + _raw_line(_T + 60_000_000_000, "BTC-USD")  # outside the window
    )
    (config.raw_log_dir / "other_1.log").write_text(_raw_line(_T, "BTC-USD"))  # not the raw log
    matches = incidents.scan_raw_window(config, "BTC-USD", _T - 5_000_000_000, _T + 5_000_000_000)
    assert matches == [_raw_line(_T - 1_000_000_000, "BTC-USD")]


def test_report_has_header_and_evidence(tmp_path: Path) -> None:
    config = _config(tmp_path)
    config.raw_log_dir.mkdir()
    (config.raw_log_dir / "raw_debug_1.log").write_text(_raw_line(_T, "BTC-USD"))
    writer = incidents.IncidentReportWriter(config)
    path = writer.write("crossed_book", _IID, "WARNING", "sim.collector", "Crossed book for X", _T)
    content = Path(path).read_text()
    assert Path(path) == config.report_dir / f"crossed_book_{_IID}_{_T // 1_000_000_000}.log"
    assert content.startswith("=== Sim Incident Report ===\n")
    assert "Type: crossed_book\n" in content
    assert f"Instrument: {_IID}\n" in content
    assert "--- Raw WS evidence (BTC-USD, 1 messages) ---\n" + _raw_line(_T, "BTC-USD") in content


def test_report_without_the_raw_sink_says_so_and_scans_nothing(tmp_path: Path) -> None:
    """
    `raw_log_enabled=False` (the process runs without the raw sink): the report is still written
    for the warning, but it never claims evidence -- not even lines a previous run with the sink
    on left behind in the directory -- and names the switch instead.
    """
    config = _config(tmp_path, raw_log_enabled=False)
    config.raw_log_dir.mkdir()
    (config.raw_log_dir / "raw_debug_1.log").write_text(_raw_line(_T, "BTC-USD"))
    writer = incidents.IncidentReportWriter(config)
    path = writer.write("crossed_book", _IID, "WARNING", "sim.collector", "Crossed book for X", _T)
    content = Path(path).read_text()
    assert "Type: crossed_book\n" in content
    assert incidents.RAW_LOG_DISABLED_LINE in content
    assert "Raw WS evidence" not in content
    assert _raw_line(_T, "BTC-USD") not in content


def test_report_name_is_one_plain_filesystem_component(tmp_path: Path) -> None:
    """`reason`/`instrument_id` come from a log line: never a path, never a rejected character."""
    config = _config(tmp_path)
    writer = incidents.IncidentReportWriter(config)
    path = Path(writer.write("../../etc", "x/y\x00z", "WARNING", "sim", "m", _T))
    assert path.parent == config.report_dir
    assert path.name == f".._.._etc_x_y_z_{_T // 1_000_000_000}.log"
    assert len(incidents.report_name("r" * 500, "i" * 500, _T)) < 255


@pytest.mark.parametrize(
    ("override", "match"),
    [
        ({"iid_pattern": re.compile(r"(?P<iid>[A-Z]+)")}, "ticker"),
        ({"evidence_needle": '"id":"BTC"'}, "{ticker}"),
    ],
)
def test_config_rejects_a_pattern_or_needle_the_scan_cannot_use(
    tmp_path: Path, override: dict[str, Any], match: str
) -> None:
    """Caught when the entrypoint builds it, not swallowed by emit() at the first incident."""
    with pytest.raises(ValueError, match=re.escape(match)):
        _config(tmp_path, **override)


def test_report_without_instrument_has_no_evidence_section(tmp_path: Path) -> None:
    writer = incidents.IncidentReportWriter(_config(tmp_path))
    path = writer.write("loop_lag", None, "WARNING", "sim.collector", "tick arrived late", 1)
    content = Path(path).read_text()
    assert Path(path).name == "loop_lag_system_0.log"
    assert "Instrument: -\n" in content
    assert "No instrument identified" in content


def test_prune_survives_concurrent_callers(tmp_path: Path) -> None:
    """
    Two incidents on different `to_thread` workers both prune: before the lock, one thread's
    unlink() racing another's stat() raised FileNotFoundError (seen in production). 20 files over
    a tiny cap force real pruning work on every thread.
    """
    config = _config(tmp_path, report_dir=tmp_path, report_dir_max_bytes=100)
    for i in range(20):
        (tmp_path / f"report_{i}.log").write_text("x" * 50)
    writer = incidents.IncidentReportWriter(config)
    with ThreadPoolExecutor(max_workers=8) as pool:
        for future in [pool.submit(writer.prune) for _ in range(8)]:
            future.result()  # re-raises FileNotFoundError if the race is back
    assert sum(f.stat().st_size for f in tmp_path.glob("*.log")) <= 100


def test_prune_tolerates_a_file_removed_by_someone_else(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An operator deleting a report between the glob and its stat is not a failed write."""
    config = _config(tmp_path, report_dir=tmp_path, report_dir_max_bytes=100)
    for i in range(3):
        (tmp_path / f"report_{i}.log").write_text("x" * 60)
    victim = tmp_path / "report_1.log"
    original_stat = Path.stat

    def _stat_after_a_deletion(self: Path, *args: Any, **kwargs: Any) -> os.stat_result:
        if self == victim and os.path.exists(victim):
            os.unlink(victim)
        return original_stat(self, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", _stat_after_a_deletion)
    incidents.IncidentReportWriter(config).prune()
    assert sum(f.stat().st_size for f in tmp_path.glob("*.log")) <= 100


def _attach(handler: logging.Handler, name: str) -> logging.Logger:
    test_logger = logging.getLogger(name)
    test_logger.addHandler(handler)
    test_logger.setLevel(logging.WARNING)
    return test_logger


@pytest.mark.asyncio
async def test_handler_debounces_repeats_of_one_incident(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One ongoing incident warns every tick; at most one report per debounce window."""
    written: list[tuple[str, str | None]] = []

    async def _fake_report(incident_type: str, iid: str | None, *_: object) -> None:
        written.append((incident_type, iid))

    handler = incidents.IncidentHandler(_config(tmp_path))
    monkeypatch.setattr(handler, "report", _fake_report)
    test_logger = _attach(handler, "test_incident_handler")
    try:
        for _ in range(3):
            test_logger.warning(f"Crossed book for {_IID} (bid=1 >= ask=1)")
            await asyncio.sleep(0)  # let the scheduled report run
    finally:
        test_logger.removeHandler(handler)
    assert written == [("crossed_book", _IID)]


@pytest.mark.asyncio
async def test_handler_emit_from_a_worker_thread_schedules_the_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    A notification failure logs from inside `asyncio.to_thread` (no running loop there); emit()
    must still schedule the report onto the process's loop.
    """
    written: list[str] = []

    async def _fake_report(incident_type: str, *_: object) -> None:
        written.append(incident_type)

    handler = incidents.IncidentHandler(_config(tmp_path), asyncio.get_running_loop())
    monkeypatch.setattr(handler, "report", _fake_report)
    test_logger = _attach(handler, "test_incident_handler_thread")
    try:
        await asyncio.to_thread(test_logger.warning, f"Crossed book for {_IID}")
        for _ in range(5):  # give run_coroutine_threadsafe's hop back to the loop time to land
            await asyncio.sleep(0)
    finally:
        test_logger.removeHandler(handler)
    assert written == ["crossed_book"]


@pytest.mark.asyncio
async def test_a_report_that_cannot_be_written_is_ledgered(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Disk full or a permission error in the scheduled report never vanishes (DATA-07)."""

    async def _failing_report(*_: object) -> None:
        raise OSError(28, "No space left on device")

    error_ledger.reset()
    handler = incidents.IncidentHandler(_config(tmp_path))
    monkeypatch.setattr(handler, "report", _failing_report)
    test_logger = _attach(handler, "test_incident_handler_failure")
    try:
        test_logger.warning(f"Crossed book for {_IID}")
        for _ in range(5):  # the report task, then the future's done-callback
            await asyncio.sleep(0)
    finally:
        test_logger.removeHandler(handler)
    assert error_ledger.counts() == {"observability.incidents.report": 1}
    assert error_ledger.last_details() == {
        "observability.incidents.report": f"incident report crossed_book/{_IID} not written"
    }
    error_ledger.reset()


@pytest.mark.asyncio
async def test_a_failed_reports_ledger_line_re_enters_once_per_debounce_window(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    In production the handler sits on the root logger, where the ledger's ERROR line for a
    failed report arrives too: it re-enters emit(), and the debounce must bound that to one
    retry per window, never a report-per-failure spin.
    """
    scheduled: list[tuple[str, str | None]] = []

    async def _failing_report(incident_type: str, iid: str | None, *_: object) -> None:
        scheduled.append((incident_type, iid))
        raise OSError(28, "No space left on device")

    error_ledger.reset()
    handler = incidents.IncidentHandler(_config(tmp_path))
    monkeypatch.setattr(handler, "report", _failing_report)
    root = logging.getLogger()
    root.addHandler(handler)
    try:
        logging.getLogger("test_incident_reentry").warning(f"Crossed book for {_IID}")
        for _ in range(20):  # report, done-callback, ledger ERROR, its report, its callback
            await asyncio.sleep(0)
    finally:
        root.removeHandler(handler)
    # The ledger line names the instrument but matches no rule; its own failure is debounced.
    assert scheduled == [("crossed_book", _IID), ("unclassified", _IID)]
    assert error_ledger.counts() == {"observability.incidents.report": 2}
    error_ledger.reset()


def _recording_handler(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, written: list[str], **overrides: Any
) -> incidents.IncidentHandler:
    """Return a handler whose scheduled reports only record the message they were for."""

    async def _fake_report(
        incident_type: str, iid: str | None, level: str, logger_name: str, message: str, *_: object
    ) -> None:
        written.append(message)

    handler = incidents.IncidentHandler(_config(tmp_path, **overrides))
    monkeypatch.setattr(handler, "report", _fake_report)
    return handler


async def _drain() -> None:
    for _ in range(5):  # every scheduled report runs
        await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_the_same_unclassified_text_from_two_loggers_is_two_reports(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Unclassified and without an instrument: only the logger tells the two apart (DW-177)."""
    written: list[str] = []
    handler = _recording_handler(tmp_path, monkeypatch, written)
    first = _attach(handler, "test_incident_unclassified_a")
    second = _attach(handler, "test_incident_unclassified_b")
    try:
        first.warning("queue backed up")
        second.warning("queue backed up")
        await _drain()
    finally:
        first.removeHandler(handler)
        second.removeHandler(handler)
    assert written == ["queue backed up", "queue backed up"]


@pytest.mark.asyncio
async def test_two_unclassified_templates_on_one_logger_are_two_reports(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    written: list[str] = []
    handler = _recording_handler(tmp_path, monkeypatch, written)
    test_logger = _attach(handler, "test_incident_two_templates")
    try:
        test_logger.warning("queue %s backed up", "trades")
        test_logger.warning("redis %s unreachable", "primary")
        await _drain()
    finally:
        test_logger.removeHandler(handler)
    assert written == ["queue trades backed up", "redis primary unreachable"]


@pytest.mark.asyncio
async def test_one_unclassified_template_with_changing_values_is_one_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The key is the template before `%`-formatting, so a per-tick warning still debounces."""
    written: list[str] = []
    handler = _recording_handler(tmp_path, monkeypatch, written)
    test_logger = _attach(handler, "test_incident_one_template")
    try:
        test_logger.warning("x %s", 1)
        test_logger.warning("x %s", 2)
        await _drain()
    finally:
        test_logger.removeHandler(handler)
    assert written == ["x 1"]


@pytest.mark.asyncio
async def test_ledger_records_debounce_per_site(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    Every `error_ledger.record()` line shares the template `"[%s] %s"` on one logger; its
    `incident_key` (the site) keeps two sites apart and one site's repeats together.
    """
    written: list[str] = []
    handler = _recording_handler(tmp_path, monkeypatch, written)
    # Not `_attach`: setting the shared ledger logger's level would outlive this test.
    ledger_logger = logging.getLogger(error_ledger.__name__)
    ledger_logger.addHandler(handler)
    error_ledger.reset()
    try:
        error_ledger.record("site.a", "first")
        error_ledger.record("site.b", "first")
        error_ledger.record("site.a", "second")
        await _drain()
    finally:
        ledger_logger.removeHandler(handler)
        error_ledger.reset()
    assert written == ["[site.a] first", "[site.b] first"]


@pytest.mark.asyncio
async def test_a_classified_incident_debounces_across_loggers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A classified incident is one incident per (type, instrument), whoever reports it."""
    written: list[str] = []
    handler = _recording_handler(tmp_path, monkeypatch, written)
    first = _attach(handler, "test_incident_classified_a")
    second = _attach(handler, "test_incident_classified_b")
    try:
        first.warning("Crossed book for %s", _IID)
        second.warning(f"Crossed book for {_IID} (bid=1 >= ask=1)")
        await _drain()
    finally:
        first.removeHandler(handler)
        second.removeHandler(handler)
    assert written == [f"Crossed book for {_IID}"]


class _Clock:
    """
    Stands in for `incidents`' `time`: one settable value for the wall and monotonic clocks, the
    wall clock shifted by `wall_step_ns` to model an NTP step.
    """

    def __init__(self, now_ns: int) -> None:
        self.now_ns = now_ns
        self.wall_step_ns = 0

    def time_ns(self) -> int:
        return self.now_ns + self.wall_step_ns

    def monotonic_ns(self) -> int:
        return self.now_ns


@pytest.mark.asyncio
async def test_expired_debounce_keys_are_swept(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    The unclassified key space is open-ended, so expired keys are dropped (at most once per
    window); a key still inside its window is kept, so no debounce decision changes.
    """
    written: list[str] = []
    handler = _recording_handler(tmp_path, monkeypatch, written)
    window = handler._config.debounce_ns
    clock = _Clock(_T)
    # The module's own `time` only: a global `time.time_ns` patch would also stamp LogRecords.
    monkeypatch.setattr(incidents, "time", clock)
    test_logger = _attach(handler, "test_incident_sweep")
    try:
        test_logger.warning("old")  # first emit: sweeps the empty dict, sets the sweep mark
        clock.now_ns = _T + window // 2
        test_logger.warning("recent")  # inside the sweep interval: no sweep, both keys held
        assert len(handler._last_report_ns) == 2
        clock.now_ns = _T + window
        test_logger.warning("new")
        await _drain()
    finally:
        test_logger.removeHandler(handler)
    assert {key[-2] for key in handler._last_report_ns} == {"recent", "new"}  # the templates
    assert written == ["old", "recent", "new"]


def test_a_report_cancelled_at_shutdown_is_noted_not_ledgered(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """
    `asyncio.run` cancels every pending task on exit: a warning in the last `lookahead_s` loses
    its report. Not a malfunction, so no ERROR that would re-enter the handler on a closing loop.
    """
    error_ledger.reset()
    future: concurrent.futures.Future[None] = concurrent.futures.Future()
    future.cancel()
    with caplog.at_level(logging.INFO, logger="observability.incidents"):
        incidents._ledger_failed_report("crossed_book", _IID, future)
    assert error_ledger.counts() == {}
    assert [r.levelno for r in caplog.records] == [logging.INFO]
    assert "cancelled at shutdown" in caplog.text


def test_pathologically_nested_json_message_is_unclassified(tmp_path: Path) -> None:
    message = "[" * 100_000 + "]" * 100_000
    assert incidents.classify_incident(_config(tmp_path), message) == ("unclassified", None)


@pytest.mark.parametrize("value", [42, ["x"], None])
def test_a_non_string_json_instrument_id_names_no_instrument(tmp_path: Path, value: object) -> None:
    message = json.dumps({"reason": "steady_state_crossed_book", "instrument_id": value})
    assert incidents.classify_incident(_config(tmp_path), message) == (
        "steady_state_crossed_book",
        None,
    )


@pytest.mark.asyncio
async def test_report_waits_the_lookahead_then_writes(tmp_path: Path) -> None:
    config = _config(tmp_path, lookahead_s=0.0)
    handler = incidents.IncidentHandler(config)
    await handler.report("crossed_book", _IID, "WARNING", "sim", "Crossed book", _T)
    assert [p.name for p in config.report_dir.iterdir()] == [
        f"crossed_book_{_IID}_{_T // 1_000_000_000}.log"
    ]


def test_prune_stale_raw_logs_deletes_orphans_only(tmp_path: Path) -> None:
    """
    The Rust FileWriter never seeds its backup queue from disk, so a previous process's rotated
    files are never pruned by it (see `prune_stale_raw_logs`).
    """
    config = _config(tmp_path, raw_log_dir=tmp_path)
    # The writer's naming: `<name>.log` without rotation, `<name>_<UTC timestamp>.log` with it.
    unrotated = tmp_path / "raw_debug.log"
    stale = tmp_path / "raw_debug_2026-09-12_134542:440.log"
    rotated = tmp_path / "raw_debug_2026-09-12_134603:112.log"
    unrelated = tmp_path / "other_file.txt"
    for path in (unrotated, stale, rotated, unrelated):
        path.write_text("x")
    incidents.prune_stale_raw_logs(config)
    assert [p.name for p in tmp_path.iterdir()] == ["other_file.txt"]


def test_prune_stale_raw_logs_handles_a_missing_dir(tmp_path: Path) -> None:
    incidents.prune_stale_raw_logs(_config(tmp_path, raw_log_dir=tmp_path / "missing"))


@pytest.mark.asyncio
async def test_flush_loop_calls_the_entrypoints_sync() -> None:
    calls: list[int] = []
    task = asyncio.create_task(incidents.raw_log_flush_loop(lambda: calls.append(1), 0.001))
    while not calls:
        await asyncio.sleep(0.001)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert calls


@pytest.mark.asyncio
async def test_an_incident_key_extra_replaces_the_text_in_the_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The documented upgrade path for an f-string warning: name it, and its repeats debounce."""
    written: list[str] = []
    handler = _recording_handler(tmp_path, monkeypatch, written)
    test_logger = _attach(handler, "test_incident_key_extra")
    extra = {error_ledger.INCIDENT_KEY_ATTR: "queue.depth"}
    try:
        for depth in (1, 2):
            test_logger.warning(f"queue depth {depth}", extra=extra)
        await _drain()
    finally:
        test_logger.removeHandler(handler)
    assert written == ["queue depth 1"]


@pytest.mark.asyncio
async def test_an_unhashable_incident_key_still_reports(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    written: list[str] = []
    handler = _recording_handler(tmp_path, monkeypatch, written)
    test_logger = _attach(handler, "test_incident_unhashable_key")
    try:
        test_logger.warning("odd extra", extra={error_ledger.INCIDENT_KEY_ATTR: ["a"]})
        await _drain()
    finally:
        test_logger.removeHandler(handler)
    assert written == ["odd extra"]


@pytest.mark.asyncio
async def test_unclassified_reports_are_capped_per_window_and_the_rest_counted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """
    A burst of distinct f-string texts is one key each; the per-window cap bounds both the
    reports scheduled and the keys held. The first record held back is logged at once and the
    count at the next sweep, and a held-back text reports in the next window. Classified
    incidents are not counted against it.
    """
    written: list[str] = []
    handler = _recording_handler(
        tmp_path, monkeypatch, written, max_unclassified_reports_per_window=2
    )
    clock = _Clock(_T)
    monkeypatch.setattr(incidents, "time", clock)
    test_logger = _attach(handler, "test_incident_cap")
    try:
        with caplog.at_level(logging.INFO, logger=incidents.__name__):
            for n in range(5):
                test_logger.warning(f"burst {n}")
            assert "over 2 per window: the rest" in caplog.text  # the onset, before any sweep
            test_logger.warning(f"Crossed book for {_IID}")
            assert len(handler._last_report_ns) == 3  # two unclassified keys + the classified one
            clock.now_ns = _T + handler._config.debounce_ns
            test_logger.warning("burst 4")
        await _drain()
    finally:
        test_logger.removeHandler(handler)
    assert written == ["burst 0", "burst 1", f"Crossed book for {_IID}", "burst 4"]
    assert "3 unclassified warnings got no incident report: over 2 reports per window" in (
        caplog.text
    )


@pytest.mark.asyncio
async def test_a_ledger_site_is_not_crowded_out_by_the_unclassified_cap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Named sites are a bounded set, so a ledgered failure always gets its report."""
    written: list[str] = []
    handler = _recording_handler(
        tmp_path, monkeypatch, written, max_unclassified_reports_per_window=1
    )
    test_logger = _attach(handler, "test_incident_cap_ledger")
    # Not `_attach`: setting the shared ledger logger's level would outlive this test.
    ledger_logger = logging.getLogger(error_ledger.__name__)
    ledger_logger.addHandler(handler)
    error_ledger.reset()
    try:
        test_logger.warning("noise 1")
        test_logger.warning("noise 2")  # over the cap
        error_ledger.record("site.a", "failed")
        await _drain()
    finally:
        test_logger.removeHandler(handler)
        ledger_logger.removeHandler(handler)
        error_ledger.reset()
    assert written == ["noise 1", "[site.a] failed"]


@pytest.mark.asyncio
async def test_close_logs_the_open_windows_held_back_count(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """No later record may come to trigger the sweep, so `close()` reports the count."""
    written: list[str] = []
    handler = _recording_handler(
        tmp_path, monkeypatch, written, max_unclassified_reports_per_window=1
    )
    test_logger = _attach(handler, "test_incident_cap_close")
    try:
        for n in range(3):
            test_logger.warning(f"burst {n}")
        await _drain()
    finally:
        test_logger.removeHandler(handler)
    with caplog.at_level(logging.INFO, logger=incidents.__name__):
        handler.close()
    assert written == ["burst 0"]
    assert "2 unclassified warnings got no incident report" in caplog.text


@pytest.mark.asyncio
async def test_a_wall_clock_step_back_does_not_suppress_reports(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The debounce runs on the monotonic clock; only `trigger_ns` follows the wall clock."""
    written: list[str] = []
    handler = _recording_handler(tmp_path, monkeypatch, written)
    clock = _Clock(_T)
    monkeypatch.setattr(incidents, "time", clock)
    test_logger = _attach(handler, "test_incident_wall_step")
    try:
        test_logger.warning("x %s", 1)
        clock.now_ns = _T + handler._config.debounce_ns
        clock.wall_step_ns = -3_600_000_000_000  # an hour back
        test_logger.warning("x %s", 2)
        await _drain()
    finally:
        test_logger.removeHandler(handler)
    assert written == ["x 1", "x 2"]


@pytest.mark.asyncio
async def test_an_incident_key_extra_on_a_classified_record_is_ignored(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    written: list[str] = []
    handler = _recording_handler(tmp_path, monkeypatch, written)
    test_logger = _attach(handler, "test_incident_key_classified")
    try:
        for site in ("a", "b"):
            test_logger.warning(
                f"Crossed book for {_IID}", extra={error_ledger.INCIDENT_KEY_ATTR: site}
            )
        await _drain()
    finally:
        test_logger.removeHandler(handler)
    assert written == [f"Crossed book for {_IID}"]


def test_a_cap_below_one_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="max_unclassified_reports_per_window"):
        _config(tmp_path, max_unclassified_reports_per_window=0)
