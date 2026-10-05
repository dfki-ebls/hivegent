/** Labels for option values defined in `lib/types.ts`, shared by every view listing them. */
export const options = {
  personality: {
    default: "Default",
    concise: "Concise",
    detailed: "Detailed",
    structured: "Structured",
    custom: "Custom",
  },
  mode: {
    interactive: "Interactive",
    read: "Read",
    write: "Write",
    plan: "Plan",
  },
  reasoningEffort: {
    auto: "Auto",
    none: "None",
    minimal: "Minimal",
    low: "Low",
    medium: "Medium",
    high: "High",
    xhigh: "Extra High",
  },
  chunkPosition: {
    line: "Line {{line}}",
    lineRange: "Lines {{start}}-{{end}}",
    fullDocument: "Full document",
    citedText: "Cited text",
  },
} as const;
