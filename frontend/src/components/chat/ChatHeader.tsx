import { HistoryIcon, MessageSquareIcon, Minimize2, SquarePen } from "lucide-react";
import { TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Button } from "@/components/ui/button";

interface ChatHeaderProps {
  hasMessages: boolean;
  compactDisabled: boolean;
  onCompact: () => void;
  onNewChat: () => void;
  onHistoryClick: () => void;
}

export function ChatHeader({
  hasMessages,
  compactDisabled,
  onCompact,
  onNewChat,
  onHistoryClick,
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
        <Button variant="ghost" size="icon" onClick={onNewChat} title="New chat">
          <SquarePen className="h-4 w-4" />
        </Button>
      </div>
    </div>
  );
}
