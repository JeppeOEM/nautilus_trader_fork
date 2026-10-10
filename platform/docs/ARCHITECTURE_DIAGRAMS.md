# Architecture Diagrams

The visual companion to `CLAUDE.md` (binding rules), `docs/DATA_DICTIONARY.md` (every channel,
file and payload) and the DDD spine (`_bmad-output/planning-artifacts/architecture/`, the
authoritative text). Nothing here is a new decision: every node, edge and risk is the existing
system, drawn. Five diagrams: the deployed system, the capture hot path, how a value changes
form end-to-end, the readers plus the nightly correction loop, and the full risk/guard map.

## 1. The deployed system (context)

One VPS (`nifelheim`, 2 vCPU / 3.7 GB / 0 swap), Docker Compose, every port localhost-only
(SEC-01; remote access via SSH tunnel). Three collector processes (one per venue, the same
image, different `command:`), the shared catalog, and the readers. The verification stack
lives on the dev box only and shares no code with capture (DATA-02).

```mermaid
flowchart TB
    subgraph VENUES["Public venues"]
        BYBIT["Bybit<br/>WS linear+spot, allLiquidation 2nd socket, REST"]
        HL["Hyperliquid<br/>WS full snapshots, REST"]
        DYDX["dYdX v4<br/>profile-gated, off since the cutover"]
    end

    subgraph COLLECT["Collectors - one asyncio process per venue, CaptureService, no DataEngine"]
        BYC["bybit_collector<br/>capture.venues.bybit"]
        HLC["hyperliquid_collector<br/>capture.venues.hyperliquid"]
        DYC["collector - dydx profile<br/>capture.venues.dydx"]
    end

    REDIS[("Redis localhost<br/>pub/sub fan-out + latest keys")]

    subgraph STORE["Durable state - ./data mounts, one shared root"]
        CAT[("Parquet catalog<br/>Nautilus layout, zstd")]
        CAND[("candles_*.db SQLite<br/>1m/5m derived bars")]
        COV[("coverage/*.jsonl<br/>every dropped or skipped window")]
        ERR[("errors/*.jsonl<br/>durable per-service ledger")]
        PREF[("preferences + alerts.toml<br/>UI state, atomic writes")]
    end

    subgraph READ["Readers"]
        API["data_api FastAPI + React SPA<br/>views/ read models only"]
        RANK["ranking_engine<br/>streaming indicators, metrics.db"]
        ARCH["archive scheduler<br/>nightly saga per venue"]
        BOTS["bots live-paper<br/>TradingNode - the one sanctioned runtime"]
    end

    TUI["bot_tui - control surface, no market values"]
    OP["Operator<br/>browser via SSH tunnel, Dozzle, Telegram"]

    BYBIT --> BYC
    HL --> HLC
    DYDX --> DYC
    COLLECT -->|"snapshots:raw derivs:raw liquidations:raw capture:hotpath"| REDIS
    COLLECT -->|"write_data per type"| CAT
    COLLECT -->|"live fold 1m/5m"| CAND
    COLLECT -->|"append + fsync per flush"| COV
    COLLECT -->|"record per site"| ERR
    REDIS -->|"collector:status collector:control"| TUI
    CAT --> API
    CAND --> API
    ERR --> API
    PREF <--> API
    REDIS -->|"rankings:live"| RANK
    RANK -->|"snapshots:raw subscribe"| REDIS
    RANK --> METRICS[("metrics.db + rank history")]
    ARCH -->|"rebuild consolidate verify prune"| CAT
    ARCH --> CAND
    REDIS -->|"liquidations:raw"| BOTS
    API --> OP
    TUI --> OP
    RANK -->|"alerts + canary pushes"| OP
    COLLECT -->|"memory canary DW-266"| OP
```

Host risks carried by this whole diagram: the box is oversubscribed on a bad day
(2026-09-12: load 10.85 on 2 cores, the *global* OOM killer cycling the uncapped
`ranking_engine`); collectors have hard caps, several readers have none; there is no swap, so
pressure kills instead of thrashing.

## 2. The capture hot path (one collector)

The write gate is `CaptureService`'s alone (never subclassed; venue variance is policy values
and extra loops). One second of truth flows venue socket to Parquet in ~60 s flushes.

```mermaid
flowchart TB
    subgraph WIRE["Venue wire - decoded by the Rust/PyO3 clients, wrapped by client.py"]
        TOPICS["Bybit orderbook.50 publicTrade tickers LINEAR<br/>Hyperliquid l2Book trades<br/>dYdX channels"]
        LIQ["Bybit allLiquidation<br/>2nd generic WS - Rust handler drops the topic"]
        RESTP["REST polls and recovery<br/>OI poll dYdX/Bybit, trade backfill, book cross-check"]
    end

    ONDATA["_on_data - O(1) enqueue only<br/>runs on the loop from the Rust callback"]
    QUEUE[("_ingest_queue - asyncio queue<br/>depth reported per flush")]
    INGEST["_ingest_loop - yields every 64 messages<br/>a failure ledgered collector.process, never raised"]

    subgraph PROCESS["_process_data branches"]
        INTAKE["TradeIntake<br/>age filter, id dedup tagged by feed,<br/>window seeded from the archive"]
        BOOK["LiveBook<br/>a delta applies at most once after a baseline,<br/>sequence canary, uncross policy"]
        DERIVS["tickers mark/index/funding, OI rows"]
        LIQP["Liquidation.from_wire_text<br/>bankruptcy price, exact integer units"]
    end

    SECOND["_second_loop per second<br/>SecondSampler.sample - THE write gate"]
    GATE{"gate checks<br/>stale book? crossed? empty top?<br/>OHLC outside book? unencodable?"}
    ROW["DydxSecondSnapshot row<br/>integer units at definition precision,<br/>book prices gap-encoded"]
    BUFFER["flush buffer - swapped each flush"]

    FLUSH["_flush_loop - 60 s at :02 past the minute"]

    subgraph OUT["Four sinks, one flush"]
        PARQ["ParquetDataCatalog.write_data<br/>trade_tick, custom_dydx_second_snapshot,<br/>mark/index/funding, custom_open_interest,<br/>custom_liquidation, definitions"]
        PUB["Redis publish<br/>snapshots:raw, derivs:raw,<br/>liquidations:raw, capture:hotpath"]
        SINK["SecondSink.apply - candles SQLite<br/>live fold commits per instrument"]
        COVR["coverage append + fsync<br/>every skipped window named"]
    end

    HOTPATH["hotpath report per flush<br/>ns/msg, queue depth, lag, mem MiB"]
    CANARY["memory canary DW-266<br/>cgroup read, fires at 90 pct once,<br/>ledger + Telegram push"]

    WATCH["_watchdog_loop - OBS-01<br/>all books stale 30 s+ pushes the operator"]
    PUSH["notify - the one transport<br/>Telegram, ntfy, webhook"]
    XCHECK["_crosscheck_loop<br/>REST book vs live, both directions"]
    BACKF["_trade_backfill_loop<br/>reconnect gaps recovered over REST,<br/>beyond venue depth: unrecoverable"]
    CTRL["collection_control loops<br/>plan reload, status, control - the plan<br/>can grow at runtime, the canary watches"]

    TOPICS --> ONDATA
    LIQ --> ONDATA
    ONDATA --> QUEUE --> INGEST --> PROCESS
    BOOK --> SECOND
    INTAKE --> SECOND
    SECOND --> GATE
    GATE -->|"accepted"| ROW --> BUFFER
    GATE -->|"rejected: skipped second + coverage line"| COVR
    DERIVS --> BUFFER
    LIQP --> BUFFER
    BUFFER --> FLUSH
    FLUSH --> PARQ
    FLUSH --> PUB
    FLUSH --> SINK
    FLUSH --> COVR
    FLUSH --> HOTPATH
    FLUSH --> CANARY
    RESTP -->|"poll rows straight into the buffer"| BUFFER
    BACKF -->|"archived only, never folded live"| PARQ
    RESTP --> XCHECK
    WATCH --> PUSH
    CANARY --> PUSH
    CTRL --> COLLECT2["apply - subscribes the plan<br/>retry loop for wire failures"]
```

The one deliberate unbounded structure is `_ingest_queue`: it is safe exactly while the
ingest loop keeps up, so its depth is sampled into every hotpath report, and a sustained
backlog shows as tick-late lines long before it becomes memory.

## 3. How a value changes form (the integrity chain)

Wire to browser, every transformation named. The invariant: **integers all the way down**
(DATA-04, Story 30.2); floats exist only inside a reader's own computation, and the browser
formats integers in one helper.

```mermaid
flowchart LR
    WIRET["wire JSON text<br/>Bybit p and S and u, sizes as strings"]
    DEC["Decimal - exact, never float<br/>dYdX mark/index re-stamped via<br/>scaleb + from_raw at FIXED_PRECISION"]
    PQ["Price / Quantity<br/>at the instrument definition precision - constant"]
    UNITS["integer units at 10^-precision<br/>exact division of Price.raw,<br/>refuses anything finer, never rounds"]
    SNAPROW["snapshot row<br/>best price + gaps, units only"]
    PARQF["Parquet integer layout<br/>zstd 16, delta-packed ts, dictionary leaves"]
    READER["readers decode units to floats<br/>for computation only - never stored back"]
    FOLD["candles fold - pv sums, 1m/5m<br/>applies each second once, watermark"]
    APIJ["API serves integer units<br/>plus both precisions on every row"]
    UI["frontend units.ts formats<br/>the single formatting helper"]

    WIRET --> DEC --> PQ --> UNITS --> SNAPROW --> PARQF --> READER --> FOLD --> APIJ --> UI

    subgraph TRADES["a trade's two lives"]
        RAWT["trade_tick archived raw<br/>ts_event = venue time, ts_init = arrival"]
        FOLDED["folded into its second's<br/>8 trade columns, live provisional"]
        REBUILT["nightly rebuild_seconds<br/>re-derives closed days from raw,<br/>the same exact fold, idempotent"]
    end
    RAWT --> FOLDED
    RAWT --> REBUILT --> FOLDED2["the archive is the truth,<br/>verified_days is the verdict"]

    subgraph LIQS["a liquidation"]
        WIREL["allLiquidation text<br/>S = the liquidated side"]
        LROW["Liquidation row<br/>bankruptcy price in units,<br/>dedup key with k-th suffix"]
        LREAD["read model<br/>price_kind constant bankruptcy,<br/>notional = size x bankruptcy - an approximation"]
    end
    WIREL --> LROW --> LREAD

    subgraph DERIV["derivatives"]
        MKIDX["mark/index/funding updates<br/>venue precision"]
        OIROW["OpenInterest units<br/>REST poll dYdX/Bybit, WS Hyperliquid"]
        DERIVPUB["derivs:raw rows + chart panes<br/>basis = mark-index, OI, funding held"]
    end
    MKIDX --> DERIVPUB
    OIROW --> DERIVPUB
```

The risks this chain exists to kill: `Price(decimal, precision)` silently returning wrong
values (NAUT-01 - `from_raw` everywhere a precision changes); float noise in stored prices
(20.7% of pre-30.2 files carried it; `archive.tools.migrate_snapshot_ints` rewrote them); a
derived value drifting from its stored inputs (the rebuild re-derives from raw, never patches
at read time); and schema drift inside one catalog directory (`register_arrow` binds one
schema per class, the catalog refuses disagreeing files - why the Liquidation columns never
landed, DW-294).

## 4. Readers and the nightly correction loop

Live is provisional; the nightly saga is what makes a day true. Everything a human sees comes
from a read model, never a second implementation of a formula (SSOT-01/02).

```mermaid
flowchart TB
    subgraph SOURCES["Sources of truth"]
        CAT[("Parquet catalog")]
        CAND[("candles SQLite")]
        REDISL[("Redis live channels")]
        METR[("metrics.db")]
    end

    subgraph APIR["data_api - routes only format and transport"]
        VIEWS["views/ read models<br/>chart_series, coin_detail, derivatives,<br/>liquidations_plus_recent, rankings_bus"]
        ROUTES["REST + /ws/live<br/>integer units + precisions"]
    end
    SPA["React SPA<br/>chart + overlays + derivs panes + liq tape,<br/>history, screener, alerts page, ErrorBar"]

    RANK["ranking_engine - the one owner of<br/>rolling metrics, per-instrument trackers<br/>kernel.indicators streaming"]
    SCREEN["screener columns<br/>funding, OI, basis, liq sums"]
    RANKLIVE["rankings:live"]
    RESEARCH["research - BacktestNode +<br/>BacktestDataConfig streaming, MEM-01"]
    BOTS2["bots - liquidations:raw to the<br/>cascade strategy via the Redis bridge"]
    ALERTS["alerting engine - conditions beyond price<br/>delivered by observability.notify"]

    subgraph NIGHT["archive scheduler - nightly saga per venue, per missed day"]
        REBUILD["rebuild_seconds<br/>closed-day trade columns from raw"]
        CREBUILD["candles.rebuild<br/>refold the store, idempotent"]
        CMP["compare_klines<br/>prove the day against the venue"]
        RECON["reconcile - not_rebuilt is never judged"]
        VERIF["verification tools - conservation, trades,<br/>book, derivs, catalog, candles<br/>vs the reference recorder's own frames"]
        CONS["consolidate small files, prune,<br/>in-place rewrites staged + verified"]
        VDAYS["verified_days - the verdict"]
    end

    CAT --> VIEWS
    CAND --> VIEWS
    REDISL --> VIEWS
    VIEWS --> ROUTES --> SPA
    REDISL --> RANK
    RANK --> RANKLIVE --> SCREEN
    RANK --> METR
    CAT --> RESEARCH
    REDISL --> BOTS2
    VIEWS --> ALERTS --> SPA
    CAT --> REBUILD --> CREBUILD --> CMP --> RECON --> VERIF --> VDAYS
    VERIF --> CONS --> CAT
    COV2[("coverage record")] --> VERIF
```

The loop's meaning: a live second is provisional until its day is rebuilt from raw trades,
reconciled against the venue's own klines, and verified against the reference recorder. A
live-fold error is *filled by the rebuild*; nothing is ever patched at read time (DATA-07).

## 5. The risk and guard map

Every risk class with its guard (what catches it), its canary (what warns) and its repair
(what fixes the data). A guard without a repair is a loud canary by design; a silent guard is
a bug in itself (DATA-07).

```mermaid
flowchart TB
    classDef risk fill:#fdd,stroke:#c00
    classDef guard fill:#ffd,stroke:#b80
    classDef repair fill:#dfd,stroke:#080

    subgraph INGESTR["ingest risks"]
        R1["message loss on the socket" ]:::risk
        R2["subscribe-time replayed history" ]:::risk
        R3["duplicates across feeds" ]:::risk
        R4["ingest loop starvation" ]:::risk
        R5["a value finer than its precision" ]:::risk
        G1["sequence canary - Bybit u exact +1<br/>gap = real loss, book dropped"]:::guard
        G2["age filter on arrival + id dedup,<br/>window seeded from the archive"]:::guard
        G3["dedup map tags the delivering feed:<br/>replay vs duplicate_feed"]:::guard
        G4["yield every 64 + queue depth in every<br/>hotpath report + tick-late canary"]:::guard
        G5["SnapshotEncodingError - refused,<br/>ledgered, never rounded"]:::guard
        P1["REST trade backfill<br/>beyond depth: unrecoverable,<br/>counted in coverage"]:::repair
        R1 --> G1 --> P1
        R2 --> G2
        R3 --> G3
        R4 --> G4
        R5 --> G5
    end

    subgraph BOOKR["book risks"]
        R6["crossed book<br/>architectural on dYdX" ]:::risk
        R7["stale or frozen feed" ]:::risk
        R8["book desynced from the venue" ]:::risk
        R9["one-sided book after an uncross" ]:::risk
        G6["level-tagged uncross ladder<br/>delete only the older level"]:::guard
        G7["stale_book_seconds gate - no row,<br/>a rendered gap, never a flatline"]:::guard
        G8["REST cross-check, both directions,<br/>per-direction tolerance"]:::guard
        G9["empty top = skipped second + ledger,<br/>never a fake row"]:::guard
        P6["forced resync - the fallback,<br/>ledgered, never the first answer"]:::repair
        P8["resync from a fresh snapshot"]:::repair
        R6 --> G6 --> P6
        R7 --> G7
        R8 --> G8 --> P8
        R9 --> G9
    end

    subgraph GATER["second and archive risks"]
        R10["OHLC outside the book" ]:::risk
        R11["missing seconds" ]:::risk
        R12["flush or store write failure" ]:::risk
        R13["crash window - queued messages" ]:::risk
        R14["corrupt or torn files" ]:::risk
        G10["ohlc_outside_book canary -<br/>live, and the repair detector"]:::guard
        G11["coverage record: every second accounted,<br/>conservation nightly reconciles 86400"]:::guard
        G12["flush_write + candle_store ledgered,<br/>store partial state tracked per instrument"]:::guard
        G13["shutdown drain budget + DW-290<br/>startup-failure unwind"]:::guard
        G14["quarantine + CatalogFiles staged,<br/>verified temp-then-rename rewrites"]:::guard
        P12["candles.rebuild - idempotent<br/>recompute from raw 1s"]:::repair
        P13["restart gap markers + REST backfill<br/>+ the nightly rebuild placing late trades"]:::repair
        P14["repair_catalog - refuses rows the<br/>trade archive covers"]:::repair
        R10 --> G10 --> P14
        R11 --> G11
        R12 --> G12 --> P12
        R13 --> G13 --> P13
        R14 --> G14 --> P14
    end

    subgraph HOSTR["host and process risks"]
        R15["collector OOM - a plan outgrew mem_limit" ]:::risk
        R16["swap thrash instead of a visible kill" ]:::risk
        R17["CPU oversubscription, the box stalls" ]:::risk
        R18["global OOM killer picks uncapped services" ]:::risk
        G15["mem_limit = measured peak x 1.5,<br/>restart: always + coverage explains the gap"]:::guard
        C15["DW-266 memory canary - 90 pct once per crossing,<br/>ledger + Telegram before the kill"]:::guard
        G16["memswap_limit = mem_limit - die visibly"]:::guard
        G17["cpu_shares 1024 collectors vs 256 batch,<br/>load-average acceptance check"]:::guard
        G18["known gap - several readers uncapped<br/>resize the VM or cap them"]:::guard
        P15["backfill repairs the window,<br/>re-measure and raise before growing"]:::repair
        R15 --> G15 --> P15
        R15 --> C15
        R16 --> G16
        R17 --> G17
        R18 --> G18
    end

    subgraph READR["read and verification risks"]
        R19["stale data shown as live" ]:::risk
        R20["derived store drifts from raw" ]:::risk
        R21["two implementations of one formula" ]:::risk
        R22["silent data rot - nobody notices" ]:::risk
        G19["gap rendering rule - a hole renders<br/>as a hole, whitespace rows capped"]:::guard
        G20["nightly rebuild re-derives,<br/>verified_days is the verdict,<br/>backtests replay on ts_init"]:::guard
        G21["SSOT - one views function per value,<br/>one ranking owner per rolling metric,<br/>boundary tests fail the second copy"]:::guard
        G22["the oracle: verification shares no code<br/>with capture, the reference recorder<br/>records raw frames independently"]:::guard
        R19 --> G19
        R20 --> G20
        R21 --> G21
        R22 --> G22
    end
```

## How to read the risk map

- A **risk** node is a failure class with a documented incident or a reasoned mechanism, not
  a hypothetical.
- A **guard** either prevents the failure (a gate, a check, a cap) or makes it loud (a canary:
  ledger + counter + ErrorBar + push). A guard that *filters* a failure away silently is
  itself a violation (DATA-07).
- A **repair** restores the data's truth from the raw archive (backfill, rebuild,
  `repair_catalog`). Repairs that genuinely cannot exist (Bybit has no liquidation history)
  are named `unrecoverable` in the coverage record instead - never silence.
- The audit register of every known wrong-data danger, with status, is
  `docs/DATA_INTEGRITY_AUDIT.md`; the deferred-work ledger (`_bmad-output/implementation-
  artifacts/deferred-work.md`) tracks every known gap in these guards.
