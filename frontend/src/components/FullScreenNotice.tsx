import type { ReactNode } from "react";

/**
 * Centered full-screen notice — an icon, a heading, and a line of muted
 * description. Shared by the maintenance and startup-error screens so their
 * layout lives in one place.
 */
export function FullScreenNotice({
  icon,
  title,
  children,
}: {
  icon: ReactNode;
  title: string;
  children: ReactNode;
}) {
  return (
    <div className="flex h-screen flex-col items-center justify-center gap-4 p-8 text-center">
      {icon}
      <h1 className="text-2xl font-semibold">{title}</h1>
      <p className="max-w-md text-sm text-muted-foreground">{children}</p>
    </div>
  );
}
