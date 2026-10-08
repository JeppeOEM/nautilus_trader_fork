/** The crash as plain text: what the panel shows and what Copy puts on the clipboard. */
export function crashReport(error: unknown, componentStack: string, at: string, href: string): string {
  const headline = error instanceof Error ? `${error.name}: ${error.message}` : `Thrown value: ${String(error)}`;
  const stack = error instanceof Error && error.stack ? error.stack : "(no stack)";
  return [
    headline,
    "",
    `URL: ${href}`,
    `Time: ${at}`,
    "",
    "Stack:",
    stack,
    "",
    "Component stack:",
    componentStack.trim() || "(none)",
  ].join("\n");
}
