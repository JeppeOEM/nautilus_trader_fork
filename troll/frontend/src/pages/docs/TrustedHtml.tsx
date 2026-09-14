import type { MouseEvent } from "react";
import { useNavigate } from "react-router";

// Renders our own authored HTML fragments (docs_page.py's ported content -- never user
// input) and intercepts clicks on [data-nav] anchors, mirroring docs_page.py's original
// document-level "click" listener (Story 15.1 Task 4).
export function resolveNavKey(key: string): string {
  if (key === "home-ind") return "/docs";
  if (key === "home-kb") return "/docs/kb";
  if (key.startsWith("i:")) return `/docs/i/${key.slice(2)}`;
  if (key.startsWith("kb:")) return `/docs/kb/${key.slice(3)}`;
  return "/docs";
}

export function useDataNavClick(): (e: MouseEvent<HTMLElement>) => void {
  const navigate = useNavigate();
  return (e: MouseEvent<HTMLElement>) => {
    const el = (e.target as HTMLElement).closest("[data-nav]");
    if (!el) return;
    e.preventDefault();
    navigate(resolveNavKey(el.getAttribute("data-nav") ?? ""));
    window.scrollTo({ top: 0 });
  };
}

export function TrustedHtml({ html, className }: { html: string; className?: string }) {
  const onClick = useDataNavClick();
  // eslint-disable-next-line react/no-danger -- content is our own static data, not user input
  return <div className={className} onClick={onClick} dangerouslySetInnerHTML={{ __html: html }} />;
}
