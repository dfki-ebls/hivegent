import { BookOpenIcon } from "lucide-react";
import type { ComponentProps } from "react";
import { useTranslation } from "react-i18next";

import { LANGUAGE } from "@/i18n";
import { NewTabLink } from "@/components/NewTabLink";
import { DOCS_URL } from "@/lib/utils";

/** The handbook in the interface language, served under its own path segment (`/en/`, `/de/`). */
const DOCS_HREF = DOCS_URL && `${DOCS_URL.replace(/\/?$/, "/")}${LANGUAGE}/`;

/** Handbook link, hidden when no handbook is configured. */
export function DocsLink(props: ComponentProps<"a">) {
  const { t } = useTranslation();

  if (!DOCS_HREF) {
    return null;
  }

  return (
    <NewTabLink {...props} href={DOCS_HREF} icon={BookOpenIcon}>
      {t(($) => $.app.userMenu.documentation)}
    </NewTabLink>
  );
}
