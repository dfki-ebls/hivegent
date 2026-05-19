import type { UIMessage } from "@ai-sdk/react";

/** Concatenate text from all text parts of a message (or part list). */
export function joinTextParts(parts: UIMessage["parts"] | undefined): string | undefined {
  if (!parts) return undefined;
  const texts = parts
    .filter((p): p is { type: "text"; text: string } => p.type === "text")
    .map((p) => p.text);
  return texts.length > 0 ? texts.join("\n") : undefined;
}

/** Find the last user message and return its id + concatenated text. */
export function getLastUserMessage(
  messages: UIMessage[],
): { id: string; text: string } | undefined {
  const last = [...messages].reverse().find((m) => m.role === "user");
  if (!last) return undefined;
  const text = joinTextParts(last.parts);
  return text ? { id: last.id, text } : undefined;
}

export function isContextLengthError(error: Error | null | undefined): boolean {
  if (!error) return false;
  const msg = error.message || "";
  return msg.includes("context_length_exceeded") || msg.includes("maximum context length");
}
