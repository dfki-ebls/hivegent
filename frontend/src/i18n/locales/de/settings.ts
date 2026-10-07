import type { settings as en } from "@/i18n/locales/en/settings";
import type { Translation } from "@/i18n/translation";

export const settings = {
  dialog: {
    title: "Einstellungen",
    description:
      "Konfiguriere deinen LLM-Anbieter und die Einstellungen des Assistenten. Diese Einstellungen werden lokal in deinem Browser gespeichert.",
    model: {
      label: "Modell",
      description:
        "Das Hauptmodell für den Chat. Lass das Feld leer, um den Standard des Servers zu verwenden.",
      placeholder: "z. B. openai/gpt-4o",
    },
    apiKey: {
      label: "API-Schlüssel (optional)",
      serverDescription:
        "Auf dem Server ist ein API-Schlüssel konfiguriert. Überschreibe ihn hier oder lass das Feld leer, um den Schlüssel des Servers zu verwenden.",
      description: "Nur für Anbieter erforderlich, die eine Authentifizierung verlangen.",
      serverPlaceholder: "API-Schlüssel des Servers wird verwendet",
      placeholder: "Gib deinen API-Schlüssel ein (falls erforderlich)",
    },
    baseUrl: {
      label: "Basis-URL",
      description: "API-Endpunkt des LLM-Anbieters.",
      placeholder: "z. B. http://localhost:11434/v1",
    },
    auxModel: {
      label: "Hilfsmodell (optional)",
      description:
        "Muss klein, schnell und bildfähig sein. Übernimmt Dokumentkonvertierung, Alternativtexte, Titel, Komprimierung, Erkundung durch Subagents und LLM-gestütztes Chunking. Bei diesen umfangreichen Aufgaben zählen Kosten und Latenz mehr als ein ausgeprägter Denkprozess. Verwendet dieselben Anbietereinstellungen wie das Hauptmodell.",
      placeholder: "z. B. openai/gpt-4o-mini",
    },
    personality: {
      label: "Persönlichkeit",
      description: "Wähle aus, wie der Assistent antwortet.",
    },
    customSystemMessage: {
      label: "Eigene Systemnachricht",
      description: "Gib eigene Systemanweisungen für den Assistenten vor.",
      placeholder: "Du bist ein hilfreicher Assistent, der …",
    },
    tools: {
      label: "Tools",
      description: "Lege fest, welche Tools der Assistent verwenden darf.",
    },
    toolGroups: {
      explore: "Erkunden",
      compute: "Berechnen",
      subagent: "Subagent",
      write: "Schreiben",
      memory: "Gedächtnis",
      web: "Web",
      conversation: "Konversation",
    },
    mcp: {
      label: "MCP-Server",
      description:
        "Verbinde externe Tool-Server über das Model Context Protocol (Streamable HTTP). Ein Präfix ordnet alle Tools eines Servers einem Namensraum zu (z. B. macht das Präfix „jira“ aus „search“ den Namen „jira_search“) und verhindert so Namenskonflikte, wenn mehrere Server ähnlich benannte Tools anbieten.",
      prefix: "Präfix: {{prefix}}",
      connectionFailed: "Verbindung fehlgeschlagen",
      testConnection: "Verbindung testen",
      removeServer: "Server entfernen",
      prefixPlaceholder: "Präfix",
      auth: "Authentifizierung:",
      authNone: "Keine",
      authHeaders: "Header",
      authOAuth2: "OAuth2-Client-Credentials",
      headerName: "Header-Name",
      headerValue: "Wert",
      removeHeader: "Header entfernen",
      addHeader: "Header hinzufügen",
      clientId: "Client-ID",
      clientSecret: "Client-Secret",
      scopes: "Scopes (optional, durch Leerzeichen getrennt)",
      addServer: "Server hinzufügen",
    },
    clear: "Leeren",
    clearMemory: {
      label: "Gedächtnis leeren",
      title: "Gedächtnis leeren?",
      description:
        "Dadurch wird das gesamte gespeicherte Gedächtnis endgültig gelöscht. Der Assistent erinnert sich dann nicht mehr an Informationen aus früheren Konversationen.",
    },
    clearScratch: {
      label: "Scratch leeren",
      title: "Scratch-Dateien löschen?",
      description:
        "Dadurch werden die Arbeitsdateien gelöscht, die der Assistent zwischen Tool-Aufrufen ablegt, in deinem eigenen Arbeitsbereich und in jeder Gruppe, in die du schreiben darfst. Deine Dokumente bleiben unverändert.",
      done_one: "{{count}} Scratch-Datei gelöscht",
      done_other: "{{count}} Scratch-Dateien gelöscht",
    },
    resetDefaults: "Auf Serverstandards zurücksetzen",
  },
  account: {
    title: "Account",
    done: "{{action}}: Fertig",
    failed: "{{action}}: Fehlgeschlagen",
    noUsers: "Noch keine Benutzer:innen haben Spuren hinterlassen.",
    noGroups: "Noch keine Gruppen registriert.",
    userMeta: "{{documents}} Dok. / {{conversations}} Konv.",
    groupMeta: "{{documents}} Dok.",
    documentCount_one: "{{count}} Dokument",
    documentCount_other: "{{count}} Dokumente",
    conversationCount_one: "{{count}} Konversation",
    conversationCount_other: "{{count}} Konversationen",
    userDanger: {
      title: "Gefahrenbereich (Benutzer:in)",
      description: "Unumkehrbare Aktionen für deinen Account. Sie betreffen nur dich.",
      resetLocal: {
        label: "Lokale Einstellungen zurücksetzen",
        description:
          "Verwirf deine Anpassungen im Browser und kehre zu den Standards des Servers zurück. Daten auf dem Server bleiben unverändert.",
      },
      deleteChats: {
        label: "Alle Chats löschen",
        description:
          "Lösche endgültig alle deine Chats auf dem Server. Diese Aktion kann nicht widerrufen werden.",
      },
      deleteDocuments: {
        label: "Alle Dokumente löschen",
        description:
          "Lösche endgültig alle deine Dokumente, Chunks, Originale und Einträge im Suchindex. Diese Aktion kann nicht widerrufen werden.",
      },
      resetEverything: {
        label: "Alles zurücksetzen",
        description:
          "Lösche endgültig alle Spuren deines Accounts auf dem Server (Konversationen, Dokumente, Tokens, Gedächtnis) und alle lokalen Browserdaten. Diese Aktion kann nicht widerrufen werden.",
      },
    },
    maintenance: {
      title: "Wartung (Admin)",
      label: "Wartungsmodus",
      description:
        "Sperre alle Benutzer:innen ohne Admin-Rechte aus und zeige ihnen statt der App einen Wartungshinweis. Admins behalten vollen Zugriff. Die Einstellung wird gespeichert und bleibt auch nach Neustarts des Servers aktiv, bis ein Admin sie ausschaltet.",
      toggleFailed: "Wartungsmodus konnte nicht umgeschaltet werden",
      enabled: "Wartungsmodus aktiviert",
      disabled: "Wartungsmodus deaktiviert",
    },
    impersonation: {
      title: "Ansicht als Benutzer:in (Admin)",
      description:
        "Nutze die App als andere:r Benutzer:in, um gemeldete Probleme nachzustellen. Die Sitzung hat die Rechte der gewählten Person, nie deine Admin-Rechte, und oben bleibt ein Banner mit einer Taste zum Beenden sichtbar.",
      list: "Als Benutzer:in ansehen",
    },
    adminDanger: {
      title: "Gefahrenbereich (Admin)",
      description:
        "Unumkehrbare Aktionen für das gesamte Deployment. Sie betreffen alle Benutzer:innen.",
      resetWorkspace: {
        label: "Arbeitsbereichsdateien zurücksetzen",
        confirm: "Arbeitsbereich zurücksetzen",
        description:
          "Löscht alle Dateien der Arbeitsbereiche auf dem Datenträger und die zugehörigen Dokumentzeilen in SQL, da beide synchron bleiben müssen. Chunks (Text und Vektor) werden mit den Dokumentzeilen entfernt. Konversationen, Tokens, Gedächtnis, Benutzer:innen und Gruppen bleiben erhalten.",
      },
      reindex: {
        label: "Wissen neu indexieren",
        confirm: "Neu indexieren",
        description:
          "Gleicht jede Fallbasis ab: Übernimmt manuell in den Arbeitsbereich kopierte Dateien, entfernt verwaiste SQL-Einträge und vereinheitlicht die Unicode-Schreibweise von Dateinamen, damit der Assistent sie ansprechen kann. Es wird nichts gelöscht oder neu eingebettet. Jederzeit sicher ausführbar und nützlich nach manuellen Dateiänderungen oder einer geänderten Embedding-Konfiguration.",
      },
      resetDatabase: {
        label: "Datenbank zurücksetzen",
        description:
          "Löscht alle Benutzer- und Gruppenzeilen samt allem, was davon abhängt: Tokens, Gedächtnis, Konversationen, Dokumente und Chunks. Dateien der Arbeitsbereiche auf dem Datenträger bleiben erhalten.",
      },
      factoryReset: {
        label: "Auf Werkseinstellungen zurücksetzen",
        description:
          "Löscht alle Dateien der Arbeitsbereiche auf dem Datenträger, alle Benutzer:innen und Gruppen und alle abhängigen Zeilen (Dokumente, Chunks, Konversationen, Tokens, Gedächtnis). Auch die lokalen Browserdaten werden gelöscht. Das Deployment kehrt in den Zustand einer frischen Installation zurück. Diese Aktion kann nicht widerrufen werden.",
      },
      wipeUser: {
        list: "Daten einer Person löschen",
        title: "Daten von {{user}} löschen",
        description:
          "Löscht alle Dokumente, Chunks, Originale, Konversationen, Tokens und Gedächtniseinträge von {{user}}. {{documents}} und {{conversations}} werden entfernt. Diese Aktion kann nicht widerrufen werden.",
        confirm: "Benutzerdaten löschen",
      },
      wipeGroup: {
        list: "Daten einer Gruppe löschen",
        title: "Daten der Gruppe {{group}} löschen",
        description_one:
          "Löscht alle Dokumente, Chunks und Originale der Gruppe {{group}}. {{count}} Dokument wird entfernt. Die Gruppe erscheint wieder, sobald eines ihrer Mitglieder (laut OIDC-Token) etwas in sie hochlädt. Diese Aktion kann nicht widerrufen werden.",
        description_other:
          "Löscht alle Dokumente, Chunks und Originale der Gruppe {{group}}. {{count}} Dokumente werden entfernt. Die Gruppe erscheint wieder, sobald eines ihrer Mitglieder (laut OIDC-Token) etwas in sie hochlädt. Diese Aktion kann nicht widerrufen werden.",
        confirm: "Gruppendaten löschen",
      },
    },
  },
} as const satisfies Translation<typeof en>;
