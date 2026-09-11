---
status: done
trigger: "the bid and ask can sometimes flatline showing incorrect price that is just frozen when i can clearly see on the live homepage that the bid or ask is moving and not flatlining"
created: 2026-06-30
updated: 2026-06-30
---

## Symptoms

- expected: Individual coin chart in dashboard shows live bid/ask prices updating continuously
- actual: Bid or ask (sometimes one, sometimes both) freezes/flatlines on an individual coin chart while other data (price ticker, volume, other coins) still updates; dYdX homepage shows price moving normally
- errors: None — silent failure, no log errors during freeze
- timeline: Always had this behavior — never observed clean consistent bid/ask updates on coin charts
- reproduction: Open any coin's chart in dashboard, wait — intermittently bid or ask stops updating; affects random instruments

## Current Focus

hypothesis: "Flatlines caused by two compounding issues: (1) `_live_books[iid]` is frozen during WS reconnect recovery because the Rust WS client re-subscribes at 2/sec rate limit — instruments near the end of the sorted queue wait up to N/2 seconds for re-subscription; during that window _second_loop emits stale pre-reconnect book state repeatedly to Redis. (2) Even without staleness, Plotly connects across time gaps with a line — when collector fix #1 causes missing timestamps in _second_rolling, Plotly draws a misleading horizontal line across the gap."
test: "Code inspection + test analysis"
expecting: "Fix prevents stale book data from reaching chart; null gap markers ensure Plotly shows honest breaks instead of implied flatlines"
next_action: "Implement staleness check in collector._second_loop + null gap insertion in dashboard._coin_chart_json"

reasoning_checkpoint:
  hypothesis: "Flatlines = _live_books[iid] frozen during WS reconnect recovery causing repeated identical bid/ask in _second_rolling"
  confirming_evidence:
    - "test_stale_feed_produces_identical_bid_ask_across_ticks explicitly documents: 'Root cause of flat lines: when no new OrderBookDeltas arrive, _live_books[iid] stays frozen'"
    - "test_stale_book_emits_identical_snapshots_each_second confirms same book → identical snapshots each second"
    - "CLAUDE.md OBS-01/OBS-02 describe frozen book as a known failure mode with historical precedent"
    - "Rust WS client re-subscribes at 2/sec after reconnect — with N instruments the last instrument waits N/2 seconds; during that window the pre-reconnect stale book is sampled repeatedly"
    - "_on_data has NO staleness tracking — it just applies deltas; _second_loop has NO check for how recently the book was updated"
  falsification_test: "If staleness check is added and flatlines persist after a reconnect, the hypothesis is wrong and cause is elsewhere (e.g., dYdX block time causing identical prices legitimately)"
  fix_rationale: "Track _last_book_update_ns per instrument; skip snapshot emission when book hasn't received OrderBookDeltas in >5s; this prevents stale pre-reconnect prices from being published. Add null insertion in _coin_chart_json for time gaps >2.5s so Plotly shows breaks instead of connecting across the gap"
  blind_spots: "Cannot verify reconnect frequency without production logs; dYdX v4 block-time (~1s) could cause legitimate 1-2s flatlines that are NOT bugs but would still look visually flat"

## Evidence

- timestamp: 2026-06-30
  checked: dashboard.py _coin_chart_json function
  found: Reads from _second_rolling deque (maxlen=300), skips crossed/empty book snapshots, plots bid/ask as Plotly mode=lines traces with NO gap detection. Plotly connects consecutive data points with a line regardless of timestamp gap.
  implication: If consecutive snapshots have the same bid/ask (stale book), a flatline appears. If timestamps are non-consecutive (missing snapshots), Plotly draws a line across the gap, which also looks flat if values are similar.

- timestamp: 2026-06-30
  checked: collector.py _on_data and _second_loop
  found: _on_data updates _live_books[iid] via book.apply_delta() for each OrderBookDeltas. _second_loop samples _live_books every second. NO staleness tracking of when the book was last updated.
  implication: If OrderBookDeltas stop arriving (WS reconnect), the book is frozen at the pre-reconnect state forever. _second_loop emits the same bid/ask every second until re-subscription delivers a fresh snapshot.

- timestamp: 2026-06-30
  checked: python/websocket.rs OrderbookSnapshot and OrderbookUpdate handlers
  found: Both deliver ONE call_python_threadsafe per message (one OrderBookDeltas per call). The CLEAR + ADD deltas for a reconnect snapshot are all in ONE batch. They're applied atomically in _on_data.
  implication: Crossed book from mid-replay is NOT the primary cause (CLEAR+ADD is atomic). The stale book AFTER reconnect (waiting for re-subscription) is the primary cause.

- timestamp: 2026-06-30
  checked: test_collector_snapshot.py test_stale_book_emits_identical_snapshots_each_second
  found: Test documents: "There is no staleness check in the snapshot path; the flatness IS the signal that the feed is down." Explicitly acknowledges no staleness check exists.
  implication: Fix is confirmed missing — need to add staleness tracking to _second_loop.

- timestamp: 2026-06-30
  checked: ladder.rs BookPrice Ord implementation for bid side
  found: Buy side uses other.value.cmp(&self.value) making higher prices sort first in BTreeMap. bids()[0] IS the best bid (highest price). asks()[0] IS the best ask (lowest price). Level ordering is correct.
  implication: Bug is NOT in level ordering. bid_prices[0] and ask_prices[0] correctly reflect best bid/ask.

## Eliminated

- hypothesis: Crossed-book guard filtering too aggressively causing gaps that appear as flatlines
  evidence: CLEAR+ADD snapshot deltas are delivered atomically in a single Python callback call; _second_loop cannot see intermediate partially-built state; guard fires only during genuine book anomalies
  timestamp: 2026-06-30

- hypothesis: bids()/asks() returning levels in wrong order causing deep-book stale level to be used as best bid/ask
  evidence: ladder.rs BookPrice.Ord: Buy side sorts descending (best bid = highest price first), Sell side sorts ascending (best ask = lowest price first). Verified by test_bids_returned_descending_best_first and test_asks_returned_ascending_best_first.
  timestamp: 2026-06-30

- hypothesis: Redis pub/sub message loss causing stale data in _second_rolling
  evidence: Redis publish failures would affect ALL instruments in the batch simultaneously, not random individual instruments
  timestamp: 2026-06-30

## Resolution

root_cause: "_live_books[iid] is frozen during WebSocket reconnect recovery. When the dYdX WS reconnects, the Rust client re-subscribes instruments at 2/sec rate limit. The last instrument in the sorted subscription queue waits up to N/2 seconds for its fresh snapshot. During this window, _second_loop has no staleness check and repeatedly emits the pre-reconnect (stale) book state. On the dashboard, Plotly's mode=lines draws a horizontal line across the resulting flatline/gap, making incorrect frozen prices visible."
fix: "Two-part fix: (1) collector._second_loop tracks _last_book_update_ns per instrument and skips snapshot emission if book hasn't received OrderBookDeltas in >5 seconds — prevents stale data from reaching Redis. (2) dashboard._coin_chart_json detects time gaps >2.5s in consecutive snapshot timestamps and inserts null values — prevents Plotly from drawing a misleading line across the gap."
verification: "46/46 tests passing across test_dashboard_chart.py, test_dashboard_ingest.py, test_collector_snapshot.py — including 4 new gap-detection tests that directly exercise the null-insertion path."
files_changed:
  - troll/dydx_collector/collector.py
  - troll/ml_signals/dashboard.py
  - troll/ml_signals/tests/test_dashboard_chart.py
  - troll/dydx_collector/tests/test_collector_snapshot.py
