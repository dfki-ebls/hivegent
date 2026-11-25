import type { settings as en } from "@/i18n/locales/en/settings";
import type { Translation } from "@/i18n/translation";

export const settings = {
  nav: {
    title: "Einstellungen",
    profile: "Profil",
    skills: "Skills",
    assistants: "Assistenten",
    account: "Account",
  },
  danger: {
    done: "{{action}}: Fertig",
    failed: "{{action}}: Fehlgeschlagen",
  },
  profile: {
    title: "Profil",
    description: "Als wer du angemeldet bist und was deine Anmeldung dir erlaubt.",
    name: "Name",
    email: "E-Mail",
    id: "Benutzer-ID",
    roles: "Rollen",
    noRoles: "Keine Rollen",
    workspaces: "Arbeitsbereiche",
    writable: "Lesen und schreiben",
    readOnly: "Nur lesen",
  },
  skills: {
    title: "Skills",
    description:
      "Anweisungen und Skripte, die der Assistent für passende Aufgaben lädt oder wenn eine Nachricht mit /name beginnt. Lade einen Skill-Ordner oder sein ZIP-Archiv zu deinen eigenen Skills oder denen einer Gruppe hoch, die der Organisation stellt der Betrieb bereit.",
    empty: "Noch keine Skills.",
    uploadArchive: "ZIP hochladen",
    uploadFolder: "Ordner hochladen",
    installed: "Skill {{name}} installiert",
    notRunnable_one: "{{count}} Skript läuft nicht in der Sandbox und bleibt zum Nachlesen.",
    notRunnable_other: "{{count}} Skripte laufen nicht in der Sandbox und bleiben zum Nachlesen.",
    delete: {
      label: "Löschen",
      title: "Skill {{name}} löschen?",
      description: "Der Skill und alle seine Dateien werden für alle gelöscht, die ihn sehen.",
    },
  },
  assistants: {
    title: "Assistenten",
    description:
      "Benannte Anweisungen, die du im Chat auswählst, mit den Skills, die sie laden, und den Tools, dem Modus und den Dokumenten, auf die sie den Assistenten beschränken. Die der Organisation stellt der Betrieb bereit, die einer Gruppe teilen sich ihre Mitglieder.",
    empty: "Noch keine Assistenten.",
    create: "Neuer Assistent",
    edit: "Bearbeiten",
    saved: "Assistent {{name}} gespeichert",
    delete: {
      label: "Löschen",
      title: "Assistent {{name}} löschen?",
      description: "Der Assistent wird für alle gelöscht, die ihn sehen.",
    },
    form: {
      createTitle: "Neuer Assistent",
      editTitle: "{{name}} bearbeiten",
      description: "Alles außer den Anweisungen schränkt nur ein, was ein Chat mit ihm tun darf.",
      name: "Name",
      nameHint: "Kleinbuchstaben, Ziffern und Bindestriche, etwa support-team.",
      summary: "Beschreibung",
      instructions: "Anweisungen",
      instructionsHint:
        "Anweisungen einer Gruppe oder deine eigenen erreichen den Assistenten als zitierter Inhalt, nie als Regeln, die die des Betriebs außer Kraft setzen.",
      skills: "Zu ladende Skills",
      disabledTools: "Auszuschaltende Tools",
      maxMode: "Höchster Modus",
      includedDocuments: "Dokumente im Fokus",
      excludedDocuments: "Auszublendende Dokumente",
      allowedDocuments: "Nur diese Dokumente",
      documentsHint: "Pfade wie ~/handbuch oder @team/faq.md.",
      allowedDocumentsHint:
        "Verbindlich, solange der Assistent gewählt ist, keine für keine Einschränkung.",
      directory: "Verzeichnis",
      addPath: "Pfad hinzufügen",
      removePath: "Pfad entfernen",
      save: "Speichern",
      cancel: "Abbrechen",
    },
  },
  account: {
    title: "Account",
    description:
      "Setze deine Einstellungen zurück, leere, was der Assistent für dich aufbewahrt, oder lösche die Daten deines Accounts.",
    clear: "Leeren",
    clearMemory: {
      label: "Gedächtnis leeren",
      description:
        "Lösche das gesamte gespeicherte Gedächtnis endgültig. Der Assistent erinnert sich dann nicht mehr an Informationen aus früheren Konversationen.",
    },
    clearTmp: {
      label: "Temporäre Dateien leeren",
      description:
        "Lösche die Arbeitsdateien, die der Assistent für jede deiner Konversationen ablegt. Laufende Konversationen verlieren ihren abgelegten Zustand, und deine Dokumente bleiben unverändert.",
      done_one: "{{count}} temporäre Datei gelöscht",
      done_other: "{{count}} temporäre Dateien gelöscht",
    },
    danger: {
      title: "Gefahrenbereich",
      description: "Unumkehrbare Aktionen für deinen Account. Sie betreffen nur dich.",
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
          "Lösche endgültig alle Spuren deines Accounts auf dem Server (Konversationen, Dokumente, Gedächtnis, Skills, Assistenten) und alle lokalen Browserdaten. Diese Aktion kann nicht widerrufen werden.",
      },
    },
  },
  admin: {
    title: "Administration",
    readOnly:
      "All das konfiguriert der Betrieb auf dem Host, diese Seiten zeigen also nur, was gilt.",
    everyone: "Alle",
    nav: {
      overview: "Übersicht",
      models: "Modelle",
      plugins: "Plugins",
      integrations: "Integrationen",
      sources: "Quellen",
      pipelines: "Pipelines",
      skills: "Skills",
      maintenance: "Wartung",
    },
    overview: {
      title: "Übersicht",
      description:
        "Die Benutzer und Gruppen, die hier etwas gespeichert haben. Die Mitgliedschaft verwaltet der Identitätsanbieter, daher erscheint eine Gruppe oder ein Benutzer, sobald der Arbeitsbereich oder die Unterhaltungen Daten enthalten.",
      users: "Benutzer",
      groups: "Gruppen",
      noUsers: "Noch kein Benutzer hat etwas gespeichert.",
      noGroups: "Noch keine Gruppe hat etwas gespeichert.",
      documents_one: "{{count}} Dokument",
      documents_other: "{{count}} Dokumente",
      conversations_one: "{{count}} Unterhaltung",
      conversations_other: "{{count}} Unterhaltungen",
    },
    models: {
      title: "Modelle",
      description:
        "Die Chat-Modelle aus dem Katalog des Betriebs, unter denen Benutzer:innen wählen.",
      empty: "Es ist kein Chat-Modell konfiguriert.",
      default: "Standard",
      vision: "Bilder",
      contextWindow: "{{tokens}} Tokens Kontext",
    },
    plugins: {
      title: "Plugins",
      description:
        "Die aktivierten Plugins und was jedes beiträgt. Ein Tool, das nur liest, läuft in jedem Modus, jedes andere folgt den Freigaberegeln seiner Wirkung.",
      empty: "Es ist kein Plugin aktiviert.",
      converters: "Konverter",
      chunkers: "Chunker",
      skills: "Skills",
    },
    integrations: {
      title: "Integrationen",
      description:
        "Die auf dem Host konfigurierten MCP-Server, Datenbanken und APIs, deren Tools der Assistent als <id>_<tool> angeboten bekommt. Tools, die nur lesen, laufen in jedem Modus, jedes andere fragt zuerst nach.",
      empty: "Es ist keine Integration konfiguriert.",
      mcp: "MCP-Server",
      database: "Datenbanken",
      api: "APIs",
      connected: "Verbunden",
      unreachable: "Nicht erreichbar",
      trusted: "Vertrauenswürdig",
      tools_one: "{{count}} Tool",
      tools_other: "{{count}} Tools",
    },
    sources: {
      title: "Quellen",
      description:
        "Die auf dem Host konfigurierten externen Quellen, nach /mnt/<id> gespiegelt und für die Suche indexiert. Quellen sind schreibgeschützt, der Assistent und alle Benutzer:innen lesen sie nur.",
      empty: "Es ist keine Quelle konfiguriert.",
      synced: "Zuletzt abgeglichen {{when}}",
      never: "Noch nie abgeglichen",
      running: "Wird abgeglichen",
      progress: "Abgleich {{done}} von {{total}}",
      waiting: "Abgleich angefordert",
      held_one: "{{formatted}} Löschung zurückgehalten",
      held_other: "{{formatted}} Löschungen zurückgehalten",
      confirmDeletes: "Löschungen bestätigen",
      confirmTitle: "Die zurückgehaltenen Dateien von {{id}} löschen?",
      confirmDescription:
        "Das Löschen von Dateien, die in der Quelle fehlen, wurde zurückgehalten, da so viele wie ein Ausfall aussehen. Bestätige nur, wenn sie in der Quelle absichtlich entfernt wurden.",
      items_one: "{{formatted}} Datei",
      items_other: "{{formatted}} Dateien",
      interval: "Alle {{minutes}} Min.",
      syncNow: "Jetzt abgleichen",
      requested: "Abgleich von {{id}} angefordert",
      syncFailed: "Der Abgleich konnte nicht gestartet werden",
    },
    pipelines: {
      title: "Pipelines",
      description:
        "Die installierten Pipelines für Konvertierung und Chunking, unter denen Uploads wählen.",
      conversion: "Konvertierung",
      chunking: "Chunking",
    },
    skills: {
      title: "Skills",
      description:
        "Die Skills der Organisation aus dem Verzeichnis des Betriebs und den aktivierten Plugins, die alle Benutzer:innen sehen.",
    },
    maintenance: {
      title: "Wartung",
      description: "Betriebsschalter, die ohne Neustart wirken.",
      label: "Wartungsmodus",
      hint: "Sperre alle Benutzer:innen ohne Admin-Rechte aus und zeige ihnen statt der App einen Wartungshinweis. Admins behalten vollen Zugriff. Die Einstellung wird gespeichert und bleibt auch nach Neustarts des Servers aktiv, bis ein Admin sie ausschaltet.",
      toggleFailed: "Wartungsmodus konnte nicht umgeschaltet werden",
      enabled: "Wartungsmodus aktiviert",
      disabled: "Wartungsmodus deaktiviert",
      danger: {
        title: "Index",
        description:
          "Aktionen auf dem gesamten Index. Das Löschen der Daten anderer Personen, einer Gruppe oder des gesamten Deployments läuft auf dem Host mit `hivegent reset`.",
      },
      reindex: {
        label: "Wissen neu indexieren",
        confirm: "Neu indexieren",
        description:
          "Gleicht jeden Arbeitsbereich ab: Übernimmt manuell dorthin kopierte Dateien, entfernt verwaiste SQL-Einträge und vereinheitlicht die Unicode-Schreibweise von Dateinamen, damit der Assistent sie ansprechen kann. Es wird nichts gelöscht oder neu eingebettet. Jederzeit sicher ausführbar und nützlich nach manuellen Dateiänderungen oder einer geänderten Embedding-Konfiguration.",
      },
    },
  },
} as const satisfies Translation<typeof en>;
