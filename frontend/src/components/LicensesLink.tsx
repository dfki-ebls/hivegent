import { ScaleIcon } from "lucide-react";
import type { ComponentProps } from "react";
import { useTranslation } from "react-i18next";

import { NewTabLink } from "@/components/NewTabLink";

/** Link to the third-party notices that `build.license` emits next to `index.html`. */
export function LicensesLink(props: ComponentProps<"a">) {
  const { t } = useTranslation();

  return (
    <NewTabLink {...props} href={`${import.meta.env.BASE_URL}licenses.txt`} icon={ScaleIcon}>
      {t(($) => $.app.userMenu.licenses)}
    </NewTabLink>
  );
}
