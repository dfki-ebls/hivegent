import { useCallback, useState } from 'react';
import { PanelLeftOpen } from 'lucide-react';

import { ChatSidebar } from './ChatSidebar';
import { DocumentCanvas } from './DocumentCanvas';
import { Button } from './ui/button';
import {
  Sheet,
  SheetContent,
  SheetHeader,
  SheetTitle,
} from './ui/sheet';

interface ChatLayoutProps {
  id: string;
}

export function ChatLayout({ id }: ChatLayoutProps) {
  const [mobileDocumentsOpen, setMobileDocumentsOpen] = useState(false);
  const [pendingChatContent, setPendingChatContent] = useState<string | null>(null);

  const handleSendToChat = useCallback((content: string) => {
    setPendingChatContent(content);
  }, []);

  const handlePendingContentConsumed = useCallback(() => {
    setPendingChatContent(null);
  }, []);

  return (
    <div className="flex h-full overflow-hidden">
      {/* Desktop: Always show DocumentCanvas */}
      <div className="hidden md:block h-full w-2/3 overflow-hidden border-r">
        <DocumentCanvas onSendToChat={handleSendToChat} />
      </div>

      {/* Mobile: Sheet for DocumentCanvas */}
      <Sheet open={mobileDocumentsOpen} onOpenChange={setMobileDocumentsOpen}>
        <SheetContent side="left" className="w-full sm:max-w-lg p-0">
          <SheetHeader className="border-b">
            <SheetTitle>Documents</SheetTitle>
          </SheetHeader>
          <div className="h-[calc(100%-60px)] overflow-hidden">
            <DocumentCanvas onSendToChat={handleSendToChat} />
          </div>
        </SheetContent>
      </Sheet>

      {/* Chat Sidebar */}
      <div className="h-full w-full md:w-1/3 overflow-hidden flex flex-col">
        {/* Mobile: Toggle button for documents */}
        <div className="md:hidden border-b p-2">
          <Button
            variant="ghost"
            size="sm"
            onClick={() => setMobileDocumentsOpen(true)}
          >
            <PanelLeftOpen className="h-4 w-4 mr-2" />
            View Documents
          </Button>
        </div>
        <div className="flex-1 overflow-hidden">
          <ChatSidebar
            id={id}
            pendingContent={pendingChatContent}
            onPendingContentConsumed={handlePendingContentConsumed}
          />
        </div>
      </div>
    </div>
  );
}
