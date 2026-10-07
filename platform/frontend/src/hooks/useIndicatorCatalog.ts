import { useEffect, useState } from "react";

import { fetchIndicatorCatalog, type IndicatorCatalogEntry } from "../api/client";

export type CatalogState =
  | { status: "loading" }
  | { status: "error" }
  | { status: "ready"; value: Record<string, IndicatorCatalogEntry> };

/**
 * Story 33.8: the picker's catalog for an alert form, fetched once `enabled` (an indicator condition
 * is shown) and kept. Held by the form that saves, not by `ConditionFields`: the save reads it
 * synchronously (`formToCondition`), so a condition is never saved typed by a catalog the save
 * cannot see yet.
 */
export function useIndicatorCatalog(enabled: boolean): CatalogState {
  const [state, setState] = useState<CatalogState>({ status: "loading" });
  const [wanted, setWanted] = useState(enabled);
  if (enabled && !wanted) setWanted(true); // once wanted, kept: a kind switched back reuses it
  useEffect(() => {
    if (!wanted) return;
    let live = true;
    fetchIndicatorCatalog()
      .then((value) => live && setState({ status: "ready", value }))
      .catch(() => live && setState({ status: "error" }));
    return () => {
      live = false;
    };
  }, [wanted]);
  return state;
}

/** The loaded catalog's entries, or undefined while it is loading or failed (`formToCondition`). */
export function catalogEntries(catalog: CatalogState): Record<string, IndicatorCatalogEntry> | undefined {
  return catalog.status === "ready" ? catalog.value : undefined;
}
