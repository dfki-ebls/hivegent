import { useEffect, useState } from "react";

import type { PipelineConfigInfo } from "@/lib/types";

type ConfigFetcher<P extends string> = (pipeline: P) => Promise<PipelineConfigInfo>;

interface LoadedConfig<P extends string> {
  fetchConfig: ConfigFetcher<P>;
  pipeline: P;
  config: PipelineConfigInfo;
}

/**
 * Config payloads are static per server process, so one fetch per pipeline
 * suffices. Keyed by fetcher so the conversion and chunking registries cannot
 * collide on a shared pipeline name.
 */
const cache = new WeakMap<
  ConfigFetcher<string>,
  Map<string, PipelineConfigInfo>
>();

function cached<P extends string>(
  fetchConfig: ConfigFetcher<P>,
  pipeline: P,
): PipelineConfigInfo | null {
  return cache.get(fetchConfig as ConfigFetcher<string>)?.get(pipeline) ?? null;
}

/**
 * Load the configuration schema and defaults for the selected pipeline.
 *
 * Returns `null` while loading, when `pipeline` is null (the `auto` pipeline
 * takes no configuration), and when the pipeline became unavailable between the
 * list and config requests. Pass a stable `fetchConfig` — a module-level API
 * function, or one memoized with `useCallback`.
 */
export function usePipelineConfig<P extends string>(
  pipeline: P | null,
  fetchConfig: ConfigFetcher<P>,
): PipelineConfigInfo | null {
  // Tagged with its request, so switching pipelines reads as loading without
  // resetting state inside the effect.
  const [loaded, setLoaded] = useState<LoadedConfig<P> | null>(null);

  useEffect(() => {
    if (pipeline === null || cached(fetchConfig, pipeline)) return;

    let active = true;
    fetchConfig(pipeline)
      .then((config) => {
        const key = fetchConfig as ConfigFetcher<string>;
        const entries = cache.get(key) ?? new Map<string, PipelineConfigInfo>();
        entries.set(pipeline, config);
        cache.set(key, entries);
        if (active) setLoaded({ fetchConfig, pipeline, config });
      })
      .catch(() => {
        // The pipeline can become unavailable between the list and config requests.
      });

    return () => {
      active = false;
    };
  }, [fetchConfig, pipeline]);

  if (pipeline === null) return null;

  if (loaded?.fetchConfig === fetchConfig && loaded.pipeline === pipeline) return loaded.config;

  return cached(fetchConfig, pipeline);
}
