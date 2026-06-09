import { BotIcon } from "lucide-react";
import { cn } from "@/lib/utils";

// EU AI Act Article 50(1): users must be informed they are interacting with an
// AI system, clearly and distinguishably, at the latest at the first
// interaction (Article 50(5)). A persistent marker under the composer keeps the
// notice visible for the whole session, which the Commission guidance favours
// over a one-time disclosure.
const DISCLOSURE_TEXT =
  "Hivegent is AI and can make mistakes. Please double-check responses.";

export function AiDisclosure({ className }: { className?: string }) {
  return (
    <p
      role="note"
      className={cn(
        "flex items-start justify-center gap-1.5 text-center text-[11px] leading-tight text-muted-foreground",
        className,
      )}
    >
      <BotIcon className="mt-px h-3 w-3 shrink-0" aria-hidden />
      <span>{DISCLOSURE_TEXT}</span>
    </p>
  );
}
