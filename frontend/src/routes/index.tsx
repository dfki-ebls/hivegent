import { createFileRoute, redirect } from "@tanstack/react-router";
import { LogInIcon } from "lucide-react";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import { DocsLink } from "@/components/docs-link";
import { LicensesLink } from "@/components/licenses-link";
import { Logo } from "@/components/logo";
import { VersionBadge } from "@/components/version-badge";
import { Button } from "@/components/ui/button";
import { Spinner } from "@/components/common/spinner";
import { getOidc, useOidc } from "@/oidc";
import { PRODUCT_NAME } from "@/lib/utils";

const LINK_BUTTON_PROPS = {
  variant: "ghost",
  size: "sm",
  className: "text-muted-foreground",
  nativeButton: false,
} as const;

export const Route = createFileRoute("/")({
  // A new chat is a conversation the client names, which its first turn stores.
  beforeLoad: async () => {
    if ((await getOidc()).isUserLoggedIn) {
      throw redirect({
        to: "/conversations/$id",
        params: { id: crypto.randomUUID() },
        replace: true,
      });
    }
  },
  component: IndexPage,
});

function IndexPage() {
  const { t } = useTranslation();
  const oidc = useOidc();
  const [isSigningIn, setIsSigningIn] = useState(false);

  // The route sends a signed-in user on to a new chat.
  if (oidc.isUserLoggedIn) return null;

  const { login } = oidc;

  return (
    <div className="flex h-full items-center justify-center p-6">
      <div className="flex flex-col items-center gap-6 text-center">
        <Logo className="w-60 max-w-full" />
        <div className="flex items-center gap-3">
          <h1 className="text-4xl font-bold">{PRODUCT_NAME}</h1>
          <VersionBadge className="self-start" />
        </div>
        <p className="max-w-md text-lg text-muted-foreground">{t(($) => $.app.landing.tagline)}</p>
        <Button
          size="lg"
          disabled={isSigningIn}
          onClick={() => {
            setIsSigningIn(true);
            login().catch(() => setIsSigningIn(false));
          }}
        >
          {isSigningIn ? (
            <Spinner data-icon="inline-start" />
          ) : (
            <LogInIcon data-icon="inline-start" />
          )}
          {t(($) => $.app.landing.signIn)}
        </Button>
        <div className="flex flex-wrap justify-center gap-1">
          <Button {...LINK_BUTTON_PROPS} render={<DocsLink />} />
          <Button {...LINK_BUTTON_PROPS} render={<LicensesLink />} />
        </div>
      </div>
    </div>
  );
}
