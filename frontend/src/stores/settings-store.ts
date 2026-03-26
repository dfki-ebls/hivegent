/**
 * Zustand store for all persisted user settings.
 *
 * Combines LLM configuration (model overrides, API keys, available models)
 * with UI preferences (active tab, pipeline selections, expanded directories)
 * into a single persisted store with Zod-validated rehydration.
 */

import { z } from "zod";
import { create } from "zustand";
import { createJSONStorage, persist, type StorageValue } from "zustand/middleware";

import { getSettings } from "../lib/api";
import { decryptApiKey, encryptApiKey, isEncrypted } from "../lib/crypto";
import {
  type BackendSettings,
  ChunkingPipeline,
  ChunkingPipelineSchema,
  ConversionPipeline,
  ConversionPipelineSchema,
  type DocumentTab,
  DocumentTabSchema,
  ExpandedDirsSchema,
  type McpServerEntry,
  type Personality,
  PersonalitySchema,
  PipelineConfigsSchema,
  type ToolsSpec,
  ToolsSpecSchema,
  type UserOverrides,
  UserOverridesSchema,
} from "../lib/types";

export interface LLMSettings {
  model: string;
  apiKey: string;
  baseUrl: string;
}

const EMPTY_OVERRIDES: UserOverrides = {
  model: "",
  apiKey: "",
  baseUrl: "",
  smallModel: "",
  visionModel: "",
};

/** Per-pipeline configuration overrides, keyed by pipeline value. */
export type PipelineConfigs = Record<string, Record<string, unknown>>;

const UI_DEFAULTS = {
  documentTab: "fetched" as DocumentTab,
  conversionPipeline: ConversionPipeline.AUTO,
  chunkingPipeline: ChunkingPipeline.AUTO,
  expandedDirs: [""] as string[],
  personality: "default" as Personality,
  customSystemMessage: "",
  processAssets: true,
  conversionConfigs: {} as PipelineConfigs,
  chunkingConfigs: {} as PipelineConfigs,
  toolsSpec: { disabledTools: [], mcpServers: [] } as ToolsSpec,
};

interface SettingsState {
  // Backend defaults (not persisted)
  backendDefaults: BackendSettings | null;

  // User LLM overrides (persisted)
  overrides: UserOverrides;

  // UI preferences (persisted)
  documentTab: DocumentTab;
  conversionPipeline: ConversionPipeline;
  chunkingPipeline: ChunkingPipeline;
  processAssets: boolean;
  expandedDirs: string[];
  personality: Personality;
  customSystemMessage: string;
  conversionConfigs: PipelineConfigs;
  chunkingConfigs: PipelineConfigs;
  toolsSpec: ToolsSpec;

  // User context (from backend, not persisted)
  readGroups: string[];
  writeGroups: string[];

  // Computed effective values (backend default + user override)
  llm: LLMSettings;
  smallModel: string;
  visionModel: string;
  hasServerApiKey: boolean;

  // LLM actions
  setLLM: (settings: Partial<LLMSettings>) => void;
  setSmallModel: (model: string) => void;
  setVisionModel: (model: string) => void;
  reset: () => void;
  initFromBackend: () => Promise<void>;

  // UI preference actions
  setDocumentTab: (tab: DocumentTab) => void;
  setConversionPipeline: (pipeline: ConversionPipeline) => void;
  setChunkingPipeline: (pipeline: ChunkingPipeline) => void;
  setProcessAssets: (value: boolean) => void;
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

/** Recompute effective values from backend defaults and user overrides. */
function computeEffective(
  defaults: BackendSettings | null,
  overrides: UserOverrides,
): Pick<SettingsState, "llm" | "smallModel" | "visionModel" | "hasServerApiKey"> {
  return {
    llm: {
      model: overrides.model || defaults?.model || "",
      apiKey: overrides.apiKey,
      baseUrl: overrides.baseUrl || defaults?.base_url || "",
    },
    smallModel: overrides.smallModel || defaults?.small_model || "",
    visionModel: overrides.visionModel || defaults?.vision_model || "",
    hasServerApiKey: defaults?.has_api_key ?? false,
  };
}

/** Shape of the partialized state written to localStorage. */
interface PersistedSettings {
  overrides: UserOverrides;
  documentTab: DocumentTab;
  conversionPipeline: ConversionPipeline;
  chunkingPipeline: ChunkingPipeline;
  processAssets: boolean;
  expandedDirs: string[];
  personality: Personality;
  customSystemMessage: string;
  conversionConfigs: PipelineConfigs;
  chunkingConfigs: PipelineConfigs;
  toolsSpec: ToolsSpec;
}

/** Encrypt a single string value if it is non-empty and not already encrypted. */
async function tryEncrypt(value: string): Promise<string> {
  if (!value || isEncrypted(value)) return value;
  return encryptApiKey(value);
}

/** Decrypt a single string value if it looks encrypted. */
async function tryDecrypt(value: string): Promise<string> {
  if (!isEncrypted(value)) return value;
  return decryptApiKey(value);
}

/** Encrypt sensitive fields inside MCP server entries (header values, OAuth2 secrets). */
async function encryptMcpServers(servers: McpServerEntry[]): Promise<McpServerEntry[]> {
  return Promise.all(
    servers.map(async (s) => {
      const encHeaders: Record<string, string> = {};
      for (const [k, v] of Object.entries(s.headers)) {
        encHeaders[k] = await tryEncrypt(v);
      }
      return {
        ...s,
        headers: encHeaders,
        oauth2: s.oauth2
          ? { ...s.oauth2, clientSecret: await tryEncrypt(s.oauth2.clientSecret) }
          : undefined,
      };
    }),
  );
}

/** Decrypt sensitive fields inside MCP server entries. */
async function decryptMcpServers(servers: McpServerEntry[]): Promise<McpServerEntry[]> {
  return Promise.all(
    servers.map(async (s) => {
      const decHeaders: Record<string, string> = {};
      for (const [k, v] of Object.entries(s.headers)) {
        decHeaders[k] = await tryDecrypt(v);
      }
      return {
        ...s,
        headers: decHeaders,
        oauth2: s.oauth2
          ? { ...s.oauth2, clientSecret: await tryDecrypt(s.oauth2.clientSecret) }
          : undefined,
      };
    }),
  );
}

/**
 * Custom storage that encrypts sensitive values before writing to localStorage
 * and decrypts them on read.  Covers the LLM API key and MCP server secrets
 * (header values, OAuth2 client secrets).
 */
const encryptedStorage = createJSONStorage(() => ({
  getItem: async (name: string): Promise<string | null> => {
    const raw = localStorage.getItem(name);
    if (!raw) return null;

    let stored: StorageValue<PersistedSettings>;
    try {
      stored = JSON.parse(raw) as StorageValue<PersistedSettings>;
    } catch {
      console.warn("Corrupted settings in localStorage, resetting to defaults");
      return null;
    }

    // Decrypt LLM API key
    const apiKey = stored.state.overrides.apiKey;
    if (typeof apiKey === "string" && isEncrypted(apiKey)) {
      try {
        stored.state.overrides.apiKey = await decryptApiKey(apiKey);
      } catch (err) {
        console.warn("Failed to decrypt API key, clearing stored value:", err);
        stored.state.overrides.apiKey = "";
      }
    }

    // Decrypt MCP server secrets
    if (stored.state.toolsSpec?.mcpServers?.length) {
      try {
        stored.state.toolsSpec.mcpServers = await decryptMcpServers(
          stored.state.toolsSpec.mcpServers,
        );
      } catch (err) {
        console.warn("Failed to decrypt MCP server secrets:", err);
      }
    }

    return JSON.stringify(stored);
  },

  setItem: async (name: string, value: string): Promise<void> => {
    const stored = JSON.parse(value) as StorageValue<PersistedSettings>;

    // Encrypt LLM API key
    const apiKey = stored.state.overrides.apiKey;
    if (typeof apiKey === "string" && apiKey && !isEncrypted(apiKey)) {
      try {
        stored.state.overrides.apiKey = await encryptApiKey(apiKey);
      } catch (err) {
        console.warn("Failed to encrypt API key, storing in plain text:", err);
      }
    }

    // Encrypt MCP server secrets
    if (stored.state.toolsSpec?.mcpServers?.length) {
      try {
        stored.state.toolsSpec.mcpServers = await encryptMcpServers(
          stored.state.toolsSpec.mcpServers,
        );
      } catch (err) {
        console.warn("Failed to encrypt MCP server secrets:", err);
      }
    }

    localStorage.setItem(name, JSON.stringify(stored));
  },

  removeItem: async (name: string): Promise<void> => {
    localStorage.removeItem(name);
  },
}));

export const useSettingsStore = create<SettingsState>()(
  persist(
    (set) => ({
      backendDefaults: null,
      overrides: EMPTY_OVERRIDES,
      readGroups: [],
      writeGroups: [],
      ...UI_DEFAULTS,

      // Initial computed values (no backend defaults yet)
      ...computeEffective(null, EMPTY_OVERRIDES),

      setLLM: (settings) =>
        set((state) => {
          const newOverrides = {
            ...state.overrides,
            ...(settings.model !== undefined ? { model: settings.model } : {}),
            ...(settings.apiKey !== undefined ? { apiKey: settings.apiKey } : {}),
            ...(settings.baseUrl !== undefined ? { baseUrl: settings.baseUrl } : {}),
          };
          return {
            overrides: newOverrides,
            ...computeEffective(state.backendDefaults, newOverrides),
          };
        }),

      setSmallModel: (model) =>
        set((state) => {
          const newOverrides = { ...state.overrides, smallModel: model };
          return {
            overrides: newOverrides,
            ...computeEffective(state.backendDefaults, newOverrides),
          };
        }),

      setVisionModel: (model) =>
        set((state) => {
          const newOverrides = { ...state.overrides, visionModel: model };
          return {
            overrides: newOverrides,
            ...computeEffective(state.backendDefaults, newOverrides),
          };
        }),

      reset: () =>
        set((state) => ({
          overrides: EMPTY_OVERRIDES,
          ...computeEffective(state.backendDefaults, EMPTY_OVERRIDES),
        })),

      initFromBackend: async () => {
        try {
          const defaults = await getSettings();
          set((state) => ({
            backendDefaults: defaults,
            readGroups: defaults.user.read_groups,
            writeGroups: defaults.user.write_groups,
            ...computeEffective(defaults, state.overrides),
          }));
        } catch {
          // Silently fail — keep existing values
        }
      },

      setDocumentTab: (tab) => set({ documentTab: tab }),

      setConversionPipeline: (pipeline) => set({ conversionPipeline: pipeline }),

      setChunkingPipeline: (pipeline) => set({ chunkingPipeline: pipeline }),

      setProcessAssets: (processAssets) => set({ processAssets }),

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
      storage: encryptedStorage,
      partialize: (state) => ({
        overrides: state.overrides,
        documentTab: state.documentTab,
        conversionPipeline: state.conversionPipeline,
        chunkingPipeline: state.chunkingPipeline,
        processAssets: state.processAssets,
        expandedDirs: state.expandedDirs,
        personality: state.personality,
        customSystemMessage: state.customSystemMessage,
        conversionConfigs: state.conversionConfigs,
        chunkingConfigs: state.chunkingConfigs,
        toolsSpec: state.toolsSpec,
      }),
      merge: (persisted, current) => {
        const data = persisted as Record<string, unknown> | undefined;
        if (!data) return current;

        const overrides = UserOverridesSchema.safeParse(data.overrides).data ?? EMPTY_OVERRIDES;

        // Migrate from flat disabledTools to toolsSpec
        let toolsSpec = ToolsSpecSchema.safeParse(data.toolsSpec).data;
        if (!toolsSpec) {
          const legacyDisabled = z.array(z.string()).safeParse(data.disabledTools).data;
          toolsSpec = {
            disabledTools: legacyDisabled ?? [],
            mcpServers: [],
          };
        }

        return {
          ...current,
          overrides,
          documentTab:
            DocumentTabSchema.safeParse(data.documentTab).data ?? UI_DEFAULTS.documentTab,
          conversionPipeline:
            ConversionPipelineSchema.safeParse(data.conversionPipeline).data ??
            UI_DEFAULTS.conversionPipeline,
          chunkingPipeline:
            ChunkingPipelineSchema.safeParse(data.chunkingPipeline).data ??
            UI_DEFAULTS.chunkingPipeline,
          processAssets:
            z.boolean().safeParse(data.processAssets).data ?? UI_DEFAULTS.processAssets,
          expandedDirs:
            ExpandedDirsSchema.safeParse(data.expandedDirs).data ?? UI_DEFAULTS.expandedDirs,
          personality:
            PersonalitySchema.safeParse(data.personality).data ?? UI_DEFAULTS.personality,
          customSystemMessage:
            z.string().safeParse(data.customSystemMessage).data ??
            UI_DEFAULTS.customSystemMessage,
          conversionConfigs:
            PipelineConfigsSchema.safeParse(data.conversionConfigs).data ??
            UI_DEFAULTS.conversionConfigs,
          chunkingConfigs:
            PipelineConfigsSchema.safeParse(data.chunkingConfigs).data ??
            UI_DEFAULTS.chunkingConfigs,
          toolsSpec,
          ...computeEffective(current.backendDefaults, overrides),
        };
      },
    },
  ),
);

/** Return all groups the user belongs to (read + write). */
export function getAllGroups(): string[] {
  const { readGroups, writeGroups } = useSettingsStore.getState();
  const union = new Set([...readGroups, ...writeGroups]);
  return [...union].sort();
}

/** Check whether the user has write access to a group. */
export function canWriteGroup(groupId: string): boolean {
  return useSettingsStore.getState().writeGroups.includes(groupId);
}
