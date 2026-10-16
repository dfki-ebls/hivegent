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
import { Spinner } from "@/components/ui/spinner";
import { cn } from "@/lib/utils";

interface EmptyStateProps {
  icon?: ReactNode;
  title?: ReactNode;
  description?: ReactNode;
  /** Tints the icon for a failure. */
  destructive?: boolean;
  className?: string;
  /** Actions below the text. */
  children?: ReactNode;
}

/** A placeholder for content that is missing, failed, or not there yet. */
export function EmptyState({
  icon,
  title,
  description,
  destructive = false,
  className,
  children,
}: EmptyStateProps) {
  return (
    <Empty className={className}>
      <EmptyHeader>
        {icon && (
          <EmptyMedia
            variant="icon"
            className={cn(destructive && "bg-destructive/10 text-destructive")}
          >
            {icon}
          </EmptyMedia>
        )}
        {title && <EmptyTitle>{title}</EmptyTitle>}
        {description && <EmptyDescription>{description}</EmptyDescription>}
      </EmptyHeader>
      {children && <EmptyContent>{children}</EmptyContent>}
    </Empty>
  );
}

/** A spinner in place of content that is loading, with *description* below it when given. */
export function LoadingState({
  description,
  className,
}: {
  description?: ReactNode;
  className?: string;
}) {
  const { t } = useTranslation();

  return (
    <EmptyState
      icon={<Spinner aria-label={t(($) => $.common.states.loading)} />}
      description={description}
      className={className}
    />
  );
}
