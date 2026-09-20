// Ported verbatim (content-wise) from troll/ml_signals/docs_page.py's KB array, as read
// 2026-09-13 (branch bmad). Story 15.1 Task 4 -- see that story's Completion Notes for the
// section-by-section (docs_page.py KB entry -> this file) checklist.

import type { KbDoc } from "./data";
import { svgArchitecture } from "./diagram";

const architectureHtml = `<figure class="diagram">${svgArchitecture()}<figcaption>The full data path: the collector is the only thing that talks to dYdX for market data; ranking_engine is the only thing that computes ranking; every UI is a pure reader of Redis. live_paper is the one module sanctioned to run its own TradingNode against dYdX directly (AD-8).</figcaption></figure>
<div class="sec"><h2>One paragraph</h2><p>A collector pulls live dYdX market data straight off the Rust adapter and writes it to a Nautilus-native Parquet catalog, publishing a live 1-second snapshot feed to Redis as it goes. A ranking engine reads that feed, scores every coin by volume or volatility, and publishes the result back to Redis. A web dashboard and a terminal UI both read the same two Redis feeds — never recomputing anything themselves — to show live charts and a watchlist. Indicators written once in <code>ml_signals</code> get reused unmodified in Jupyter research, Nautilus backtests, and a live paper-trading bot (<code>live_paper</code>), which publishes its own status to Redis so the TUI can monitor and start/stop it.</p></div>
<div class="sec"><h2>Module map</h2><table><tr><th>Module</th><th>Role</th><th>Talks to</th></tr>
<tr><td><code>dydx_collector/</code></td><td>Captures live dYdX data → Parquet + <code>snapshots:raw</code></td><td>dYdX WS/REST, Redis (publish), catalog (write)</td></tr>
<tr><td><code>ml_signals/</code></td><td>Shared indicators + web dashboard + backtesting</td><td>catalog (read), Redis (read snapshots/rankings, publish ranking:control), metrics.db (read)</td></tr>
<tr><td><code>ranking_engine/</code></td><td>Sole computer of coin ranking</td><td>Redis (read snapshots/control, publish rankings:live), metrics.db (write), dYdX REST</td></tr>
<tr><td><code>live_paper/</code></td><td>The actual trading bot — TradingNode + Strategy, paper or gated real-money</td><td>dYdX WS/HTTP, Redis (bots:status/control)</td></tr>
<tr><td><code>bot_tui/</code></td><td>Keyboard-only terminal UI, interactive/on-demand</td><td>Redis (read rankings/snapshots/bots:status, publish ranking:control/bots:control)</td></tr>
</table><p>Module boundary rule (AD-4): every module downstream of the collector depends only on shared data types and Redis/HTTP contracts — never another module's internal state. <code>dydx_collector</code> never imports from anything downstream of it.</p></div>
<div class="sec"><h2>Redis channel reference</h2><table><tr><th>Channel</th><th>Publisher</th><th>Subscriber</th><th>Payload</th></tr>
<tr><td><code>snapshots:raw</code></td><td>dydx_collector</td><td>ranking_engine, dashboard, bot_tui</td><td>One DydxSecondSnapshot per instrument per second</td></tr>
<tr><td><code>rankings:live</code></td><td>ranking_engine</td><td>dashboard, bot_tui</td><td>{mode, ranks:[...], stale_instrument_ids} on change + heartbeat</td></tr>
<tr><td><code>ranking:control</code></td><td>dashboard, bot_tui</td><td>ranking_engine</td><td>Mode-switch request, last-write-wins</td></tr>
<tr><td><code>bots:status</code></td><td>live_paper</td><td>bot_tui</td><td>Per-bot PnL/position/mode/heartbeat, every 5s</td></tr>
<tr><td><code>bots:control</code></td><td>bot_tui</td><td>live_paper</td><td>{bot_id, action: start|stop} — never a mode field</td></tr>
</table></div>
<div class="sec"><h2>Deployment topology</h2><p>All services bind <code>127.0.0.1</code> only / <code>network_mode: host</code> — nothing is reachable without an SSH tunnel over Tailscale. <code>redis</code>/<code>collector</code>/<code>dashboard</code>/<code>ranking_engine</code>/<code>data_api</code>/<code>dozzle</code> start by default with <code>restart: always</code>. <code>live-paper</code> is explicit opt-in (<code>profiles: ["live-paper"]</code>, <code>make up-live-paper</code>) with a capped <code>on-failure:5</code> restart policy so a bad config can't crash-loop against dYdX's API. <code>bot_tui</code> is a one-shot interactive tool, never a background daemon.</p></div>
<div class="sec"><h2>What's genuinely not finished</h2><ul>
<li><b>DummyStrategy is a wiring proof, not a tuned strategy</b> — by design, but means nothing here is validated to make money.</li>
<li><b>live_paper Cache persistence + TUI trades blotter/PnL chart</b> (backlog) — blocks real trade-history visibility across restarts.</li>
<li><b>orders_inflight() race guard has no test</b> proving it holds under a real concurrent-fill race.</li>
<li><b>EXTERNAL vs INTERNAL bar aggregation choice</b> picked defensively, never confirmed better against a real dYdX connection.</li>
<li><b>Real-money path (RealMoneyConfig)</b> exists and is gated but has never been exercised even once.</li>
</ul></div>`;

export const KB: KbDoc[] = [
  {
    id: "architecture", group: "start", name: "System Architecture",
    tagline: "What each module owns, and exactly how the pieces talk — Redis channels, SQLite, Parquet, HTTP.",
    html: architectureHtml,
    refs: ["troll/ARCHITECTURE.md"],
  },
  {
    id: "getting-started", group: "ops", name: "Getting Started",
    tagline: "Build the base image, configure instruments, deploy, and reach the dashboard remotely over Tailscale.",
    html: `<div class="sec"><h2>First-time setup</h2><p>From <code>troll/</code>:</p><div class="formula">make build-base   # ~15 min, once only — compiles Nautilus from source
make up            # build collector image (seconds) and start collecting
make logs          # tail live collector output
make web           # open Dozzle log viewer (http://localhost:8080)</div></div>
<div class="sec"><h2>Configure instruments</h2><p>Edit <code>troll/dydx_collector/config.toml</code> — hot-reloaded every <code>config_reload_seconds</code> (30s default), no restart needed:</p><div class="formula">[[instruments]]
id = "BTC-USD-PERP.DYDX"
bar_intervals = ["1-MINUTE"]</div></div>
<div class="sec"><h2>Run the dashboard</h2><p><code>make up</code> starts <code>data_api</code>, which serves this UI at <code>http://localhost:9100</code>. Reads directly from the catalog + Redis, no collector restart needed.</p></div>
<div class="sec"><h2>Remote access via Tailscale + SSH tunnel</h2><p>Dashboard, Redis, and Dozzle all bind <code>127.0.0.1</code> only — nothing is reachable from the public internet or even the tailnet directly.</p><div class="formula">ssh -N -L 9100:127.0.0.1:9100 -L 8080:127.0.0.1:8080 you@&lt;vps-tailscale-ip&gt;</div><p>Then open <code>http://localhost:9100</code> (dashboard) or <code>:8080</code> (Dozzle) locally. <code>-N</code> holds the tunnel open with no remote shell.</p>
<p><b>Feed-health alerts:</b> set <code>WATCHDOG_NTFY_URL</code> (e.g. an ntfy.sh topic) on the <code>collector</code> service to get a push notification if every subscribed instrument's book goes stale for 30s+ (OBS-01). Without it, the same condition is only logged.</p></div>
<div class="sec"><h2>Inspect the catalog directly</h2><div class="formula">from nautilus_trader.persistence.catalog import ParquetDataCatalog
catalog = ParquetDataCatalog("troll/dydx_collector/catalog")
catalog.instruments()
catalog.trade_ticks(instrument_ids=["BTC-USD-PERP.DYDX"])</div></div>`,
    refs: ["troll/README.md"],
  },
  {
    id: "bot-ops", group: "ops", name: "Bot Operations",
    tagline: "Starting/stopping the live paper bot, writing a new strategy, and spotting a stale feed without digging through logs.",
    html: `<div class="sec"><h2>Two separate strategy paths — not interchangeable</h2><table><tr><th></th><th>Backtest / research</th><th>Live paper bot</th></tr>
<tr><td>Where</td><td><code>ml_signals/*_strategy.py</code></td><td><code>live_paper/strategy.py</code></td></tr>
<tr><td>Runtime</td><td><code>BacktestNode</code></td><td><code>TradingNode</code> (AD-8 exception)</td></tr>
<tr><td>Wiring</td><td><code>ImportableStrategyConfig</code>, string path</td><td>One strategy hardcoded in <code>node.py</code></td></tr>
<tr><td>Start/stop</td><td>One-shot process call</td><td>Long-running Docker container, Redis pub/sub or bot_tui</td></tr></table></div>
<div class="sec"><h2>Starting/stopping live_paper</h2><div class="formula">make up-live-paper   # start (background, live-paper profile)
docker exec dydx-redis redis-cli PUBLISH bots:control '{"bot_id":"bot-01","action":"stop"}'
docker exec dydx-redis redis-cli PUBLISH bots:control '{"bot_id":"bot-01","action":"start"}'</div><p>Stopping does <b>not</b> flatten an open position — no auto-flatten logic exists. A message with the wrong <code>bot_id</code> is silently ignored, so this is safe against a shared Redis instance with multiple bots.</p>
<p>Via <code>bot_tui</code>: <code>make tui</code> → Bots pane (<code>:bots</code>) → highlight → <code>s</code> to start/stop. Stopping a <em>running</em> bot opens a type-to-confirm prompt; starting doesn't. Press <code>v</code> on a bot's detail view to read its strategy source read-only; <code>i</code> to see its incidents log (restarts, feed interruptions), persisted in Redis (<code>bots:incidents:{bot_id}</code>, last 50 kept).</p></div>
<div class="sec"><h2>Writing a new live strategy</h2><ol style="padding-left:20px"><li>New <code>Strategy</code> + <code>StrategyConfig</code> pair in <code>live_paper/</code>, following <code>strategy.py</code>'s <code>DummyStrategy</code> as reference.</li><li>Swap the import/instantiation in <code>node.py</code>'s <code>build_node()</code>.</li><li>Add new tunables to both <code>PaperConfig</code>/<code>RealMoneyConfig</code> and <code>config.toml</code>. Never add a <code>mode</code> key — <code>load_paper_config()</code> hard-errors on it by design.</li><li>Rebuild — code is baked into the image, not bind-mounted.</li></ol></div>
<div class="sec"><h2>Spotting downtime without digging through logs</h2><ul><li>A single coin's feed going stale shows as a <code>~ stale feed: SOL-USD-PERP.DYDX</code> banner in both bot_tui's Coins-pane breadcrumb and the web dashboard's status line — same <code>stale_instrument_ids</code> field, both UIs (SSOT-04).</li><li>A bot's own feed going stale, or the process restarting, is in its Incidents log (<code>i</code> key in bot_tui's Bot-detail).</li></ul><p>Neither parses log files — both are computed from data these processes already track.</p></div>`,
    refs: ["troll/docs/BOT_OPERATIONS.md"],
  },
  {
    id: "data-dictionary", group: "data", name: "Data Dictionary",
    tagline: "What the collector actually stores, per raw type — source, cadence, retention, and known dead fields.",
    html: `<div class="sec"><h2>Raw types collected</h2><table><tr><th>Type</th><th>Source</th><th>Cadence</th><th>Downstream use</th></tr>
<tr><td><code>TradeTick</code></td><td>v4_trades WS</td><td>event-driven</td><td class="neg">no longer written</td></tr>
<tr><td><code>OrderBookDeltas</code></td><td>v4_orderbook WS</td><td>event-driven</td><td>applied to in-memory book; stored only if <code>store_order_book_deltas=true</code> (no instrument opts in today)</td></tr>
<tr><td><code>Bar</code></td><td>candles WS</td><td>—</td><td class="neg">never subscribed — dead capability</td></tr>
<tr><td><code>MarkPriceUpdate</code> / <code>IndexPriceUpdate</code></td><td>markets WS</td><td>event-driven</td><td class="neg">stored, no reader found</td></tr>
<tr><td><code>FundingRateUpdate</code></td><td>markets WS</td><td>event-driven</td><td class="neg">stored, no reader found</td></tr>
<tr><td><code>DydxSecondSnapshot</code></td><td>built in-process from book + trades</td><td>every <code>snapshot_interval_seconds</code> (0.5s default)</td><td>the core record — feeds every live indicator on this site</td></tr>
<tr><td><code>OpenInterest</code></td><td>REST poll (stdlib urllib)</td><td>every 300s</td><td class="neg">stored field itself unused; the <em>parallel</em> volume24H liquidity classification from the same poll <b>is</b> used</td></tr>
<tr><td><code>InstrumentStatus</code></td><td>markets WS</td><td>event-driven</td><td class="neg">stored, no reader found</td></tr>
</table><p><code>DydxSecondSnapshot</code> fields: <code>bid_prices/sizes</code>, <code>ask_prices/sizes</code> (top 20 levels), <code>buy_volume/sell_volume</code>, <code>buy_count/sell_count</code>, <code>open/high/low/close_price</code> (this is the collector's only surviving record of traded price now that raw <code>TradeTick</code> is no longer persisted). Every computed indicator on this site's Indicators tab traces back to this one record.</p></div>
<div class="sec"><h2>Guards before a snapshot is ever emitted</h2><p><code>_second_loop</code> skips crossed books (forces a resubscribe after 3s of a persistent cross — see the <a data-nav="kb:pm-crossed-book">crossed-book postmortem</a>) and skips stale books with no deltas for 5s — both DATA-01 “flag the gap, never fabricate” implementations.</p></div>
<div class="sec"><h2>Retention: the honest version</h2><table><tr><th>Type</th><th>Pinned instruments</th><th>Any other dYdX market</th></tr>
<tr><td><code>TradeTick</code></td><td>no longer written</td><td>4h, no longer growing</td></tr>
<tr><td><code>OrderBookDeltas</code></td><td>not stored (nothing opts in)</td><td>not stored</td></tr>
<tr><td>Mark/Index/Funding/Status</td><td class="neg">unlimited</td><td>4h</td></tr>
<tr><td><code>DydxSecondSnapshot</code></td><td class="neg">unlimited</td><td>not collected</td></tr>
<tr><td><code>OpenInterest</code></td><td class="neg">unlimited</td><td>4h</td></tr>
</table><p>Every configured instrument is <code>pinned=true</code>, and pinned instruments are permanently exempt from the prune loop — so mark/index price, funding, open interest, instrument status, and 1s book snapshots for every configured coin accumulate forever with no built-in cap today.</p></div>`,
    refs: ["troll/docs/DATA_DICTIONARY.md"],
  },
  {
    id: "database-setup", group: "data", name: "Database & Persistence",
    tagline: "Four separate storage mechanisms — what each is for, who owns writes, and how to reach it remotely.",
    html: `<div class="sec"><h2>Four mechanisms, no overlap</h2><table><tr><th>Mechanism</th><th>For</th><th>Durable?</th><th>Reachable?</th></tr>
<tr><td>Redis</td><td>Live pub/sub + latest-value cache</td><td>No</td><td>127.0.0.1:6379 only</td></tr>
<tr><td>metrics.db (SQLite)</td><td>31-day rolling per-instrument history</td><td>Yes</td><td>file only</td></tr>
<tr><td>fills.db (SQLite)</td><td>Append-only live-paper fill ledger</td><td>Yes</td><td>file only</td></tr>
<tr><td>Parquet catalog</td><td>Raw market data archive</td><td>Yes</td><td>file only</td></tr>
</table><p>SEC-01 governs every port here: localhost-only, always. Remote access is SSH tunnel only.</p></div>
<div class="sec"><h2>Redis specifics</h2><p><code>redis:8-alpine</code>, container <code>dydx-redis</code>. <b>No persistence, no TTLs, no complex types</b> — if the container restarts, everything is gone and services just republish on their next cycle. Staleness is judged consumer-side (age of the payload's own timestamp), never a Redis expiry. Only plain <code>GET</code>/<code>SET</code>/<code>PUBLISH</code>/<code>SUBSCRIBE</code> — no hashes, sorted sets, streams.</p></div>
<div class="sec"><h2>metrics.db</h2><p>Owned by <code>ranking_engine/metrics_store.py</code>. Table: <code>snapshots(ts, instrument_id, price, pct_1h, pct_24h, volatility, ofi, microprice, spread, rank, volume24h)</code>, upserted every 60s, pruned to 31 days. <b>Writer:</b> ranking_engine exclusively. <b>Reader:</b> dashboard only, mounted read-only.</p><p>Note: the <code>ofi</code> column here is the <em>top-of-book-only</em> <code>OrderFlowImbalance</code>, a different metric from the multi-level <code>ofi_10_z</code>/<code>ofi_3/5/10</code> fields that live only in <code>rankings:live</code>. The SQLite history and the live ranking table track genuinely different OFI computations — don't expect them to match.</p></div>
<div class="sec"><h2>fills.db</h2><p>Owned by <code>live_paper/fills_store.py</code>. Append-only, event-sourced — one row per <code>OrderFilled</code> event. Exists specifically because Nautilus's <code>Cache.positions_closed()</code> silently discards prior closed positions on a NETTING-mode position reopen; <code>fills.db</code> is the durable source of truth trade history is rebuilt from instead.</p></div>
<div class="sec"><h2>config.toml — mutable runtime state, not static config</h2><p>Easy to mistake for a static file, but it's the one file the running system rewrites on its own: every <code>collector:control</code> action (start/unpin/stop/pin_top_liquid) triggers a full rewrite via <code>save_config()</code> — hand-added comments won't survive it. Hot-reloaded every 30s by the collector, so a hand-edit is picked up without a restart.</p></div>
<div class="sec"><h2>Viewing it remotely</h2><table><tr><th>Command</th><th>What it does</th></tr>
<tr><td><code>make remote-db</code></td><td>Tunnels Redis to localhost so a GUI client can connect as if local</td></tr>
<tr><td><code>make remote-web</code></td><td>Tunnels + opens the dashboard</td></tr>
<tr><td><code>make remote-tui</code></td><td>SSHes in with a real TTY and runs bot_tui interactively</td></tr>
</table><p>The two SQLite files and the Parquet catalog have no server to tunnel to — <code>rsync</code> a copy down instead.</p></div>`,
    refs: ["troll/docs/DATABASE_SETUP.md"],
  },
  {
    id: "backtesting", group: "backtest", name: "Backtesting & Strategies",
    tagline: "Which existing backtest runner to copy, how to build a strategy, and how to wire it in.",
    html: `<div class="sec"><h2>Which existing backtest to copy</h2><table><tr><th>Data granularity</th><th>Feed</th><th>Copy</th></tr>
<tr><td>Bars (aggregated from trades)</td><td><code>TradeTick</code> → internal <code>Bar</code></td><td><code>backtest_dydx.py</code></td></tr>
<tr><td>Raw 1s book snapshots</td><td><code>DydxSecondSnapshot</code></td><td><code>backtest_snapshot.py</code></td></tr>
<tr><td>Raw deltas + trades + bars</td><td><code>OrderBookDelta</code>, <code>TradeTick</code>, <code>Bar</code></td><td><code>backtest_ofi.py</code></td></tr>
</table><p>Don't write a new backtest runner from scratch — copy the closest match and swap the <code>strategy_path</code>/<code>config_path</code>/<code>data=[...]</code> list. <code>backtest_dydx.py</code> defaults to backtesting every coin in the live Watchlist (needs <code>data_api</code> running); pass <code>symbols=[...]</code> to skip that dependency.</p></div>
<div class="sec"><h2>Strategy conventions (every *_strategy.py follows these)</h2><ul>
<li><code>frozen=True</code> on the config class.</li>
<li><code>on_start</code>: look up <code>self.instrument</code> via <code>self.cache.instrument(...)</code>; <code>self.stop()</code> + log an error if missing, never assume it's there.</li>
<li>Subscribe to exactly the feeds needed — <code>subscribe_bars</code>, <code>subscribe_trade_ticks</code>, <code>subscribe_order_book_deltas</code>, or <code>subscribe_data(DataType(DydxSecondSnapshot), ...)</code> for the pre-computed snapshot.</li>
<li>Position checks via <code>self.portfolio.is_flat/is_net_long/is_net_short</code>, never hand-rolled tracking.</li>
<li>Reuse <code>ml_signals/indicators.py</code>/<code>book_features.py</code> — never reimplement OFI/OBI/microprice/spread inline.</li>
</ul></div>
<div class="sec"><h2>Wiring a strategy into a backtest run</h2><ol style="padding-left:20px"><li>Point <code>strategy_path</code>/<code>config_path</code> at the new files.</li><li>List every <code>BacktestDataConfig</code> the strategy subscribes to — missing one means the subscription silently gets no data, no error.</li><li>Custom types (e.g. <code>DydxSecondSnapshot</code>) need an explicit <code>client_id=str(venue)</code> and usually a parallel <code>TradeTick</code> config purely for instrument auto-registration.</li><li>Only <code>1-MINUTE</code> bars are actually in the catalog — anything else needs internal aggregation from <code>TradeTick</code>.</li></ol><p>Trust <code>BacktestResult.stats_pnls</code>/<code>stats_returns</code> for whether trades happened — <code>total_orders</code>/<code>total_positions</code> were found unreliable (sometimes 0 despite real fills) in this pinned nautilus_trader version.</p></div>`,
    refs: ["troll/ml_signals/BACKTESTING.md"],
  },
  {
    id: "pm-crossed-book", group: "postmortem", name: "Crossed-Book Root Cause",
    tagline: "72% of crossed-book episodes proven to be our own pipeline's fault, not the venue's — exact locus still open.",
    status: "open",
    html: `<div class="sec"><p><b>Standard applied:</b> DATA-02 — detect-and-recover is not sufficient, the exact mechanism must be known. <b>Status: <span class="neg">still genuinely open</span></b>, not quietly resolved just because the visible symptom is smaller.</p></div>
<div class="sec"><h2>Symptom</h2><p>The collector's <code>_second_loop</code> frequently logs “Crossed book” (bid ≥ ask) for BTC/ETH and other liquid instruments, escalating to a forced resubscribe if it persists.</p></div>
<div class="sec"><h2>Two timing bugs found and fixed along the way</h2><ul>
<li><b><code>_CROSSED_RESYNC_NS</code> 15s → 3s.</b> A real desync never self-heals from more deltas alone; waiting 15s before forcing a resubscribe was pure downside.</li>
<li><b><code>_flush_loop</code>'s periodic Parquet write was blocking the event loop.</b> <code>write_data()</code> ran synchronously on the event-loop thread every <code>flush_interval_seconds</code> (60s), during which crossed-book detection couldn't run at all. <span class="callout">An earlier fix attempt was wrong</span> — assumed the cause was delta-processing bursts and built a decoupled ingest queue; that helped but didn't fix it. The staleness canary's suspiciously exact ~69–71s periodicity (matching the 60s flush interval almost exactly) is what pointed at the real cause. Actual fix: <code>_flush_once</code> now offloads the write via <code>asyncio.to_thread</code>.</li>
</ul></div>
<div class="sec"><h2>Permanent instrumentation kept in production</h2><ul><li>Per-side last-delta timestamps in every crossed-book log line.</li><li><code>_second_loop</code> staleness canary (2s slack) — the thing that caught the wrong first diagnosis above.</li><li><code>nautilus_pyo3.init_logging()</code> now called in <code>main()</code> — this collector never went through TradingNode/Kernel startup, so every Rust-side <code>log::error!</code>/<code>warn!</code> in the whole dYdX adapter was silently dropped, never emitted, before this fix.</li></ul></div>
<div class="sec"><h2>The decisive experiment</h2><p>Built an independent reference WS client — pure Python + aiohttp, zero shared code with nautilus_trader — connecting directly to dYdX's indexer, maintaining its own naive book, and dumping an episode file the instant the real collector logged a crossed book.</p>
<table><tr><th>Result</th><th>Count</th><th>Meaning</th></tr>
<tr><td>Reference client matches collector's (wrong) reading</td><td>16</td><td>genuine venue-side crossing — dYdX's own served book really is crossed (expected under dYdX v4's per-validator, pre-consensus book architecture)</td></tr>
<tr><td class="neg">Reference client diverges (shows correct data collector doesn't)</td><td><b>41 (72%)</b></td><td class="neg">our own pipeline provably lost/failed to apply data the venue actually sent</td></tr>
</table><p>Byte-level proof for one BTC-USD episode: dYdX sent an ask-level removal; the reference client correctly moved its ask; the collector's book never reflected it. Narrowed to exactly two possibilities: a connection-specific transport-level loss unique to our WS connection, or a silent failure inside the Rust WS handler (audited — no silent-drop path found in <code>crates/adapters/dydx/src/websocket/handler.rs</code>).</p></div>
<div class="sec"><h2>Open question</h2><p>Pinning down category 2's exact locus. Three options were laid out: (A) TLS-intercept the collector's own connection — blocked, requires patching <code>crates/</code> which FORK-01/02 forbid without an explicit exception; (B) stop here — explicitly rejected given DATA-02; (C) run 2–3 independent reference clients against <em>each other</em>, not just vs. the collector — no crates/ changes needed, ~1 hour of work, <b>recommended next step, not yet built.</b></p></div>`,
    refs: ["troll/.planning/debug/crossed-book-root-cause.md", "troll/CLAUDE.md DATA-02, DATA-04"],
  },
  {
    id: "pm-nifelheim", group: "postmortem", name: "Nifelheim Resource Exhaustion",
    tagline: "Stale books across nearly every instrument + ranking_engine's silent restart loop, both traced to one root cause: the VPS is oversubscribed.",
    status: "deferred",
    html: `<div class="sec"><p><b>Status:</b> mechanism identified (DATA-02 standard met). <b>No fix applied</b> — infra decision deferred by the user.</p></div>
<div class="sec"><h2>Symptom</h2><p>Widespread “Stale book” warnings across nearly every subscribed instrument (BTC/ETH/SOL included) — 31 <code>_second_loop tick arrived Ns late</code> events (2–21.5s late) over a 95-minute window, each followed by a stale-book burst hitting ~27–28 of ~28 instruments simultaneously.</p></div>
<div class="sec"><h2>Root cause: host CPU + memory oversubscription</h2><p>2 vCPU / 3.7GB, 0 swap. <code>uptime</code> load average 10.85 on a 2-core box (4–5× oversubscribed). <code>dydx-collector</code> alone at 105% CPU continuous; <code>dydx-ranking-engine</code> RestartCount=430, cycling every ~2 minutes — caught live via <code>docker events</code>: <code>container oom → die (exitCode=137) → start</code>. This is the <b>host's global OOM-killer</b>, confirmed not a container memory cap (<code>Memory=0</code>, uncapped) and not a code-level exit path (grepped — no <code>sys.exit</code>/unhandled-exception path exists in <code>engine.py</code>).</p><p><code>free -h</code>: 119Mi free / 1.0Gi available out of 3.7Gi — essentially no slack before <code>ranking_engine</code> even accumulates its rolling-window state.</p></div>
<div class="sec"><h2>Why this wasn't caught earlier</h2><p>A prior instance of the same failure class (2026-09-11, <code>compute_all()</code> defaulting to ~300 instruments × 25h lookback × 32-way concurrency) was already fixed once by scoping to only pinned/live instruments — that reduced per-cycle memory but didn't add headroom, and a later commit that bumped the collector's instrument cap re-triggered the same OOM class under a new guise: a silent restart loop instead of an obvious one-time crash.</p></div>
<div class="sec"><h2>Options discussed, none chosen</h2><ol style="padding-left:20px"><li>Add swap — quick, reversible, doesn't fix CPU oversubscription, trades OOM-kills for thrashing under sustained pressure.</li><li>Resize the VM — addresses the actual root cause, provider-side action.</li><li>Reduce load — fewer instruments, or move dashboard/bot_tui/ranking_engine off this box.</li><li><b>(Chosen for now) Document and defer.</b></li></ol></div>
<div class="sec"><h2>Addendum (Epic 13, Story 13.1)</h2><p><code>_slow_loop_task</code> now passes <code>max_workers=4</code> to <code>compute_all()</code> (was 32) — bounds concurrent Parquet reads on the 60s cycle. An explicit small mitigation, <b>not</b> a resolution of the underlying CPU/RAM oversubscription. Real before/after <code>docker stats</code> evidence was not collected (no SSH access from this dev environment) — whether this measurably reduces the OOM rate in production is still an open, deferred verification. Story 13.2 is the actual fix: removes the recurring Parquet re-scan from the hot path entirely.</p></div>`,
    refs: ["troll/.planning/debug/nifelheim-resource-exhaustion-2026-09-12.md"],
  },
  {
    id: "fix-flatline", group: "fixed", name: "Bid/Ask Flatline on Coin Chart",
    tagline: "A per-coin chart's bid/ask froze during WS reconnect recovery while the homepage kept moving — root-caused and fixed.",
    status: "done",
    html: `<div class="sec"><p><b>Symptom:</b> an individual coin's chart in the dashboard would freeze bid or ask (sometimes both) while other data kept updating and the venue's own price was clearly still moving. Silent failure, no log errors.</p></div>
<div class="sec"><h2>Root cause</h2><p><code>_live_books[iid]</code> is frozen during WebSocket reconnect recovery. When the dYdX WS reconnects, the Rust client re-subscribes instruments at a 2/sec rate limit — the last instrument in the sorted subscription queue waits up to N/2 seconds for its fresh snapshot. During that window, <code>_second_loop</code> had no staleness check and repeatedly emitted the pre-reconnect (stale) book state. Plotly's <code>mode=lines</code> then drew a straight, misleading line across that flat stretch.</p></div>
<div class="sec"><h2>Fix (two parts)</h2><ol style="padding-left:20px"><li><code>collector._second_loop</code> tracks <code>_last_book_update_ns</code> per instrument and skips snapshot emission once a book hasn't received deltas in &gt;5s — stops stale data from ever reaching Redis.</li><li><code>dashboard._coin_chart_json</code> detects gaps &gt;2.5s between consecutive snapshot timestamps and inserts a null — Plotly then draws an honest break instead of a flat line.</li></ol><p>Verified across 46/46 tests, including 4 new gap-detection tests exercising the null-insertion path directly.</p></div>`,
    refs: ["troll/.planning/done/bid-ask-flatline-coin-chart.md"],
  },
  {
    id: "fix-unpin-exclude", group: "fixed", name: 'Unpin → config.exclude, with confirm',
    tagline: 'Folded the collector\'s separate "unpinned" list into the existing exclude denylist, and added a type-to-confirm guard before unpinning.',
    status: "done",
    html: `<div class="sec"><h2>What changed</h2><ol style="padding-left:20px"><li><code>"unpin"</code> now writes straight into <code>config.exclude</code> instead of a separate <code>CollectorConfig.unpinned</code> field, which was dropped entirely.</li><li>bot_tui's “unpinned” section now reflects <code>config.exclude</code> as a whole — a coin hand-added to <code>exclude</code> in <code>config.toml</code> shows up there too, no separate “via unpin” distinction any more.</li><li>Pressing <code>p</code> to unpin now opens the same type-to-confirm prompt pattern already used for <code>x</code>/stop, instead of firing immediately.</li></ol></div>
<div class="sec"><h2>Why this is a real behavior change, not just a rename</h2><p><code>classify_liquidity</code> already treats every id in <code>exclude</code> as permanently illiquid regardless of volume — a hand-curated denylist for stablecoins/garbage markets. Folding <code>unpin</code> into it means an unpinned coin is now <em>also</em> permanently labeled “illiquid” in <code>collector:status</code>, not just “not currently collected.” Presumably the intended strength, called out explicitly so it isn't a surprise later.</p></div>
<div class="sec"><h2>Design note taken</h2><p>The confirm-guard implementation was generalized into one shared “pending collector action” state (action-name parameterized) rather than duplicating the stop-confirm's four methods a second time — in line with this repo's deletion-over-addition preference (DESIGN-03) given the two flows would otherwise be near-identical.</p></div>`,
    refs: ["troll/.planning/done/collector-unpin-exclude-confirm.md"],
  },
  {
    id: "fix-ranking-clarity", group: "fixed", name: "Ranking Table: Vol Label + Lean Placement",
    tagline: "Disambiguated the ranking table's bare “Vol” column and moved microprice lean off the cross-instrument table onto the single-coin page.",
    status: "done",
    html: `<div class="sec"><p><b>Date:</b> 2026-09-13. <b>Trigger:</b> both the web ranking table and bot_tui's Coins pane showed a bare <code>“Vol”</code> header sitting on the same row as <code>“Vol Score”</code> and <code>“Vol24h”</code> — three different numbers, one ambiguous name. The same table also showed <code>“u lean”</code> (microprice lean), a single-coin directional signal, mixed in among the cross-coin ranking columns.</p></div>
<div class="sec"><h2>Change</h2><ul>
<li>Relabeled the <code>volatility</code> ranking-table column from <code>“Vol”</code> to <code>“Vol(catalog)”</code> in <code>ml_signals/ranking_columns.py</code> (the single SSOT both the web table and bot_tui's Coins pane render from) and in the web dashboard's client-side ranking-table script — matching the label the coin-detail page already used for the same field, so it now reads identically everywhere it appears. See <a data-nav="i:vol_catalog">Vol(catalog)</a> for the full definition.</li>
<li>Moved <code>microprice_lean</code> out of <code>RANKING_COLS</code> (shared by both ranking tables) into <code>_HISTORY_ONLY_COLS</code> — the same mechanism already used to keep <code>rank</code> off the live table. It's still shown on both single-coin detail views (web + bot_tui) and now also still charts correctly on the 31-day history page. See <a data-nav="i:microprice_lean">Microprice Lean</a>.</li>
</ul></div>
<div class="sec"><h2>Why this approach</h2><p><code>_HISTORY_ONLY_COLS</code> already existed for exactly this purpose (“field charted on the coin's history page, not shown as a ranking-table column”) — reusing it instead of introducing a second parallel list keeps the SSOT intact (troll/CLAUDE.md SSOT-04) and required touching only the column metadata, not either UI's rendering code.</p></div>`,
    refs: ["troll/ml_signals/ranking_columns.py", "troll/ml_signals/dashboard.py"],
  },
  {
    id: "self-hosted-docs", group: "fixed", name: "Signal Atlas moved self-hosted",
    tagline: "This page itself: moved from a standalone Claude Artifact into dashboard.py as /docs, dark-themed to match the rest of the app.",
    status: "done",
    html: `<div class="sec"><p>Originally built as a standalone Claude Artifact so it could be reviewed before committing to a permanent home. Moved into <code>ml_signals/docs_page.py</code> and served at <code>/docs</code> — same content model (client-side hash-routed, data-driven indicator/KB pages), retheme only: the light/adaptive-theme CSS was dropped for one fixed dark palette matching <code>dashboard.py</code>'s own <code>_CSS</code> (<code>#0d1117</code>/<code>#161b22</code>/<code>#21262d</code>/<code>#58a6ff</code>, the same GitHub-dark-style tokens already used throughout the ranking table and charts), and the Google-Fonts IBM Plex pairing was dropped for the app's existing plain <code>monospace</code> stack so this page doesn't make an external request the rest of the app doesn't already make. Story 15.1 (this repo's chart-frontend-rewrite epic) ported this same content again, from the aiohttp dashboard.py into a React page — see that story for the port's own checklist.</p></div>`,
    refs: ["troll/ml_signals/docs_page.py", "troll/ml_signals/dashboard.py"],
  },
];
