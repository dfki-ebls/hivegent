import { describe, expect, it, vi } from "vitest";

import { createTestQueryClient } from "@/test/render";

describe("object URL queries", () => {
  it("revoke a URL once its entry holds another one or drops", () => {
    const revokeObjectURL = vi.fn<typeof URL.revokeObjectURL>();
    vi.stubGlobal(
      "URL",
      class extends URL {
        static override revokeObjectURL = revokeObjectURL;
      },
    );
    const client = createTestQueryClient();
    const queryKey = ["workspace", "asset", "~/a.png", null] as const;
    const options = { queryKey, meta: { objectUrl: true } };

    client.getQueryCache().build(client, options);
    client.setQueryData(queryKey, "blob:first");
    client.setQueryData(queryKey, "blob:second");
    expect(revokeObjectURL).toHaveBeenCalledExactlyOnceWith("blob:first");

    client.removeQueries({ queryKey });
    expect(revokeObjectURL).toHaveBeenLastCalledWith("blob:second");
  });
});
