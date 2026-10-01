/**
 * Story 32.2: whether a coin's volume pane is shown, kept in `localStorage` under
 * `chart-volume:{iid}` next to `chart-timeframe:{iid}`.
 *
 * Known limit: a per-browser convenience, not server config. Upgrade path: Story 32.6 moves
 * this key and the timeframe into the server-side per-coin layout and imports them once;
 * this file is the one place that read/write lives, so 32.6 replaces exactly this.
 */
export function volumeStorageKey(instrumentId: string): string {
  return `chart-volume:${instrumentId}`;
}

/** On by default; only an explicit "off" turns it off. A blocked storage reads as on. */
export function loadVolumeOn(instrumentId: string): boolean {
  try {
    return localStorage.getItem(volumeStorageKey(instrumentId)) !== "off";
  } catch (err) {
    console.error("chart-volume: localStorage unreadable, volume pane stays on", err);
    return true;
  }
}

export function saveVolumeOn(instrumentId: string, on: boolean): void {
  try {
    localStorage.setItem(volumeStorageKey(instrumentId), on ? "on" : "off");
  } catch (err) {
    console.error("chart-volume: localStorage unwritable, the choice will not survive a reload", err);
  }
}
