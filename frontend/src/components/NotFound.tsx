import { Link } from "@tanstack/react-router";
import { Compass, MoveLeft } from "lucide-react";

import { Button } from "./ui/button";

/**
 * Catch-all screen rendered by the root route whenever no route matches the
 * current URL. It lives inside the main outlet, so the header chrome stays in
 * place and the user can navigate straight back home.
 */
export function NotFound() {
  return (
    <div className="flex h-full flex-col items-center justify-center gap-6 p-8 text-center">
      <div className="relative flex items-center justify-center">
        <span className="select-none text-9xl font-bold tracking-tight text-muted-foreground/10">
          404
        </span>
        <Compass className="absolute h-14 w-14 text-muted-foreground" />
      </div>
      <div className="flex flex-col items-center gap-2">
        <h1 className="text-2xl font-semibold">Page not found</h1>
        <p className="max-w-md text-sm text-muted-foreground">
          The page you are looking for does not exist or may have been moved.
        </p>
      </div>
      <Button asChild>
        <Link to="/">
          <MoveLeft className="mr-2 h-4 w-4" />
          Back to home
        </Link>
      </Button>
    </div>
  );
}
