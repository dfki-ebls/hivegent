import "i18next";

import type { en } from "@/i18n/locales/en";

declare module "i18next" {
  interface CustomTypeOptions {
    defaultNS: "translation";
    resources: { translation: typeof en };
    enableSelector: true;
    strictKeyChecks: true;
  }
}
