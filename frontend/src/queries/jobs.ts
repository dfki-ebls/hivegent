import { mutationOptions } from "@tanstack/react-query";

import { cancelJob } from "@/lib/api";

export const jobMutations = {
  /** Request cancellation of a job; the job feed reflects the resulting state. */
  cancel: () => mutationOptions({ mutationKey: ["jobs", "cancel"], mutationFn: cancelJob }),
};
