import { PanelLeftOpenIcon } from "lucide-react";
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

import { keyPrefix } from "@/i18n";
import { ConversationIdProvider } from "@/hooks/chat/use-conversation-id";
import { useDocumentCanvasStore } from "@/stores/document-canvas-store";
import { ChatSidebar } from "@/components/chat/ChatSidebar";
import { DocumentCanvas } from "@/components/documents/DocumentCanvas";
import { Button } from "@/components/ui/button";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";

const T_OPTIONS = keyPrefix(($) => $.chat.layout);

interface ChatLayoutProps {
  id: string;
  draft?: boolean;
  onNewDraft?: () => void;
}

export function ChatLayout({ id, draft = false, onNewDraft }: ChatLayoutProps) {
  const { t } = useTranslation(undefined, T_OPTIONS);
  const [mobileDocumentsOpen, setMobileDocumentsOpen] = useState(false);
  const openChat = useDocumentCanvasStore((state) => state.openChat);

  // Single entry point for every navigation path (sidebar, links, draft
  // adoption): surfaces the chat's primary view, while a reload keeps the
  // remembered tab and a manual switch within one conversation is left alone.
  useEffect(() => {
    openChat(id, draft);
  }, [id, draft, openChat]);

  return (
    <ConversationIdProvider value={draft ? null : id}>
      <div className="flex h-full overflow-hidden">
        <div className="hidden h-full w-1/2 overflow-hidden border-r md:block">
          <DocumentCanvas />
        </div>

        <Sheet open={mobileDocumentsOpen} onOpenChange={setMobileDocumentsOpen}>
          <SheetContent side="left" className="p-0 data-[side=left]:w-full data-[side=left]:sm:max-w-lg">
            <SheetHeader className="border-b">
              <SheetTitle>{t(($) => $.documents)}</SheetTitle>
              <SheetDescription className="sr-only">
                {t(($) => $.documentsDescription)}
              </SheetDescription>
            </SheetHeader>
            <div className="min-h-0 flex-1 overflow-hidden">
              <DocumentCanvas />
            </div>
          </SheetContent>
        </Sheet>

        <div className="flex h-full w-full flex-col overflow-hidden md:w-1/2">
          <div className="border-b p-2 md:hidden">
            <Button variant="ghost" size="sm" onClick={() => setMobileDocumentsOpen(true)}>
              <PanelLeftOpenIcon data-icon="inline-start" />
              {t(($) => $.viewDocuments)}
            </Button>
          </div>
          <div className="min-h-0 flex-1 overflow-hidden">
            <ChatSidebar id={id} draft={draft} onNewDraft={onNewDraft} />
          </div>
        </div>
      </div>
    </ConversationIdProvider>
  );
}
