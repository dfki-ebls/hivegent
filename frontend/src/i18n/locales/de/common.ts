import type { common as en } from "@/i18n/locales/en/common";
import type { Translation } from "@/i18n/translation";

export const common = {
  actions: {
    cancel: "Abbrechen",
    copy: "Kopieren",
    create: "Erstellen",
    delete: "Löschen",
    dismiss: "Ausblenden",
    edit: "Bearbeiten",
    open: "Öffnen",
    remove: "Entfernen",
    rename: "Umbenennen",
    retry: "Erneut versuchen",
    save: "Sichern",
    upload: "Hochladen",
  },
  states: {
    loading: "Wird geladen …",
    loadingImage: "Bild wird geladen …",
    working: "Wird ausgeführt …",
    error: "Fehler",
    unknownError: "Etwas ist schiefgelaufen.",
    copyFailed: "Kopieren in die Zwischenablage fehlgeschlagen.",
  },
} as const satisfies Translation<typeof en>;
