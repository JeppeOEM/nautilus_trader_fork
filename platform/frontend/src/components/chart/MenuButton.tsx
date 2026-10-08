import { type ReactNode, useEffect, useId, useRef, useState } from "react";

// Chart UX rework (2026-10-08): the top bar's dropdowns (chart type, Layout, Settings). A button that
// opens a panel under itself; Esc, a press outside, or a pick that asks for it closes the panel.

interface Props {
  /** The button's text (its accessible name, unless `ariaLabel`). */
  label: ReactNode;
  ariaLabel?: string;
  title?: string;
  disabled?: boolean;
  /** The menu's accessible name; `role="menu"` when the panel holds menu items, else a group. */
  menuLabel: string;
  role?: "menu" | "group";
  /** Right-aligns the panel under the button (a button at the bar's right end). */
  alignRight?: boolean;
  children: (close: () => void) => ReactNode;
}

export default function MenuButton({ label, ariaLabel, title, disabled = false, menuLabel, role = "menu", alignRight = false, children }: Props) {
  const [requested, setOpen] = useState(false);
  // A button disabled while its panel is open (Lines mode for a candle-only menu) shows it closed.
  const open = requested && !disabled;
  const rootRef = useRef<HTMLSpanElement | null>(null);
  const panelId = useId();

  useEffect(() => {
    if (!open) return;
    const onKey = (event: KeyboardEvent): void => {
      if (event.key !== "Escape") return;
      // The menu's Esc: nothing else on the page (focus view, an armed tool) acts on it.
      event.stopPropagation();
      setOpen(false);
    };
    const onPress = (event: MouseEvent): void => {
      if (rootRef.current && !rootRef.current.contains(event.target as Node)) setOpen(false);
    };
    window.addEventListener("keydown", onKey, true);
    document.addEventListener("mousedown", onPress);
    return () => {
      window.removeEventListener("keydown", onKey, true);
      document.removeEventListener("mousedown", onPress);
    };
  }, [open]);

  return (
    <span className="menu-button" ref={rootRef}>
      <button
        type="button"
        aria-haspopup={role === "menu" ? "menu" : "true"}
        aria-expanded={open}
        aria-controls={open ? panelId : undefined}
        aria-label={ariaLabel}
        title={title}
        disabled={disabled}
        className={open ? "menu-open" : undefined}
        onClick={() => setOpen((was) => !was)}
      >
        {label} <span aria-hidden="true">▾</span>
      </button>
      {open && (
        <div id={panelId} role={role} aria-label={menuLabel} className={alignRight ? "menu-panel menu-panel-right" : "menu-panel"}>
          {children(() => setOpen(false))}
        </div>
      )}
    </span>
  );
}
