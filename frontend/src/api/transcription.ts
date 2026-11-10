import { z } from "zod";

import { requestJson } from "@/api/client";
import { apiUrl } from "@/lib/health";

const TranscriptionResponseSchema = z.object({ text: z.string() });

/** Transcribe recorded audio via the backend STT endpoint. */
export async function transcribeAudio(audio: Blob): Promise<string> {
  const form = new FormData();
  form.append("audio", audio, "recording.webm");

  return (
    await requestJson(apiUrl("/transcription"), "transcribeAudio", TranscriptionResponseSchema, {
      method: "POST",
      body: form,
    })
  ).text;
}
