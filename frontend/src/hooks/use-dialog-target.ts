import { useState } from "react";

/**
 * What a dialog shows and whether it is open, kept apart so the target outlives the close
 * and the dialog keeps its content while it animates out.
 */
export function useDialogTarget<T>(initial: T) {
  const [target, setTarget] = useState(initial);
  const [open, setOpen] = useState(false);

  const show = (next: T) => {
    setTarget(next);
    setOpen(true);
  };

  return { target, open, show, setOpen };
}
