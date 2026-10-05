import i18n, { type KeyPrefixSelector } from "i18next";
import { initReactI18next } from "react-i18next";

import { de } from "@/i18n/locales/de";
import { en } from "@/i18n/locales/en";

const LANGUAGES = ["en", "de"] as const;

export type Language = (typeof LANGUAGES)[number];

function isLanguage(value: string | undefined): value is Language {
  return LANGUAGES.some((language) => language === value);
}

/**
 * The first supported language in the browser's preference list, a regional variant
 * matching its base language, and English when none is supported.
 */
export function browserLanguage(tags: readonly string[]): Language {
  return tags.map((tag) => tag.split("-")[0]).find(isLanguage) ?? "en";
}

/** The interface language, fixed for the page's lifetime since only the browser decides it. */
export const LANGUAGE = browserLanguage(navigator.languages);

/** Bundled statically: two small catalogs cost less than a lazy-loading round trip. */
void i18n.use(initReactI18next).init({
  resources: { en: { translation: en }, de: { translation: de } },
  lng: LANGUAGE,
  initAsync: false,
  interpolation: { escapeValue: false },
});

document.documentElement.lang = LANGUAGE;

/** Whether *key* has an entry in *catalog*, narrowing it for a typed lookup. */
export function isCatalogKey<T extends object>(catalog: T, key: PropertyKey): key is keyof T {
  return Object.hasOwn(catalog, key);
}

/**
 * `useTranslation` options scoped to a catalog section, created once at module level:
 * react-i18next memoizes on the options' identity, so inline options rebuild `t` every render.
 */
export function keyPrefix<const K extends KeyPrefixSelector<"translation">>(
  selector: K,
): { keyPrefix: K } {
  return { keyPrefix: selector };
}

export { i18n };
