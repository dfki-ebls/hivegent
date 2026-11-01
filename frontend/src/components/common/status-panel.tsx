import { LoaderIcon, type LucideIcon } from "lucide-react";
import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";
import {
  Empty,
  EmptyContent,
  EmptyDescription,
  EmptyHeader,
  EmptyMedia,
  EmptyTitle,
} from "@/components/ui/empty";
import { Spinner } from "@/components/common/spinner";
import { cn } from "@/lib/utils";

interface StatusPanelProps {
  icon: LucideIcon;
  title: string;
  description?: ReactNode;
  /** Shows a spinner in place of the icon. */
  loading?: boolean;
  action?: ReactNode;
  className?: string;
}

/** A centered state in place of content: loading, empty or failed. */
export function StatusPanel({
  icon: Icon,
  title,
  description,
  loading = false,
  action,
  className,
}: StatusPanelProps) {
  return (
    <Empty className={cn("p-4 md:p-6", className)}>
      <EmptyHeader>
        <EmptyMedia variant="icon">{loading ? <Spinner /> : <Icon />}</EmptyMedia>
        <EmptyTitle className="text-base">{title}</EmptyTitle>
        {description && <EmptyDescription>{description}</EmptyDescription>}
      </EmptyHeader>
      {action && <EmptyContent>{action}</EmptyContent>}
    </Empty>
  );
}

/** A spinner in place of content that is loading, with *description* below it when given. */
export function LoadingPanel({
  description,
  className,
}: {
  description?: ReactNode;
  className?: string;
}) {
  const { t } = useTranslation();

  return (
    <StatusPanel
      icon={LoaderIcon}
      title={t(($) => $.common.states.loading)}
      description={description}
      loading
      className={className}
    />
  );
}
