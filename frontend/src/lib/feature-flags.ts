/**
 * Build-time feature flags.
 *
 * Flags live only in the frontend and are baked in at build time from
 * `VITE_FEATURE_*` environment variables.  When a flag disables a feature,
 * the UI for it is hidden and the matching request payload is omitted, so
 * the backend behavior follows implicitly without a parallel flag system.
 *
 * Set `VITE_FEATURE_ALL` to flip every flag at once; per-flag env vars still
 * win when set, so you can enable everything and then disable a single flag.
 *
 * Add a new flag by:
 *   1. Adding a typed property to {@link FeatureFlags}.
 *   2. Adding a default to {@link FEATURE_DEFAULTS}.
 *   3. Adding the env var entry to {@link FEATURE_ENV} below — Vite replaces
 *      `import.meta.env.VITE_FEATURE_*` references at build time, so each
 *      lookup must be a direct property access (not computed).
 *   4. Adding the env var declaration to `src/vite-env.d.ts` so TypeScript
 *      knows about it.
 *   5. Gating every surface the feature touches:
 *        - UI: hide controls and skip data fetches that drive them.
 *        - Wire: zero out the matching request payload (single chokepoint
 *          per feature, e.g. `buildToolsPayload` in `lib/api.ts`).
 *        - Persistence: omit the feature's slice from zustand `partialize`
 *          and force the empty default in `merge`, so localStorage never
 *          retains data — including secrets — for a disabled feature, and
 *          stale entries from a build where the flag was on don't leak in
 *          when it's flipped off.  Apply the same to any other persistent
 *          stores (IndexedDB, cookies, OPFS) the feature uses.
 */

import { z } from "zod";

/** Typed feature flag set.  All flags are booleans. */
export interface FeatureFlags {
  /**
   * Allow users to override the LLM provider (model, API key, base URL,
   * auxiliary model) from the settings dialog.  When disabled, the Model
   * column is hidden and outgoing requests omit overrides, so the backend
   * always uses its configured defaults.
   */
  llmSpec: boolean;

  /**
   * Allow users to choose conversion + chunking pipelines and tweak their
   * configuration.  When disabled, the pipeline selectors are hidden and
   * an empty {@link PipelineSpec} is sent for every upload/reconvert/
   * rechunk, so the backend picks AUTO for both.
   */
  pipelineSpec: boolean;

  /**
   * Allow users to pick how extracted assets (images, etc.) are handled
   * during ingestion — ignore, store, or describe.  When disabled, the
   * Assets selector is hidden and `process_assets` is omitted from every
   * request, so the backend applies its STORE default.
   */
  assetSpec: boolean;

  /**
   * Allow users to toggle built-in tools and configure custom MCP servers
   * from the settings dialog.  When disabled, the Tools + MCP Servers
   * section is hidden and no per-user tool configuration is sent to the
   * backend, so the agent runs with all built-in tools enabled and no
   * custom MCP servers.
   */
  toolsSpec: boolean;

  /**
   * Allow users to switch the agent into plan mode from the composer.
   * When disabled, the Mode selector is hidden and outgoing requests
   * always send `mode: "execute"`, so the backend never appends the plan
   * instructions and the "Execute the plan" follow-up never appears.
   */
  planning: boolean;
}

const FEATURE_DEFAULTS: FeatureFlags = {
  llmSpec: false,
  pipelineSpec: false,
  assetSpec: false,
  toolsSpec: false,
  planning: false,
};

/**
 * Map of flags to their raw env var values.
 *
 * Each entry MUST use a literal `import.meta.env.VITE_FEATURE_*` lookup so
 * Vite can statically replace it during the build.
 */
const FEATURE_ENV: Record<keyof FeatureFlags, string | undefined> = {
  llmSpec: import.meta.env.VITE_FEATURE_LLM_SPEC,
  pipelineSpec: import.meta.env.VITE_FEATURE_PIPELINE_SPEC,
  assetSpec: import.meta.env.VITE_FEATURE_ASSET_SPEC,
  toolsSpec: import.meta.env.VITE_FEATURE_TOOLS_SPEC,
  planning: import.meta.env.VITE_FEATURE_PLANNING,
};

function parseBool(raw: string | undefined, key: string): boolean | undefined {
  if (raw === undefined || raw === "") return undefined;
  const parsed = z.stringbool().safeParse(raw);
  if (parsed.success) return parsed.data;
  console.warn(`Invalid boolean "${raw}" for feature flag "${key}", ignoring`);
  return undefined;
}

function resolveFeatureFlags(): FeatureFlags {
  const master = parseBool(import.meta.env.VITE_FEATURE_ALL, "ALL");
  const resolved = {} as FeatureFlags;
  for (const key of Object.keys(FEATURE_DEFAULTS) as (keyof FeatureFlags)[]) {
    resolved[key] = parseBool(FEATURE_ENV[key], key) ?? master ?? FEATURE_DEFAULTS[key];
  }
  return resolved;
}

/** Resolved feature flags for the current build. */
export const featureFlags: FeatureFlags = resolveFeatureFlags();
