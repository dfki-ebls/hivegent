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

import { getSettings, MaintenanceError } from "@/lib/api";
import { featureFlags } from "@/lib/feature-flags";
import {
  AssetProcessingMode,
  AssetProcessingModeSchema,
  type AttachmentLimits,
  type BackendSettings,
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
  // Backend defaults, used only to display placeholders in the settings UI
  backendDefaults: BackendSettings | null;

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

  // Whether the backend rejected us with its maintenance gate. While
  // true, the root route shows the maintenance screen instead of the
  // app; the init loop keeps polling so it clears itself.
  maintenance: boolean;

  // LLM actions
  setOverride: (partial: Partial<UserOverrides>) => void;
  reset: () => void;
  initFromBackend: () => Promise<void>;

  // UI preference actions
  setConversionPipeline: (pipeline: ConversionPipeline) => void;
  setChunkingPipeline: (pipeline: ChunkingPipeline) => void;
  setAssetMode: (mode: AssetProcessingMode) => void;
  toggleExpandedDir: (path: string) => void;
  setExpandedDirs: (dirs: string[]) => void;
  setPersonality: (personality: Personality) => void;
  setCustomSystemMessage: (message: string) => void;
  setConversionConfig: (pipeline: string, config: Record<string, unknown>) => void;
  setChunkingConfig: (pipeline: string, config: Record<string, unknown>) => void;
  resetConversionConfig: (pipeline: string) => void;
  resetChunkingConfig: (pipeline: string) => void;
  setDisabledTools: (tools: string[]) => void;
  toggleTool: (toolName: string) => void;
  addMcpServer: (server: McpServerEntry) => void;
  removeMcpServer: (index: number) => void;
  updateMcpServer: (index: number, server: McpServerEntry) => void;
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
      backendDefaults: null,
      overrides: EMPTY_OVERRIDES,
      maintenance: false,
      ...UI_DEFAULTS,

      setOverride: (partial) =>
        set((state) => ({
          overrides: { ...state.overrides, ...partial },
        })),

      reset: () => set({ overrides: EMPTY_OVERRIDES }),

      initFromBackend: async () => {
        // getSettings goes through authFetch, which already gates on backend
        // readiness, so the first attempt never races a booting backend. Keep
        // retrying on failure; during maintenance this doubles as the recovery
        // poll, so the app loads by itself once an admin turns the mode back off.
        while (true) {
          try {
            const defaults = await getSettings();
            set({ maintenance: false, backendDefaults: defaults });
            return;
          } catch (e) {
            const maintenance = e instanceof MaintenanceError;
            if (maintenance) {
              set({ maintenance: true });
            }
            await new Promise((resolve) => setTimeout(resolve, maintenance ? 5000 : 1000));
          }
        }
      },

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

      setConversionConfig: (pipeline, config) =>
        set((state) => ({
          conversionConfigs: { ...state.conversionConfigs, [pipeline]: config },
        })),

      setChunkingConfig: (pipeline, config) =>
        set((state) => ({
          chunkingConfigs: { ...state.chunkingConfigs, [pipeline]: config },
        })),

      resetConversionConfig: (pipeline) =>
        set((state) => {
          const { [pipeline]: _, ...rest } = state.conversionConfigs;
          return { conversionConfigs: rest };
        }),

      resetChunkingConfig: (pipeline) =>
        set((state) => {
          const { [pipeline]: _, ...rest } = state.chunkingConfigs;
          return { chunkingConfigs: rest };
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

      removeMcpServer: (index) =>
        set((state) => ({
          toolsSpec: {
            ...state.toolsSpec,
            mcpServers: state.toolsSpec.mcpServers.filter((_, i) => i !== index),
          },
        })),

      updateMcpServer: (index, server) =>
        set((state) => ({
          toolsSpec: {
            ...state.toolsSpec,
            mcpServers: state.toolsSpec.mcpServers.map((s, i) => (i === index ? server : s)),
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

/** The fixed role name that grants administrator privileges. */
const ADMIN_ROLE = "admin";

/**
 * Zustand selector: whether the user is an administrator.
 *
 * Derived from the fixed `admin` role in the user's roles — same rule as
 * the server's `User.is_admin` property.
 */
export function selectIsAdmin(state: SettingsState): boolean {
  return state.backendDefaults?.user.roles.includes(ADMIN_ROLE) ?? false;
}

/**
 * Zustand selector: the current user's backend id, if known.
 *
 * Shares the id space of the admin user listings, so it can filter the
 * caller out of self-targeting lists (impersonation, per-user wipes).
 */
export function selectUserId(state: SettingsState): string | undefined {
  return state.backendDefaults?.user.id;
}

/**
 * Zustand selector: the constraints the composer enforces on attachments.
 *
 * Undefined until the settings load, a sub-second window in which the
 * composer simply does not filter and the server rejects what it must.
 */
export function selectAttachmentLimits(state: SettingsState): AttachmentLimits | undefined {
  return state.backendDefaults?.attachments;
}
