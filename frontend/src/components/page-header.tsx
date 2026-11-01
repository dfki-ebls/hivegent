import { cn } from "@/lib/utils";

/** The title and description atop a full-page route. */
export function PageHeader({
  title,
  description,
  className,
}: {
  title: string;
  description: string;
  className?: string;
}) {
  return (
    <header className={cn("flex flex-col gap-1", className)}>
      <h1 className="text-2xl font-semibold">{title}</h1>
      <p className="text-sm text-muted-foreground">{description}</p>
    </header>
  );
}
