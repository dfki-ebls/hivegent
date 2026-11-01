import { z } from "zod";

/** The lines ``start_line`` to ``end_line`` of a text, as ``read_document`` and ``web_fetch`` return them. */
export const LineRangeSchema = z.object({
  start_line: z.number(),
  end_line: z.number(),
  total_lines: z.number(),
  content: z.string(),
});
export type LineRange = z.infer<typeof LineRangeSchema>;

export const DocumentRangeSchema = LineRangeSchema.extend({
  file_path: z.string(),
  content_hash: z.string(),
});
export type DocumentRange = z.infer<typeof DocumentRangeSchema>;

const GrepLineSchema = z.object({
  line_number: z.number(),
  text: z.string(),
  is_match: z.boolean(),
});
export const GrepMatchSchema = z.object({
  filename: z.string(),
  lines: z.array(GrepLineSchema),
});

export const RetrievedChunkSchema = z.object({
  store_key: z.string().nullable().optional(),
  filename: z.string(),
  chunk_index: z.number(),
  text: z.string(),
  token_count: z.number(),
  score: z.number(),
  start_line: z.number(),
  end_line: z.number(),
  start_index: z.number(),
  end_index: z.number(),
  image_path: z.string().nullable().optional(),
});
