import type { options as en } from "@/i18n/locales/en/options";
import type { Translation } from "@/i18n/translation";

export const options = {
  personality: {
    default: "Standard",
    concise: "Knapp",
    detailed: "Ausführlich",
    structured: "Strukturiert",
    custom: "Eigene",
  },
  mode: {
    interactive: "Interaktiv",
    read: "Lesen",
    write: "Schreiben",
    plan: "Planen",
  },
  reasoningEffort: {
    auto: "Automatisch",
    none: "Keiner",
    minimal: "Minimal",
    low: "Niedrig",
    medium: "Mittel",
    high: "Hoch",
    xhigh: "Sehr hoch",
  },
  chunkPosition: {
    line: "Zeile {{line}}",
    lineRange: "Zeilen {{start}} bis {{end}}",
    fullDocument: "Ganzes Dokument",
    citedText: "Zitierter Text",
  },
} as const satisfies Translation<typeof en>;
