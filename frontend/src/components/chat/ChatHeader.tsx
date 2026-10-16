import {
  DownloadIcon,
  HistoryIcon,
  MessageSquareIcon,
  Minimize2Icon,
  SquarePenIcon,
  UploadIcon,
} from "lucide-react";
import { useTranslation } from "react-i18next";
import { TooltipButton } from "@/components/TooltipButton";
import { TabsList, TabsTrigger } from "@/components/ui/tabs";
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
    <div className="flex h-15 shrink-0 items-center justify-between gap-2 border-b px-4">
      <TabsList>
        <TabsTrigger value="chat">
          <MessageSquareIcon data-icon="inline-start" />
          {t(($) => $.chat)}
        </TabsTrigger>
        <TabsTrigger value="history">
          <HistoryIcon data-icon="inline-start" />
          {t(($) => $.history)}
        </TabsTrigger>
      </TabsList>
      <div className="flex items-center gap-1">
        {activeTab === "history" && (
          <TooltipButton label={t(($) => $.import)} onClick={onImport}>
            <UploadIcon />
          </TooltipButton>
        )}
        {activeTab === "chat" && hasMessages && (
          <TooltipButton label={t(($) => $.compact)} onClick={onCompact} disabled={compactDisabled}>
            <Minimize2Icon />
          </TooltipButton>
        )}
        {activeTab === "chat" && onExport && (
          <TooltipButton label={t(($) => $.export)} onClick={onExport}>
            <DownloadIcon />
          </TooltipButton>
        )}
        <TooltipButton label={t(($) => $.newChat)} onClick={onNewChat}>
          <SquarePenIcon />
        </TooltipButton>
      </div>
    </div>
  );
}
