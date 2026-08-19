import type { ChatStatus, FileUIPart } from "ai";
import { useCallback, useState } from "react";
import { toast } from "sonner";
import {
  PromptInput,
  PromptInputBody,
  PromptInputFooter,
  PromptInputSubmit,
  PromptInputTextarea,
  PromptInputTools,
  type PromptInputProps,
} from "@/components/ai-elements/prompt-input";
import { SpeechInput } from "@/components/ai-elements/speech-input";
import { AttachedFiles } from "@/components/chat/composer/AttachedFiles";
import { DocumentFilterBadges } from "@/components/chat/composer/DocumentFilterBadges";
import { FileSelectButton } from "@/components/chat/composer/FileSelectButton";
import { ModeSelector } from "@/components/chat/composer/ModeSelector";
import { ReasoningEffortSelector } from "@/components/chat/composer/ReasoningEffortSelector";
import { SettingsDialog } from "@/components/SettingsDialog";
import { featureFlags } from "@/lib/feature-flags";
import type { AgentMode, AttachmentLimits, ReasoningEffort } from "@/lib/types";
import { formatFileSize } from "@/lib/utils";
import { selectAttachmentLimits, useSettingsStore } from "@/stores/settings-store";

type AttachmentError = Parameters<NonNullable<PromptInputProps["onError"]>>[0];

/** Phrase a rejected attachment, mirroring what the chat route would say. */
function attachmentErrorMessage(
  err: AttachmentError,
  limits: AttachmentLimits | undefined
): string {
  if (err.code === "accept") {
    return "Only images can be attached. Upload other documents to your workspace, where the assistant can search them.";
  }
  if (err.code === "max_file_size") {
    return `Images must be under ${formatFileSize(limits?.max_bytes ?? 0)}.`;
  }
  return err.message;
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
  // The draft lives here rather than in ChatSidebar so a keystroke re-renders
  // the composer alone instead of the message list and the whole sidebar.
  const [input, setInput] = useState("");

  // Served by the backend so the picker, the paste handler, and the chat
  // route all gate on one table: a file the model could not read is refused
  // here rather than after a round trip.
  const attachments = useSettingsStore(selectAttachmentLimits);

  // Stable across keystrokes: `onError` feeds PromptInput's attachment
  // callbacks, whose memoization the draft state above would otherwise
  // invalidate on every character typed.
  const onAttachmentError = useCallback(
    (err: AttachmentError) => toast.error(attachmentErrorMessage(err, attachments)),
    [attachments]
  );

  // Hide the mic when SpeechInput could only render disabled: no Web Speech
  // API and no recording fallback (needs MediaRecorder plus a server-side
  // transcriber). Mirrors the mode detection inside SpeechInput.
  const showSpeechInput =
    "SpeechRecognition" in window ||
    "webkitSpeechRecognition" in window ||
    ("MediaRecorder" in window && "mediaDevices" in navigator && Boolean(onAudioRecorded));

  return (
    <PromptInput
      accept={attachments?.media_types.join(",")}
      maxFileSize={attachments?.max_bytes}
      onError={onAttachmentError}
      onSubmit={(msg) => {
        setInput("");
        onSubmit(msg.text, msg.files);
      }}
    >
      <DocumentFilterBadges />
      <AttachedFiles />
      <PromptInputBody>
        <PromptInputTextarea
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder={isStreaming ? "Steer the conversation..." : "Ask about your documents..."}
        />
      </PromptInputBody>
      <PromptInputFooter>
        <PromptInputTools>
          <FileSelectButton />
          {showSpeechInput && (
            <SpeechInput
              type="button"
              variant="ghost"
              size="icon"
              className="bg-transparent text-muted-foreground hover:bg-accent hover:text-accent-foreground rounded-md"
              disabled={status !== "ready"}
              onTranscriptionChange={(text) =>
                setInput((prev) => (prev ? `${prev} ${text}` : text))
              }
              onAudioRecorded={onAudioRecorded}
            />
          )}
          <SettingsDialog />
          <ReasoningEffortSelector value={reasoningEffort} onChange={onReasoningEffortChange} />
          {featureFlags.agentModes && (
            <ModeSelector value={agentMode} onChange={onAgentModeChange} />
          )}
        </PromptInputTools>
        <PromptInputSubmit status={status} onStop={onStop} />
      </PromptInputFooter>
    </PromptInput>
  );
}
