import { BookOpen } from "lucide-react";

import { cn, DOCS_URL } from "@/lib/utils";

/** Handbook link opening in a new tab, hidden when no handbook is configured.
 * Styled by callers via `className`. */
export function DocsLink({ className }: { className?: string }) {
  if (!DOCS_URL) {
    return null;
  }

  return (
    <a
      href={DOCS_URL}
      target="_blank"
      rel="noreferrer"
      className={cn("flex items-center gap-2", className)}
    >
      <BookOpen className="h-4 w-4" />
      Documentation
    </a>
  );
}
