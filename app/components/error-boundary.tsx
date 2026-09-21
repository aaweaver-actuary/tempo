import { Component, type ErrorInfo, type ReactNode } from "react";
import { DebugErrorPanel } from "./debug-error-panel";
import { reportDebugError } from "../lib/debug-reporting";

type ErrorBoundaryProps = { children: ReactNode };
type ErrorBoundaryState = { hasError: boolean };

export class TempoErrorBoundary extends Component<ErrorBoundaryProps, ErrorBoundaryState> {
  state: ErrorBoundaryState = { hasError: false };

  static getDerivedStateFromError(): ErrorBoundaryState {
    return { hasError: true };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    reportDebugError(error, {
      kind: "render",
      source: "react.error-boundary",
      operation: info.componentStack ?? undefined,
    });
  }

  render() {
    if (!this.state.hasError) return this.props.children;
    return (
      <main className="tempo-debug-crash-screen">
        <h1>Tempo needs to reload</h1>
        <p>The workspace could not render this screen. Your local data was not changed.</p>
        <button type="button" onClick={() => window.location.reload()}>Reload Tempo</button>
        <DebugErrorPanel boundaryFallback />
      </main>
    );
  }
}
