/**
 * Adapter registry — the single place a chat platform is wired in. Adding an
 * integration is one entry here (`name → { enabledByDefault, create }`); the
 * turn handler, webhook mounting, and enablement resolution are all generic, so
 * nothing else needs to change. Enablement per adapter is `config.adapters[name]`
 * (from `ENABLE_<NAME>` / the file) falling back to the entry's `enabledByDefault`.
 */

import { createTeamsAdapter } from "@chat-adapter/teams";
import type { Adapter } from "chat";

import type { BridgeConfig } from "./config.js";

interface AdapterSpec {
  /** Whether this adapter runs unless an override disables it. */
  readonly enabledByDefault: boolean;
  /** Construct the adapter; may be async (e.g. a dynamic import). */
  create(cfg: BridgeConfig): Adapter | Promise<Adapter>;
}

export const ADAPTER_REGISTRY: Record<string, AdapterSpec> = {
  teams: {
    enabledByDefault: true,
    create: (cfg) => createTeamsAdapter({ appType: "SingleTenant", userName: cfg.botUserName }),
  },
  web: {
    // Dev-only browser debug adapter; the dynamic import keeps its client-framework
    // peers out of the production dependency graph.
    enabledByDefault: false,
    create: async (cfg) => {
      const { createWebAdapter } = await import("@chat-adapter/web");
      return createWebAdapter({ userName: cfg.botUserName, getUser: () => ({ id: "dev" }) });
    },
  },
};

function enabledEntries(overrides: Record<string, boolean>): [string, AdapterSpec][] {
  return Object.entries(ADAPTER_REGISTRY).filter(
    ([name, spec]) => overrides[name] ?? spec.enabledByDefault,
  );
}

/** Names of the adapters enabled under the given overrides. Pure; used for logging/tests. */
export function enabledAdapterNames(overrides: Record<string, boolean>): string[] {
  return enabledEntries(overrides).map(([name]) => name);
}

export async function buildAdapters(cfg: BridgeConfig): Promise<Record<string, Adapter>> {
  const built = await Promise.all(
    enabledEntries(cfg.adapters).map(
      async ([name, spec]) => [name, await spec.create(cfg)] as const,
    ),
  );

  return Object.fromEntries(built);
}
