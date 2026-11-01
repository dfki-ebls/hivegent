import { z } from "zod";

import { apiUrl, jsonRequest, requestJson } from "@/api/client";

const TransparencyDetectionResponseSchema = z.object({
  status: z.enum(["detected", "not_detected", "inconclusive"]),
  method: z.literal("watermark"),
  message: z.string(),
  signed_report: z.string(),
});
type TransparencyDetectionResponse = z.infer<typeof TransparencyDetectionResponseSchema>;

/** Verify whether free-form text carries this deployment's watermark. */
export function detectAiGeneratedText(text: string): Promise<TransparencyDetectionResponse> {
  return requestJson(
    apiUrl("/transparency/detect"),
    "verifyWatermark",
    TransparencyDetectionResponseSchema,
    jsonRequest("POST", { text }),
  );
}
