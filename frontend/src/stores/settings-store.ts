/**
 * Zustand store for all persisted user settings.
 *
 * Combines LLM configuration (model overrides, API keys, available models)
 * with UI preferences (active tab, pipeline selections, expanded directories)
 * into a single persisted store with Zod-validated rehydration.
 */

import { create } from 'zustand';
import { persist } from 'zustand/middleware';

import { fetchSettings } from '../lib/api';
import {
  ChunkingPipeline,
  ChunkingPipelineSchema,
  ConversionPipeline,
  ConversionPipelineSchema,
  DocumentTabSchema,
  ExpandedDirsSchema,
  ModelConfigArraySchema,
  UserOverridesSchema,
  type BackendSettings,
  type DocumentTab,
  type ModelConfig,
  type UserOverrides,
} from '../lib/types';

export type { ModelConfig };

export interface LLMSettings {
  model: string;
  apiKey: string;
  baseUrl: string;
}

const EMPTY_OVERRIDES: UserOverrides = {
  model: '',
  apiKey: '',
  baseUrl: '',
  smallModel: '',
  visionModel: '',
};

const UI_DEFAULTS = {
  documentTab: 'fetched' as DocumentTab,
  conversionPipeline: ConversionPipeline.AUTO,
  chunkingPipeline: ChunkingPipeline.AUTO,
  expandedDirs: [''] as string[],
};

interface SettingsState {
  // Backend defaults (not persisted)
  backendDefaults: BackendSettings | null;

  // User LLM overrides (persisted)
  overrides: UserOverrides;

  // User-managed model list (persisted)
  availableModels: ModelConfig[];

  // UI preferences (persisted)
  documentTab: DocumentTab;
  conversionPipeline: ConversionPipeline;
  chunkingPipeline: ChunkingPipeline;
  expandedDirs: string[];

  // Computed effective values (backend default + user override)
  llm: LLMSettings;
  smallModel: string;
  visionModel: string;
  hasServerApiKey: boolean;

  // LLM actions
  setLLM: (settings: Partial<LLMSettings>) => void;
  setSmallModel: (model: string) => void;
  setVisionModel: (model: string) => void;
  addModel: (model: ModelConfig) => void;
  removeModel: (value: string) => void;
  reset: () => void;
  initFromBackend: () => Promise<void>;

  // UI preference actions
  setDocumentTab: (tab: DocumentTab) => void;
  setConversionPipeline: (pipeline: ConversionPipeline) => void;
  setChunkingPipeline: (pipeline: ChunkingPipeline) => void;
  toggleExpandedDir: (path: string) => void;
  setExpandedDirs: (dirs: string[]) => void;
}

/** Recompute effective values from backend defaults and user overrides. */
function computeEffective(
  defaults: BackendSettings | null,
  overrides: UserOverrides
): Pick<SettingsState, 'llm' | 'smallModel' | 'visionModel' | 'hasServerApiKey'> {
  return {
    llm: {
      model: overrides.model || defaults?.model || '',
      apiKey: overrides.apiKey,
      baseUrl: overrides.baseUrl || defaults?.base_url || '',
    },
    smallModel: overrides.smallModel || defaults?.small_model || '',
    visionModel: overrides.visionModel || defaults?.vision_model || '',
    hasServerApiKey: defaults?.has_api_key ?? false,
  };
}

export const useSettingsStore = create<SettingsState>()(
  persist(
    (set) => ({
      backendDefaults: null,
      overrides: EMPTY_OVERRIDES,
      availableModels: [],
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

      addModel: (model) =>
        set((state) => ({
          availableModels: [...state.availableModels, model],
        })),

      removeModel: (value) =>
        set((state) => ({
          availableModels: state.availableModels.filter((m) => m.value !== value),
        })),

      reset: () =>
        set((state) => ({
          overrides: EMPTY_OVERRIDES,
          availableModels: [],
          ...computeEffective(state.backendDefaults, EMPTY_OVERRIDES),
        })),

      initFromBackend: async () => {
        try {
          const defaults = await fetchSettings();
          set((state) => ({
            backendDefaults: defaults,
            ...computeEffective(defaults, state.overrides),
          }));
        } catch {
          // Silently fail — keep existing values
        }
      },

      setDocumentTab: (tab) => set({ documentTab: tab }),

      setConversionPipeline: (pipeline) => set({ conversionPipeline: pipeline }),

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
    }),
    {
      name: 'snipscout-settings',
      partialize: (state) => ({
        overrides: state.overrides,
        availableModels: state.availableModels,
        documentTab: state.documentTab,
        conversionPipeline: state.conversionPipeline,
        chunkingPipeline: state.chunkingPipeline,
        expandedDirs: state.expandedDirs,
      }),
      merge: (persisted, current) => {
        const data = persisted as Record<string, unknown> | undefined;
        if (!data) return current;

        const overrides =
          UserOverridesSchema.safeParse(data.overrides).data ?? EMPTY_OVERRIDES;
        const availableModels =
          ModelConfigArraySchema.safeParse(data.availableModels).data ?? [];

        return {
          ...current,
          overrides,
          availableModels,
          documentTab:
            DocumentTabSchema.safeParse(data.documentTab).data ?? UI_DEFAULTS.documentTab,
          conversionPipeline:
            ConversionPipelineSchema.safeParse(data.conversionPipeline).data ??
            UI_DEFAULTS.conversionPipeline,
          chunkingPipeline:
            ChunkingPipelineSchema.safeParse(data.chunkingPipeline).data ??
            UI_DEFAULTS.chunkingPipeline,
          expandedDirs:
            ExpandedDirsSchema.safeParse(data.expandedDirs).data ?? UI_DEFAULTS.expandedDirs,
          ...computeEffective(current.backendDefaults, overrides),
        };
      },
    }
  )
);
