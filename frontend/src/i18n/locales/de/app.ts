import type { app as en } from "@/i18n/locales/en/app";
import type { Translation } from "@/i18n/translation";

export const app = {
  userMenu: {
    fallbackName: "Benutzer:in",
    account: "Account",
    documentation: "Dokumentation",
    toolDebugger: "Tool-Debugger",
    signOut: "Abmelden",
  },
  landing: {
    tagline:
      "Dein intelligenter Dokumentassistent auf Basis von RAG. Lade Dokumente hoch und chatte mit deiner Wissensbasis.",
    signIn: "Anmelden",
  },
  connecting: {
    connecting: "Verbindung zum Server wird hergestellt …",
    stillConnecting: "Verbindung zum Server wird weiterhin hergestellt …",
  },
  startup: {
    failed: "Start nicht möglich",
    configFailed: "Die Laufzeitkonfiguration konnte nicht geladen werden (HTTP {{status}})",
  },
  maintenance: {
    title: "Wartungsarbeiten",
    description:
      "Die App ist vorübergehend nicht verfügbar, während ein Admin Wartungsarbeiten durchführt. Diese Seite wird automatisch aktualisiert, sobald die App wieder verfügbar ist.",
  },
  notFound: {
    title: "Seite nicht gefunden",
    description: "Die gesuchte Seite existiert nicht oder wurde verschoben.",
    backHome: "Zur Startseite",
  },
  errorBoundary: {
    title: "Etwas ist schiefgelaufen",
    description:
      "In der App ist ein unerwarteter Fehler aufgetreten. Lade die Seite neu oder lösche alle lokalen Daten, wenn das Problem weiterhin besteht.",
    reload: "Neu laden",
    clearData: "Lokale Daten löschen und neu laden",
  },
  impersonation: {
    viewingAs: "Ansicht als <bold>{{user}}</bold>",
    exit: "Beenden",
  },
  schemaForm: {
    commaSeparated: "Durch Kommas getrennte Werte",
    noOptions: "Keine Konfigurationsoptionen verfügbar.",
  },
  toolDebugger: {
    title: "Tool-Debugger",
    description:
      "Rufe ein beliebiges Agent-Tool direkt auf, um zustandsbehaftetes Verhalten wie die pgvector-Suche zu testen. Argumente und ihre Typen werden aus dem Schema des jeweiligen Tools abgeleitet.",
    adminRequired: "Admin-Zugriff erforderlich",
    adminRequiredDescription: "Der Tool-Debugger ist nur für Admins verfügbar.",
    loadFailed: "Tools konnten nicht geladen werden: {{error}}",
    selectTool: "Wähle ein Tool zum Debuggen aus",
    run: "Tool ausführen",
    requestFailed: "Anfrage fehlgeschlagen: {{error}}",
    noResults: "Noch keine Ergebnisse",
    noResultsDescription:
      "Wähle ein Tool aus, fülle seine Argumente aus und führe es aus, um hier die Ausgabe zu sehen.",
    result: "Ergebnis",
    elapsed: "{{ms}} ms",
    ok: "ok",
    error: "Fehler",
    llmText: "LLM-Text",
    llmTextHint: "Als Text an das Modell übergebener Rückgabewert",
    structuredData: "Strukturierte Daten",
    structuredDataHint: "Strukturiertes Ergebnis, das die Oberfläche verwendet",
    noOutput: "Das Tool hat keine Ausgabe geliefert.",
  },
  api: {
    status: {
      tooLarge:
        "Dieser Upload ist zu groß. Wähle eine kleinere Datei aus oder teile sie in mehrere Teile auf.",
      tooManyRequests:
        "Zu viele Anfragen in kurzer Zeit. Warte einen Moment und versuche es erneut.",
      unavailable:
        "Der Server ist vorübergehend nicht verfügbar und startet vermutlich neu. Versuche es gleich noch einmal.",
    },
    errors: {
      fetchSettings: "Einstellungen konnten nicht geladen werden",
      verifyWatermark: "Das Text-Wasserzeichen konnte nicht geprüft werden",
      transcribeAudio: "Die Audioaufnahme konnte nicht transkribiert werden",
      fetchTools: "Tools konnten nicht geladen werden",
      fetchToolSchemas: "Tool-Schemas konnten nicht geladen werden",
      runTool: "Tool konnte nicht ausgeführt werden",
      testMcpServer: "Der MCP-Server konnte nicht getestet werden",
      upload: "Upload fehlgeschlagen",
      save: "Sichern fehlgeschlagen",
      uploadCollection: "Upload der Sammlung fehlgeschlagen",
      delete: "Löschen fehlgeschlagen",
      fetchDocumentContent: "Dokumentinhalt konnte nicht geladen werden",
      fetchDocumentAsset: "Dokumentressource konnte nicht geladen werden",
      download: "Download fehlgeschlagen",
      replaceOriginal: "Ersetzen fehlgeschlagen",
      listConversations: "Konversationen konnten nicht geladen werden",
      update: "Aktualisieren fehlgeschlagen",
      generateTitle: "Titel konnte nicht erstellt werden",
      compact: "Komprimieren fehlgeschlagen",
      fetchConversation: "Konversation konnte nicht geladen werden",
      importConversation: "Import fehlgeschlagen",
      fetchConversionPipelines: "Konvertierungspipelines konnten nicht geladen werden",
      fetchConversionPipelineConfig:
        "Konfiguration der Konvertierungspipeline konnte nicht geladen werden",
      fetchChunkingPipelines: "Chunking-Pipelines konnten nicht geladen werden",
      fetchChunkingPipelineConfig:
        "Konfiguration der Chunking-Pipeline konnte nicht geladen werden",
      fetchChunks: "Chunks konnten nicht geladen werden",
      fetchLineCounts: "Zeilenanzahlen konnten nicht geladen werden",
      listAssets: "Ressourcen konnten nicht geladen werden",
      updateDescription: "Beschreibung konnte nicht aktualisiert werden",
      generateDescription: "Beschreibung konnte nicht erstellt werden",
      deleteDescription: "Beschreibung konnte nicht gelöscht werden",
      rechunk: "Neu-Chunking fehlgeschlagen",
      reconvert: "Neukonvertierung fehlgeschlagen",
      listJobs: "Aufträge konnten nicht geladen werden",
      cancelJob: "Auftrag konnte nicht abgebrochen werden",
      jobFeed: "Auftragsfeed fehlgeschlagen",
      bulkRechunk: "Neu-Chunking mehrerer Dokumente fehlgeschlagen",
      bulkReconvert: "Neukonvertierung mehrerer Dokumente fehlgeschlagen",
      bulkMove: "Bewegen mehrerer Dokumente fehlgeschlagen",
      bulkDelete: "Löschen mehrerer Dokumente fehlgeschlagen",
      fetchDirectories: "Ordnerstruktur konnte nicht geladen werden",
      createDirectory: "Ordner konnte nicht erstellt werden",
      deleteDirectory: "Ordner konnte nicht gelöscht werden",
      moveDirectory: "Ordner konnte nicht bewegt werden",
      move: "Bewegen fehlgeschlagen",
      deleteConversations: "Konversationen konnten nicht gelöscht werden",
      deleteDocuments: "Dokumente konnten nicht gelöscht werden",
      clearMemory: "Gedächtnis konnte nicht geleert werden",
      clearScratch: "Scratch-Dateien konnten nicht gelöscht werden",
      deleteUserData: "Benutzerdaten konnten nicht gelöscht werden",
      deleteGroupData: "Gruppendaten konnten nicht gelöscht werden",
      adminAction: "Admin-Aktion fehlgeschlagen",
      listUsers: "Benutzer:innen konnten nicht geladen werden",
      listGroups: "Gruppen konnten nicht geladen werden",
      readMaintenance: "Wartungsmodus konnte nicht gelesen werden",
      setMaintenance: "Wartungsmodus konnte nicht festgelegt werden",
    },
  },
} as const satisfies Translation<typeof en>;
