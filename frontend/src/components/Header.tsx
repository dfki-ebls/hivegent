import { Link } from "@tanstack/react-router";
import { Bug, LogOut, User, UserCog } from "lucide-react";
import { useOidc } from "../oidc";
import { JobTray } from "./JobTray";
import { Logo } from "./Logo";
import { VersionBadge } from "./VersionBadge";
import { selectIsAdmin, useSettingsStore } from "../stores/settings-store";
import { Button } from "./ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "./ui/dropdown-menu";

function UserMenu() {
  const oidc = useOidc();
  const isAdmin = useSettingsStore(selectIsAdmin);

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
    "User";

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button variant="ghost" size="sm" className="gap-2">
          <User className="h-4 w-4" />
          <span className="hidden sm:inline">{userName}</span>
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end">
        {decodedIdToken.email && (
          <>
            <DropdownMenuLabel className="font-normal text-muted-foreground">
              {decodedIdToken.email}
            </DropdownMenuLabel>
            <DropdownMenuSeparator />
          </>
        )}
        <DropdownMenuItem asChild>
          <Link to="/settings/account" className="flex items-center gap-2">
            <UserCog className="h-4 w-4" />
            Account
          </Link>
        </DropdownMenuItem>
        {isAdmin && (
          <DropdownMenuItem asChild>
            <Link to="/debug" className="flex items-center gap-2">
              <Bug className="h-4 w-4" />
              Tool Debugger
            </Link>
          </DropdownMenuItem>
        )}
        <DropdownMenuSeparator />
        <DropdownMenuItem
          onClick={() => logout({ redirectTo: "home" })}
          className="flex items-center gap-2"
        >
          <LogOut className="h-4 w-4" />
          Sign out
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

export function Header() {
  return (
    <header className="flex items-center justify-between border-b bg-background px-4 py-2">
      <Link
        to="/"
        className="flex items-center gap-2 text-foreground hover:opacity-80 transition-opacity"
      >
        <Logo className="h-10 w-10" />
        <h1 className="text-xl font-semibold">Hivegent</h1>
        <VersionBadge />
      </Link>

      <div className="flex items-center gap-1">
        <JobTray />
        <UserMenu />
      </div>
    </header>
  );
}
