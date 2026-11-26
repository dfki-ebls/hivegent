/**
 * One-off migration of the custom system message that earlier releases kept in
 * the browser into a personal assistant on the server.
 *
 * It runs once per browser after sign-in and deletes the stored settings when
 * done, so it is idempotent and a failure retries on the next load.  Remove
 * this file and its call in `routes/__root.tsx` once the release shipping it
 * has been deployed everywhere for a while.
 */

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef } from "react";
import { z } from "zod";

import { assistantMutations, assistantQueries } from "@/api/assistants";
import { PERSONAL_SCOPE } from "@/api/documents";
import { preferenceMutations, preferenceQueries } from "@/api/preferences";
import { LANGUAGE, type Language } from "@/i18n";

/** The key the settings store of earlier releases persisted under. */
const STORAGE_KEY = "hivegent-settings";

/** The name of the assistant the system message becomes. */
const NAME = "custom";

/** The backend's limit on an assistant's instructions. */
const MAX_INSTRUCTIONS = 20_000;

const DESCRIPTION: Record<Language, string> = {
  en: "Taken over from the custom system message of your earlier browser settings",
  de: "Übernommen aus der eigenen Systemnachricht deiner früheren Browsereinstellungen",
};

const LegacySettingsSchema = z.object({
  state: z.object({ personality: z.string(), customSystemMessage: z.string() }),
});

/** The custom system message the *stored* settings of an earlier release chose, if any. */
export function legacySystemMessage(stored: string): string | undefined {
  let json: unknown;

  try {
    json = JSON.parse(stored);
  } catch {
    return undefined;
  }

  const settings = LegacySettingsSchema.safeParse(json).data?.state;

  if (settings?.personality !== "custom") return undefined;

  return settings.customSystemMessage.trim().slice(0, MAX_INSTRUCTIONS) || undefined;
}

/**
 * Turn the custom system message of an earlier release into a personal
 * assistant once *enabled*, chosen unless the user already chose one, and
 * never replacing an assistant of the same name.
 */
export function useLegacySystemMessageMigration(enabled: boolean): void {
  const client = useQueryClient();
  const { mutateAsync: saveAssistant } = useMutation(assistantMutations.save());
  const { mutateAsync: setPreferences } = useMutation(preferenceMutations.set());
  const started = useRef(false);

  useEffect(() => {
    const stored = localStorage.getItem(STORAGE_KEY);

    if (!enabled || stored === null || started.current) return;

    started.current = true;

    const migrate = async () => {
      const instructions = legacySystemMessage(stored);

      if (instructions) {
        const [assistants, preferences] = await Promise.all([
          client.query(assistantQueries.list()),
          client.query(preferenceQueries.get()),
        ]);
        const taken = assistants.some(
          ({ scope, name }) => scope === PERSONAL_SCOPE && name === NAME,
        );

        if (!taken) {
          const saved = await saveAssistant({
            scope: PERSONAL_SCOPE,
            name: NAME,
            spec: {
              description: DESCRIPTION[LANGUAGE],
              instructions,
              skills: [],
              disabled_tools: [],
              included_documents: [],
              excluded_documents: [],
              allowed_documents: [],
              max_mode: "write",
            },
          });

          if (preferences.assistant === null) {
            await setPreferences({ assistant: saved.ref });
          }
        }
      }

      localStorage.removeItem(STORAGE_KEY);
    };

    // A failure keeps the stored settings for the next load, the mutations toast it.
    migrate().catch((error: unknown) => console.warn("System message migration failed", error));
  }, [enabled, client, saveAssistant, setPreferences]);
}
