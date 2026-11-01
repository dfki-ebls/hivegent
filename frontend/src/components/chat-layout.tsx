import { PanelLeftOpenIcon } from "lucide-react";
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

import { keyPrefix } from "@/i18n";
import { ChatProvider } from "@/components/chat/chat-context";
import { useHivegentChat } from "@/hooks/chat/use-hivegent-chat";
import type { ChatMessage } from "@/lib/chat/message";
import { useDocumentCanvasStore } from "@/stores/document-canvas-store";
import { ChatSidebar } from "@/components/chat/chat-sidebar";
import { DocumentCanvas } from "@/components/documents/document-canvas";
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
  /** The stored messages the chat starts from, none for a new conversation. */
  messages: ChatMessage[];
}

/** The documents beside the chat of one conversation, mounted once per conversation. */
export function ChatLayout({ id, messages }: ChatLayoutProps) {
  const { t } = useTranslation(undefined, T_OPTIONS);
  const [mobileDocumentsOpen, setMobileDocumentsOpen] = useState(false);
  const openChat = useDocumentCanvasStore((state) => state.openChat);
  const { chat, subagents } = useHivegentChat(id, messages);
  const empty = messages.length === 0;

  // Single entry point for every navigation path (sidebar, links, a new chat):
  // surfaces the chat's primary view, while a reload keeps the remembered tab
  // and a manual switch within one conversation is left alone.
  useEffect(() => {
    openChat(id, empty);
  }, [id, empty, openChat]);

  return (
    <ChatProvider chat={chat} conversationId={id} subagents={subagents}>
      <div className="flex h-full overflow-hidden">
        <div className="hidden h-full w-1/2 overflow-hidden border-r md:block">
          <DocumentCanvas />
        </div>

        <Sheet open={mobileDocumentsOpen} onOpenChange={setMobileDocumentsOpen}>
          <SheetContent
            side="left"
            className="p-0 data-[side=left]:w-full data-[side=left]:sm:max-w-lg"
          >
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
            <ChatSidebar />
          </div>
        </div>
      </div>
    </ChatProvider>
  );
}
