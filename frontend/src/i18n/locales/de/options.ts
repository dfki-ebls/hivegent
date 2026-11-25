import type { options as en } from "@/i18n/locales/en/options";
import type { Translation } from "@/i18n/translation";

export const options = {
  mode: {
    interactive: "Interaktiv",
    read: "Lesen",
    write: "Schreiben",
  },
  reasoningEffort: {
    low: "Niedrig",
    medium: "Mittel",
    high: "Hoch",
  },
  chunkPosition: {
    line: "Zeile {{line}}",
    lineRange: "Zeilen {{start}} bis {{end}}",
    fullDocument: "Ganzes Dokument",
    citedText: "Zitierter Text",
  },
} as const satisfies Translation<typeof en>;
