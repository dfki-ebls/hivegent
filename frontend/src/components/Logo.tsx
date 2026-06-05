import { cn } from "@/lib/utils";

/** The Hivegent brand mark, served from the canonical `/logo.svg`. */
export function Logo({ className }: { className?: string }) {
  return <img src="/logo.svg" alt="Hivegent" className={cn("object-contain", className)} />;
}
