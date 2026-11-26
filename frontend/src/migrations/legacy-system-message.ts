/**
 * One-off migration of the custom system message that earlier releases kept in
 * the browser into the user's instructions on the server.
 *
 * It runs once per browser after sign-in and deletes the stored settings when
 * done, so it is idempotent and a failure retries on the next load.  Remove
 * this file and its call in `routes/__root.tsx` once the release shipping it
 * has been deployed everywhere for a while.
 */

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef } from "react";
import { z } from "zod";

import { MAX_INSTRUCTIONS, preferenceMutations, preferenceQueries } from "@/api/preferences";

/** The key the settings store of earlier releases persisted under. */
const STORAGE_KEY = "hivegent-settings";

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
 * Turn the custom system message of an earlier release into the user's
 * instructions once *enabled*, never replacing instructions they already wrote.
 */
export function useLegacySystemMessageMigration(enabled: boolean): void {
  const client = useQueryClient();
  const { mutateAsync: setPreferences } = useMutation(preferenceMutations.set());
  const started = useRef(false);

  useEffect(() => {
    const stored = localStorage.getItem(STORAGE_KEY);

    if (!enabled || stored === null || started.current) return;

    started.current = true;

    const migrate = async () => {
      const instructions = legacySystemMessage(stored);

      if (instructions && !(await client.query(preferenceQueries.get())).instructions) {
        await setPreferences({ instructions });
      }

      localStorage.removeItem(STORAGE_KEY);
    };

    // A failure keeps the stored settings for the next load, the mutation toasts it.
    migrate().catch((error: unknown) => console.warn("System message migration failed", error));
  }, [enabled, client, setPreferences]);
}
