/** Labels for option values defined in `lib/types.ts`, shared by every view listing them. */
export const options = {
  mode: {
    interactive: "Interactive",
    read: "Read",
    write: "Write",
  },
  reasoningEffort: {
    low: "Low",
    medium: "Medium",
    high: "High",
  },
  chunkPosition: {
    line: "Line {{line}}",
    lineRange: "Lines {{start}}-{{end}}",
    fullDocument: "Full document",
    citedText: "Cited text",
  },
} as const;
