/** Platform-agnostic turn handler: map thread → conversation, stream the reply. */

import type { Message, MessageContext, Thread } from "chat";

import type { BridgeConfig } from "./config.js";
import type { ServiceTokenProvider } from "./hivegent/auth.js";
import {
  ConversationNotFoundError,
  continueConversation,
  startConversation,
  type ChatResult,
} from "./hivegent/client.js";
import { parseHivegentStream } from "./hivegent/stream.js";

// EU AI Act Article 50(1) and (5): the first reply of every conversation tells
// the reader that an AI system is answering, as the web UI's persistent notice does.
const AI_DISCLOSURE = "_Hivegent is AI and can make mistakes. Please double-check responses._";

/** Per-thread state persisted by the Chat SDK (maps to a hivegent conversation). */
export interface ThreadState extends Record<string, unknown> {
  hivegentConversationId?: string;
}

export interface TurnDeps {
  cfg: BridgeConfig;
  token?: ServiceTokenProvider;
}

export function createTurnHandler(
  deps: TurnDeps,
): (thread: Thread<ThreadState>, message: Message, context?: MessageContext) => Promise<void> {
  const { cfg, token } = deps;

  async function runChat(
    existing: string | undefined,
    text: string,
    accessToken: string | undefined,
  ): Promise<ChatResult> {
    if (!existing) {
      return startConversation(text, accessToken, cfg);
    }

    try {
      return await continueConversation(existing, text, accessToken, cfg);
    } catch (err) {
      if (err instanceof ConversationNotFoundError) {
        return startConversation(text, accessToken, cfg);
      }

      throw err;
    }
  }

  return async function handleTurn(
    thread: Thread<ThreadState>,
    message: Message,
    context?: MessageContext,
  ): Promise<void> {
    // The state read, token fetch, and typing indicator are independent round-trips.
    const typing = thread.startTyping();
    const [state, accessToken] = await Promise.all([
      thread.state,
      token ? token.getToken() : Promise.resolve(undefined),
    ]);
    await typing;

    const existing = state?.hivegentConversationId;
    const text = [...(context?.skipped ?? []), message].map((item) => item.text).join("\n\n");
    const { response, conversationId } = await runChat(existing, text, accessToken);

    const started = conversationId !== existing;

    if (started) {
      await thread.setState({ hivegentConversationId: conversationId });
    }

    await thread.post(relay(thread, response, started));
  };
}

/** Reduce the hivegent stream to answer text, showing tool activity as typing status. */
async function* relay(
  thread: Thread<ThreadState>,
  response: Response,
  disclose: boolean,
): AsyncGenerator<string> {
  if (disclose) {
    yield `${AI_DISCLOSURE}\n\n`;
  }

  for await (const event of parseHivegentStream(response)) {
    if (event.kind === "status") {
      await thread.startTyping(event.label);
      continue;
    }

    if (event.kind === "error") {
      yield `⚠️ ${event.text}`;
      return;
    }

    yield event.text;
  }
}
