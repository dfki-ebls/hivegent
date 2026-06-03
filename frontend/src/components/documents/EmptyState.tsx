import type { ReactNode } from "react";

interface EmptyStateProps {
  icon: ReactNode;
  title: string;
  description?: string;
}

export function EmptyState({ icon, title, description }: EmptyStateProps) {
  return (
    <div className="flex h-full flex-col items-center justify-center gap-3 p-8 text-muted-foreground">
      {icon}
      <p className="text-center">{title}</p>
      {description && <p className="text-center text-sm">{description}</p>}
    </div>
  );
}
