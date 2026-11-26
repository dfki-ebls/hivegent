import type { settings as en } from "@/i18n/locales/en/settings";
import type { Translation } from "@/i18n/translation";

export const settings = {
  nav: {
    title: "Einstellungen",
    profile: "Profil",
    instructions: "Anweisungen",
    skills: "Skills",
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
      "Anweisungen und Skripte, die der Assistent für passende Aufgaben lädt oder wenn eine Nachricht mit /name beginnt. Schreibe einen Skill oder lade einen Skill-Ordner oder sein ZIP-Archiv zu deinen eigenen Skills oder denen einer Gruppe hoch, die der Organisation stellt der Betrieb bereit.",
    empty: "Noch keine Skills.",
    create: "Neuer Skill",
    saved: "Skill {{name}} gespeichert",
    form: {
      createTitle: "Neuer Skill",
      editTitle: "{{name}} bearbeiten",
      description:
        "Eine SKILL.md beginnt mit einem Frontmatter, das den Skill benennt und beschreibt, wann er passt, gefolgt von seinen Anweisungen. Lade einen Ordner hoch, um Skripte oder andere Dateien hinzuzufügen.",
      name: "Name",
      nameHint:
        "Kleinbuchstaben, Ziffern und Bindestriche, etwa csv-check. Er muss dem Namen im Frontmatter entsprechen.",
      content: "SKILL.md",
      templateDescription: "Was der Skill tut und wann er passt.",
      templateBody: "Die Anweisungen, denen zu folgen ist.",
      save: "Speichern",
      cancel: "Abbrechen",
      close: "Schließen",
    },
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
  instructions: {
    title: "Anweisungen",
    description:
      "Was der Assistent in all deinen Chats beachtet, etwa deine Rolle, die Sprache und den Ton, die du bevorzugst, oder wie Antworten formatiert sein sollen. Was nur manche Aufgaben brauchen, gehört in einen Skill.",
    label: "Deine Anweisungen",
    hint: "Sie erreichen den Assistenten als deine eigenen Worte, nie als Regeln, die die des Betriebs außer Kraft setzen.",
    save: "Speichern",
    saved: "Deine Anweisungen sind gespeichert",
    memory: {
      title: "Gedächtnis",
      description:
        "Was sich der Assistent in früheren Chats über dich notiert hat, jede Notiz erst nach deiner Zustimmung gespeichert. Er liest das Gedächtnis als Notizen, nie als Anweisungen, Regeln gehören also in deine Anweisungen oben.",
      label: "Gemerkte Notizen",
      save: "Speichern",
      saved: "Das Gedächtnis ist gespeichert",
    },
  },
  account: {
    title: "Account",
    description:
      "Exportiere deine Daten, leere, was der Assistent für dich aufbewahrt, oder lösche die Daten deines Accounts.",
    clear: "Leeren",
    export: {
      title: "Export",
      description:
        "Lade deine Daten als ZIP-Archiv herunter, alles auf einmal oder einzeln. Das Archiv ist eine Kopie zum Aufbewahren, nicht zum erneuten Importieren.",
      download: "Herunterladen",
      parts: {
        all: {
          label: "Alles",
          description: "Alle folgenden Teile in einem Archiv.",
        },
        documents: {
          label: "Dokumente",
          description:
            "Die Dateien deines persönlichen Arbeitsbereichs, Originale und ihr Markdown.",
        },
        conversations: {
          label: "Konversationen",
          description:
            "Jeder Chat mit den Systemprompts, unter denen er lief, und seinen temporären Dateien.",
        },
        memory: {
          label: "Gedächtnis",
          description: "Was sich der Assistent über Konversationen hinweg über dich merkt.",
        },
        skills: {
          label: "Skills",
          description: "Die Dateien deiner eigenen Skills.",
        },
        preferences: {
          label: "Einstellungen",
          description: "Die Entscheidungen, die du für deine Chats getroffen hast.",
        },
      },
    },
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
          "Lösche endgültig alle Spuren deines Accounts auf dem Server (Konversationen, Dokumente, Gedächtnis, Skills) und alle lokalen Browserdaten. Diese Aktion kann nicht widerrufen werden.",
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
