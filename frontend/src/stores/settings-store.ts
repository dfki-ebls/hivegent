import { create } from 'zustand';
import { persist } from 'zustand/middleware';

export interface ModelConfig {
  name: string;
  value: string;
}

export interface LLMSettings {
  model: string;
  apiKey: string;
  baseUrl: string;
}

interface SettingsState {
  llm: LLMSettings;
  systemPrompt: string;
  availableModels: ModelConfig[];
  setLLM: (settings: Partial<LLMSettings>) => void;
  setSystemPrompt: (prompt: string) => void;
  addModel: (model: ModelConfig) => void;
  removeModel: (value: string) => void;
  reset: () => void;
}

const DEFAULT_MODELS: ModelConfig[] = [
  { name: 'GPT OSS 20B', value: 'openai/gpt-oss-20b' },
  { name: 'Qwen3 32B', value: 'qwen/qwen3-32b' },
];

const DEFAULT_SETTINGS: LLMSettings = {
  model: 'openai/gpt-oss-20b',
  apiKey: '',
  baseUrl: 'http://localhost:1234/v1',
};

const DEFAULT_SYSTEM_PROMPT = `You are a helpful RAG (Retrieval-Augmented Generation) assistant.

You have access to a collection of documents that you can search and retrieve.
Use the available tools to find and read documents before answering questions.

Be helpful, accurate, and cite which documents your information comes from.`;

export const useSettingsStore = create<SettingsState>()(
  persist(
    (set) => ({
      llm: DEFAULT_SETTINGS,
      systemPrompt: DEFAULT_SYSTEM_PROMPT,
      availableModels: DEFAULT_MODELS,

      setLLM: (settings) =>
        set((state) => ({
          llm: { ...state.llm, ...settings },
        })),

      setSystemPrompt: (prompt) => set({ systemPrompt: prompt }),

      addModel: (model) =>
        set((state) => ({
          availableModels: [...state.availableModels, model],
        })),

      removeModel: (value) =>
        set((state) => ({
          availableModels: state.availableModels.filter((m) => m.value !== value),
        })),

      reset: () =>
        set(() => ({
          llm: DEFAULT_SETTINGS,
          systemPrompt: DEFAULT_SYSTEM_PROMPT,
          availableModels: DEFAULT_MODELS,
        })),
    }),
    {
      name: 'snipscout-settings',
      partialize: (state) => ({
        llm: {
          model: state.llm.model,
          apiKey: state.llm.apiKey,
          baseUrl: state.llm.baseUrl,
        },
        systemPrompt: state.systemPrompt,
        availableModels: state.availableModels,
      }),
    }
  )
);
