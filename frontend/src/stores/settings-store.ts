/**
 * Zustand store for all persisted user settings.
 *
 * Holds raw LLM override strings (empty = no override; backend applies its
 * own default at request time) alongside UI preferences, persisted with
 * Zod-validated rehydration.
 */

import { z } from "zod";
import { create } from "zustand";
import { createJSONStorage, persist, type StorageValue } from "zustand/middleware";

import { getSettings } from "../lib/api";
import { decryptApiKey, encryptApiKey, isEncrypted } from "../lib/crypto";
import { featureFlags } from "../lib/feature-flags";
import {
  AssetProcessingMode,
  AssetProcessingModeSchema,
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

const EMPTY_OVERRIDES: UserOverrides = {
  model: "",
  apiKey: "",
  baseUrl: "",
  auxModel: "",
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
  assetMode: AssetProcessingMode.DESCRIBE,
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
  documentTab: DocumentTab;
  conversionPipeline: ConversionPipeline;
  chunkingPipeline: ChunkingPipeline;
  assetMode: AssetProcessingMode;
  expandedDirs: string[];
  personality: Personality;
  customSystemMessage: string;
  conversionConfigs: PipelineConfigs;
  chunkingConfigs: PipelineConfigs;
  toolsSpec: ToolsSpec;

  // User context (from backend, not persisted)
  readGroups: string[];
  writeGroups: string[];
  adminGroup: string;

  // LLM actions
  setOverride: (partial: Partial<UserOverrides>) => void;
  reset: () => void;
  initFromBackend: () => Promise<void>;

  // UI preference actions
  setDocumentTab: (tab: DocumentTab) => void;
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

/** Shape of the partialized state written to localStorage. */
interface PersistedSettings {
  overrides: UserOverrides;
  documentTab: DocumentTab;
  conversionPipeline: ConversionPipeline;
  chunkingPipeline: ChunkingPipeline;
  assetMode: AssetProcessingMode;
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
      adminGroup: "",
      ...UI_DEFAULTS,

      setOverride: (partial) =>
        set((state) => ({
          overrides: { ...state.overrides, ...partial },
        })),

      reset: () => set({ overrides: EMPTY_OVERRIDES }),

      initFromBackend: async () => {
        while (true) {
          try {
            const defaults = await getSettings();
            set({
              backendDefaults: defaults,
              readGroups: defaults.user.read_groups,
              writeGroups: defaults.user.write_groups,
              adminGroup: defaults.admin_group,
            });
            return;
          } catch {
            await new Promise((resolve) => setTimeout(resolve, 1000));
          }
        }
      },

      setDocumentTab: (tab) => set({ documentTab: tab }),

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
      storage: encryptedStorage,
      partialize: (state) => ({
        documentTab: state.documentTab,
        assetMode: state.assetMode,
        expandedDirs: state.expandedDirs,
        personality: state.personality,
        customSystemMessage: state.customSystemMessage,
        // Each block below is gated by its feature flag.  When off, the
        // slice is omitted from persisted state so secrets and stale user
        // input never touch localStorage and don't survive a flag flip.
        ...(featureFlags.llmSpec ? { overrides: state.overrides } : {}),
        ...(featureFlags.pipelineSpec
          ? {
              conversionPipeline: state.conversionPipeline,
              chunkingPipeline: state.chunkingPipeline,
              conversionConfigs: state.conversionConfigs,
              chunkingConfigs: state.chunkingConfigs,
            }
          : {}),
        ...(featureFlags.toolsSpec ? { toolsSpec: state.toolsSpec } : {}),
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
          documentTab: pick(DocumentTabSchema, data.documentTab, UI_DEFAULTS.documentTab),
          assetMode: pick(AssetProcessingModeSchema, data.assetMode, UI_DEFAULTS.assetMode),
          expandedDirs: pick(ExpandedDirsSchema, data.expandedDirs, UI_DEFAULTS.expandedDirs),
          personality: pick(PersonalitySchema, data.personality, UI_DEFAULTS.personality),
          customSystemMessage: pick(
            z.string(),
            data.customSystemMessage,
            UI_DEFAULTS.customSystemMessage,
          ),
          ...(featureFlags.llmSpec
            ? { overrides: pick(UserOverridesSchema, data.overrides, EMPTY_OVERRIDES) }
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
          ...(featureFlags.toolsSpec
            ? { toolsSpec: pick(ToolsSpecSchema, data.toolsSpec, UI_DEFAULTS.toolsSpec) }
            : {}),
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

/**
 * Zustand selector: whether the user is an administrator.
 *
 * Derived from `adminGroup` membership — same rule as the server's
 * `User.is_admin` property.  Empty `adminGroup` disables the gate.
 */
export function selectIsAdmin(state: SettingsState): boolean {
  const { adminGroup, readGroups, writeGroups } = state;
  if (!adminGroup) return false;
  return readGroups.includes(adminGroup) || writeGroups.includes(adminGroup);
}
