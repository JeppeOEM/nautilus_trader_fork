import { Component, type ErrorInfo, type ReactNode } from "react";

import { crashReport } from "../lib/crashReport";

// DATA-07: a render crash must be readable where it happens. Without a boundary React unmounts the
// whole root on an uncaught render error and leaves a black page; this keeps the rest of the app up
// and shows the error, its stack and the component stack in place of the crashed subtree.
// React itself still reports every caught error through console.error, which <ErrorBar> counts.

interface Props {
  children: ReactNode;
  /** Names the crashed area in the heading ("page", "app"). */
  area: string;
}

interface State {
  error: unknown;
  componentStack: string;
  at: string;
}

const NO_CRASH: State = { error: null, componentStack: "", at: "" };

export default class CrashBoundary extends Component<Props, State> {
  state: State = NO_CRASH;

  static getDerivedStateFromError(error: unknown): Partial<State> {
    return { error: error ?? new Error("a null or undefined value was thrown"), at: new Date().toISOString() };
  }

  componentDidCatch(_error: unknown, info: ErrorInfo): void {
    this.setState({ componentStack: info.componentStack ?? "" });
  }

  private reset = (): void => this.setState(NO_CRASH);

  render(): ReactNode {
    const { error, componentStack, at } = this.state;
    if (error === null) return this.props.children;
    const report = crashReport(error, componentStack, at, window.location.href);
    const copy = (): void => {
      navigator.clipboard?.writeText(report).catch((err: unknown) => {
        console.error("crash panel: copy to clipboard failed", err);
      });
    };
    return (
      <section
        role="alert"
        aria-label={`The ${this.props.area} crashed`}
        style={{ padding: "1em", color: "var(--color-text-strong)" }}
      >
        <h2 style={{ color: "var(--color-danger)", margin: "0 0 0.5em" }}>The {this.props.area} crashed</h2>
        <p style={{ display: "flex", gap: "0.6em", flexWrap: "wrap", margin: "0 0 0.8em" }}>
          <button type="button" onClick={copy}>
            Copy error
          </button>
          <button type="button" onClick={this.reset}>
            Try again
          </button>
          <button type="button" onClick={() => window.location.reload()}>
            Reload page
          </button>
        </p>
        <pre
          style={{
            whiteSpace: "pre-wrap",
            overflowWrap: "anywhere",
            maxHeight: "70vh",
            overflow: "auto",
            border: "1px solid var(--color-border)",
            padding: "0.8em",
            margin: 0,
            fontSize: "0.85em",
          }}
        >
          {report}
        </pre>
      </section>
    );
  }
}
