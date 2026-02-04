import { useState } from 'react';
import { Check, Copy } from 'lucide-react';

import { Button } from './ui/button';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from './ui/dialog';
import { Alert, AlertDescription } from './ui/alert';

interface TokenCreatedDialogProps {
  token: string | null;
  tokenName: string | null;
  onClose: () => void;
}

export function TokenCreatedDialog({
  token,
  tokenName,
  onClose,
}: TokenCreatedDialogProps) {
  const [copied, setCopied] = useState(false);

  const handleCopy = async () => {
    if (!token) return;
    await navigator.clipboard.writeText(token);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  return (
    <Dialog open={!!token} onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="sm:max-w-[525px]">
        <DialogHeader>
          <DialogTitle>Token Created</DialogTitle>
          <DialogDescription>
            Your new API token "{tokenName}" has been created. Copy it now - you
            won't be able to see it again.
          </DialogDescription>
        </DialogHeader>
        <div className="py-4">
          <Alert>
            <AlertDescription className="font-mono text-sm break-all">
              {token}
            </AlertDescription>
          </Alert>
        </div>
        <DialogFooter className="sm:justify-between">
          <Button variant="outline" onClick={handleCopy} className="gap-2">
            {copied ? (
              <>
                <Check className="h-4 w-4" />
                Copied!
              </>
            ) : (
              <>
                <Copy className="h-4 w-4" />
                Copy to clipboard
              </>
            )}
          </Button>
          <Button onClick={onClose}>Done</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
