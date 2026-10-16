import { MicIcon, SquareIcon } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { InputGroupButton } from "@/components/ui/input-group";
import { Spinner } from "@/components/ui/spinner";
import { WithTooltip } from "@/components/TooltipButton";
import { LANGUAGE } from "@/i18n";

/** The part of the Web Speech API the DOM library does not declare yet. */
interface SpeechRecognition extends EventTarget {
  continuous: boolean;
  lang: string;
  start(): void;
  stop(): void;
  addEventListener(type: "result", listener: (event: SpeechRecognitionEvent) => void): void;
  addEventListener(type: "end" | "error", listener: () => void): void;
}

type SpeechRecognitionConstructor = new () => SpeechRecognition;

declare global {
  interface Window {
    SpeechRecognition?: SpeechRecognitionConstructor;
    webkitSpeechRecognition?: SpeechRecognitionConstructor;
  }
}

const Recognition = window.SpeechRecognition ?? window.webkitSpeechRecognition;

const canRecord = "MediaRecorder" in window && "mediaDevices" in navigator;

interface DictateButtonProps {
  disabled: boolean;
  onText: (text: string) => void;
  /** Transcribes a recording where the browser cannot recognize speech itself. */
  transcribe?: (audio: Blob) => Promise<string>;
}

/**
 * Dictates into the draft, with the browser's speech recognition or a recording,
 * and renders nothing where neither can run.
 */
export function DictateButton({ disabled, onText, transcribe }: DictateButtonProps) {
  const { t } = useTranslation();
  const [listening, setListening] = useState(false);
  const [transcribing, setTranscribing] = useState(false);
  const stopRef = useRef<(() => void) | null>(null);

  useEffect(() => () => stopRef.current?.(), []);

  const recognize = (Constructor: SpeechRecognitionConstructor) => {
    const recognition = new Constructor();
    recognition.continuous = true;
    recognition.lang = LANGUAGE;
    recognition.addEventListener("result", (event) => {
      const final = Array.from(event.results)
        .slice(event.resultIndex)
        .filter((result) => result.isFinal);
      const text = final.map((result) => result[0]?.transcript ?? "").join("");
      if (text) onText(text);
    });
    // Dropping the stop callback releases the recognition with the listening session.
    const end = () => {
      stopRef.current = null;
      setListening(false);
    };
    recognition.addEventListener("end", end);
    recognition.addEventListener("error", end);
    recognition.start();
    stopRef.current = () => recognition.stop();
  };

  const record = async (send: (audio: Blob) => Promise<string>) => {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    const recorder = new MediaRecorder(stream);
    const chunks: Blob[] = [];
    recorder.addEventListener("dataavailable", (event) => chunks.push(event.data));
    recorder.addEventListener("stop", () => {
      for (const track of stream.getTracks()) track.stop();
      stopRef.current = null;
      setListening(false);
      setTranscribing(true);
      send(new Blob(chunks, { type: recorder.mimeType }))
        .then((text) => text && onText(text))
        .catch(() => {})
        .finally(() => setTranscribing(false));
    });
    recorder.start();
    stopRef.current = () => recorder.state === "recording" && recorder.stop();
  };

  const toggle = () => {
    if (listening) {
      stopRef.current?.();
      return;
    }

    setListening(true);
    if (Recognition) recognize(Recognition);
    else if (transcribe) record(transcribe).catch(() => setListening(false));
  };

  if (!Recognition && !(canRecord && transcribe)) return null;

  return (
    <WithTooltip label={t(($) => $.chat.composer.dictate)}>
      <InputGroupButton
        size="icon-sm"
        variant={listening ? "destructive" : "ghost"}
        aria-pressed={listening}
        disabled={(disabled && !listening) || transcribing}
        onClick={toggle}
      >
        {transcribing ? <Spinner /> : listening ? <SquareIcon /> : <MicIcon />}
      </InputGroupButton>
    </WithTooltip>
  );
}
