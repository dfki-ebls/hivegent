/**
 * Application-level error boundary with recovery options.
 *
 * Catches render errors and offers users a way to recover by either
 * reloading the page or clearing all local storage and reloading.
 */

import { Component, type ErrorInfo, type ReactNode } from "react";

import { clearAllStorage } from "../stores/storage";

interface Props {
  children: ReactNode;
}

interface State {
  hasError: boolean;
  error: Error | null;
}

export class AppErrorBoundary extends Component<Props, State> {
  constructor(props: Props) {
    super(props);
    this.state = { hasError: false, error: null };
  }

  static getDerivedStateFromError(error: Error): State {
    return { hasError: true, error };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error(
      "AppErrorBoundary caught an error:",
      error,
      info.componentStack,
    );
  }

  render() {
    if (!this.state.hasError) {
      return this.props.children;
    }

    return (
      <div className="flex h-screen items-center justify-center bg-background p-8">
        <div className="mx-auto max-w-md space-y-6 text-center">
          <h1 className="text-2xl font-bold text-foreground">
            Something went wrong
          </h1>
          <p className="text-muted-foreground">
            The application encountered an unexpected error. You can try
            reloading the page, or clear all local data if the problem persists.
          </p>
          {this.state.error && (
            <pre className="rounded-md bg-muted p-4 text-left text-xs text-muted-foreground overflow-auto max-h-32">
              {this.state.error.message}
            </pre>
          )}
          <div className="flex flex-col gap-3 sm:flex-row sm:justify-center">
            <button
              onClick={() => window.location.reload()}
              className="inline-flex items-center justify-center rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground hover:bg-primary/90"
            >
              Reload
            </button>
            <button
              onClick={clearAllStorage}
              className="inline-flex items-center justify-center rounded-md border border-input bg-background px-4 py-2 text-sm font-medium text-foreground hover:bg-accent hover:text-accent-foreground"
            >
              Clear local data &amp; reload
            </button>
          </div>
        </div>
      </div>
    );
  }
}
