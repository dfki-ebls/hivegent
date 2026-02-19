import { formatDistanceToNow } from "date-fns";
import { Trash2 } from "lucide-react";

import type { TokenInfo } from "../lib/types";
import { Button } from "./ui/button";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "./ui/table";

interface TokenListProps {
  tokens: TokenInfo[];
  onRevoke: (tokenId: string, tokenName: string) => void;
}

export function TokenList({ tokens, onRevoke }: TokenListProps) {
  if (tokens.length === 0) {
    return (
      <div className="text-center py-8 text-muted-foreground">
        <p>No API tokens yet.</p>
        <p className="text-sm mt-1">
          Create a token to access the API programmatically.
        </p>
      </div>
    );
  }

  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead>Name</TableHead>
          <TableHead>Created</TableHead>
          <TableHead>Expires</TableHead>
          <TableHead>Last Used</TableHead>
          <TableHead className="w-[70px]" />
        </TableRow>
      </TableHeader>
      <TableBody>
        {tokens.map((token) => (
          <TableRow key={token.id}>
            <TableCell className="font-medium">{token.name}</TableCell>
            <TableCell className="text-muted-foreground">
              {formatDistanceToNow(new Date(token.created_at), {
                addSuffix: true,
              })}
            </TableCell>
            <TableCell className="text-muted-foreground">
              {token.expires_at
                ? formatDistanceToNow(new Date(token.expires_at), {
                    addSuffix: true,
                  })
                : "Never"}
            </TableCell>
            <TableCell className="text-muted-foreground">
              {token.last_used_at
                ? formatDistanceToNow(new Date(token.last_used_at), {
                    addSuffix: true,
                  })
                : "Never"}
            </TableCell>
            <TableCell>
              <Button
                variant="ghost"
                size="icon"
                onClick={() => onRevoke(token.id, token.name)}
                title="Revoke token"
              >
                <Trash2 className="h-4 w-4 text-destructive" />
              </Button>
            </TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}
