// Ported from platform/ml_signals/docs_page.py's svgArchitecture()/box()/line()/poly()/linelabel()/esc()
// -- straight port, no redesign (Story 15.1 Task 4).

function esc(s: string): string {
  return String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

function box(x: number, y: number, w: number, h: number, label: string): string {
  const lines = label.split("\n");
  const ty = y + h / 2 - (lines.length - 1) * 7;
  const text = lines
    .map(
      (l, i) =>
        `<text x="${x + w / 2}" y="${ty + i * 15}" text-anchor="middle" font-size="12" font-family="monospace">${esc(l)}</text>`,
    )
    .join("");
  return `<rect x="${x}" y="${y}" width="${w}" height="${h}" rx="6" fill="none" stroke="currentColor" stroke-width="1.4"/>${text}`;
}

function linelabel(x: number, y: number, label: string, vertical: boolean): string {
  const lines = String(label).split("\n");
  const dy = vertical ? -6 * (lines.length - 1) : 0;
  return lines
    .map(
      (l, i) =>
        `<text x="${x}" y="${y + dy + i * 11}" text-anchor="middle" font-size="9.5" font-family="monospace" fill="currentColor" opacity="0.8">${esc(l)}</text>`,
    )
    .join("");
}

function line(x1: number, y1: number, x2: number, y2: number, label?: string): string {
  let out = `<line x1="${x1}" y1="${y1}" x2="${x2}" y2="${y2}" stroke="currentColor" stroke-width="1.3" marker-end="url(#arrow)"/>`;
  if (label) {
    const horiz = y1 === y2;
    const lx = horiz ? (x1 + x2) / 2 : x1 + 42;
    const ly = horiz ? y1 - 8 : (y1 + y2) / 2;
    out += linelabel(lx, ly, label, !horiz);
  }
  return out;
}

function poly(points: [number, number][], label?: string, labelPos?: [number, number]): string {
  const pointStr = points.map((p) => `${p[0]},${p[1]}`).join(" ");
  let out = `<polyline points="${pointStr}" fill="none" stroke="currentColor" stroke-width="1.2" stroke-dasharray="3 3" marker-end="url(#arrow)"/>`;
  if (label && labelPos) out += linelabel(labelPos[0], labelPos[1], label, true);
  return out;
}

export function svgArchitecture(): string {
  return (
    '<svg viewBox="0 0 700 640" role="img" aria-label="Data flows from dYdX via the collector into the Parquet catalog and a live Redis snapshots:raw feed. ranking_engine subscribes to that feed and publishes rankings:live back to Redis, plus writes metrics.db. The web dashboard and bot_tui both read snapshots:raw and rankings:live directly and never compute either themselves. Separately, live_paper runs its own TradingNode connection straight to dYdX and exchanges bots:status and bots:control with bot_tui over Redis.">' +
    '<defs><marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" fill="currentColor"/></marker></defs>' +
    box(260, 20, 260, 46, "dYdX WS / REST") +
    box(260, 110, 260, 46, "Collector") +
    box(20, 110, 180, 46, "Parquet catalog") +
    box(260, 200, 260, 46, "Redis: snapshots:raw") +
    box(260, 290, 260, 46, "ranking_engine") +
    box(20, 290, 180, 46, "metrics.db (SQLite)") +
    box(260, 380, 260, 46, "Redis: rankings:live") +
    box(260, 470, 260, 60, "dashboard (web) +\nbot_tui (terminal)") +
    box(20, 470, 180, 46, "live_paper\n(TradingNode)") +
    box(20, 560, 300, 46, "Redis: bots:status / bots:control") +
    line(390, 66, 390, 110, "WS / HTTP") +
    line(390, 156, 390, 200, "publishes ~1/s") +
    line(390, 246, 390, 290, "subscribes") +
    line(390, 336, 390, 380, "publishes on change\n+ 5s heartbeat") +
    line(390, 426, 390, 470, "reads") +
    line(260, 133, 200, 133, "writes") +
    line(260, 313, 200, 313, "writes, 60s") +
    poly(
      [
        [520, 223],
        [620, 223],
        [620, 500],
        [520, 500],
      ],
      "subscribes\n(direct)",
      [636, 360],
    ) +
    poly(
      [
        [260, 43],
        [6, 43],
        [6, 493],
        [20, 493],
      ],
      "WS / HTTP",
      [26, 270],
    ) +
    line(110, 516, 110, 560, "status") +
    line(150, 560, 150, 516, "control") +
    poly(
      [
        [320, 583],
        [380, 583],
        [380, 530],
      ],
      "bots:status /\nbots:control",
      [350, 571],
    ) +
    "</svg>"
  );
}
