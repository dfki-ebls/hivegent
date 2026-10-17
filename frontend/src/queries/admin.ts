import { mutationOptions, queryOptions } from "@tanstack/react-query";

import { adminGetMaintenance, adminOverview, adminSetMaintenance } from "@/lib/api";

const all = ["admin"] as const;

export const adminQueries = {
  /** Every user and group with their data counts, for the admin sections. */
  overview: () => queryOptions({ queryKey: [...all, "overview"], queryFn: adminOverview }),
  /** Whether the server currently rejects everyone but admins. */
  maintenance: () =>
    queryOptions({ queryKey: [...all, "maintenance"], queryFn: adminGetMaintenance }),
};

export const adminMutations = {
  maintenance: () =>
    mutationOptions({
      mutationKey: [...all, "maintenance"],
      mutationFn: adminSetMaintenance,
      meta: { invalidates: [adminQueries.maintenance().queryKey] },
    }),
};
