/**
 * Zustand store for all persisted user settings.
 *
 * Holds LLM override strings (empty = no override; backend applies its own
 * default at request time) alongside UI preferences.
 *
 * Sensitive fields stay in memory only; persisted state is Zod-validated and
 * stripped of BYO API keys, MCP headers, and OAuth client secrets.
 */

import { z } from "zod";
import { create } from "zustand";
import { createJSONStorage, persist } from "zustand/middleware";

import { featureFlags } from "@/lib/feature-flags";
import {
  AssetProcessingMode,
  AssetProcessingModeSchema,
  ChunkingPipeline,
  ChunkingPipelineSchema,
  ConversionPipeline,
  ConversionPipelineSchema,
  ExpandedDirsSchema,
  type McpServerEntry,
  type Personality,
  PersonalitySchema,
  type PersistedOverrides,
  PersistedOverridesSchema,
  type PersistedToolsSpec,
  PersistedToolsSpecSchema,
  PipelineConfigsSchema,
  type PipelineKind,
  type ToolsSpec,
  type UserOverrides,
} from "@/lib/types";

const EMPTY_OVERRIDES: UserOverrides = {
  model: "",
  apiKey: "",
  baseUrl: "",
  auxModel: "",
};

/** Per-pipeline configuration overrides, keyed by pipeline value. */
export type PipelineConfigs = Record<string, Record<string, unknown>>;

/** The state field holding each pipeline kind's per-pipeline configs. */
export const PIPELINE_CONFIG_FIELDS = {
  conversion: "conversionConfigs",
  chunking: "chunkingConfigs",
} as const satisfies Record<PipelineKind, string>;

/**
 * Exact shape written to localStorage — the persist middleware's persisted-state
 * type. Flag-gated slices are optional (omitted when their flag is off), and the
 * secret fields are absent by construction via {@link PersistedOverrides} and
 * {@link PersistedToolsSpec}, so the `partialize` return below cannot leak them.
 */
interface PersistedSettings {
  expandedDirs: string[];
  personality: Personality;
  customSystemMessage: string;
  overrides?: PersistedOverrides;
  conversionPipeline?: ConversionPipeline;
  chunkingPipeline?: ChunkingPipeline;
  conversionConfigs?: PipelineConfigs;
  chunkingConfigs?: PipelineConfigs;
  assetMode?: AssetProcessingMode;
  toolsSpec?: PersistedToolsSpec;
}

const UI_DEFAULTS = {
  conversionPipeline: ConversionPipeline.AUTO,
  chunkingPipeline: ChunkingPipeline.AUTO,
  expandedDirs: [""] as string[],
  personality: "default" as Personality,
  customSystemMessage: "",
  assetMode: AssetProcessingMode.STORE,
  conversionConfigs: {} as PipelineConfigs,
  chunkingConfigs: {} as PipelineConfigs,
  toolsSpec: { disabledTools: [], mcpServers: [] } as ToolsSpec,
};

interface SettingsState {
  // User LLM overrides (persisted). Empty string means "no override".
  overrides: UserOverrides;

  // UI preferences (persisted)
  conversionPipeline: ConversionPipeline;
  chunkingPipeline: ChunkingPipeline;
  assetMode: AssetProcessingMode;
  expandedDirs: string[];
  personality: Personality;
  customSystemMessage: string;
  conversionConfigs: PipelineConfigs;
  chunkingConfigs: PipelineConfigs;
  toolsSpec: ToolsSpec;

  // LLM actions
  setOverride: (partial: Partial<UserOverrides>) => void;
  reset: () => void;

  // UI preference actions
  setConversionPipeline: (pipeline: ConversionPipeline) => void;
  setChunkingPipeline: (pipeline: ChunkingPipeline) => void;
  setAssetMode: (mode: AssetProcessingMode) => void;
  toggleExpandedDir: (path: string) => void;
  setExpandedDirs: (dirs: string[]) => void;
  setPersonality: (personality: Personality) => void;
  setCustomSystemMessage: (message: string) => void;
  setPipelineConfig: (
    kind: PipelineKind,
    pipeline: string,
    config: Record<string, unknown>,
  ) => void;
  resetPipelineConfig: (kind: PipelineKind, pipeline: string) => void;
  setDisabledTools: (tools: string[]) => void;
  toggleTool: (toolName: string) => void;
  addMcpServer: (server: McpServerEntry) => void;
  removeMcpServer: (id: string) => void;
}

// The persisted types omit the secret fields outright (apiKey; MCP header
// values and the oauth2 block), so the compiler rejects any attempt to store
// them. Both helpers build their result field-by-field — an allowlist — so a
// future sensitive field is dropped from storage until consciously added here.
function persistedOverrides(overrides: UserOverrides): PersistedOverrides {
  return { model: overrides.model, baseUrl: overrides.baseUrl, auxModel: overrides.auxModel };
}

function persistedToolsSpec(toolsSpec: ToolsSpec): PersistedToolsSpec {
  return {
    disabledTools: toolsSpec.disabledTools,
    mcpServers: toolsSpec.mcpServers.map((server) => ({
      id: server.id,
      url: server.url,
      toolPrefix: server.toolPrefix,
    })),
  };
}

// Restore the in-memory ToolsSpec from its persisted form: the omitted secret
// fields come back empty (matching a freshly-added server before the user
// re-enters them this session).
function rehydrateToolsSpec(spec: PersistedToolsSpec): ToolsSpec {
  return {
    disabledTools: spec.disabledTools,
    mcpServers: spec.mcpServers.map((server) => ({ ...server, headers: {} })),
  };
}

export const useSettingsStore = create<SettingsState>()(
  persist(
    (set) => ({
      overrides: EMPTY_OVERRIDES,
      ...UI_DEFAULTS,

      setOverride: (partial) =>
        set((state) => ({
          overrides: { ...state.overrides, ...partial },
        })),

      reset: () => set({ overrides: EMPTY_OVERRIDES }),

      setConversionPipeline: (pipeline) => set({ conversionPipeline: pipeline }),

      setChunkingPipeline: (pipeline) => set({ chunkingPipeline: pipeline }),

      setAssetMode: (assetMode) => set({ assetMode }),

      toggleExpandedDir: (path) =>
        set((state) => {
          const dirs = new Set(state.expandedDirs);
          if (dirs.has(path)) {
            dirs.delete(path);
          } else {
            dirs.add(path);
          }
          return { expandedDirs: [...dirs] };
        }),

      setExpandedDirs: (dirs) => set({ expandedDirs: dirs }),

      setPersonality: (personality) => set({ personality }),

      setCustomSystemMessage: (customSystemMessage) => set({ customSystemMessage }),

      setPipelineConfig: (kind, pipeline, config) =>
        set((state) => {
          const field = PIPELINE_CONFIG_FIELDS[kind];
          return { [field]: { ...state[field], [pipeline]: config } };
        }),

      resetPipelineConfig: (kind, pipeline) =>
        set((state) => {
          const field = PIPELINE_CONFIG_FIELDS[kind];
          const { [pipeline]: _, ...rest } = state[field];
          return { [field]: rest };
        }),

      setDisabledTools: (tools) =>
        set((state) => ({
          toolsSpec: { ...state.toolsSpec, disabledTools: tools },
        })),

      toggleTool: (toolName) =>
        set((state) => {
          const disabled = new Set(state.toolsSpec.disabledTools);
          if (disabled.has(toolName)) {
            disabled.delete(toolName);
          } else {
            disabled.add(toolName);
          }
          return {
            toolsSpec: { ...state.toolsSpec, disabledTools: [...disabled] },
          };
        }),

      addMcpServer: (server) =>
        set((state) => ({
          toolsSpec: {
            ...state.toolsSpec,
            mcpServers: [...state.toolsSpec.mcpServers, server],
          },
        })),

      removeMcpServer: (id) =>
        set((state) => ({
          toolsSpec: {
            ...state.toolsSpec,
            mcpServers: state.toolsSpec.mcpServers.filter((server) => server.id !== id),
          },
        })),
    }),
    {
      name: "hivegent-settings",
      storage: createJSONStorage(() => localStorage),
      partialize: (state): PersistedSettings => ({
        expandedDirs: state.expandedDirs,
        personality: state.personality,
        customSystemMessage: state.customSystemMessage,
        // Each block below is gated by its feature flag.  When off, the
        // slice is omitted from persisted state so secrets and stale user
        // input never touch localStorage and don't survive a flag flip.
        ...(featureFlags.llmSpec ? { overrides: persistedOverrides(state.overrides) } : {}),
        ...(featureFlags.pipelineSpec
          ? {
              conversionPipeline: state.conversionPipeline,
              chunkingPipeline: state.chunkingPipeline,
              conversionConfigs: state.conversionConfigs,
              chunkingConfigs: state.chunkingConfigs,
            }
          : {}),
        ...(featureFlags.assetSpec ? { assetMode: state.assetMode } : {}),
        ...(featureFlags.toolsSpec ? { toolsSpec: persistedToolsSpec(state.toolsSpec) } : {}),
      }),
      merge: (persisted, current) => {
        const data = persisted as Record<string, unknown> | undefined;
        if (!data) return current;
        const pick = <T>(schema: z.ZodType<T>, value: unknown, fallback: T): T =>
          schema.safeParse(value).data ?? fallback;

        // Flag-gated slices mirror `partialize`: when off, fall through to
        // `current`'s defaults so stale data from a previous build with the
        // flag on is ignored.
        return {
          ...current,
          expandedDirs: pick(ExpandedDirsSchema, data.expandedDirs, UI_DEFAULTS.expandedDirs),
          personality: pick(PersonalitySchema, data.personality, UI_DEFAULTS.personality),
          customSystemMessage: pick(
            z.string(),
            data.customSystemMessage,
            UI_DEFAULTS.customSystemMessage,
          ),
          ...(featureFlags.llmSpec
            ? {
                overrides: {
                  ...EMPTY_OVERRIDES,
                  ...pick(
                    PersistedOverridesSchema,
                    data.overrides,
                    persistedOverrides(EMPTY_OVERRIDES),
                  ),
                },
              }
            : {}),
          ...(featureFlags.pipelineSpec
            ? {
                conversionPipeline: pick(
                  ConversionPipelineSchema,
                  data.conversionPipeline,
                  UI_DEFAULTS.conversionPipeline,
                ),
                chunkingPipeline: pick(
                  ChunkingPipelineSchema,
                  data.chunkingPipeline,
                  UI_DEFAULTS.chunkingPipeline,
                ),
                conversionConfigs: pick(
                  PipelineConfigsSchema,
                  data.conversionConfigs,
                  UI_DEFAULTS.conversionConfigs,
                ),
                chunkingConfigs: pick(
                  PipelineConfigsSchema,
                  data.chunkingConfigs,
                  UI_DEFAULTS.chunkingConfigs,
                ),
              }
            : {}),
          ...(featureFlags.assetSpec
            ? { assetMode: pick(AssetProcessingModeSchema, data.assetMode, UI_DEFAULTS.assetMode) }
            : {}),
          ...(featureFlags.toolsSpec
            ? {
                toolsSpec: rehydrateToolsSpec(
                  pick(
                    PersistedToolsSpecSchema,
                    data.toolsSpec,
                    persistedToolsSpec(UI_DEFAULTS.toolsSpec),
                  ),
                ),
              }
            : {}),
        };
      },
    },
  ),
);
