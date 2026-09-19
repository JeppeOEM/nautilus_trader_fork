import { useErrorLog } from "../hooks/useErrorLog";

/** Always-visible strip while any frontend or backend error has been recorded (DATA-07). */
export default function ErrorBar() {
  const { frontend, lastFrontend, backend, lastBackend } = useErrorLog();
  const sites = Object.entries(backend);
  if (frontend === 0 && sites.length === 0) return null;
  return (
    <div
      role="alert"
      style={{ background: "var(--vga-red)", color: "var(--vga-white)", padding: "0.3em 1em", fontSize: "0.85em" }}
    >
      ERR
      {frontend > 0 && <> browser×{frontend}: {lastFrontend}</>}
      {sites.map(([site, n]) => (
        <span key={site}> · {site}×{n}: {lastBackend[site]}</span>
      ))}
    </div>
  );
}
