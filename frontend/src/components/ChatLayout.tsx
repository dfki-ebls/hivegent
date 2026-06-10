import { PanelLeftOpen } from "lucide-react";
import { useState } from "react";

import { ChatSidebar } from "./chat/ChatSidebar";
import { DocumentCanvas } from "./documents/DocumentCanvas";
import { Button } from "./ui/button";
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from "./ui/sheet";

interface ChatLayoutProps {
  id: string;
  draft?: boolean;
}

export function ChatLayout({ id, draft = false }: ChatLayoutProps) {
  const [mobileDocumentsOpen, setMobileDocumentsOpen] = useState(false);

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
            <SheetDescription className="sr-only">Browse fetched documents</SheetDescription>
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
          <ChatSidebar id={id} draft={draft} />
        </div>
      </div>
    </div>
  );
}
