import { cn, PRODUCT_NAME } from "@/lib/utils";

/** The Hivegent brand mark, served from the canonical `/logo.svg`. */
export function Logo({ className }: { className?: string }) {
  return <img src="/logo.svg" alt={PRODUCT_NAME} className={cn("object-contain", className)} />;
}
