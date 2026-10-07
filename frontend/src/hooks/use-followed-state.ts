import { type Dispatch, type SetStateAction, useEffect, useState } from "react";

import { canonicalPath, splitScopePath } from "@/lib/api";
import { followEach, followPath } from "@/lib/utils";
import { onWorkspaceChanged } from "@/stores/jobs-store";

/** Where a local path is once a change applied, null when it was deleted or left the scope. */
export type FollowLocal = (local: string) => string | null;

/** *files* once a change applied, or *files* itself when none of them changed. */
export function followSet(files: Set<string>, follow: FollowLocal): Set<string> {
  const current = [...files];
  const next = followEach(current, follow);

  return next === current ? files : new Set(next);
}

/**
 * State of local paths in *scope* that follows every workspace change, this
 * tab's own and the feed's alike, so a moved entry stays selected or open and
 * a deleted one drops out. *follow* must be stable and return its value when
 * nothing changed, so an unrelated change renders nothing.
 */
export function useFollowedState<T>(
  scope: string,
  initial: T,
  follow: (value: T, path: FollowLocal) => T,
): [T, Dispatch<SetStateAction<T>>] {
  const [value, setValue] = useState(initial);

  useEffect(
    () =>
      onWorkspaceChanged((changed) => {
        const path: FollowLocal = (local) => {
          const next = followPath(canonicalPath(scope, local), changed);
          const split = next === null ? null : splitScopePath(next);

          return split?.scope === scope ? split.local : null;
        };

        setValue((current) => follow(current, path));
      }),
    [scope, follow],
  );

  return [value, setValue];
}
