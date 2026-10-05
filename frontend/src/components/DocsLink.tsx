import { BookOpen } from "lucide-react";
import type { ComponentProps } from "react";
import { useTranslation } from "react-i18next";

import { LANGUAGE } from "@/i18n";
import { cn, DOCS_URL } from "@/lib/utils";

/** Handbook link opening in a new tab, hidden when no handbook is configured.
 * Points at the handbook in the interface language, which is served under its
 * own path segment (`/en/`, `/de/`).
 * Styled by callers via `className`. Every other anchor prop is forwarded too,
 * so slotting this into a `DropdownMenuItem` still receives the ref and pointer
 * handlers Radix needs to highlight the entry. */
export function DocsLink({ className, ...props }: ComponentProps<"a">) {
  const { t } = useTranslation();

  if (!DOCS_URL) {
    return null;
  }

  return (
    <a
      href={`${DOCS_URL.replace(/\/?$/, "/")}${LANGUAGE}/`}
      target="_blank"
      rel="noreferrer"
      {...props}
      className={cn("flex items-center gap-2", className)}
    >
      <BookOpen className="h-4 w-4" />
      {t(($) => $.app.userMenu.documentation)}
    </a>
  );
}
