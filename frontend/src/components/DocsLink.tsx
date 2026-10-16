import { BookOpenIcon } from "lucide-react";
import type { ComponentProps } from "react";
import { useTranslation } from "react-i18next";

import { LANGUAGE } from "@/i18n";
import { DOCS_URL } from "@/lib/utils";

/** The handbook in the interface language, served under its own path segment (`/en/`, `/de/`). */
const DOCS_HREF = DOCS_URL && `${DOCS_URL.replace(/\/?$/, "/")}${LANGUAGE}/`;

/**
 * Handbook link opening in a new tab, hidden when no handbook is configured.
 * Meant as the `render` element of a `DropdownMenuItem` or `Button`, which lay
 * out and size its icon, so every anchor prop is forwarded.
 */
export function DocsLink(props: ComponentProps<"a">) {
  const { t } = useTranslation();

  if (!DOCS_HREF) {
    return null;
  }

  return (
    <a href={DOCS_HREF} target="_blank" rel="noreferrer" {...props}>
      <BookOpenIcon data-icon="inline-start" />
      {t(($) => $.app.userMenu.documentation)}
    </a>
  );
}
