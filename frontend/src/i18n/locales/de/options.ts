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
  conversionPipeline: {
    auto: {
      label: "Automatisch",
      description: "Wählt automatisch die beste Pipeline für jede Datei",
    },
    llm: {
      label: "LLM",
      description: "Verwendet das Vision-Modell für alle Dateien",
    },
    marker: {
      label: "Marker",
      description: "Ideal für PDF-Dokumente",
    },
    docling: {
      label: "Docling",
      description: "Ideal für Office-Dokumente",
    },
    mineru: {
      label: "MinerU",
      description: "Hochwertige PDF-Analyse (kein XLSX)",
    },
    pandoc: {
      label: "Pandoc",
      description:
        "Universeller Konverter für ODT, RST, RTF, EPUB, LaTeX, Org, DocBook, Typst und mehr",
    },
    markitdown: {
      label: "MarkItDown",
      description: "Konverter von Microsoft für Office, PDF, Bilder und mehr",
    },
    kreuzberg: {
      label: "Kreuzberg",
      description: "Textextraktion aus über 75 Formaten mit OCR-Unterstützung",
    },
    anydoc: {
      label: "anydoc",
      description: "Schneller struktureller Konverter für Office, OpenDocument, RTF, EPUB und CSV",
    },
    "pdf-inspector": {
      label: "pdf-inspector",
      description: "Schneller, layoutbewusster Konverter von PDF zu Markdown, ohne OCR",
    },
    "pdf-oxide": {
      label: "pdf_oxide",
      description: "Leistungsstarker, Rust-basierter Konverter von PDF zu Markdown",
    },
    "table-chef": {
      label: "Table Chef",
      description: "CSV/Excel zu Markdown-Tabellen über pandas",
    },
    "plain-text": {
      label: "Reiner Text",
      description: "Text-, Konfigurations-, Datenserialisierungs- und Quelldateien unverändert",
    },
  },
  chunkingPipeline: {
    auto: {
      label: "Automatisch",
      description: "Empfohlener Standard: strukturbewusste rekursive Aufteilung",
    },
    none: {
      label: "Keine",
      description: "Behält das gesamte Dokument als einen einzigen Chunk",
    },
    token: {
      label: "Token",
      description: "Chunks mit fester Tokenanzahl für eine gleichmäßige Verarbeitung",
    },
    fast: {
      label: "Schnell",
      description: "Schnelle Aufteilung anhand von Trennzeichen",
    },
    sentence: {
      label: "Satz",
      description: "Berücksichtigt Satzgrenzen, gut für Fließtext und reinen Text",
    },
    recursive: {
      label: "Rekursiv",
      description: "Hierarchische Aufteilung nach Überschriften, Absätzen und Sätzen",
    },
    table: {
      label: "Tabelle",
      description: "Zeilenweise Aufteilung für tabellarische Daten",
    },
    markdown: {
      label: "Markdown",
      description: "Zerlegt Markdown in semantische Elemente (Text, Tabellen, Code)",
    },
    semantic: {
      label: "Semantisch",
      description: "Teilt nach semantischer Ähnlichkeit mithilfe von Embeddings",
    },
    code: {
      label: "Code",
      description: "Syntaxbewusste Aufteilung mit tree-sitter",
    },
    neural: {
      label: "Neuronal",
      description: "Erkennt Chunkgrenzen mit einem neuronalen Modell",
    },
    late: {
      label: "Late",
      description: "Chunkgrenzen auf Basis von Late-Interaction-Embeddings",
    },
    slumber: {
      label: "Slumber",
      description: "Intelligente Chunkgrenzen, vom LLM bestimmt",
    },
  },
  chunkPosition: {
    line: "Zeile {{line}}",
    lineRange: "Zeilen {{start}} bis {{end}}",
    fullDocument: "Ganzes Dokument",
    citedText: "Zitierter Text",
  },
} as const satisfies Translation<typeof en>;
