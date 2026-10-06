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
  conversionPipeline: {
    auto: {
      label: "Auto",
      description: "Automatically selects the best pipeline for each file",
    },
    llm: {
      label: "LLM",
      description: "Uses vision model for all files",
    },
    marker: {
      label: "Marker",
      description: "Best for PDF documents",
    },
    docling: {
      label: "Docling",
      description: "Best for Office documents",
    },
    mineru: {
      label: "MinerU",
      description: "High-quality PDF parsing (no XLSX)",
    },
    pandoc: {
      label: "Pandoc",
      description:
        "Universal converter for ODT, RST, RTF, EPUB, LaTeX, Org, DocBook, Typst, and more",
    },
    markitdown: {
      label: "MarkItDown",
      description: "Microsoft's converter for Office, PDF, images, and more",
    },
    kreuzberg: {
      label: "Kreuzberg",
      description: "Text extraction from 75+ formats with OCR support",
    },
    anydoc: {
      label: "anydoc",
      description: "Fast structural converter for Office, OpenDocument, RTF, EPUB, and CSV",
    },
    "pdf-inspector": {
      label: "pdf-inspector",
      description: "Fast layout-aware PDF to markdown converter, no OCR",
    },
    "pdf-oxide": {
      label: "pdf_oxide",
      description: "High-performance Rust-based PDF to markdown converter",
    },
    "table-chef": {
      label: "Table Chef",
      description: "CSV/Excel to markdown tables via pandas",
    },
    "plain-text": {
      label: "Plain Text",
      description: "Text, configuration, data-serialization, and source files as-is",
    },
  },
  chunkingPipeline: {
    auto: {
      label: "Auto",
      description: "Recommended default: structure-aware recursive splitting",
    },
    none: {
      label: "None",
      description: "Keep the full document as a single chunk",
    },
    token: {
      label: "Token",
      description: "Fixed token-count chunks for uniform processing",
    },
    fast: {
      label: "Fast",
      description: "High-throughput delimiter-based splitting",
    },
    sentence: {
      label: "Sentence",
      description: "Respects sentence boundaries, good for prose and plain text",
    },
    recursive: {
      label: "Recursive",
      description: "Hierarchical splitting by headings, paragraphs, and sentences",
    },
    table: {
      label: "Table",
      description: "Row-based splitting for tabular data",
    },
    markdown: {
      label: "Markdown",
      description: "Parses markdown into semantic elements (text, tables, code)",
    },
    semantic: {
      label: "Semantic",
      description: "Splits by semantic similarity using embeddings",
    },
    code: {
      label: "Code",
      description: "Syntax-aware splitting using tree-sitter",
    },
    neural: {
      label: "Neural",
      description: "Neural model-based chunk boundary detection",
    },
    late: {
      label: "Late",
      description: "Late-interaction embedding-aware chunk boundaries",
    },
    slumber: {
      label: "Slumber",
      description: "LLM-guided intelligent chunk boundary decisions",
    },
  },
  chunkPosition: {
    line: "Line {{line}}",
    lineRange: "Lines {{start}}-{{end}}",
    fullDocument: "Full document",
    citedText: "Cited text",
  },
} as const;
