import {
  DownloadIcon,
  HistoryIcon,
  MessageSquareIcon,
  Minimize2,
  SquarePen,
  UploadIcon,
} from "lucide-react";
import { useTranslation } from "react-i18next";
import { TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Button } from "@/components/ui/button";
import { keyPrefix } from "@/i18n";

const T_OPTIONS = keyPrefix(($) => $.chat.header);

export type ChatTab = "chat" | "history";

interface ChatHeaderProps {
  activeTab: ChatTab;
  hasMessages: boolean;
  compactDisabled: boolean;
  onCompact: () => void;
  onNewChat: () => void;
  onImport: () => void;
  onExport?: () => void;
}

export function ChatHeader({
  activeTab,
  hasMessages,
  compactDisabled,
  onCompact,
  onNewChat,
  onImport,
  onExport,
}: ChatHeaderProps) {
  const { t } = useTranslation(undefined, T_OPTIONS);

  return (
    <div className="shrink-0 border-b px-4 flex items-center justify-between h-15">
      <TabsList>
        <TabsTrigger value="chat">
          <MessageSquareIcon className="h-4 w-4 mr-1.5" />
          {t(($) => $.chat)}
        </TabsTrigger>
        <TabsTrigger value="history">
          <HistoryIcon className="h-4 w-4 mr-1.5" />
          {t(($) => $.history)}
        </TabsTrigger>
      </TabsList>
      <div className="flex items-center gap-1">
        {activeTab === "history" ? (
          <Button variant="ghost" size="icon" onClick={onImport} title={t(($) => $.import)}>
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
                title={t(($) => $.compact)}
              >
                <Minimize2 className="h-4 w-4" />
              </Button>
            )}
            {onExport && (
              <Button variant="ghost" size="icon" onClick={onExport} title={t(($) => $.export)}>
                <DownloadIcon className="h-4 w-4" />
              </Button>
            )}
          </>
        )}
        <Button variant="ghost" size="icon" onClick={onNewChat} title={t(($) => $.newChat)}>
          <SquarePen className="h-4 w-4" />
        </Button>
      </div>
    </div>
  );
}
