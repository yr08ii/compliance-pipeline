import { Component, type ErrorInfo, type ReactNode } from "react";

/** Keeps a render error inside the screen that caused it.
 *
 *  Without this, one bad screen unmounts the whole tree and the operator sees
 *  a white page with no indication of what failed — the worst possible outcome
 *  for a monitoring tool, since it looks identical to the server being down.
 *  A contained failure keeps the navigation usable and names the problem. */
export class ErrorBoundary extends Component<
  { children: ReactNode; resetKey?: string },
  { error: Error | null }
> {
  state: { error: Error | null } = { error: null };

  static getDerivedStateFromError(error: Error) {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    // Local-only tool: the console is where an operator or engineer will look.
    console.error("Screen failed to render:", error, info.componentStack);
  }

  componentDidUpdate(prev: { children: ReactNode; resetKey?: string }) {
    // Navigating away from a broken screen should clear the error, or the
    // boundary would hold the failure across the whole session.
    if (this.state.error && prev.resetKey !== this.props.resetKey) {
      this.setState({ error: null });
    }
  }

  render() {
    if (!this.state.error) return this.props.children;

    return (
      <div className="card border-[#f0cfcd] bg-[var(--danger-bg)] p-5">
        <h2 className="text-[1.05rem] text-[var(--danger)]">This screen failed to load</h2>
        <p className="mt-1.5 text-[0.92rem] leading-6 text-[var(--text)]">
          The rest of the app still works — use the navigation to continue. The error
          below is also in the browser console.
        </p>
        <pre className="mt-3 overflow-x-auto rounded-[var(--radius)] bg-white px-4 py-3 text-[0.8rem] text-[var(--danger)]">
          {this.state.error.message}
        </pre>
        <button
          type="button"
          onClick={() => this.setState({ error: null })}
          className="focus-ring mt-3 rounded-[var(--radius)] bg-[var(--navy-800)] px-4 py-2 text-[0.88rem] font-medium text-white transition hover:bg-[var(--navy-700)]"
        >
          Try again
        </button>
      </div>
    );
  }
}
