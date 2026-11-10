import { z } from "zod";

import { requestJson, requestVoid } from "@/api/client";
import { apiUrl } from "@/lib/health";

const TmpClearedResponseSchema = z.object({ files_removed: z.number() });
type TmpClearedResponse = z.infer<typeof TmpClearedResponseSchema>;

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
