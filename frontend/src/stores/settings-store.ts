/**
 * Zustand store for all persisted user settings.
 *
 * Combines LLM configuration (model overrides, API keys, available models)
 * with UI preferences (active tab, pipeline selections, expanded directories)
 * into a single persisted store with Zod-validated rehydration.
 */

import { create } from "zustand";
import {
  createJSONStorage,
  persist,
  type StorageValue,
} from "zustand/middleware";

import { fetchSettings } from "../lib/api";
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
  type Personality,
  PersonalitySchema,
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

const UI_DEFAULTS = {
  documentTab: "fetched" as DocumentTab,
  conversionPipeline: ConversionPipeline.AUTO,
  chunkingPipeline: ChunkingPipeline.AUTO,
  expandedDirs: [""] as string[],
  personality: "default" as Personality,
  customSystemMessage: "",
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
  expandedDirs: string[];
  personality: Personality;
  customSystemMessage: string;

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
  toggleExpandedDir: (path: string) => void;
  setExpandedDirs: (dirs: string[]) => void;
  setPersonality: (personality: Personality) => void;
  setCustomSystemMessage: (message: string) => void;
}

/** Recompute effective values from backend defaults and user overrides. */
function computeEffective(
  defaults: BackendSettings | null,
  overrides: UserOverrides,
): Pick<
  SettingsState,
  "llm" | "smallModel" | "visionModel" | "hasServerApiKey"
> {
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
  expandedDirs: string[];
  personality: Personality;
  customSystemMessage: string;
}

/**
 * Custom storage that encrypts the API key before writing to localStorage
 * and decrypts it on read.  All other fields pass through unchanged.
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

    const apiKey = stored.state.overrides.apiKey;
    if (typeof apiKey === "string" && isEncrypted(apiKey)) {
      try {
        stored.state.overrides.apiKey = await decryptApiKey(apiKey);
      } catch (err) {
        console.warn("Failed to decrypt API key, clearing stored value:", err);
        stored.state.overrides.apiKey = "";
      }
    }

    return JSON.stringify(stored);
  },

  setItem: async (name: string, value: string): Promise<void> => {
    const stored = JSON.parse(value) as StorageValue<PersistedSettings>;
    const apiKey = stored.state.overrides.apiKey;
    if (typeof apiKey === "string" && apiKey && !isEncrypted(apiKey)) {
      try {
        stored.state.overrides.apiKey = await encryptApiKey(apiKey);
      } catch (err) {
        console.warn("Failed to encrypt API key, storing in plain text:", err);
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
            ...(settings.apiKey !== undefined
              ? { apiKey: settings.apiKey }
              : {}),
            ...(settings.baseUrl !== undefined
              ? { baseUrl: settings.baseUrl }
              : {}),
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
          const defaults = await fetchSettings();
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

      setConversionPipeline: (pipeline) =>
        set({ conversionPipeline: pipeline }),

      setChunkingPipeline: (pipeline) => set({ chunkingPipeline: pipeline }),

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

      setCustomSystemMessage: (customSystemMessage) =>
        set({ customSystemMessage }),
    }),
    {
      name: "hivegent-settings",
      storage: encryptedStorage,
      partialize: (state) => ({
        overrides: state.overrides,
        documentTab: state.documentTab,
        conversionPipeline: state.conversionPipeline,
        chunkingPipeline: state.chunkingPipeline,
        expandedDirs: state.expandedDirs,
        personality: state.personality,
        customSystemMessage: state.customSystemMessage,
      }),
      merge: (persisted, current) => {
        const data = persisted as Record<string, unknown> | undefined;
        if (!data) return current;

        const overrides =
          UserOverridesSchema.safeParse(data.overrides).data ?? EMPTY_OVERRIDES;

        return {
          ...current,
          overrides,
          documentTab:
            DocumentTabSchema.safeParse(data.documentTab).data ??
            UI_DEFAULTS.documentTab,
          conversionPipeline:
            ConversionPipelineSchema.safeParse(data.conversionPipeline).data ??
            UI_DEFAULTS.conversionPipeline,
          chunkingPipeline:
            ChunkingPipelineSchema.safeParse(data.chunkingPipeline).data ??
            UI_DEFAULTS.chunkingPipeline,
          expandedDirs:
            ExpandedDirsSchema.safeParse(data.expandedDirs).data ??
            UI_DEFAULTS.expandedDirs,
          personality:
            PersonalitySchema.safeParse(data.personality).data ??
            UI_DEFAULTS.personality,
          customSystemMessage:
            typeof data.customSystemMessage === "string"
              ? data.customSystemMessage
              : UI_DEFAULTS.customSystemMessage,
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
