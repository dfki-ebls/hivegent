import { create } from 'zustand';
import { persist } from 'zustand/middleware';

import type { BackendSettings } from '../lib/api';
import { fetchSettings } from '../lib/api';

export interface ModelConfig {
  name: string;
  value: string;
}

export interface LLMSettings {
  model: string;
  apiKey: string;
  baseUrl: string;
}

/** User-provided overrides stored in localStorage. Empty string = use backend default. */
interface UserOverrides {
  model: string;
  apiKey: string;
  baseUrl: string;
  smallModel: string;
  visionModel: string;
}

const EMPTY_OVERRIDES: UserOverrides = {
  model: '',
  apiKey: '',
  baseUrl: '',
  smallModel: '',
  visionModel: '',
};

interface SettingsState {
  // Backend defaults (not persisted)
  backendDefaults: BackendSettings | null;

  // User overrides (persisted)
  overrides: UserOverrides;

  // User-managed model list (persisted)
  availableModels: ModelConfig[];

  // Computed effective values (backend default + user override)
  llm: LLMSettings;
  smallModel: string;
  visionModel: string;
  hasServerApiKey: boolean;

  // Actions
  setLLM: (settings: Partial<LLMSettings>) => void;
  setSmallModel: (model: string) => void;
  setVisionModel: (model: string) => void;
  addModel: (model: ModelConfig) => void;
  removeModel: (value: string) => void;
  reset: () => void;
  initFromBackend: () => Promise<void>;
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
    }),
    {
      name: 'snipscout-settings',
      partialize: (state) => ({
        overrides: state.overrides,
        availableModels: state.availableModels,
      }),
      merge: (persisted, current) => {
        const merged = { ...current, ...(persisted as Partial<SettingsState>) };
        return {
          ...merged,
          ...computeEffective(merged.backendDefaults, merged.overrides),
        };
      },
    }
  )
);
