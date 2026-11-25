/**
 * Adapter registry, the single place a chat platform is wired in. Adding an
 * integration is one entry here plus its switch in the config's `adapters`,
 * the turn handler and webhook mounting are generic.
 */

import { createTeamsAdapter } from "@chat-adapter/teams";
import type { Adapter } from "chat";

import type { BridgeConfig } from "./config.js";

interface AdapterSpec {
  /** Construct the adapter; may be async (e.g. a dynamic import). */
  create(cfg: BridgeConfig): Adapter | Promise<Adapter>;
  /**
   * Whether the bot answers a message, given its platform payload, every message when absent.
   * Asked by the handlers, so only of a payload the adapter has authenticated.
   */
  readonly admits?: (cfg: BridgeConfig, raw: unknown) => boolean;
}

interface TeamsActivity {
  conversation?: { tenantId?: unknown };
  channelData?: { tenant?: { id?: unknown } };
}

/** The tenant a Teams activity comes from, where the Teams adapter reads it too. */
export function teamsTenant(raw: unknown): string | undefined {
  const activity = (typeof raw === "object" && raw !== null ? raw : {}) as TeamsActivity;
  const tenant = activity.conversation?.tenantId ?? activity.channelData?.tenant?.id;

  return typeof tenant === "string" ? tenant : undefined;
}

export const ADAPTER_REGISTRY: Record<keyof BridgeConfig["adapters"], AdapterSpec> = {
  teams: {
    create: ({ oidc, teams, botUserName }) => {
      // Teams users must reach hivegent as the service principal and only from the operator's tenants.
      if (!oidc) {
        throw new Error("The Teams adapter needs `oidc`, the service client of hivegent");
      }

      if (!teams) {
        throw new Error("The Teams adapter needs `teams`, its app and the tenant ids it answers");
      }

      return createTeamsAdapter({
        appType: "SingleTenant",
        appId: teams.appId,
        appPassword: teams.appPassword,
        appTenantId: teams.tenantId,
        userName: botUserName,
      });
    },
    admits: (cfg, raw) => {
      const tenant = teamsTenant(raw);

      return tenant !== undefined && (cfg.teams?.allowedTenants.includes(tenant) ?? false);
    },
  },
  web: {
    // Dev-only browser debug adapter, the dynamic import keeps its client-framework
    // peers out of the production dependency graph.
    // Unauthenticated, so the config allows it only without `oidc`, where the bot holds no credentials.
    create: async (cfg) => {
      const { createWebAdapter } = await import("@chat-adapter/web");
      return createWebAdapter({ userName: cfg.botUserName, getUser: () => ({ id: "dev" }) });
    },
  },
};

/** Whether the bot answers *raw*, a message the adapter named *adapter* received. */
export function admits(cfg: BridgeConfig, adapter: string, raw: unknown): boolean {
  const spec = Object.hasOwn(ADAPTER_REGISTRY, adapter)
    ? ADAPTER_REGISTRY[adapter as keyof typeof ADAPTER_REGISTRY]
    : undefined;

  return spec?.admits?.(cfg, raw) ?? true;
}

export async function buildAdapters(cfg: BridgeConfig): Promise<Record<string, Adapter>> {
  const enabled: Partial<Record<string, boolean>> = cfg.adapters;
  const built = await Promise.all(
    Object.entries(ADAPTER_REGISTRY)
      .filter(([name]) => enabled[name])
      .map(async ([name, spec]) => [name, await spec.create(cfg)] as const),
  );

  return Object.fromEntries(built);
}
