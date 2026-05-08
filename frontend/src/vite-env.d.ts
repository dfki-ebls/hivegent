/// <reference types="vite/client" />

/**
 * Augments {@link ImportMetaEnv} with project-specific build-time variables.
 *
 * Keep `VITE_FEATURE_*` entries in sync with `FEATURE_ENV` in
 * `src/lib/feature-flags.ts`.
 */
interface ImportMetaEnv {
  readonly VITE_API_URL?: string;

  readonly VITE_FEATURE_LLM_SPEC?: string;
  readonly VITE_FEATURE_PIPELINE_SPEC?: string;
  readonly VITE_FEATURE_TOOLS_SPEC?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
