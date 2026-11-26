import { mutationOptions, queryOptions } from "@tanstack/react-query";
import { z } from "zod";

import { jsonRequest, requestBlob, requestJson, requestVoid } from "@/api/client";
import { downloadBlob } from "@/lib/download";
import { apiUrl } from "@/lib/health";

const TmpClearedResponseSchema = z.object({ files_removed: z.number() });
type TmpClearedResponse = z.infer<typeof TmpClearedResponseSchema>;

/** The user's memory page, which the assistant writes with their approval (see backend `MemoryPage`). */
const MemoryPageSchema = z.object({ content: z.string() });

const memoryKey = ["memory"] as const;

export const memoryQueries = {
  get: () =>
    queryOptions({
      queryKey: memoryKey,
      queryFn: () => requestJson(apiUrl("/memory"), "fetchMemory", MemoryPageSchema),
    }),
};

export const memoryMutations = {
  /** Replace the memory page, an empty one clearing it. */
  save: () =>
    mutationOptions({
      mutationKey: [...memoryKey, "save"],
      mutationFn: (content: string) =>
        requestJson(
          apiUrl("/memory"),
          "saveMemory",
          MemoryPageSchema,
          jsonRequest("PUT", { content }),
        ),
      meta: { invalidates: [memoryKey] },
    }),
};

/** Clear the user's persistent memory. */
export function clearMemory(): Promise<void> {
  return requestVoid(apiUrl("/memory"), "clearMemory", { method: "DELETE" });
}

/** Drop the `/tmp` folder of every one of the user's conversations. */
export function clearTmp(): Promise<TmpClearedResponse> {
  return requestJson(apiUrl("/tmp"), "clearTmp", TmpClearedResponseSchema, { method: "DELETE" });
}

/** Delete all user data (conversations, documents, tokens). */
export function deleteAllUserData(): Promise<void> {
  return requestVoid(apiUrl("/user-data"), "deleteUserData", { method: "DELETE" });
}

/** The parts of the user's data, each exported on its own or with the others. */
export const EXPORT_PARTS = [
  "documents",
  "conversations",
  "memory",
  "skills",
  "preferences",
] as const;
export type ExportPart = (typeof EXPORT_PARTS)[number];

/** Download one part of the user's data, or all of it, as a ZIP archive. */
export async function exportUserData(part?: ExportPart): Promise<void> {
  const query = part ? `?${new URLSearchParams({ part })}` : "";
  const archive = await requestBlob(apiUrl(`/export${query}`), "exportUserData");
  downloadBlob(archive, `hivegent-${part ?? "export"}.zip`);
}
