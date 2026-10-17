import { AlertCircle, CheckCircle2, RotateCw, X } from "lucide-react";
import type { TFunction } from "i18next";
import { useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";

import { ACTIVE_JOB_STATUSES, type JobView } from "@/lib/types";
import { cn } from "@/lib/utils";
import { onJobStarted, useJobsStore } from "@/stores/jobs-store";
import { type UploadItem, onUploadAdded, useUploadQueue } from "@/stores/upload-queue-store";
import { TooltipButton } from "@/components/TooltipButton";
import { Button } from "@/components/ui/button";
import {
  Item,
  ItemActions,
  ItemContent,
  ItemDescription,
  ItemGroup,
  ItemMedia,
  ItemTitle,
} from "@/components/ui/item";
import {
  Popover,
  PopoverContent,
  PopoverHeader,
  PopoverTitle,
  PopoverTrigger,
} from "@/components/ui/popover";
import { Progress, ProgressValue } from "@/components/ui/progress";
import { Spinner } from "@/components/ui/spinner";

// A row's visual tone, decoupled from whether it came from a job or an upload.
type Tone = "active" | "success" | "error" | "muted";

interface TaskAction {
  label: string;
  run: () => void;
}

// The unified shape the tray renders, mapped from either source so a job and a
// still-uploading file share one list, one badge, and one row layout.
interface TaskRow {
  id: string;
  order: number;
  title: string;
  statusText: string;
  tone: Tone;
  progress: { current: number; total: number } | null;
  retry?: TaskAction;
  /** The X button, cancelling active work or dismissing a failure. */
  close?: TaskAction;
}

// Job stages and errors arrive localized from the backend,
// so only the fallbacks are translated here.
function jobStatusLabel(job: JobView, t: TFunction): string {
  switch (job.status) {
    case "failed":
      return job.error ?? t(($) => $.documents.jobs.status.failed);
    case "succeeded":
      return t(($) => $.documents.jobs.status.done);
    case "cancelled":
      return t(($) => $.documents.jobs.status.cancelled);
    case "queued":
      return job.stage ?? t(($) => $.documents.jobs.status.queued);
    default:
      return job.stage ?? t(($) => $.documents.jobs.status.processing);
  }
}

interface JobActions {
  cancel: (id: string) => void;
  dismiss: (id: string) => void;
}

function jobRow(job: JobView, { cancel, dismiss }: JobActions, t: TFunction): TaskRow {
  const active = ACTIVE_JOB_STATUSES.has(job.status);
  const failed = job.status === "failed";

  return {
    id: job.id,
    order: job.created_at,
    title: job.title,
    statusText: jobStatusLabel(job, t),
    tone: active ? "active" : job.status === "succeeded" ? "success" : failed ? "error" : "muted",
    progress: active && job.progress && job.progress.total > 0 ? job.progress : null,
    close: active
      ? { label: t(($) => $.documents.jobs.cancelTask), run: () => cancel(job.id) }
      : failed
        ? { label: t(($) => $.documents.jobs.dismissTask), run: () => dismiss(job.id) }
        : undefined,
  };
}

interface UploadActions {
  retry: (id: string) => void;
  cancel: (id: string) => void;
  dismiss: (id: string) => void;
}

function uploadRow(item: UploadItem, a: UploadActions, t: TFunction): TaskRow {
  const failed = item.status === "failed";

  return {
    id: item.id,
    order: item.createdAt,
    title: item.name,
    statusText: item.error ?? t(($) => $.documents.jobs.status[item.status]),
    tone: failed ? "error" : "active",
    progress: null,
    retry:
      failed && item.retryable
        ? { label: t(($) => $.common.actions.retry), run: () => a.retry(item.id) }
        : undefined,
    close: failed
      ? { label: t(($) => $.documents.jobs.dismissUpload), run: () => a.dismiss(item.id) }
      : { label: t(($) => $.documents.jobs.cancelUpload), run: () => a.cancel(item.id) },
  };
}

function ToneIcon({ tone, ...props }: { tone: Tone; "data-icon"?: "inline-start" }) {
  switch (tone) {
    case "active":
      return <Spinner aria-hidden className="text-primary" {...props} />;
    case "success":
      return <CheckCircle2 className="text-green-600" {...props} />;
    case "error":
      return <AlertCircle className="text-destructive" {...props} />;
    default:
      return <X className="text-muted-foreground" {...props} />;
  }
}

function TaskRowView({ row }: { row: TaskRow }) {
  const { progress } = row;

  return (
    <Item size="xs" className="flex-nowrap px-0">
      <ItemMedia variant="icon">
        <ToneIcon tone={row.tone} />
      </ItemMedia>
      <ItemContent className="min-w-0">
        <ItemTitle className="max-w-full">{row.title}</ItemTitle>
        <ItemDescription
          className={cn(
            "line-clamp-none text-xs whitespace-pre-line break-words",
            row.tone === "error" && "text-destructive",
          )}
        >
          {row.statusText}
        </ItemDescription>
        {progress && (
          <Progress value={progress.current} max={progress.total} className="gap-1">
            <ProgressValue className="text-xs">
              {() => `${progress.current}/${progress.total}`}
            </ProgressValue>
          </Progress>
        )}
      </ItemContent>
      <ItemActions className="gap-1">
        {row.retry && (
          <Button variant="outline" size="xs" onClick={row.retry.run}>
            <RotateCw data-icon="inline-start" />
            {row.retry.label}
          </Button>
        )}
        {row.close && (
          <TooltipButton size="icon-xs" label={row.close.label} onClick={row.close.run}>
            <X />
          </TooltipButton>
        )}
      </ItemActions>
    </Item>
  );
}

/**
 * Header indicator and popover for background work. Renders one list over two
 * sources: uploads still being prepared or transferred from the upload queue,
 * and the server-side jobs they hand off to from the `/jobs` feed. An upload
 * item leaves the queue the moment its job is created, so the job row takes over
 * its row without ever showing both.
 */
export function JobTray() {
  const { t } = useTranslation();
  const start = useJobsStore((s) => s.start);
  const jobsMap = useJobsStore((s) => s.jobs);
  const jobCancel = useJobsStore((s) => s.cancel);
  const jobDismiss = useJobsStore((s) => s.dismiss);

  const uploadMap = useUploadQueue((s) => s.items);
  const retry = useUploadQueue((s) => s.retry);
  const uploadCancel = useUploadQueue((s) => s.cancel);
  const uploadDismiss = useUploadQueue((s) => s.dismiss);

  // The tray is the only cue for background work, so it pops open the moment a
  // new entry is added — an enqueued upload or a starting job — and the user
  // sees what is happening without hunting for the indicator. Both sources fire
  // a one-shot "added" event: onUploadAdded for the queue, and onJobStarted,
  // which already filters out the feed's reconnect re-seed so a page load with
  // jobs still running does not pop the tray. A manual close while work is still
  // running stays closed until the next genuinely new entry.
  const [open, setOpen] = useState(false);

  useEffect(() => {
    start();
    const reveal = () => setOpen(true);
    const offUpload = onUploadAdded(reveal);
    const offJob = onJobStarted(reveal);

    return () => {
      offUpload();
      offJob();
    };
  }, [start]);

  const rows = useMemo(() => {
    const uploadRows = Object.values(uploadMap).map((item) =>
      uploadRow(item, { retry, cancel: uploadCancel, dismiss: uploadDismiss }, t),
    );
    const jobRows = Object.values(jobsMap).map((job) =>
      jobRow(job, { cancel: (id) => void jobCancel(id), dismiss: jobDismiss }, t),
    );

    return [...uploadRows, ...jobRows].sort((a, b) => a.order - b.order);
  }, [uploadMap, jobsMap, retry, uploadCancel, uploadDismiss, jobCancel, jobDismiss, t]);

  if (rows.length === 0) return null;

  const activeCount = rows.filter((r) => r.tone === "active").length;
  const attentionCount = rows.filter((r) => r.tone === "error").length;
  const summary: { tone: Tone; label: string } =
    activeCount > 0
      ? { tone: "active", label: t(($) => $.documents.jobs.processing, { count: activeCount }) }
      : attentionCount > 0
        ? {
            tone: "error",
            label: t(($) => $.documents.jobs.needAttention, { count: attentionCount }),
          }
        : { tone: "success", label: t(($) => $.documents.jobs.status.done) };

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger render={<Button variant="ghost" size="sm" />}>
        <ToneIcon tone={summary.tone} data-icon="inline-start" />
        <span className="sr-only sm:not-sr-only">{summary.label}</span>
      </PopoverTrigger>
      <PopoverContent align="end" className="w-80">
        <PopoverHeader>
          <PopoverTitle className="text-sm">{t(($) => $.documents.jobs.title)}</PopoverTitle>
        </PopoverHeader>
        <ItemGroup className="max-h-80 overflow-y-auto">
          {rows.map((row) => (
            <TaskRowView key={row.id} row={row} />
          ))}
        </ItemGroup>
      </PopoverContent>
    </Popover>
  );
}
