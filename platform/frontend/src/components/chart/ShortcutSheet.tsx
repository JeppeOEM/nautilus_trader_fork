import { SHORTCUTS } from "../../lib/shortcuts";
import SettingsDialogShell from "./SettingsDialogShell";

/** Story 33.12: the `?` sheet, every chart shortcut straight from the one table (`lib/shortcuts.ts`). */
export default function ShortcutSheet({ onClose }: { onClose: () => void }) {
  return (
    <SettingsDialogShell title="Keyboard shortcuts" className="shortcut-sheet" onClose={onClose}>
      <div className="indicator-dialog-head">
        <strong>Keyboard shortcuts</strong>
        <button type="button" onClick={onClose}>
          Close
        </button>
      </div>
      <p className="indicator-dialog-tag">None act while you type in a field or a dialog is open.</p>
      <table className="shortcut-sheet-table">
        <tbody>
          {SHORTCUTS.map((row) => (
            <tr key={row.keys}>
              <th scope="row">
                <kbd>{row.keys}</kbd>
              </th>
              <td>{row.does}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </SettingsDialogShell>
  );
}
