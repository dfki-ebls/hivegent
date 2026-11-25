/** Constructs the Chat SDK bot and wires the platform-agnostic handlers. */

import { createPostgresState } from "@chat-adapter/state-pg";
import { Chat, type Adapter, type Message, type Thread } from "chat";

import { admits, buildAdapters } from "./adapters.js";
import type { BridgeConfig } from "./config.js";
import { createTurnHandler, type ThreadState, type TurnDeps } from "./turn.js";

export async function createBot(
  cfg: BridgeConfig,
  deps: Omit<TurnDeps, "cfg">,
): Promise<Chat<Record<string, Adapter>, ThreadState>> {
  const adapters = await buildAdapters(cfg);

  const chat = new Chat<Record<string, Adapter>, ThreadState>({
    userName: cfg.botUserName,
    adapters,
    state: createPostgresState({ url: cfg.postgresUrl, keyPrefix: "hivegent-bridge" }),
    concurrency: "queue",
  });

  const handleTurn = createTurnHandler({ cfg, ...deps });
  // Asked first in every handler, which the adapter only reaches with an authenticated payload,
  // so a message of a tenant outside the allowlist is dropped before it subscribes or answers.
  const admitted = (thread: Thread<ThreadState>, message: Message) =>
    admits(cfg, thread.adapter.name, message.raw);

  chat.onDirectMessage(async (thread, message, _channel, context) => {
    if (admitted(thread, message)) {
      await handleTurn(thread, message, context);
    }
  });

  chat.onNewMention(async (thread, message, context) => {
    if (!admitted(thread, message)) {
      return;
    }

    await thread.subscribe();
    await handleTurn(thread, message, context);
  });

  chat.onSubscribedMessage(async (thread, message, context) => {
    if (admitted(thread, message)) {
      await handleTurn(thread, message, context);
    }
  });

  return chat;
}
