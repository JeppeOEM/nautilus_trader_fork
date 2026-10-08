import { type FocusEvent, type KeyboardEvent, useEffect, useId, useRef, useState } from "react";

import {
  CURSOR_GROUP,
  DRAWING_GROUPS,
  type ChartTool,
  type ChartToolDef,
  type ToolGroupDef,
  shownTool,
} from "../../lib/chartTools";
import { MAGNET_RADIUS_PX, type MagnetMode } from "../../lib/drawingKit";
import { safeDecimal } from "../../lib/drawings";

interface GroupButtonProps {
  group: ToolGroupDef;
  activeTool: ChartTool;
  lastUsed: Partial<Record<string, ChartTool>>;
  open: boolean;
  isDisabled: (tool: ChartToolDef) => boolean;
  onPick: (tool: ChartTool) => void;
  onOpenChange: (open: boolean) => void;
}

/**
 * One rail group: the button arms the tool it shows; the expander (shown only for a group of more
 * than one tool) opens a menu of the group's tools. Escape in the menu closes it and returns focus
 * to the expander without reaching the page's Esc-disarms handler; a click outside closes it.
 */
function ToolGroupButton({ group, activeTool, lastUsed, open, isDisabled, onPick, onOpenChange }: GroupButtonProps) {
  const shown = shownTool(group, lastUsed);
  const menuId = useId();
  const rootRef = useRef<HTMLDivElement>(null);
  const expanderRef = useRef<HTMLButtonElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);

  // The latest callback, read by the listener below: the rail hands a fresh closure every render,
  // and re-subscribing on each would be churn for nothing.
  const onOpenChangeRef = useRef(onOpenChange);
  useEffect(() => {
    onOpenChangeRef.current = onOpenChange;
  }, [onOpenChange]);

  useEffect(() => {
    if (!open) return;
    // The menu takes focus on its first usable item, the WAI-ARIA menu-button pattern.
    // With every item disabled (no precision yet) the menu itself holds focus, so Escape still reaches it.
    const menu = menuRef.current;
    (menu?.querySelector<HTMLButtonElement>("button:not(:disabled)") ?? menu)?.focus();
    const handlePointerDown = (event: MouseEvent): void => {
      if (!rootRef.current?.contains(event.target as Node)) onOpenChangeRef.current(false);
    };
    document.addEventListener("mousedown", handlePointerDown);
    return () => document.removeEventListener("mousedown", handlePointerDown);
  }, [open]);

  const handleMenuKey = (event: KeyboardEvent<HTMLDivElement>): void => {
    if (event.key === "Escape") {
      // Closes the menu only: the armed tool stays armed.
      event.stopPropagation();
      onOpenChange(false);
      expanderRef.current?.focus();
      return;
    }
    if (event.key !== "ArrowDown" && event.key !== "ArrowUp") return;
    event.preventDefault();
    const items = [...(menuRef.current?.querySelectorAll<HTMLButtonElement>("button:not(:disabled)") ?? [])];
    if (items.length === 0) return;
    const at = items.indexOf(document.activeElement as HTMLButtonElement);
    const down = event.key === "ArrowDown";
    // From the menu itself (no usable item focused), Down goes to the first item and Up to the last.
    const next = at === -1 ? (down ? 0 : items.length - 1) : (at + (down ? 1 : -1) + items.length) % items.length;
    items[next].focus();
  };

  const pressed = activeTool === shown.id;
  return (
    // Focus leaving the group by keyboard (Tab) closes its menu, like a click outside does.
    <div
      className="chart-tool-group"
      ref={rootRef}
      onBlur={(e: FocusEvent<HTMLDivElement>) => {
        if (open && !rootRef.current?.contains(e.relatedTarget as Node | null)) onOpenChange(false);
      }}
    >
      <button
        type="button"
        className={pressed ? "tabbtn active" : "tabbtn"}
        aria-pressed={pressed}
        aria-label={shown.ariaLabel}
        data-tool={shown.id}
        title={shown.title}
        disabled={isDisabled(shown)}
        onClick={() => onPick(shown.id)}
      >
        {shown.label}
      </button>
      {group.tools.length > 1 && (
        <button
          ref={expanderRef}
          type="button"
          className="chart-tool-expander"
          aria-label={`${group.label} tools`}
          aria-haspopup="menu"
          aria-expanded={open}
          aria-controls={open ? menuId : undefined}
          onClick={() => onOpenChange(!open)}
        >
          {"▸"}
        </button>
      )}
      {open && (
        <div
          className="chart-tool-flyout"
          id={menuId}
          role="menu"
          aria-label={group.label}
          ref={menuRef}
          tabIndex={-1}
          onKeyDown={handleMenuKey}
        >
          {group.tools.map((tool) => (
            <button
              key={tool.id}
              type="button"
              role="menuitemradio"
              aria-checked={activeTool === tool.id}
              aria-label={tool.ariaLabel}
              data-tool={tool.id}
              title={tool.title}
              disabled={isDisabled(tool)}
              onClick={() => {
                onOpenChange(false);
                onPick(tool.id);
              }}
            >
              {tool.label}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

/** Story 33.10: the rail's drawing actions after the groups (magnet, undo/redo, hide, delete). */
export interface DrawingActions {
  magnet: MagnetMode;
  /** Cycles the magnet off -> weak -> strong -> off. */
  onMagnet: () => void;
  canUndo: boolean;
  canRedo: boolean;
  onUndo: () => void;
  onRedo: () => void;
  /** Hide all drawings (the layout's `drawings_hidden`). */
  allHidden: boolean;
  onHideAll: () => void;
  /** Drawings hidden one by one from their menu; "Show hidden (n)" shows them all again. */
  hiddenCount: number;
  onShowHidden: () => void;
  /** Delete all drawings (the page confirms first); off while there are none or they cannot be edited. */
  deleteAllDisabled: boolean;
  /** Why Show hidden and Delete all are off while Hide all is on (nothing on screen to act on); null
   * while it is off. */
  hiddenAllReason: string | null;
  onDeleteAll: () => void;
}

const MAGNET_TITLES: Record<MagnetMode, string> = {
  off: "Magnet off: points land where clicked",
  weak: `Weak magnet: a point within ${MAGNET_RADIUS_PX} px of the bar's open, high, low or close snaps to it`,
  strong: "Strong magnet: every point snaps to the nearest open, high, low or close of its bar",
};

/** Story 33.10: the drawing actions, after the groups. */
function DrawingActionButtons({ actions }: { actions: DrawingActions }) {
  return (
    <>
      <hr className="chart-toolbar-divider" />
      <button
        type="button"
        className={actions.magnet === "off" ? "tabbtn" : "tabbtn active"}
        aria-pressed={actions.magnet !== "off"}
        aria-label="Magnet"
        title={`${MAGNET_TITLES[actions.magnet]} (click: off, weak, strong)`}
        onClick={actions.onMagnet}
      >
        {`Mag ${actions.magnet}`}
      </button>
      <button type="button" className="tabbtn" aria-label="Undo" title="Undo (Ctrl+Z)" disabled={!actions.canUndo} onClick={actions.onUndo}>
        Undo
      </button>
      <button type="button" className="tabbtn" aria-label="Redo" title="Redo (Ctrl+Shift+Z)" disabled={!actions.canRedo} onClick={actions.onRedo}>
        Redo
      </button>
      <button
        type="button"
        className={actions.allHidden ? "tabbtn active" : "tabbtn"}
        aria-pressed={actions.allHidden}
        aria-label="Hide all drawings"
        title={actions.allHidden ? "Drawings hidden (the drawing tools are off): click to show them" : "Hide all drawings"}
        onClick={actions.onHideAll}
      >
        Hide all
      </button>
      {actions.hiddenCount > 0 && (
        <button
          type="button"
          className="tabbtn"
          title={actions.hiddenAllReason ?? "Show every drawing hidden from its menu"}
          disabled={actions.hiddenAllReason !== null}
          onClick={actions.onShowHidden}
        >
          {`Show hidden (${safeDecimal(actions.hiddenCount, 0)})`}
        </button>
      )}
      <button
        type="button"
        className="tabbtn"
        aria-label="Delete all drawings"
        title={actions.hiddenAllReason ?? "Delete all drawings of this coin (asks first; undoable)"}
        disabled={actions.deleteAllDisabled || actions.hiddenAllReason !== null}
        onClick={actions.onDeleteAll}
      >
        Del all
      </button>
    </>
  );
}

interface ToolRailProps {
  activeTool: ChartTool;
  /** The tool each group last armed, by group id: the group's button shows it. */
  lastUsed: Partial<Record<string, ChartTool>>;
  isDisabled: (tool: ChartToolDef) => boolean;
  /** Arms a tool (from a group button or a menu item). */
  onPick: (tool: ChartTool) => void;
  crosshairOn: boolean;
  onCrosshairToggle: () => void;
  /** Story 33.10: absent = no drawing actions (a rail of tools alone). */
  actions?: DrawingActions;
}

/** The chart's left rail (Story 18.1, grouped TradingView-style): cursor, crosshair toggle, divider,
 * then the drawing groups, then (Story 33.10) a divider and the drawing actions. At most one group's
 * menu is open at a time. */
export default function ToolRail({ activeTool, lastUsed, isDisabled, onPick, crosshairOn, onCrosshairToggle, actions }: ToolRailProps) {
  const [openGroup, setOpenGroup] = useState<string | null>(null);
  const renderGroup = (group: ToolGroupDef) => (
    <ToolGroupButton
      key={group.id}
      group={group}
      activeTool={activeTool}
      lastUsed={lastUsed}
      open={openGroup === group.id}
      isDisabled={isDisabled}
      onPick={onPick}
      onOpenChange={(open) => setOpenGroup((current) => (open ? group.id : current === group.id ? null : current))}
    />
  );
  return (
    <div className="chart-toolbar" role="toolbar" aria-label="Chart tools">
      {renderGroup(CURSOR_GROUP)}
      <button
        type="button"
        className={crosshairOn ? "tabbtn active" : "tabbtn"}
        aria-pressed={crosshairOn}
        aria-label="Crosshair toggle"
        onClick={onCrosshairToggle}
      >
        Cross
      </button>
      <hr className="chart-toolbar-divider" />
      {DRAWING_GROUPS.map(renderGroup)}
      {actions && <DrawingActionButtons actions={actions} />}
    </div>
  );
}
