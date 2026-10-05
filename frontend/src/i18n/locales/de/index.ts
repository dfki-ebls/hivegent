import { app } from "@/i18n/locales/de/app";
import { chat } from "@/i18n/locales/de/chat";
import { common } from "@/i18n/locales/de/common";
import { documents } from "@/i18n/locales/de/documents";
import { options } from "@/i18n/locales/de/options";
import { settings } from "@/i18n/locales/de/settings";
import type { en } from "@/i18n/locales/en";
import type { Translation } from "@/i18n/translation";

export const de = {
  app,
  chat,
  common,
  documents,
  options,
  settings,
} as const satisfies Translation<typeof en>;
