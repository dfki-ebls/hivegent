import { useState } from "react";
import { DownloadIcon, ScanSearchIcon } from "lucide-react";
import { Trans, useTranslation } from "react-i18next";

import { FormSection } from "@/components/FormSection";
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
import { keyPrefix } from "@/i18n";
import { formatNumber } from "@/i18n/format";
import { detectAiGeneratedText } from "@/lib/api";
import { downloadBlob } from "@/lib/download";
import type { TransparencyDetectionResponse } from "@/lib/types";
import { cn, errorMessage } from "@/lib/utils";

const T_OPTIONS = keyPrefix(($) => $.chat.transparency);

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
  const { t } = useTranslation(undefined, T_OPTIONS);
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
          {t(($) => $.trigger)}
        </Button>
      </DialogTrigger>
      <DialogContent className="sm:max-w-3xl max-h-[90vh] overflow-y-auto">
        <DialogHeader>
          <DialogTitle>{t(($) => $.title)}</DialogTitle>
          <DialogDescription>{t(($) => $.description)}</DialogDescription>
        </DialogHeader>

        <div className="grid gap-4 py-4">
          <FormSection
            label={t(($) => $.textLabel)}
            htmlFor="verify-text"
            description={t(($) => $.textDescription, { tokens: formatNumber(minimumTokens) })}
          >
            <Textarea
              id="verify-text"
              value={text}
              onChange={(event) => {
                setText(event.target.value);
                setResult(null);
                setError(null);
              }}
              placeholder={t(($) => $.placeholder)}
              className="h-[40vh] min-h-[100px] resize-y overflow-y-auto field-sizing-fixed"
            />
          </FormSection>

          {result && (
            <FormSection
              label={t(($) => $.resultLabel)}
              description={t(($) => $.resultDescription)}
            >
              <output className={cn("text-sm font-medium", STATUS_STYLES[result.status])}>
                {result.message}
              </output>
            </FormSection>
          )}

          {error && (
            <p role="alert" className="text-sm text-destructive">
              {error}
            </p>
          )}

          {contactEmail && (
            <p className="text-xs text-muted-foreground">
              <Trans
                i18nKey={($) => $.chat.transparency.contact}
                components={{
                  email: (
                    <a className="underline" href={`mailto:${contactEmail}`}>
                      {contactEmail}
                    </a>
                  ),
                }}
              />
            </p>
          )}
        </div>

        <DialogFooter className="flex-row">
          {result && (
            <Button variant="outline" size="sm" className="mr-auto" onClick={downloadReport}>
              <DownloadIcon />
              {t(($) => $.download)}
            </Button>
          )}
          <Button size="sm" onClick={detect} disabled={!text.trim() || checking}>
            <ScanSearchIcon />
            {checking ? t(($) => $.verifying) : t(($) => $.verify)}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
