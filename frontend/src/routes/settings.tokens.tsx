import { createFileRoute } from "@tanstack/react-router";
import { Key } from "lucide-react";
import { useCallback, useEffect, useState } from "react";

import { CreateTokenDialog } from "../components/CreateTokenDialog";
import { TokenCreatedDialog } from "../components/TokenCreatedDialog";
import { TokenList } from "../components/TokenList";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "../components/ui/alert-dialog";
import { createToken, listTokens, revokeToken } from "../lib/api";
import type { TokenInfo } from "../lib/types";

export const Route = createFileRoute("/settings/tokens")({
  component: TokensPage,
});

function TokensPage() {
  const [tokens, setTokens] = useState<TokenInfo[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [isCreating, setIsCreating] = useState(false);
  const [newToken, setNewToken] = useState<string | null>(null);
  const [newTokenName, setNewTokenName] = useState<string | null>(null);
  const [revokeConfirm, setRevokeConfirm] = useState<{
    id: string;
    name: string;
  } | null>(null);

  const fetchTokens = useCallback(async () => {
    try {
      const data = await listTokens();
      setTokens(data);
    } catch (error) {
      console.error("Failed to fetch tokens:", error);
    } finally {
      setIsLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchTokens();
  }, [fetchTokens]);

  const handleCreate = async (name: string, expiresInDays?: number) => {
    setIsCreating(true);
    try {
      const response = await createToken(name, expiresInDays);
      setNewToken(response.token);
      setNewTokenName(name);
      await fetchTokens();
    } catch (error) {
      console.error("Failed to create token:", error);
    } finally {
      setIsCreating(false);
    }
  };

  const handleRevoke = async () => {
    if (!revokeConfirm) return;
    try {
      await revokeToken(revokeConfirm.id);
      await fetchTokens();
    } catch (error) {
      console.error("Failed to revoke token:", error);
    } finally {
      setRevokeConfirm(null);
    }
  };

  return (
    <div className="container max-w-4xl mx-auto py-8 px-4">
      <div className="flex items-center justify-between mb-8">
        <div className="flex items-center gap-3">
          <Key className="h-6 w-6" />
          <h1 className="text-2xl font-semibold">API Tokens</h1>
        </div>
        <CreateTokenDialog onCreate={handleCreate} isCreating={isCreating} />
      </div>

      <div className="border rounded-lg">
        {isLoading ? (
          <div className="py-8 text-center text-muted-foreground">Loading tokens...</div>
        ) : (
          <TokenList tokens={tokens} onRevoke={(id, name) => setRevokeConfirm({ id, name })} />
        )}
      </div>

      <TokenCreatedDialog
        token={newToken}
        tokenName={newTokenName}
        onClose={() => {
          setNewToken(null);
          setNewTokenName(null);
        }}
      />

      <AlertDialog open={!!revokeConfirm} onOpenChange={(open) => !open && setRevokeConfirm(null)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Revoke API Token</AlertDialogTitle>
            <AlertDialogDescription>
              Are you sure you want to revoke the token "{revokeConfirm?.name}"? Any applications
              using this token will no longer be able to access the API.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction
              onClick={handleRevoke}
              className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
            >
              Revoke Token
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}
