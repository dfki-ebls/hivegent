import { useState } from "react";
import { DownloadIcon, ScanSearchIcon } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { Textarea } from "@/components/ui/textarea";
import { detectAiGeneratedText } from "@/lib/api";
import { downloadBlob } from "@/lib/download";
import type { TransparencyDetectionResponse } from "@/lib/types";
import { cn, errorMessage } from "@/lib/utils";

interface AiTransparencyDialogProps {
  contactEmail: string | null;
  minimumTokens: number;
}

const STATUS_STYLES = {
  detected: "text-green-700 dark:text-green-400",
  not_detected: "text-muted-foreground",
  inconclusive: "text-amber-700 dark:text-amber-400",
} as const;

export function AiTransparencyDialog({ contactEmail, minimumTokens }: AiTransparencyDialogProps) {
  const [open, setOpen] = useState(false);
  const [text, setText] = useState("");
  const [result, setResult] = useState<TransparencyDetectionResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [checking, setChecking] = useState(false);

  function changeOpen(nextOpen: boolean): void {
    setOpen(nextOpen);

    if (!nextOpen) {
      setText("");
      setResult(null);
      setError(null);
    }
  }

  async function detect(): Promise<void> {
    setChecking(true);
    setError(null);
    setResult(null);

    try {
      setResult(await detectAiGeneratedText(text));
    } catch (cause) {
      setError(errorMessage(cause));
    } finally {
      setChecking(false);
    }
  }

  function downloadReport(): void {
    if (!result) return;

    downloadBlob(
      new Blob([result.signed_report], { type: "application/jwt" }),
      `hivegent-watermark-report-${new Date().toISOString().replaceAll(":", "-")}.jwt`,
    );
  }

  return (
    <Dialog open={open} onOpenChange={changeOpen}>
      <DialogTrigger asChild>
        <Button variant="link" size="xs" className="h-auto px-1 text-xs">
          Verify AI text
        </Button>
      </DialogTrigger>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Verify Hivegent text</DialogTitle>
          <DialogDescription>
            Paste text to check for Hivegent&apos;s imperceptible watermark. The text is processed
            only for this check and is not retained.
          </DialogDescription>
        </DialogHeader>
        <Textarea
          value={text}
          onChange={(event) => {
            setText(event.target.value);
            setResult(null);
            setError(null);
          }}
          rows={10}
          placeholder={`For a reliable negative result, use more than ${minimumTokens} tokens.`}
          aria-label="Text to verify"
        />
        {result && (
          <output className="block space-y-1 text-sm">
            <p className={cn("font-medium", STATUS_STYLES[result.status])}>{result.message}</p>
            <p className="text-xs text-muted-foreground">
              Result based on an imperceptible text watermark. The signed report contains only a
              SHA-256 hash of the submitted text and detector metadata.
            </p>
          </output>
        )}
        {error && (
          <p role="alert" className="text-sm text-destructive">
            {error}
          </p>
        )}
        <p className="text-xs text-muted-foreground">
          Watermarks can be damaged by editing or translation. A negative result never proves human
          authorship.
          {contactEmail && (
            <>
              {" "}
              Qualified external reviewers can request access at{" "}
              <a className="underline" href={`mailto:${contactEmail}`}>
                {contactEmail}
              </a>
              .
            </>
          )}
        </p>
        <DialogFooter>
          {result && (
            <Button variant="outline" onClick={downloadReport}>
              <DownloadIcon aria-hidden />
              Download signed report
            </Button>
          )}
          <Button onClick={detect} disabled={!text.trim() || checking}>
            <ScanSearchIcon aria-hidden />
            {checking ? "Checking" : "Verify text"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
