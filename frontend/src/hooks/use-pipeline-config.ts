import { useEffect, useReducer } from "react";

import { getPipelineConfig, listPipelines } from "@/lib/api";
import type { PipelineByKind, PipelineConfigInfo, PipelineKind } from "@/lib/types";

/** A shared request that remembers its result, so a remount reads it synchronously. */
interface Cached<T> {
  promise: Promise<T>;
  value?: T;
}

/** Start a shared request, calling `evict` on failure so a later mount retries. */
function cached<T>(load: () => Promise<T>, evict: () => void): Cached<T> {
  const entry: Cached<T> = { promise: load() };
  entry.promise.then((value) => {
    entry.value = value;
  }, evict);
  return entry;
}

/** Defer a shared request to its first use. */
function lazy<T>(load: () => Promise<T>): () => Cached<T> {
  let entry: Cached<T> | undefined;
  return () =>
    (entry ??= cached(load, () => {
      entry = undefined;
    }));
}

/** Read a cached request, re-rendering once it resolves. */
function useCached<T>(entry: Cached<T> | null): T | null {
  const [, settled] = useReducer((n: number) => n + 1, 0);

  useEffect(() => {
    if (!entry || entry.value !== undefined) return;

    let active = true;
    entry.promise.then(
      () => active && settled(),
      () => {},
    );

    return () => {
      active = false;
    };
  }, [entry]);

  return entry?.value ?? null;
}

// Pipelines and their configs are static per server process, so every mount
// shares one request. Configs are keyed by stage too, so conversion and
// chunking cannot collide on a shared pipeline name.
const pipelineLists: { [K in PipelineKind]: () => Cached<PipelineByKind[K][]> } = {
  conversion: lazy(() => listPipelines("conversion")),
  chunking: lazy(() => listPipelines("chunking")),
};
const configs = new Map<string, Cached<PipelineConfigInfo>>();

/** The installed pipelines of one stage, empty while loading or unavailable. */
export function usePipelines<K extends PipelineKind>(stage: K): PipelineByKind[K][] {
  return useCached(pipelineLists[stage]()) ?? [];
}

/**
 * Load the configuration schema and defaults for the selected pipeline.
 *
 * Returns `null` while loading, when `pipeline` is null (the `auto` pipeline
 * takes no configuration), and when the pipeline became unavailable between the
 * list and config requests.
 */
export function usePipelineConfig(
  stage: PipelineKind,
  pipeline: string | null,
): PipelineConfigInfo | null {
  let entry: Cached<PipelineConfigInfo> | null = null;

  if (pipeline !== null) {
    const key = `${stage}/${pipeline}`;
    entry = configs.get(key) ?? null;

    if (!entry) {
      entry = cached(
        () => getPipelineConfig(stage, pipeline),
        () => configs.delete(key),
      );
      configs.set(key, entry);
    }
  }

  return useCached(entry);
}
