import { PanelLeftOpen } from "lucide-react";
import { useEffect, useState } from "react";

import { useDocumentCanvasStore } from "@/stores/document-canvas-store";
import { ChatSidebar } from "@/components/chat/ChatSidebar";
import { DocumentCanvas } from "@/components/documents/DocumentCanvas";
import { Button } from "@/components/ui/button";
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from "@/components/ui/sheet";

interface ChatLayoutProps {
  id: string;
  draft?: boolean;
  onNewDraft?: () => void;
}

export function ChatLayout({ id, draft = false, onNewDraft }: ChatLayoutProps) {
  const [mobileDocumentsOpen, setMobileDocumentsOpen] = useState(false);
  const openChat = useDocumentCanvasStore((state) => state.openChat);

  // Single entry point for every navigation path (sidebar, links, draft
  // adoption): surfaces the chat's primary view, while a reload keeps the
  // remembered tab and a manual switch within one conversation is left alone.
  useEffect(() => {
    openChat(id, draft);
  }, [id, draft, openChat]);

  return (
    <div className="flex h-full overflow-hidden">
      {/* Desktop: Always show DocumentCanvas */}
      <div className="hidden md:block h-full w-1/2 overflow-hidden border-r">
        <DocumentCanvas />
      </div>

      {/* Mobile: Sheet for DocumentCanvas */}
      <Sheet open={mobileDocumentsOpen} onOpenChange={setMobileDocumentsOpen}>
        <SheetContent side="left" className="w-full sm:max-w-lg p-0">
          <SheetHeader className="border-b">
            <SheetTitle>Documents</SheetTitle>
            <SheetDescription className="sr-only">Browse context and documents</SheetDescription>
          </SheetHeader>
          <div className="h-[calc(100%-60px)] overflow-hidden">
            <DocumentCanvas />
          </div>
        </SheetContent>
      </Sheet>

      {/* Chat Sidebar */}
      <div className="h-full w-full md:w-1/2 overflow-hidden flex flex-col">
        {/* Mobile: Toggle button for documents */}
        <div className="md:hidden border-b p-2">
          <Button variant="ghost" size="sm" onClick={() => setMobileDocumentsOpen(true)}>
            <PanelLeftOpen className="h-4 w-4 mr-2" />
            View Documents
          </Button>
        </div>
        <div className="flex-1 overflow-hidden">
          <ChatSidebar id={id} draft={draft} onNewDraft={onNewDraft} />
        </div>
      </div>
    </div>
  );
}
