/**
 * Configuration: one JSON file of non-secret settings validated by `FileSchema`,
 * read from `BRIDGE_CONFIG_FILE` (default `config.json`), plus the secrets named
 * in `resolveConfig`, each read from its environment variable or else from the
 * credential file `hivegent-bridge.<NAME>` in `$CREDENTIALS_DIRECTORY`.
 */

import { readFileSync } from "node:fs";
import { join } from "node:path";

import { z } from "zod";

const nonEmpty = z.string().min(1);
const httpUrl = z.url({ protocol: /^https?$/ });

// Paths are appended to these, so a trailing slash would double up.
const baseUrl = httpUrl.refine((url) => !url.endsWith("/"), "must not end with a slash");

/** Whether *host* names the loopback interface, as a URL hostname with IPv6 brackets or a bare bind address. */
function isLoopback(host: string): boolean {
  return ["localhost", "127.0.0.1", "[::1]", "::1"].includes(host) || host.endsWith(".localhost");
}

/** Whether *url* is https, or http on a loopback host alone, the rule the backend applies to its IdP. */
export function isSecureEndpoint(url: URL): boolean {
  return url.protocol === "https:" || (url.protocol === "http:" && isLoopback(url.hostname));
}

const secureUrl = httpUrl.refine(
  (url) => isSecureEndpoint(new URL(url)),
  "must be https, or http on a loopback host",
);

export const FileSchema = z
  .strictObject({
    backendUrl: baseUrl,
    host: nonEmpty.default("127.0.0.1"),
    port: z.int().min(1).max(65535).default(3003),
    botUserName: nonEmpty.default("hivegent"),
    /** Absent for local debugging against `hivegent dev`, which the Teams adapter refuses. */
    oidc: z
      // The issuer exactly as the IdP names it in `iss`, trailing slash included.
      .strictObject({ issuer: secureUrl, clientId: nonEmpty, scope: nonEmpty.optional() })
      .optional(),
    adapters: z.strictObject({ teams: z.boolean(), web: z.boolean().default(false) }),
    /** The Teams app and the Entra tenant ids whose activities it answers, every other refused. */
    teams: z
      .strictObject({
        appId: nonEmpty,
        tenantId: nonEmpty,
        allowedTenants: z.array(nonEmpty).min(1),
      })
      .optional(),
  })
  .refine((cfg) => !cfg.adapters.web || (!cfg.oidc && isLoopback(cfg.host)), {
    // Unauthenticated, so it may only reach a backend that needs no credentials, and only from this machine.
    message:
      "The web adapter answers anyone, so it runs only without oidc on a loopback host, against `hivegent dev`",
    path: ["adapters", "web"],
  });

export type FileConfig = z.infer<typeof FileSchema>;
export type OidcConfig = NonNullable<FileConfig["oidc"]> & { clientSecret: string };
export type TeamsConfig = NonNullable<FileConfig["teams"]> & { appPassword: string };

export type BridgeConfig = Omit<FileConfig, "oidc" | "teams"> & {
  postgresUrl: string;
  oidc?: OidcConfig;
  teams?: TeamsConfig;
};

/** Looks up a secret by name, undefined when it is not set. */
export type SecretSource = (name: string) => string | undefined;

/** Secrets from the environment, else from credential files in `$CREDENTIALS_DIRECTORY`. */
export function secretSource(env: NodeJS.ProcessEnv): SecretSource {
  const dir = env.CREDENTIALS_DIRECTORY;

  return (name) => {
    const value = env[name];

    if (value) {
      return value;
    }

    if (!dir) {
      return undefined;
    }

    try {
      return readFileSync(join(dir, `hivegent-bridge.${name}`), "utf8").trim() || undefined;
    } catch (err) {
      if ((err as NodeJS.ErrnoException).code === "ENOENT") {
        return undefined;
      }

      throw err;
    }
  };
}

function requireSecret(secrets: SecretSource, name: string): string {
  const value = secrets(name);

  if (!value) {
    throw new Error(`Missing secret ${name}`);
  }

  return value;
}

/** Validate the parsed config file and add the secrets its sections need. */
export function resolveConfig(file: unknown, secrets: SecretSource): BridgeConfig {
  const { oidc, teams, ...rest } = FileSchema.parse(file);

  return {
    ...rest,
    postgresUrl: requireSecret(secrets, "POSTGRES_URL"),
    oidc: oidc && { ...oidc, clientSecret: requireSecret(secrets, "OIDC_CLIENT_SECRET") },
    teams: teams && { ...teams, appPassword: requireSecret(secrets, "TEAMS_APP_PASSWORD") },
  };
}

export function loadConfig(): BridgeConfig {
  const path = process.env.BRIDGE_CONFIG_FILE ?? "config.json";
  const file: unknown = JSON.parse(readFileSync(path, "utf8"));

  return resolveConfig(file, secretSource(process.env));
}
