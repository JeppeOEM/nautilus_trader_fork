import { type ReactNode, useEffect, useRef } from "react";

interface Props {
  /** The dialog's accessible name. */
  title: string;
  onClose: () => void;
  children: ReactNode;
}

/**
 * The modal every chart settings dialog sits in (Story 32.3's legend gear, Story 32.5's Fibonacci
 * and position settings): a native `<dialog>` opened with `showModal`, so Esc-to-close, the
 * backdrop and focus trapping come with the platform. Esc, or a backdrop click that also began on
 * the backdrop, calls `onClose`; the content is mounted only while the dialog is, so its drafts
 * always start from the saved state. The padding sits on the body, not the dialog: a click on the
 * dialog's own padding would read as a backdrop click and close it with the drafts.
 */
export default function SettingsDialogShell({ title, onClose, children }: Props) {
  const ref = useRef<HTMLDialogElement>(null);
  const pressedOnBackdrop = useRef(false);

  useEffect(() => {
    const dialog = ref.current;
    if (!dialog || dialog.open) return;
    // showModal gives Esc-to-close, a backdrop and focus trapping natively; jsdom lacks it.
    if (typeof dialog.showModal === "function") dialog.showModal();
    else dialog.setAttribute("open", "");
  }, []);

  return (
    <dialog
      ref={ref}
      className="indicator-dialog indicator-settings"
      aria-label={title}
      onClose={onClose}
      onMouseDown={(e) => {
        pressedOnBackdrop.current = e.target === e.currentTarget;
      }}
      onClick={(e) => {
        // A click on the backdrop, not on the content -- and only when the press began there too:
        // a text-selection drag that ends on the backdrop must not close the dialog.
        if (e.target === e.currentTarget && pressedOnBackdrop.current) onClose();
        pressedOnBackdrop.current = false;
      }}
    >
      <div className="indicator-settings-body">{children}</div>
    </dialog>
  );
}
