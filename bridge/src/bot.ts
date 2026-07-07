/** Constructs the Chat SDK bot and wires the platform-agnostic handlers. */

import { createPostgresState } from "@chat-adapter/state-pg";
import { Chat, type Adapter } from "chat";

import { buildAdapters } from "./adapters.js";
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

  chat.onDirectMessage((thread, message, _channel, context) => handleTurn(thread, message, context));

  chat.onNewMention(async (thread, message, context) => {
    await thread.subscribe();
    await handleTurn(thread, message, context);
  });

  chat.onSubscribedMessage((thread, message, context) => handleTurn(thread, message, context));

  return chat;
}
