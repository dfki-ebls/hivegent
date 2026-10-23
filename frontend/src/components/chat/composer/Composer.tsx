import type { ChatStatus, FileUIPart } from "ai";
import { ImagePlusIcon } from "lucide-react";
import { type FormEvent, useLayoutEffect, useRef } from "react";
import { useTranslation } from "react-i18next";
import { AttachedFiles } from "@/components/chat/composer/AttachedFiles";
import { ChatTextarea } from "@/components/chat/composer/ChatTextarea";
import { DictateButton } from "@/components/chat/composer/DictateButton";
import { DocumentFilterBadges } from "@/components/chat/composer/DocumentFilterBadges";
import { SubmitStopButton } from "@/components/chat/composer/SubmitStopButton";
import { ModeSelector } from "@/components/chat/composer/ModeSelector";
import { ReasoningEffortSelector } from "@/components/chat/composer/ReasoningEffortSelector";
import { SettingsDialog } from "@/components/SettingsDialog";
import { WithTooltip } from "@/components/TooltipButton";
import { InputGroup, InputGroupAddon, InputGroupButton } from "@/components/ui/input-group";
import { toast } from "@/components/ui/toast";
import { keyPrefix } from "@/i18n";
import { formatFileSize } from "@/i18n/format";
import { selectAttachmentLimits, useServerSettings } from "@/queries/settings";
import { type ComposerFile, useComposerStore } from "@/stores/composer-store";

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
  onSubmit: (text: string, files: FileUIPart[]) => void;
  status: ChatStatus;
  onStop: () => void;
  isStreaming: boolean;
  onAudioRecorded?: (audio: Blob) => Promise<string>;
}

/**
 * The message field with its attachments and settings.
 * The draft lives in the uncontrolled textarea, mirrored to the composer store so it outlives
 * the remount when a draft becomes a conversation, and the composer subscribes only to whether
 * it is blank, so typing re-renders neither the message list nor the composer's own controls.
 */
export function Composer({
  onSubmit,
  status,
  onStop,
  isStreaming,
  onAudioRecorded,
}: ComposerProps) {
  const { t } = useTranslation(undefined, T_OPTIONS);
  // Served by the backend so the picker, the paste handler, and the chat
  // route all gate on one table: a file the model could not read is refused
  // here rather than after a round trip.
  const limits = useServerSettings(selectAttachmentLimits);
  const files = useComposerStore((state) => state.files);
  const agentMode = useComposerStore((state) => state.agentMode);
  const reasoningEffort = useComposerStore((state) => state.reasoningEffort);
  const hasText = useComposerStore((state) => state.text.trim() !== "");
  const { text, setText, setFiles, setFocused, setAgentMode, setReasoningEffort, clearDraft } =
    useComposerStore.getState();
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const canSend = hasText || files.length > 0;

  // Runs before the field leaves the DOM, while it can still tell whether it has focus.
  useLayoutEffect(() => {
    const textarea = textareaRef.current;
    if (useComposerStore.getState().focused) textarea?.focus();

    return () => setFocused(document.activeElement === textarea);
  }, [setFocused]);

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

  const submit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();

    if (!canSend) return;

    onSubmit(
      textareaRef.current?.value ?? "",
      files.map((file) => file.part),
    );
    event.currentTarget.reset();
    clearDraft();
  };

  const dictate = (spoken: string) => {
    const textarea = textareaRef.current;
    if (!textarea) return;

    textarea.value = textarea.value ? `${textarea.value} ${spoken}` : spoken;
    setText(textarea.value);
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
          defaultValue={text}
          onChange={(event) => setText(event.target.value)}
          placeholder={isStreaming ? t(($) => $.steerPlaceholder) : t(($) => $.placeholder)}
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
          <DictateButton transcribe={onAudioRecorded} onText={dictate} />
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
            streaming={isStreaming}
            empty={!canSend}
            onStop={onStop}
          />
        </InputGroupAddon>
      </InputGroup>
    </form>
  );
}
