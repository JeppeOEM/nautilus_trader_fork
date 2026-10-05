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
`scripts/capture_hl_ws.py`, the raw-frame capture harness (DW-169): the subscribe plan per venue,
the CLI refusals that happen before any network I/O, and the generic summary. No network.
"""

import asyncio
import importlib.util
import io
import json
from pathlib import Path
from types import ModuleType
from typing import Any

import aiohttp
import pytest
from _source_tree import PLATFORM_DIR


def _script() -> ModuleType:
    path = PLATFORM_DIR / "scripts" / "capture_hl_ws.py"
    spec = importlib.util.spec_from_file_location("capture_hl_ws", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _write_rows(path: Path, raws: list[Any], start_ns: int = 0, step_ns: int = 10**9) -> None:
    lines = [
        json.dumps({"recv_ns": start_ns + i * step_ns, "raw": raw}) for i, raw in enumerate(raws)
    ]
    path.write_text("\n".join(lines) + "\n")


def test_bybit_default_plan_is_linear_url_and_trade_book_topics() -> None:
    url, subs = _script().subscribe_plan("bybit", "BTCUSDT", None, None)

    assert url == "wss://stream.bybit.com/v5/public/linear"
    assert subs == [
        {"op": "subscribe", "args": ["publicTrade.BTCUSDT", "orderbook.50.BTCUSDT"]},
    ]


def test_hyperliquid_default_plan_is_trades_and_l2book() -> None:
    url, subs = _script().subscribe_plan("hyperliquid", "BTC", None, None)

    assert url == "wss://api.hyperliquid.xyz/ws"
    assert subs == [
        {"method": "subscribe", "subscription": {"type": "trades", "coin": "BTC"}},
        {"method": "subscribe", "subscription": {"type": "l2Book", "coin": "BTC"}},
    ]


def test_bybit_url_override_keeps_default_payload() -> None:
    spot = "wss://stream.bybit.com/v5/public/spot"
    url, subs = _script().subscribe_plan("bybit", "BTCUSDT", spot, None)

    assert url == spot
    assert subs[0]["args"] == ["publicTrade.BTCUSDT", "orderbook.50.BTCUSDT"]


def test_other_venue_uses_given_url_and_payloads_verbatim() -> None:
    payloads = [{"op": "sub", "ch": "trades"}, ["raw", "array"]]

    url, subs = _script().subscribe_plan("other", "IGNORED", "wss://x", payloads)

    assert url == "wss://x"
    assert subs == payloads


def test_subscribe_replaces_known_venue_defaults() -> None:
    payload = {"method": "subscribe", "subscription": {"type": "bbo", "coin": "ETH"}}

    url, subs = _script().subscribe_plan("hyperliquid", "BTC", None, [payload])

    assert url == "wss://api.hyperliquid.xyz/ws"
    assert subs == [payload]


@pytest.mark.parametrize(
    ("url", "subs"),
    [(None, [{"op": "sub"}]), ("wss://x", None), ("wss://x", [])],
)
def test_other_venue_plan_refuses_missing_url_or_payload(
    url: str | None,
    subs: list[Any] | None,
) -> None:
    with pytest.raises(ValueError, match="--venue other"):
        _script().subscribe_plan("other", "BTC", url, subs)


@pytest.mark.parametrize(
    ("argv", "missing"),
    [
        (["--venue", "other", "--url", "wss://x"], "--subscribe"),
        (["--venue", "other", "--subscribe", '{"op": "sub"}'], "--url"),
    ],
)
def test_cli_other_venue_without_url_or_subscribe_exits_2(
    argv: list[str],
    missing: str,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _script()
    monkeypatch.setattr(module, "capture", _no_network)

    with pytest.raises(SystemExit) as exc:
        module.main(argv)

    assert exc.value.code == 2
    assert missing in capsys.readouterr().err


def test_cli_bad_subscribe_json_exits_2_naming_the_value(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _script()
    monkeypatch.setattr(module, "capture", _no_network)

    with pytest.raises(SystemExit) as exc:
        module.main(["--venue", "other", "--url", "wss://x", "--subscribe", "{bad"])

    assert exc.value.code == 2
    assert "{bad" in capsys.readouterr().err


def test_cli_passes_parsed_payloads_to_capture(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    module = _script()
    seen: list[tuple[Any, ...]] = []

    def _record(*args: Any) -> None:
        seen.append(args)

    monkeypatch.setattr(module, "capture", _record)

    module.main(
        ["--venue", "other", "--url", "wss://x", "--subscribe", '{"a": 1}', "--subscribe", "[2]"],
    )

    assert seen == [("other", "BTC", 600.0, "hl_capture.jsonl", "wss://x", ['{"a": 1}', "[2]"])]


def test_cli_subscribe_keeps_the_exact_text_and_unwraps_a_json_string(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.chdir(tmp_path)
    module = _script()
    seen: list[tuple[Any, ...]] = []
    monkeypatch.setattr(module, "capture", lambda *args: seen.append(args))
    exact = '{"price_step": 0.10, "n": 1e5,  "k": 1, "k": 2}'

    module.main(
        ["--venue", "other", "--url", "wss://x", "--subscribe", exact, "--subscribe", '"ping"']
    )

    assert seen[0][5] == [exact, "ping"]


def test_generic_summary_counts_mixed_frames_and_arrival_gaps(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    path = tmp_path / "x.jsonl"
    _write_rows(path, [{"event": "hello"}, ["a", 1], "pong", {"data": 3}], step_ns=500_000_000)

    _script().summarize(str(path))

    out = capsys.readouterr().out
    assert "frames: 4 (2 JSON object, 2 other)" in out
    assert "arrival gap median 0.50s min 0.50s max 0.50s over 3 gap(s)" in out


def test_explicit_other_forces_generic_summary_on_known_frames(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    path = tmp_path / "hl.jsonl"
    book = {"channel": "l2Book", "data": {"levels": [[], []]}}
    _write_rows(path, [book, book])

    _script().main(["--summarize", str(path), "--venue", "other"])

    out = capsys.readouterr().out
    assert "frames: 2 (2 JSON object, 0 other)" in out
    assert "l2Book" not in out


def test_detection_tolerates_non_object_frames() -> None:
    module = _script()
    rows = [{"recv_ns": 0, "raw": raw} for raw in ["x", [1], {"topic": "orderbook.50.BTCUSDT"}]]

    assert module.detect_venue(rows) == "bybit"
    assert module.detect_venue([{"recv_ns": 0, "raw": {"channel": "trades"}}]) == "hyperliquid"
    assert module.detect_venue([{"recv_ns": 0, "raw": [1]}]) == "other"


def test_plan_refuses_an_unknown_venue() -> None:
    with pytest.raises(ValueError, match="unknown venue 'okx'"):
        _script().subscribe_plan("okx", "BTC", "wss://x", None)


@pytest.mark.parametrize(
    ("argv", "message"),
    [
        (["--coin", "ETH", "--subscribe", '{"a": 1}'], "--coin is not used with --subscribe"),
        (["--seconds", "0"], "--seconds must be positive"),
        (["--seconds", "nan"], "--seconds must be positive and finite"),
        (["--seconds", "inf"], "--seconds must be positive and finite"),
        (["--coin", ""], "--coin must not be empty"),
        (["--summarize", "x.jsonl", "--url", "wss://x"], "--url is a capture-only flag"),
        (["--out", "taken.jsonl"], "refusing to overwrite"),
    ],
)
def test_cli_refuses_ignored_coin_bad_seconds_and_existing_out(
    argv: list[str],
    message: str,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "taken.jsonl").write_text("evidence\n")
    module = _script()
    monkeypatch.setattr(module, "capture", _no_network)

    with pytest.raises(SystemExit) as exc:
        module.main(argv)

    assert exc.value.code == 2
    assert message in capsys.readouterr().err
    assert (tmp_path / "taken.jsonl").read_text() == "evidence\n"


def test_frame_row_keeps_json_text_and_binary_and_ends_on_close() -> None:
    frame_row = _script().frame_row

    assert frame_row(aiohttp.WSMsgType.TEXT, '{"a": 1}') == {"raw": {"a": 1}}
    assert frame_row(aiohttp.WSMsgType.TEXT, "pong") == {"text": "pong"}
    assert frame_row(aiohttp.WSMsgType.BINARY, b"\x00\xff") == {"raw_b64": "AP8="}
    assert frame_row(aiohttp.WSMsgType.CLOSE, 1000) is None
    assert frame_row(aiohttp.WSMsgType.ERROR, None) is None


def test_pump_sends_string_payload_as_text_and_reports_an_early_close() -> None:
    ws = _FakeWs(
        [
            (aiohttp.WSMsgType.TEXT, '{"a": 1}'),
            (aiohttp.WSMsgType.TEXT, "pong"),
            (aiohttp.WSMsgType.CLOSE, 1006),
        ],
    )
    out = io.StringIO()

    frames, ended = asyncio.run(_script()._pump(ws, ["ping", {"op": "sub"}], 1e18, out))

    assert ws.sent == [("str", "ping"), ("json", {"op": "sub"})]
    assert frames == 2
    assert ended.startswith("ENDED EARLY: CLOSE")
    rows = [json.loads(line) for line in out.getvalue().splitlines()]
    assert [{k: v for k, v in r.items() if k != "recv_ns"} for r in rows] == [
        {"raw": {"a": 1}},
        {"text": "pong"},
    ]


def test_summary_reports_unparseable_lines_instead_of_crashing(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    path = tmp_path / "cut.jsonl"
    _write_rows(path, [{"event": "hello"}])
    path.write_text(path.read_text() + '{"recv_ns": 5, "raw": {"cut')

    _script().summarize(str(path), "other")

    out = capsys.readouterr().out
    assert "WARNING: 1 unparseable line(s)" in out
    assert "frames: 1 (1 JSON object, 0 other)" in out


def test_explicit_known_venue_overrides_detection(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    path = tmp_path / "by.jsonl"
    _write_rows(path, [{"channel": "trades", "data": []}])

    _script().summarize(str(path), "bybit")

    assert "orderbook: 0 frames" in capsys.readouterr().out


def test_bybit_summary_counts_u_steps(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = tmp_path / "by.jsonl"
    topic = "orderbook.50.BTCUSDT"
    _write_rows(
        path,
        [
            {"topic": topic, "type": "snapshot", "data": {"u": 1}},
            *({"topic": topic, "type": "delta", "data": {"u": u}} for u in (2, 3, 5, 4)),
        ],
    )

    _script().summarize(str(path))

    assert (
        "orderbook: 5 frames, 1 snapshot(s); delta u steps: +1 x1, gaps(>1) x1, regress(<=0) x1; "
        "max step 2"
    ) in capsys.readouterr().out


def test_hyperliquid_summary_reports_replay_and_book_cadence(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    path = tmp_path / "hl.jsonl"
    _write_rows(
        path,
        [
            {"channel": "trades", "data": [{"time": 0}, {"time": 1000}]},
            {"channel": "l2Book", "data": {"levels": [[1], [2]]}},
            {"channel": "l2Book", "data": {"levels": [[1], [3]]}},
        ],
        start_ns=3 * 10**9,
    )

    _script().summarize(str(path))

    out = capsys.readouterr().out
    assert "first trades frame: 2 trades, age 2.0s..3.0s" in out
    assert "l2Book: 2 frames, gap median 1.00s min 1.00s max 1.00s; levels changed in 1/1" in out


class _FakeWs:
    def __init__(
        self,
        frames: list[tuple[aiohttp.WSMsgType, Any]],
        fail_send: bool = False,
    ) -> None:
        self._frames = list(frames)
        self._fail_send = fail_send
        self.sent: list[tuple[str, Any]] = []
        self.close_code: int | None = 1008 if fail_send else None

    async def send_str(self, data: str) -> None:
        if self._fail_send:
            raise ConnectionResetError("closed by venue")
        self.sent.append(("str", data))

    async def send_json(self, data: Any) -> None:
        self.sent.append(("json", data))

    async def receive(self, timeout: float) -> aiohttp.WSMessage:
        kind, data = self._frames.pop(0)
        return aiohttp.WSMessage(kind, data, None)


def test_pump_reports_a_refused_send_as_an_early_end() -> None:
    ws = _FakeWs([], fail_send=True)

    frames, ended = asyncio.run(_script()._pump(ws, ['{"op": "sub"}'], 1e18, io.StringIO()))

    assert frames == 0
    assert ended.startswith("ENDED EARLY: send of")
    assert "close code 1008" in ended


def test_capture_removes_the_empty_file_of_a_failed_connect(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _script()

    async def _refused(*args: Any, **kwargs: Any) -> tuple[int, str]:
        raise aiohttp.ClientConnectionError("dns")

    monkeypatch.setattr(module, "_stream", _refused)
    out = tmp_path / "x.jsonl"

    with pytest.raises(aiohttp.ClientConnectionError):
        module.capture("other", "BTC", 1.0, str(out), "wss://x", ['{"a": 1}'])

    assert not out.exists()


def test_summary_counts_non_row_json_lines_as_unparseable(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    path = tmp_path / "mixed.jsonl"
    _write_rows(path, [{"event": "hello"}])
    path.write_text(path.read_text() + '[1]\n"text"\n{"raw": {}}\n{"recv_ns": 1.5, "raw": {}}\n')

    _script().summarize(str(path), "other")

    out = capsys.readouterr().out
    assert "WARNING: 4 unparseable line(s)" in out
    assert "frames: 1 (1 JSON object, 0 other)" in out


def test_summaries_survive_an_empty_first_trades_frame(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    hl = tmp_path / "hl.jsonl"
    _write_rows(hl, [{"channel": "trades", "data": []}])
    by = tmp_path / "by.jsonl"
    _write_rows(by, [{"topic": "publicTrade.BTCUSDT", "data": []}])

    _script().summarize(str(hl), "hyperliquid")
    _script().summarize(str(by), "bybit")

    out = capsys.readouterr().out
    assert "first trades frame: 0 trades (nothing replayed)" in out
    assert "first publicTrade frame: 0 trades (nothing replayed)" in out


def test_hyperliquid_summary_says_when_there_is_nothing_to_summarise(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    path = tmp_path / "bbo.jsonl"
    _write_rows(path, [{"channel": "bbo", "data": {}}, {"channel": "bbo", "data": {}}])

    _script().summarize(str(path), "hyperliquid")

    assert "no trades frame and 0 l2Book frame(s) in 2 JSON object frame(s)" in (
        capsys.readouterr().out
    )


def _no_network(*args: Any) -> None:
    raise AssertionError("capture must not run")


def test_topics_and_a_coin_list_build_every_subscription_and_split_bybit_at_ten_args() -> None:
    coins = ",".join(f"C{i}USDT" for i in range(6))

    url, subs = _script().subscribe_plan(
        "bybit", coins, None, None, ["allLiquidation", "publicTrade"]
    )

    assert url == "wss://stream.bybit.com/v5/public/linear"
    args = [arg for sub in subs for arg in sub["args"]]
    assert [len(sub["args"]) for sub in subs] == [10, 2]
    assert args[:2] == ["allLiquidation.C0USDT", "allLiquidation.C1USDT"]
    assert args[-1] == "publicTrade.C5USDT"


def test_hyperliquid_topics_subscribe_each_coin() -> None:
    _, subs = _script().subscribe_plan("hyperliquid", "BTC,ETH", None, None, ["trades"])

    assert subs == [
        {"method": "subscribe", "subscription": {"type": "trades", "coin": "BTC"}},
        {"method": "subscribe", "subscription": {"type": "trades", "coin": "ETH"}},
    ]


@pytest.mark.parametrize(
    ("argv", "message"),
    [
        (["--topic", "trades", "--subscribe", '{"a": 1}'], "--topic is not used with --subscribe"),
        (["--venue", "other", "--url", "wss://x", "--topic", "t"], "--venue other requires"),
        (["--coin", "BTC,"], "--coin must not be empty"),
    ],
)
def test_cli_refuses_unusable_topic_and_coin_lists(
    argv: list[str],
    message: str,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    module = _script()
    monkeypatch.setattr(module, "capture", _no_network)

    with pytest.raises(SystemExit) as exc:
        module.main(argv)

    assert exc.value.code == 2
    assert message in capsys.readouterr().err


def test_cli_passes_topics_to_capture(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    module = _script()
    seen: list[dict[str, Any]] = []
    monkeypatch.setattr(module, "capture", lambda *args, **kwargs: seen.append(kwargs))

    module.main(["--venue", "bybit", "--coin", "BTCUSDT", "--topic", "allLiquidation"])

    assert seen == [{"topics": ["allLiquidation"]}]
