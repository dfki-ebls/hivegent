import { BookOpenIcon } from "lucide-react";
import type { ComponentProps } from "react";
import { useTranslation } from "react-i18next";

import { LANGUAGE } from "@/i18n";
import { NewTabLink } from "@/components/new-tab-link";
import { runtimeConfig } from "@/runtime-config";

/** Handbook link in the interface language (`/en/`, `/de/`), hidden when the backend links none. */
export function DocsLink(props: ComponentProps<"a">) {
  const { t } = useTranslation();
  const docsUrl = runtimeConfig().docs_url;

  if (!docsUrl) {
    return null;
  }

  return (
    <NewTabLink {...props} href={`${docsUrl.replace(/\/?$/, "/")}${LANGUAGE}/`} icon={BookOpenIcon}>
      {t(($) => $.app.userMenu.documentation)}
    </NewTabLink>
  );
}
