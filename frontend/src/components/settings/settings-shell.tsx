import { Link, type LinkProps, Outlet } from "@tanstack/react-router";
import type { LucideIcon } from "lucide-react";

import { Button } from "@/components/ui/button";

/** A page of a settings area, by the route it links to. */
interface ShellPage {
  to: LinkProps["to"];
  label: string;
  icon: LucideIcon;
}

/** A settings area: its pages in a side navigation beside the open one. */
export function SettingsShell({
  title,
  note,
  pages,
}: {
  title: string;
  /** A line shown atop every page of the area. */
  note?: string;
  pages: readonly ShellPage[];
}) {
  return (
    <div className="mx-auto flex max-w-5xl flex-col gap-6 px-4 py-8 md:flex-row">
      <nav aria-label={title} className="flex shrink-0 flex-wrap gap-1 md:w-48 md:flex-col">
        <h1 className="mb-2 w-full px-3 text-lg font-semibold">{title}</h1>
        {pages.map(({ to, label, icon: Icon }) => (
          <Button
            key={to}
            variant="ghost"
            nativeButton={false}
            className="justify-start data-[status=active]:bg-muted"
            render={<Link to={to} />}
          >
            <Icon data-icon="inline-start" />
            {label}
          </Button>
        ))}
      </nav>
      <div className="flex min-w-0 flex-1 flex-col gap-6">
        {note && <p className="text-sm text-muted-foreground">{note}</p>}
        <Outlet />
      </div>
    </div>
  );
}
