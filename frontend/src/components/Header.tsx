import { Link, useNavigate } from "@tanstack/react-router";
import { FileSearch, Key, LogOut, User, UserCog } from "lucide-react";
import { isOidcConfigured } from "../lib/auth-config";
import { useAuth } from "../lib/auth-context";
import { Button } from "./ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "./ui/dropdown-menu";

function UserMenu() {
  const auth = useAuth();
  const navigate = useNavigate();

  if (!auth.isAuthenticated) {
    return null;
  }

  const userName = auth.user?.name || auth.user?.email || "User";

  const handleLogout = async () => {
    await auth.signOut();
    // For local auth, navigate to home after sign out
    // For OIDC, signOut triggers a redirect
    if (!isOidcConfigured()) {
      navigate({ to: "/" });
    }
  };

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
        <DropdownMenuItem asChild>
          <Link to="/settings/tokens" className="flex items-center gap-2">
            <Key className="h-4 w-4" />
            API Tokens
          </Link>
        </DropdownMenuItem>
        <DropdownMenuSeparator />
        <DropdownMenuItem onClick={handleLogout} className="flex items-center gap-2">
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
