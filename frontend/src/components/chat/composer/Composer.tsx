import type { ChatStatus, FileUIPart } from "ai";
import { ArrowUpIcon, ImagePlusIcon, SquareIcon } from "lucide-react";
import { type FormEvent, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";
import { AttachedFiles, type ComposerFile } from "@/components/chat/composer/AttachedFiles";
import { DictateButton } from "@/components/chat/composer/DictateButton";
import { DocumentFilterBadges } from "@/components/chat/composer/DocumentFilterBadges";
import { ModeSelector } from "@/components/chat/composer/ModeSelector";
import { ReasoningEffortSelector } from "@/components/chat/composer/ReasoningEffortSelector";
import { SettingsDialog } from "@/components/SettingsDialog";
import {
  InputGroup,
  InputGroupAddon,
  InputGroupButton,
  InputGroupTextarea,
} from "@/components/ui/input-group";
import { Spinner } from "@/components/ui/spinner";
import { keyPrefix } from "@/i18n";
import { formatFileSize } from "@/i18n/format";
import { featureFlags } from "@/lib/feature-flags";
import type { AgentMode, ReasoningEffort } from "@/lib/types";
import { selectAttachmentLimits, useSettingsStore } from "@/stores/settings-store";

const T_OPTIONS = keyPrefix(($) => $.chat.composer);

function readDataUrl(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.addEventListener("load", () =>
      typeof reader.result === "string" ? resolve(reader.result) : reject(reader.error),
    );
    reader.addEventListener("error", () => reject(reader.error));
    reader.readAsDataURL(file);
  });
}

interface ComposerProps {
  onSubmit: (text: string, files?: FileUIPart[]) => void;
  status: ChatStatus;
  onStop: () => void;
  isStreaming: boolean;
  agentMode: AgentMode;
  onAgentModeChange: (value: AgentMode) => void;
  reasoningEffort: ReasoningEffort;
  onReasoningEffortChange: (value: ReasoningEffort) => void;
  onAudioRecorded?: (audio: Blob) => Promise<string>;
}

/**
 * The message field with its attachments and settings.
 * The draft lives in the uncontrolled textarea and only whether it is blank in state,
 * so typing re-renders neither the message list nor the composer's own controls.
 */
export function Composer({
  onSubmit,
  status,
  onStop,
  isStreaming,
  agentMode,
  onAgentModeChange,
  reasoningEffort,
  onReasoningEffortChange,
  onAudioRecorded,
}: ComposerProps) {
  const { t } = useTranslation(undefined, T_OPTIONS);
  // Served by the backend so the picker, the paste handler, and the chat
  // route all gate on one table: a file the model could not read is refused
  // here rather than after a round trip.
  const limits = useSettingsStore(selectAttachmentLimits);
  const [hasText, setHasText] = useState(false);
  const [files, setFiles] = useState<ComposerFile[]>([]);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const canSend = hasText || files.length > 0;

  /** Attach the images among *incoming* that the limits allow, saying why any other is refused. */
  const attach = async (incoming: File[]) => {
    const accepted = incoming.filter((file) => !limits || limits.media_types.includes(file.type));
    const sized = accepted.filter((file) => !limits || file.size <= limits.max_bytes);
    const capacity = (limits?.max_count ?? Infinity) - files.length;

    if (accepted.length < incoming.length) {
      toast.error(t(($) => $.attachmentErrors.accept));
    }

    if (sized.length < accepted.length) {
      toast.error(
        t(($) => $.attachmentErrors.maxFileSize, { size: formatFileSize(limits?.max_bytes ?? 0) }),
      );
    }

    if (sized.length > capacity) {
      toast.error(t(($) => $.attachmentErrors.maxFiles, { count: limits?.max_count ?? 0 }));
    }

    const added = await Promise.all(
      sized.slice(0, Math.max(0, capacity)).map(async (file): Promise<ComposerFile> => ({
        id: crypto.randomUUID(),
        part: {
          type: "file",
          mediaType: file.type,
          filename: file.name,
          url: await readDataUrl(file),
        },
      })),
    );
    setFiles((prev) => [...prev, ...added]);
  };

  const submit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    onSubmit(
      textareaRef.current?.value ?? "",
      files.map((file) => file.part),
    );
    event.currentTarget.reset();
    setHasText(false);
    setFiles([]);
  };

  const dictate = (spoken: string) => {
    const textarea = textareaRef.current;
    if (!textarea) return;

    textarea.value = textarea.value ? `${textarea.value} ${spoken}` : spoken;
    setHasText(textarea.value.trim() !== "");
  };

  return (
    <form onSubmit={submit}>
      <input
        ref={fileInputRef}
        type="file"
        multiple
        accept={limits?.media_types.join(",")}
        className="sr-only"
        tabIndex={-1}
        onChange={(event) => {
          void attach(Array.from(event.target.files ?? []));
          event.target.value = "";
        }}
      />
      <InputGroup>
        <DocumentFilterBadges />
        <AttachedFiles
          files={files}
          onRemove={(id) => setFiles((prev) => prev.filter((file) => file.id !== id))}
        />
        <InputGroupTextarea
          ref={textareaRef}
          onChange={(event) => setHasText(event.target.value.trim() !== "")}
          placeholder={isStreaming ? t(($) => $.steerPlaceholder) : t(($) => $.placeholder)}
          className="max-h-48 min-h-16 p-3"
          onKeyDown={(event) => {
            if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
              event.preventDefault();
              if (canSend) event.currentTarget.form?.requestSubmit();
            } else if (
              event.key === "Backspace" &&
              event.currentTarget.value === "" &&
              files.length > 0
            ) {
              event.preventDefault();
              setFiles((prev) => prev.slice(0, -1));
            }
          }}
          onPaste={(event) => {
            const pasted = Array.from(event.clipboardData.files);
            if (pasted.length === 0) return;

            event.preventDefault();
            void attach(pasted);
          }}
        />
        <InputGroupAddon align="block-end" className="flex-wrap">
          <InputGroupButton
            size="icon-sm"
            title={t(($) => $.attachImageHint)}
            aria-label={t(($) => $.attachImage)}
            onClick={() => fileInputRef.current?.click()}
          >
            <ImagePlusIcon />
          </InputGroupButton>
          <DictateButton
            disabled={status !== "ready"}
            transcribe={onAudioRecorded}
            onText={dictate}
          />
          <SettingsDialog />
          <ReasoningEffortSelector value={reasoningEffort} onChange={onReasoningEffortChange} />
          {featureFlags.agentModes && (
            <ModeSelector value={agentMode} onChange={onAgentModeChange} />
          )}
          {/* Enter still submits while streaming, which steers the running turn. */}
          <InputGroupButton
            type={isStreaming ? "button" : "submit"}
            size="icon-sm"
            variant="default"
            className="ml-auto rounded-full"
            aria-label={isStreaming ? t(($) => $.stop) : t(($) => $.submit)}
            disabled={!isStreaming && !canSend}
            onClick={isStreaming ? onStop : undefined}
          >
            {status === "submitted" ? <Spinner /> : isStreaming ? <SquareIcon /> : <ArrowUpIcon />}
          </InputGroupButton>
        </InputGroupAddon>
      </InputGroup>
    </form>
  );
}
