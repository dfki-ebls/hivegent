import type { app as en } from "@/i18n/locales/en/app";
import type { Translation } from "@/i18n/translation";

export const app = {
  userMenu: {
    fallbackName: "Benutzer:in",
    account: "Account",
    documentation: "Dokumentation",
    licenses: "Lizenzen",
    toolDebugger: "Tool-Debugger",
    chatDebugger: "Chat-Debugger",
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
  adminOnly: {
    title: "Admin-Zugriff erforderlich",
    description: "Diese Seite ist nur für Admins verfügbar.",
  },
  chatDebugger: {
    title: "Chat-Debugger",
    description:
      "Befrage einen Agenten zur Konversation eines beliebigen Nutzers, um Tool-Fehler, Probleme in der Agentenschleife und fehlende Funktionen zu finden. Die Sitzung wird nicht gespeichert, und der Bericht ersetzt die Daten der Konversation durch Platzhalter.",
    conversationId: "Konversations-ID",
    open: "Öffnen",
    notFound: "Konversation nicht gefunden",
    emptyTitle: "Konversation öffnen",
    emptyDescription:
      "Gib die ID der Konversation eines beliebigen Nutzers ein, um sie zu debuggen.",
    placeholder: "Frage zu dieser Konversation …",
    defaultPrompt: "Analysiere diese Konversation und liste deine Befunde auf.",
    report: "Bericht schreiben",
    reportTitle: "Bericht",
    reportDescription:
      "Aus dieser Sitzung verdichtet, die Daten der Konversation sind durch Platzhalter ersetzt.",
    masked_one:
      "{{count}} Stelle wiederholte noch die Konversation und wurde als <REDACTED_n> maskiert. Prüfe den Bericht, bevor du ihn teilst.",
    masked_other:
      "{{count}} Stellen wiederholten noch die Konversation und wurden als <REDACTED_n> maskiert. Prüfe den Bericht, bevor du ihn teilst.",
  },
  toolDebugger: {
    title: "Tool-Debugger",
    description:
      "Rufe ein beliebiges Agent-Tool direkt auf, um zustandsbehaftetes Verhalten wie die pgvector-Suche zu testen. Argumente und ihre Typen werden aus dem Schema des jeweiligen Tools abgeleitet.",
    loadFailed: "Tools konnten nicht geladen werden",
    selectTool: "Wähle ein Tool zum Debuggen aus",
    run: "Tool ausführen",
    requestFailed: "Anfrage fehlgeschlagen",
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
      fetchDebugConversation: "Die Konversation konnte nicht geöffnet werden",
      writeDebugReport: "Der Bericht konnte nicht geschrieben werden",
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
      importConversation: "Import fehlgeschlagen",
      fetchPipelines: "Pipelines konnten nicht geladen werden",
      fetchPipelineConfig: "Konfiguration der Pipeline konnte nicht geladen werden",
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
      fetchDirectories: "Ordnerstruktur konnte nicht geladen werden",
      applyChanges: "Änderungen konnten nicht übernommen werden",
      deleteConversations: "Konversationen konnten nicht gelöscht werden",
      deleteDocuments: "Dokumente konnten nicht gelöscht werden",
      clearMemory: "Gedächtnis konnte nicht geleert werden",
      clearTmp: "Temporäre Dateien konnten nicht gelöscht werden",
      deleteUserData: "Benutzerdaten konnten nicht gelöscht werden",
      deleteGroupData: "Gruppendaten konnten nicht gelöscht werden",
      adminAction: "Admin-Aktion fehlgeschlagen",
      readAdminOverview: "Admin-Übersicht konnte nicht geladen werden",
      readMaintenance: "Wartungsmodus konnte nicht gelesen werden",
      setMaintenance: "Wartungsmodus konnte nicht festgelegt werden",
    },
  },
} as const satisfies Translation<typeof en>;
