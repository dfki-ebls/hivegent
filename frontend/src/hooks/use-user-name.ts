import { useTranslation } from "react-i18next";
import { useOidc } from "@/oidc";

/** The most readable name the ID token offers for the current user. */
export function useUserName(): string {
  const { t } = useTranslation();
  const oidc = useOidc();
  const token = oidc.isUserLoggedIn ? oidc.decodedIdToken : undefined;
  const fullName = [token?.given_name, token?.family_name].filter(Boolean).join(" ");

  return (
    fullName ||
    token?.name ||
    token?.preferred_username ||
    token?.email ||
    t(($) => $.app.userMenu.fallbackName)
  );
}
