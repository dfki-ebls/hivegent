import { useMutation } from "@tanstack/react-query";
import { useState } from "react";
import { DownloadIcon, ScanSearchIcon } from "lucide-react";
import { Trans, useTranslation } from "react-i18next";

import { ErrorAlert } from "@/components/ErrorAlert";
import { DialogBody, ScrollDialogContent } from "@/components/ScrollDialogContent";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { Field, FieldDescription, FieldGroup, FieldLabel, FieldTitle } from "@/components/ui/field";
import { Spinner } from "@/components/ui/spinner";
import { Textarea } from "@/components/ui/textarea";
import { keyPrefix } from "@/i18n";
import { formatNumber } from "@/i18n/format";
import { detectAiGeneratedText } from "@/lib/api";
import { downloadBlob } from "@/lib/download";
import { cn, errorMessage } from "@/lib/utils";

const T_OPTIONS = keyPrefix(($) => $.chat.transparency);

interface AiTransparencyDialogProps {
  contactEmail: string | null;
  minimumTokens: number;
}

// The theme has no success or warning token, so the outcomes keep their palette colors.
const STATUS_STYLES = {
  detected: "text-green-700 dark:text-green-400",
  not_detected: "text-muted-foreground",
  inconclusive: "text-amber-700 dark:text-amber-400",
} as const;

export function AiTransparencyDialog({ contactEmail, minimumTokens }: AiTransparencyDialogProps) {
  const { t } = useTranslation(undefined, T_OPTIONS);
  const [text, setText] = useState("");
  const detection = useMutation({
    mutationFn: detectAiGeneratedText,
    meta: { inlineError: true },
  });
  const { data: result, error, isPending: checking } = detection;

  function changeOpen(open: boolean): void {
    if (!open) {
      setText("");
      detection.reset();
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
    <Dialog onOpenChange={changeOpen}>
      <DialogTrigger render={<Button variant="link" size="xs" className="h-auto px-1" />}>
        {t(($) => $.trigger)}
      </DialogTrigger>
      <ScrollDialogContent className="sm:max-w-3xl">
        <DialogHeader>
          <DialogTitle>{t(($) => $.title)}</DialogTitle>
          <DialogDescription>{t(($) => $.description)}</DialogDescription>
        </DialogHeader>

        <DialogBody>
          <FieldGroup>
            <Field>
              <FieldLabel htmlFor="verify-text">{t(($) => $.textLabel)}</FieldLabel>
              <Textarea
                id="verify-text"
                value={text}
                onChange={(event) => {
                  setText(event.target.value);
                  detection.reset();
                }}
                placeholder={t(($) => $.placeholder)}
                className="h-[40vh] resize-y field-sizing-fixed"
              />
              <FieldDescription>
                {t(($) => $.textDescription, { tokens: formatNumber(minimumTokens) })}
              </FieldDescription>
            </Field>

            {result && (
              <Field>
                <FieldTitle>{t(($) => $.resultLabel)}</FieldTitle>
                <output className={cn("text-sm font-medium", STATUS_STYLES[result.status])}>
                  {result.message}
                </output>
                <FieldDescription>{t(($) => $.resultDescription)}</FieldDescription>
              </Field>
            )}

            {error && <ErrorAlert>{errorMessage(error)}</ErrorAlert>}

            {contactEmail && (
              <FieldDescription>
                <Trans
                  i18nKey={($) => $.chat.transparency.contact}
                  components={{ email: <a href={`mailto:${contactEmail}`}>{contactEmail}</a> }}
                />
              </FieldDescription>
            )}
          </FieldGroup>
        </DialogBody>

        <DialogFooter>
          {result && (
            <Button variant="outline" size="sm" className="sm:mr-auto" onClick={downloadReport}>
              <DownloadIcon data-icon="inline-start" />
              {t(($) => $.download)}
            </Button>
          )}
          <Button size="sm" onClick={() => detection.mutate(text)} disabled={!text.trim() || checking}>
            {checking ? (
              <Spinner data-icon="inline-start" />
            ) : (
              <ScanSearchIcon data-icon="inline-start" />
            )}
            {checking ? t(($) => $.verifying) : t(($) => $.verify)}
          </Button>
        </DialogFooter>
      </ScrollDialogContent>
    </Dialog>
  );
}
