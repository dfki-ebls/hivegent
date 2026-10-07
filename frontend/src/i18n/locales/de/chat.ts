import type { chat as en } from "@/i18n/locales/en/chat";
import type { Translation } from "@/i18n/translation";

export const chat = {
  layout: {
    documents: "Dokumente",
    documentsDescription: "Kontext und Dokumente durchsuchen",
    viewDocuments: "Dokumente anzeigen",
  },
  header: {
    chat: "Chat",
    history: "Verlauf",
    import: "Konversation aus JSON importieren",
    importFile: "Konversationsdatei importieren",
    compact: "Konversation komprimieren",
    export: "Konversation als JSON exportieren",
    newChat: "Neuer Chat",
  },
  sidebar: {
    imported: "Konversation importiert",
    approvalBlocked: "Verfügbar, sobald die aktuelle Antwort fertig ist.",
    toolDenied:
      "Dieser Tool-Aufruf wurde abgelehnt und daher nicht ausgeführt. Rufe dasselbe Tool nicht erneut mit denselben oder ähnlichen Argumenten auf. Brich diesen Schritt ab, erkläre, was du vorhattest, und warte auf weitere Anweisungen.",
  },
  messages: {
    emptyTitle: "Frag zu deinen Dokumenten",
    emptyDescription:
      "Starte eine Konversation, um deine Dokumente zu durchsuchen und zu erkunden.",
    scrollToEnd: "Zum Ende scrollen",
    dismiss: "Ausblenden",
    submitEdit: "Senden",
    attachedImage: "Angehängtes Bild",
  },
  reasoning: {
    thinking: "Denkt nach …",
    thoughtBriefly: "Einige Sekunden nachgedacht",
    thoughtFor_one: "{{count}} Sekunde nachgedacht",
    thoughtFor_other: "{{count}} Sekunden nachgedacht",
  },
  contextLimit: {
    title: "Kontextlimit erreicht",
    message:
      "Komprimiere diese Konversation, um ihren Verlauf zusammenzufassen, und sende die letzte Nachricht dann erneut.",
    action: "Komprimieren und wiederholen",
  },
  compaction: {
    bannerTitle: "Fortgesetzte Konversation",
    bannerText:
      "Diese Konversation wurde aus einem <previous>früheren Chat</previous> komprimiert.",
    compacting: "Konversation wird komprimiert",
    compactingDescription:
      "Frühere Nachrichten werden zusammengefasst, damit sie ins Kontextfenster passen.",
    compacted: "Konversation komprimiert",
    compactedDescription: "Öffne sie, um dort weiterzumachen, wo dieser Chat aufgehört hat.",
    failed: "Konversation konnte nicht komprimiert werden",
  },
  navGuard: {
    title: "Aktuelle Antwort stoppen?",
    description:
      "Dieser Chat erzeugt noch eine Antwort. Wenn du ihn jetzt verlässt, wird sie gestoppt. Eine unvollständige Antwort wird gesichert, sodass du später zurückkehren und diese Konversation fortsetzen kannst.",
    stay: "Bleiben",
    leave: "Verlassen und stoppen",
  },
  steering: {
    queued_one: "Nachricht in der Warteschlange",
    queued_other: "Nachrichten in der Warteschlange",
  },
  composer: {
    placeholder: "Frag zu deinen Dokumenten …",
    steerPlaceholder: "Konversation lenken …",
    submit: "Senden",
    stop: "Stoppen",
    dictate: "Diktieren",
    attachImage: "Bild anhängen",
    attachImageHint: "Hänge ein Bild an, das sich der Assistent ansehen soll",
    removeAttachment: "{{name}} entfernen",
    removeImage: "Bild entfernen",
    removeFilter: "{{name}} aus dem Filter entfernen",
    attachmentErrors: {
      accept:
        "Du kannst nur Bilder anhängen. Lade andere Dokumente in deinen Arbeitsbereich hoch, dort kann der Assistent sie durchsuchen.",
      maxFileSize: "Bilder müssen kleiner als {{size}} sein.",
      maxFiles_one:
        "Du kannst höchstens {{count}} Bild anhängen, da der Modellserver pro Anfrage nicht mehr annimmt.",
      maxFiles_other:
        "Du kannst höchstens {{count}} Bilder anhängen, da der Modellserver pro Anfrage nicht mehr annimmt.",
    },
  },
  suggestions: {
    overview: {
      label: "Überblick über den Arbeitsbereich",
      prompt:
        "Gib mir einen Überblick über meine Dokumentsammlung. Liste die wichtigsten Ordner oder Kategorien auf und beschreibe kurz, welche Arten von Geschäftsdokumenten jeweils enthalten sind.",
    },
    actionItems: {
      label: "Offene Aufgaben",
      prompt:
        "Durchsuche meine Dokumente nach offenen Aufgaben, To-dos und Nachverfolgungen. Liste jede davon mit der verantwortlichen Person und einer eventuell genannten Frist auf.",
    },
    deadlines: {
      label: "Anstehende Fristen",
      prompt:
        "Finde die wichtigen Termine und Fristen in meinen Dokumenten und liste sie chronologisch auf. Gib jeweils an, aus welchem Dokument sie stammen.",
    },
    decisions: {
      label: "Wichtige Entscheidungen",
      prompt:
        "Finde die wichtigen Entscheidungen, die in meinen Dokumenten festgehalten sind. Fasse für jede zusammen, was entschieden wurde, und nenne das Dokument und das Datum.",
    },
    risks: {
      label: "Risiken und Hindernisse",
      prompt:
        "Durchsuche meine Dokumente nach Risiken, Hindernissen oder offenen Problemen, die angesprochen wurden, und fasse sie nach Thema gruppiert zusammen.",
    },
  },
  conversations: {
    loading: "Konversationen werden geladen …",
    untitled: "Ohne Titel",
    titleLabel: "Titel der Konversation",
    editTitle: "Titel bearbeiten",
    generateTitle: "Titel mit KI erzeugen",
    delete: "Konversation löschen",
    search: "Konversationen durchsuchen …",
    emptyTitle: "Noch keine Konversationen",
    emptyDescription: "Starte einen neuen Chat, um loszulegen",
    noMatchesTitle: "Keine passenden Konversationen",
    noMatchesDescription: "Versuche es mit einem anderen Suchbegriff",
    deleteTitle: "Konversation löschen?",
    deleteNamed:
      "„{{title}}“ und alle Nachrichten darin werden endgültig gelöscht. Du kannst diese Aktion nicht widerrufen.",
    deleteUnnamed:
      "Diese Konversation und alle Nachrichten darin werden endgültig gelöscht. Du kannst diese Aktion nicht widerrufen.",
  },
  disclosure: {
    notice: "Hivegent ist eine KI und kann Fehler machen. Überprüfe die Antworten bitte.",
  },
  transparency: {
    trigger: "KI-Text prüfen",
    title: "Hivegent-Text prüfen",
    description:
      "Prüfe einen Text auf das nicht wahrnehmbare Wasserzeichen von Hivegent. Der Text wird nur für diese Prüfung verarbeitet und nicht gespeichert.",
    textLabel: "Text",
    textDescription:
      "Verwende für ein verlässliches negatives Ergebnis mehr als {{tokens}} Tokens. Wasserzeichen können durch Bearbeitung oder Übersetzung beschädigt werden, daher beweist ein negatives Ergebnis nie, dass ein Mensch den Text verfasst hat.",
    placeholder: "Füge den zu prüfenden Text ein …",
    resultLabel: "Ergebnis",
    resultDescription:
      "Basiert auf einem nicht wahrnehmbaren Textwasserzeichen. Der signierte Bericht enthält nur einen SHA-256-Hash des eingereichten Texts und Metadaten des Detektors.",
    contact: "Qualifizierte externe Prüfer:innen können unter <email /> Zugang anfordern.",
    download: "Signierten Bericht herunterladen",
    verify: "Text prüfen",
    verifying: "Wird geprüft …",
  },
  citation: {
    openDocument: "Aktuelles Dokument öffnen",
    openEvidence: "Erfasste Zeilen öffnen",
    noEvidence: "Für diese Zeilen wurde keine belegende Tool-Ausgabe erfasst.",
    evidenceDescription:
      "{{position}} aus der Tool-Ausgabe, die mit dieser Konversation gesichert wurde. Der Dokumentlink öffnet separat die aktuelle Version im Arbeitsbereich.",
    capturedBy: "Erfasst durch {{source}}",
  },
  image: {
    unavailable: "[Bild nicht verfügbar: {{src}}]",
  },
  tools: {
    names: {
      delete_document: "Dokument löschen",
      edit_document: "Dokument bearbeiten",
      explore: "Erkunden",
      get_conversation: "Konversation lesen",
      glob_documents: "Dokumente finden",
      grep: "Text suchen",
      jq: "JSON abfragen",
      list_conversations: "Konversationen auflisten",
      list_documents: "Dokumente auflisten",
      move_document: "Dokument bewegen",
      query_table: "Tabelle abfragen",
      read_binary_document: "Binärdokument lesen",
      read_document: "Dokument lesen",
      run_python: "Python ausführen",
      save_memory: "Im Gedächtnis sichern",
      search: "Semantische Suche",
      web_fetch: "Webseite abrufen",
      web_search: "Websuche",
      write_document: "Dokument schreiben",
    },
    status: {
      "approval-requested": "Wartet auf Erlaubnis",
      "approval-responded": "Beantwortet",
      "input-available": "Läuft",
      "input-streaming": "Ausstehend",
      "output-available": "Abgeschlossen",
      "output-denied": "Abgelehnt",
      "output-error": "Fehler",
    },
    sections: {
      parameters: "Parameter",
      result: "Ergebnis",
      steps: "Schritte",
      program: "Programm",
    },
    approval: {
      question: "Darf der Assistent <bold>{{tool}}</bold> ausführen?",
      approve: "Erlauben",
      deny: "Ablehnen",
      approved: "Erlaubt",
      denied: "Abgelehnt",
    },
    subagent: {
      reasoning: "Denkprozess",
      response: "Antwort",
    },
    binary: {
      pages_one: "Seite {{pages}}",
      pages_other: "Seiten {{pages}}",
      frames_one: "{{count}} Frame ausgewertet",
      frames_other: "{{count}} Frames ausgewertet",
      duration: "{{duration}} s",
      attached: "An das Modell übergeben",
      previewUnavailable: "Vorschau nicht verfügbar",
    },
    runPython: {
      titleWithScript: "{{tool}} · {{script}}",
      viewSource: "Quelltext anzeigen",
      sourceDescription:
        "Das Skript in seiner aktuell gesicherten Form. Es wird bei jeder Ausführung neu gelesen, eine spätere Änderung kann also von der Version abweichen, die dieser Aufruf ausgeführt hat.",
      inlineProgram: "Inline-Programm",
      noProgram: "Es wurde weder Python-Code noch ein Skriptpfad angegeben.",
      truncated: "Die Ausgabe wurde gekürzt.",
      returned: "Rückgabewert:",
      noOutput: "Das Programm hat nichts ausgegeben und keinen Wert zurückgegeben.",
      savedTo: "Gesichert in <path>{{path}}</path>.",
      notSaved: "In <path>{{path}}</path> wurde nichts gesichert.",
      destination: "Ausgabeziel: <path>{{path}}</path>.",
      approvalQuestion: "Darf dieses Programm <path>{{path}}</path> erstellen oder ersetzen?",
      approvalHint:
        "Python läuft in einer isolierten Sandbox. Es kann deine Dokumente lesen und die Suchtools des Assistenten verwenden. Zum Sichern des hier genannten Dokuments ist deine Erlaubnis erforderlich. Prüfe zuerst das Programm.",
    },
  },
} as const satisfies Translation<typeof en>;
