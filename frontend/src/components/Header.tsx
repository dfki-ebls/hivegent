import { Link } from '@tanstack/react-router';
import { FileSearch } from 'lucide-react';

export function Header() {
  return (
    <header className="flex items-center border-b bg-background px-4 py-3">
      <Link to="/" className="flex items-center gap-2 text-foreground hover:opacity-80 transition-opacity">
        <FileSearch className="h-6 w-6" />
        <h1 className="text-xl font-semibold">SnipScout</h1>
      </Link>
    </header>
  );
}
