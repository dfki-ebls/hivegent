/**
 * Application-level error boundary with recovery options.
 *
 * Catches render errors and offers users a way to recover by either
 * reloading the page or clearing all local data.
 */

import { TriangleAlertIcon } from "lucide-react";
import { Component, type ErrorInfo, type ReactNode } from "react";
import { useTranslation } from "react-i18next";

import { EmptyState } from "@/components/EmptyState";
import { Button } from "@/components/ui/button";
import { CodeBlock, CodeBlockContent } from "@/components/ui/code-block";
import { errorMessage } from "@/lib/utils";
import { clearAllStorage } from "@/stores/storage";

interface Props {
  children: ReactNode;
}

type State = { failed: false } | { failed: true; error: unknown };

export class AppErrorBoundary extends Component<Props, State> {
  state: State = { failed: false };

  static getDerivedStateFromError(error: unknown): State {
    return { failed: true, error };
  }

  componentDidCatch(error: unknown, info: ErrorInfo) {
    console.error("AppErrorBoundary caught an error:", error, info.componentStack);
  }

  render() {
    if (!this.state.failed) {
      return this.props.children;
    }

    return <ErrorFallback message={errorMessage(this.state.error)} />;
  }
}

/** A function component so the recovery screen follows the interface language. */
function ErrorFallback({ message }: { message: string }) {
  const { t } = useTranslation();

  return (
    <EmptyState
      icon={<TriangleAlertIcon />}
      title={t(($) => $.app.errorBoundary.title)}
      description={t(($) => $.app.errorBoundary.description)}
      destructive
      className="h-dvh"
    >
      <CodeBlock size="sm" variant="destructive">
        <CodeBlockContent wrap className="max-h-32">
          {message}
        </CodeBlockContent>
      </CodeBlock>
      <div className="flex flex-col gap-2 sm:flex-row">
        <Button onClick={() => window.location.reload()}>
          {t(($) => $.app.errorBoundary.reload)}
        </Button>
        <Button variant="outline" onClick={clearAllStorage}>
          {t(($) => $.app.errorBoundary.clearData)}
        </Button>
      </div>
    </EmptyState>
  );
}
