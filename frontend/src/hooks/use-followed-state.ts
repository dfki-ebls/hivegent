import { type Dispatch, type SetStateAction, useEffect, useEffectEvent, useState } from "react";

import { followPath } from "@/lib/utils";
import { onWorkspaceChanged } from "@/stores/jobs-store";

/** Where a canonical path is once a change applied, null when it was deleted. */
export type Follow = (path: string) => string | null;

/**
 * Run *apply* on every workspace change, this tab's own and the feed's alike,
 * with the change as a function on canonical paths.
 */
export function useWorkspaceFollow(apply: (follow: Follow) => void): void {
  const onChange = useEffectEvent(apply);

  useEffect(
    () => onWorkspaceChanged((changed) => onChange((path) => followPath(path, changed))),
    [],
  );
}

/**
 * State of canonical paths that follows every workspace change, so a moved
 * entry stays selected or open and a deleted one drops out. *follow* returns
 * its value when nothing changed, so an unrelated change renders nothing.
 */
export function useFollowedState<T>(
  initial: T,
  follow: (value: T, path: Follow) => T,
): [T, Dispatch<SetStateAction<T>>] {
  const [value, setValue] = useState(initial);

  useWorkspaceFollow((path) => setValue((current) => follow(current, path)));

  return [value, setValue];
}
