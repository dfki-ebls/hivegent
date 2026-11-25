import type { LucideIcon } from "lucide-react";
import type { ReactNode } from "react";

import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { cn } from "@/lib/utils";

interface SectionProps {
  icon?: LucideIcon;
  /** The card's title, left out where the page header already names it. */
  title?: string;
  description?: string;
  destructive?: boolean;
  /** Shown in place of the children while set, such as for a list without entries. */
  empty?: string;
  children: ReactNode;
}

/** A card of a settings page, the same on every page. */
export function Section({
  icon: Icon,
  title,
  description,
  destructive,
  empty,
  children,
}: SectionProps) {
  return (
    <Card className={cn(destructive && "ring-destructive/30 dark:ring-destructive/40")}>
      {title !== undefined && (
        <CardHeader>
          <CardTitle className={cn("flex items-center gap-2", destructive && "text-destructive")}>
            {Icon && <Icon className="size-4" />}
            {title}
          </CardTitle>
          {description && <CardDescription>{description}</CardDescription>}
        </CardHeader>
      )}
      <CardContent className="flex flex-col gap-6">
        {empty === undefined ? children : <p className="text-sm text-muted-foreground">{empty}</p>}
      </CardContent>
    </Card>
  );
}
