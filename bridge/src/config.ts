/**
 * Layered configuration: an optional JSON file (non-secret settings, e.g. rendered
 * by the NixOS module into the store) overlaid by environment variables, which win.
 * Mirrors the backend's `HIVEGENT_CONFIG_FILE` (TOML) + `HIVEGENT_*` env pattern.
 * The file path comes from `BRIDGE_CONFIG_FILE` (default `config.json`); a missing
 * file is tolerated. Keep secrets in the environment, not the file.
 */

import { readFileSync } from "node:fs";

export interface OidcConfig {
  issuer: string;
  clientId: string;
  clientSecret: string;
  scope?: string;
}

export interface BridgeConfig {
  hivegentUrl: string;
  /** Absent for local debugging against an auth-disabled hivegent. */
  oidc?: OidcConfig;
  reasoningEffort: string;
  disabledTools: string[];
  postgresUrl: string;
  botUserName: string;
  host: string;
  port: number;
  /** Per-adapter enablement overrides; unset adapters use the registry default. */
  adapters: Record<string, boolean>;
}

/** Shape of the optional JSON config file (all keys optional). */
export interface FileConfig {
  hivegentUrl?: string;
  oidc?: Partial<OidcConfig>;
  reasoningEffort?: string;
  postgresUrl?: string;
  botUserName?: string;
  host?: string;
  port?: number;
  adapters?: Record<string, boolean>;
}

const DEFAULT_HOST = "127.0.0.1";
const DEFAULT_CONFIG_FILE = "config.json";

function trimTrailingSlash(url: string): string {
  return url.replace(/\/+$/, "");
}

function boolFrom(value: string | undefined): boolean | undefined {
  return value === undefined ? undefined : value !== "false" && value !== "0";
}

export function readFileConfig(path: string | undefined): FileConfig {
  const file = path ?? DEFAULT_CONFIG_FILE;

  try {
    return JSON.parse(readFileSync(file, "utf8")) as FileConfig;
  } catch (err) {
    if ((err as NodeJS.ErrnoException).code === "ENOENT") {
      return {};
    }

    throw new Error(`Failed to read config file ${file}: ${String(err)}`);
  }
}

function requireValue(value: string | undefined, source: string): string {
  if (!value) {
    throw new Error(`Missing required configuration: ${source}`);
  }

  return value;
}

/**
 * Per-adapter enablement overrides, from the file's `adapters` map plus any
 * `ENABLE_<NAME>` env var (env wins). Unknown names are harmless — the registry
 * only consults the adapters it knows.
 */
function resolveAdapters(env: NodeJS.ProcessEnv, file: FileConfig): Record<string, boolean> {
  const adapters: Record<string, boolean> = { ...file.adapters };

  for (const [key, value] of Object.entries(env)) {
    const name = /^ENABLE_(.+)$/.exec(key)?.[1];

    if (name && value !== undefined) {
      adapters[name.toLowerCase()] = boolFrom(value) ?? true;
    }
  }

  return adapters;
}

/** Merge a file config with environment variables (env wins). Pure and testable. */
export function resolveConfig(env: NodeJS.ProcessEnv, file: FileConfig): BridgeConfig {
  const clientId = env.OIDC_CLIENT_ID ?? file.oidc?.clientId;
  const oidc: OidcConfig | undefined = clientId
    ? {
        issuer: trimTrailingSlash(
          requireValue(env.OIDC_ISSUER ?? file.oidc?.issuer, "OIDC_ISSUER"),
        ),
        clientId,
        clientSecret: requireValue(
          env.OIDC_CLIENT_SECRET ?? file.oidc?.clientSecret,
          "OIDC_CLIENT_SECRET",
        ),
        scope: env.OIDC_SCOPE ?? file.oidc?.scope,
      }
    : undefined;

  return {
    hivegentUrl: trimTrailingSlash(
      requireValue(env.HIVEGENT_URL ?? file.hivegentUrl, "HIVEGENT_URL"),
    ),
    oidc,
    reasoningEffort: env.REASONING_EFFORT ?? file.reasoningEffort ?? "high",
    // The bot is read-only over documents and never writes cross-conversation memory.
    disabledTools: ["edit_document", "write_document", "save_memory"],
    postgresUrl: requireValue(env.POSTGRES_URL ?? file.postgresUrl, "POSTGRES_URL"),
    botUserName: env.BOT_USERNAME ?? file.botUserName ?? "hivegent",
    host: env.HOST ?? file.host ?? DEFAULT_HOST,
    port: env.PORT !== undefined ? Number(env.PORT) : (file.port ?? 3001),
    adapters: resolveAdapters(env, file),
  };
}

export function loadConfig(): BridgeConfig {
  return resolveConfig(process.env, readFileConfig(process.env.BRIDGE_CONFIG_FILE));
}
