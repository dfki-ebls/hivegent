import { Link } from "@tanstack/react-router";
import { BugIcon, LogOutIcon, UserCogIcon, UserIcon } from "lucide-react";
import { useTranslation } from "react-i18next";
import { useOidc } from "@/oidc";
import { DocsLink } from "@/components/DocsLink";
import { JobTray } from "@/components/JobTray";
import { Logo } from "@/components/Logo";
import { VersionBadge } from "@/components/VersionBadge";
import { useIsAdmin } from "@/queries/settings";
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

  if (!oidc.isUserLoggedIn) {
    return null;
  }

  const { decodedIdToken, logout } = oidc;
  const fullName = [decodedIdToken.given_name, decodedIdToken.family_name]
    .filter(Boolean)
    .join(" ");
  const userName =
    fullName ||
    decodedIdToken.name ||
    decodedIdToken.preferred_username ||
    decodedIdToken.email ||
    t(($) => $.app.userMenu.fallbackName);

  return (
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
                {decodedIdToken.email && <ItemDescription>{decodedIdToken.email}</ItemDescription>}
              </ItemContent>
            </Item>
          </DropdownMenuLabel>
          <DropdownMenuItem render={<Link to="/settings/account" />}>
            <UserCogIcon />
            {t(($) => $.app.userMenu.account)}
          </DropdownMenuItem>
          <DropdownMenuItem render={<DocsLink />} />
          {isAdmin && (
            <DropdownMenuItem render={<Link to="/debug" />}>
              <BugIcon />
              {t(($) => $.app.userMenu.toolDebugger)}
            </DropdownMenuItem>
          )}
        </DropdownMenuGroup>
        <DropdownMenuSeparator />
        <DropdownMenuItem onClick={() => logout({ redirectTo: "home" })}>
          <LogOutIcon />
          {t(($) => $.app.userMenu.signOut)}
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
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
