import {
  DownloadIcon,
  HistoryIcon,
  MessageSquareIcon,
  Minimize2,
  SquarePen,
  UploadIcon,
} from "lucide-react";
import { TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Button } from "@/components/ui/button";

export type ChatTab = "chat" | "history";

interface ChatHeaderProps {
  activeTab: ChatTab;
  hasMessages: boolean;
  compactDisabled: boolean;
  onCompact: () => void;
  onNewChat: () => void;
  onHistoryClick: () => void;
  onImport: () => void;
  onExport?: () => void;
}

export function ChatHeader({
  activeTab,
  hasMessages,
  compactDisabled,
  onCompact,
  onNewChat,
  onHistoryClick,
  onImport,
  onExport,
}: ChatHeaderProps) {
  return (
    <div className="shrink-0 border-b px-4 flex items-center justify-between h-15">
      <TabsList>
        <TabsTrigger value="chat">
          <MessageSquareIcon className="h-4 w-4 mr-1.5" />
          Chat
        </TabsTrigger>
        <TabsTrigger value="history" onClick={onHistoryClick}>
          <HistoryIcon className="h-4 w-4 mr-1.5" />
          History
        </TabsTrigger>
      </TabsList>
      <div className="flex items-center gap-1">
        {activeTab === "history" ? (
          <Button
            variant="ghost"
            size="icon"
            onClick={onImport}
            title="Import conversation from JSON"
          >
            <UploadIcon className="h-4 w-4" />
          </Button>
        ) : (
          <>
            {hasMessages && (
              <Button
                variant="ghost"
                size="icon"
                onClick={onCompact}
                disabled={compactDisabled}
                title="Compact conversation"
              >
                <Minimize2 className="h-4 w-4" />
              </Button>
            )}
            {onExport && (
              <Button
                variant="ghost"
                size="icon"
                onClick={onExport}
                title="Export conversation as JSON"
              >
                <DownloadIcon className="h-4 w-4" />
              </Button>
            )}
          </>
        )}
        <Button variant="ghost" size="icon" onClick={onNewChat} title="New chat">
          <SquarePen className="h-4 w-4" />
        </Button>
      </div>
    </div>
  );
}
