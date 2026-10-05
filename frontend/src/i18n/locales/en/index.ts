import { app } from "@/i18n/locales/en/app";
import { chat } from "@/i18n/locales/en/chat";
import { common } from "@/i18n/locales/en/common";
import { documents } from "@/i18n/locales/en/documents";
import { options } from "@/i18n/locales/en/options";
import { settings } from "@/i18n/locales/en/settings";

/** The source catalog: its literal types drive key and interpolation checking. */
export const en = { app, chat, common, documents, options, settings } as const;
