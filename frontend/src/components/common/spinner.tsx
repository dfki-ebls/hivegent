import type { LucideIcon } from "lucide-react";
import type { ComponentProps } from "react";
import { useTranslation } from "react-i18next";
import { Spinner as GeneratedSpinner } from "@/components/ui/spinner";

/** The generated spinner, labeled in the interface language. */
export function Spinner(props: ComponentProps<typeof GeneratedSpinner>) {
  const { t } = useTranslation();

  return <GeneratedSpinner aria-label={t(($) => $.common.states.loading)} {...props} />;
}

/** The leading icon of a button, a spinner while its action is *pending*, else its *icon* if any. */
export function PendingIcon({ pending, icon: Icon }: { pending: boolean; icon?: LucideIcon }) {
  if (pending) return <Spinner aria-hidden data-icon="inline-start" />;

  return Icon && <Icon data-icon="inline-start" />;
}
