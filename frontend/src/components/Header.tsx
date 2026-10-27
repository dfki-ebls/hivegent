import { Link } from "@tanstack/react-router";
import {
  BugIcon,
  LogOutIcon,
  ScanSearchIcon,
  UserCogIcon,
  UserIcon,
  WrenchIcon,
} from "lucide-react";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { useOidc } from "@/oidc";
import { DebugChatDialog } from "@/components/DebugChatDialog";
import { AiTransparencyDialog } from "@/components/AiTransparencyDialog";
import { DocsLink } from "@/components/DocsLink";
import { LicensesLink } from "@/components/LicensesLink";
import { JobTray } from "@/components/JobTray";
import { Logo } from "@/components/Logo";
import { VersionBadge } from "@/components/VersionBadge";
import { useIsAdmin, useServerSettings } from "@/queries/settings";
import { useUserName } from "@/hooks/use-user-name";
import { PRODUCT_NAME } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuGroup,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Item, ItemContent, ItemDescription, ItemTitle } from "@/components/ui/item";

function UserMenu() {
  const { t } = useTranslation();
  const oidc = useOidc();
  const isAdmin = useIsAdmin();
  const transparency = useServerSettings((s) => s.transparency);
  const userName = useUserName();
  const [debugChatOpen, setDebugChatOpen] = useState(false);
  const [transparencyOpen, setTransparencyOpen] = useState(false);

  if (!oidc.isUserLoggedIn) {
    return null;
  }

  const { decodedIdToken, logout } = oidc;

  return (
    <>
      <DropdownMenu>
        <DropdownMenuTrigger render={<Button variant="ghost" size="sm" aria-label={userName} />}>
          <UserIcon data-icon="inline-start" />
          <span className="hidden sm:inline">{userName}</span>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="end" className="w-auto">
          <DropdownMenuGroup>
            <DropdownMenuLabel>
              <Item size="xs">
                <ItemContent>
                  <ItemTitle>{userName}</ItemTitle>
                  {decodedIdToken.email && (
                    <ItemDescription>{decodedIdToken.email}</ItemDescription>
                  )}
                </ItemContent>
              </Item>
            </DropdownMenuLabel>
            <DropdownMenuItem render={<Link to="/settings/account" />}>
              <UserCogIcon />
              {t(($) => $.app.userMenu.account)}
            </DropdownMenuItem>
            {transparency?.enabled && (
              <DropdownMenuItem onClick={() => setTransparencyOpen(true)}>
                <ScanSearchIcon />
                {t(($) => $.app.userMenu.verifyText)}
              </DropdownMenuItem>
            )}
            <DropdownMenuItem render={<DocsLink />} />
            <DropdownMenuItem render={<LicensesLink />} />
            {isAdmin && (
              <>
                <DropdownMenuItem render={<Link to="/debug/tools" />}>
                  <WrenchIcon />
                  {t(($) => $.app.userMenu.toolDebugger)}
                </DropdownMenuItem>
                <DropdownMenuItem onClick={() => setDebugChatOpen(true)}>
                  <BugIcon />
                  {t(($) => $.app.userMenu.chatDebugger)}
                </DropdownMenuItem>
              </>
            )}
          </DropdownMenuGroup>
          <DropdownMenuSeparator />
          <DropdownMenuItem onClick={() => logout({ redirectTo: "home" })}>
            <LogOutIcon />
            {t(($) => $.app.userMenu.signOut)}
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>
      {isAdmin && <DebugChatDialog open={debugChatOpen} onOpenChange={setDebugChatOpen} />}
      {transparency?.enabled && (
        <AiTransparencyDialog
          open={transparencyOpen}
          onOpenChange={setTransparencyOpen}
          contactEmail={transparency.contact_email}
          minimumTokens={transparency.minimum_watermark_tokens}
        />
      )}
    </>
  );
}

export function Header() {
  return (
    <header className="flex items-center justify-between border-b bg-background px-4 py-2">
      <Link to="/" className="flex items-center gap-2 transition-opacity hover:opacity-80">
        <Logo className="size-10" />
        <h1 className="text-xl font-semibold">{PRODUCT_NAME}</h1>
        <VersionBadge />
      </Link>

      <div className="flex items-center gap-1">
        <JobTray />
        <UserMenu />
      </div>
    </header>
  );
}
