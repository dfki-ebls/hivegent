import type { ComponentProps } from "react";
import { useTranslation } from "react-i18next";
import { Spinner as GeneratedSpinner } from "@/components/ui/spinner";

/** The generated spinner, labeled in the interface language. */
export function Spinner(props: ComponentProps<typeof GeneratedSpinner>) {
  const { t } = useTranslation();

  return <GeneratedSpinner aria-label={t(($) => $.common.states.loading)} {...props} />;
}
