import { Link } from "@tanstack/react-router";
import { FileSearch, LogOut, User, UserCog } from "lucide-react";
import { useOidc } from "../oidc";
import { Button } from "./ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "./ui/dropdown-menu";

function UserMenu() {
  const oidc = useOidc();

  if (!oidc.isUserLoggedIn) {
    return null;
  }

  const { decodedIdToken, logout } = oidc;
  const userName =
    decodedIdToken.name ?? decodedIdToken.preferred_username ?? decodedIdToken.email ?? "User";

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button variant="ghost" size="sm" className="gap-2">
          <User className="h-4 w-4" />
          <span className="hidden sm:inline">{userName}</span>
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end">
        <DropdownMenuItem asChild>
          <Link to="/settings/account" className="flex items-center gap-2">
            <UserCog className="h-4 w-4" />
            Account
          </Link>
        </DropdownMenuItem>
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
    <header className="flex items-center justify-between border-b bg-background px-4 py-3">
      <Link
        to="/"
        className="flex items-center gap-2 text-foreground hover:opacity-80 transition-opacity"
      >
        <FileSearch className="h-6 w-6" />
        <h1 className="text-xl font-semibold">Hivegent</h1>
      </Link>

      <UserMenu />
    </header>
  );
}
