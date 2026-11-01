import type { FileUIPart } from "ai";
import { ImagePlusIcon } from "lucide-react";
import { type SubmitEvent, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { AttachedFiles, type ComposerFile } from "@/components/chat/composer/attached-files";
import { ChatTextarea } from "@/components/chat/composer/chat-textarea";
import { DictateButton } from "@/components/chat/composer/dictate-button";
import { DocumentFilterBadges } from "@/components/chat/composer/document-filter-badges";
import { SubmitStopButton } from "@/components/chat/composer/submit-stop-button";
import { ModeSelector } from "@/components/chat/composer/mode-selector";
import { ReasoningEffortSelector } from "@/components/chat/composer/reasoning-effort-selector";
import { SettingsDialog } from "@/components/settings-dialog";
import { WithTooltip } from "@/components/common/tooltip-button";
import { InputGroup, InputGroupAddon, InputGroupButton } from "@/components/ui/input-group";
import { toast } from "@/components/ui/toast";
import { keyPrefix } from "@/i18n";
import { formatFileSize } from "@/i18n/format";
import { selectAttachmentLimits, useServerSettings } from "@/api/settings";
import { transcribeAudio } from "@/api/transcription";
import { useChatContext, useChatSelector } from "@/components/chat/chat-context";
import { isChatBusy } from "@/lib/chat/chat-utils";
import { useComposerStore } from "@/stores/composer-store";

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

/**
 * The message field of the chat being rendered, with its attachments and settings.
 * The draft lives in the uncontrolled textarea, and the composer tracks only whether
 * it is blank, so typing re-renders neither the message list nor the composer's own controls.
 */
export function Composer({ onSubmit }: { onSubmit: (text: string, files: FileUIPart[]) => void }) {
  const { t } = useTranslation(undefined, T_OPTIONS);
  // Served by the backend so the picker, the paste handler, and the chat
  // route all gate on one table: a file the model could not read is refused
  // here rather than after a round trip.
  const limits = useServerSettings(selectAttachmentLimits);
  const transcribe = useServerSettings((settings) => settings.stt_model) ? transcribeAudio : undefined;
  const { chat } = useChatContext();
  const status = useChatSelector((chat) => chat.status);
  const streaming = isChatBusy(status);
  const agentMode = useComposerStore((state) => state.agentMode);
  const reasoningEffort = useComposerStore((state) => state.reasoningEffort);
  const { setAgentMode, setReasoningEffort } = useComposerStore.getState();
  const [files, setFiles] = useState<ComposerFile[]>([]);
  const [hasText, setHasText] = useState(false);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const canSend = hasText || files.length > 0;

  /** Attach the images among *incoming* that the limits allow, saying why any other is refused. */
  const attach = async (incoming: File[]) => {
    const accepted = incoming.filter((file) => !limits || limits.media_types.includes(file.type));
    const sized = accepted.filter((file) => !limits || file.size <= limits.max_bytes);
    const capacity = (limits?.max_count ?? Infinity) - files.length;

    if (accepted.length < incoming.length) {
      toast.add({ type: "error", title: t(($) => $.attachmentErrors.accept) });
    }

    if (sized.length < accepted.length) {
      toast.add({
        type: "error",
        title: t(($) => $.attachmentErrors.maxFileSize, {
          size: formatFileSize(limits?.max_bytes ?? 0),
        }),
      });
    }

    if (sized.length > capacity) {
      toast.add({
        type: "error",
        title: t(($) => $.attachmentErrors.maxFiles, { count: limits?.max_count ?? 0 }),
      });
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

  const submit = (event: SubmitEvent<HTMLFormElement>) => {
    event.preventDefault();

    if (!canSend) return;

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
        <ChatTextarea
          ref={textareaRef}
          onChange={(event) => setHasText(event.target.value.trim() !== "")}
          placeholder={streaming ? t(($) => $.steerPlaceholder) : t(($) => $.placeholder)}
          onKeyDown={(event) => {
            if (event.key === "Backspace" && event.currentTarget.value === "" && files.length > 0) {
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
          <WithTooltip label={t(($) => $.attachImageHint)}>
            <InputGroupButton size="icon-sm" onClick={() => fileInputRef.current?.click()}>
              <ImagePlusIcon />
            </InputGroupButton>
          </WithTooltip>
          <DictateButton transcribe={transcribe} onText={dictate} />
          <SettingsDialog />
          <ReasoningEffortSelector
            value={reasoningEffort}
            onChange={setReasoningEffort}
            className="ml-auto"
          />
          <ModeSelector value={agentMode} onChange={setAgentMode} />
          {/* Enter still submits while streaming, which steers the running turn. */}
          <SubmitStopButton
            status={status}
            empty={!canSend}
            onStop={() => void chat.stop()}
          />
        </InputGroupAddon>
      </InputGroup>
    </form>
  );
}
