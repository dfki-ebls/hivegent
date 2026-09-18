import { BotIcon } from "lucide-react";
import { AiTransparencyDialog } from "@/components/chat/AiTransparencyDialog";
import { cn } from "@/lib/utils";
import { useSettingsStore } from "@/stores/settings-store";

// EU AI Act Article 50(1): users must be informed they are interacting with an
// AI system, clearly and distinguishably, at the latest at the first
// interaction (Article 50(5)). A persistent marker under the composer keeps the
// notice visible for the whole session, which the Commission guidance favours
// over a one-time disclosure.
const DISCLOSURE_TEXT = "Hivegent is AI and can make mistakes. Please double-check responses.";

export function AiDisclosure({ className }: { className?: string }) {
  const transparency = useSettingsStore((state) => state.backendDefaults?.transparency);

  return (
    <div
      role="note"
      className={cn(
        // WCAG 2.1 AA (Code of Practice, Measure 2.3): the notice and the
        // verification entry point stay on the type scale rather than an
        // arbitrary size below it.
        "flex items-start justify-center gap-1.5 text-center text-xs leading-snug text-muted-foreground",
        className,
      )}
    >
      <BotIcon className="mt-px h-3 w-3 shrink-0" aria-hidden />
      <span>{DISCLOSURE_TEXT}</span>
      {transparency?.enabled && (
        <AiTransparencyDialog
          contactEmail={transparency.contact_email}
          minimumTokens={transparency.minimum_watermark_tokens}
        />
      )}
    </div>
  );
}
