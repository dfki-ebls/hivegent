import { AlertCircle, CheckCircle2, Loader2, RotateCw, X } from "lucide-react";
import { type ReactNode, useEffect, useMemo, useState } from "react";

import { ACTIVE_JOB_STATUSES, type JobView } from "@/lib/types";
import { onJobStarted, useJobsStore } from "@/stores/jobs-store";
import {
  type UploadItem,
  type UploadItemStatus,
  onUploadAdded,
  useUploadQueue,
} from "@/stores/upload-queue-store";
import { Button } from "@/components/ui/button";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { Progress } from "@/components/ui/progress";

// A row's visual tone, decoupled from whether it came from a job or an upload.
type Tone = "active" | "success" | "error" | "muted";

// Status-line color per tone; only failures deviate from muted.
const TONE_TEXT_CLASS: Record<Tone, string> = {
  active: "text-muted-foreground",
  success: "text-muted-foreground",
  error: "text-destructive",
  muted: "text-muted-foreground",
};

interface TaskAction {
  key: string;
  label?: string; // a text button; an icon-only button when absent
  icon?: ReactNode;
  ariaLabel: string;
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
  actions: TaskAction[];
}

const UPLOAD_STATUS_LABEL: Record<UploadItemStatus, string> = {
  preparing: "Preparing...",
  queued: "Queued",
  uploading: "Uploading...",
  failed: "Failed",
};

function jobStatusLabel(job: JobView): string {
  switch (job.status) {
    case "failed":
      return job.error ?? "Failed";
    case "succeeded":
      return "Done";
    case "cancelled":
      return "Cancelled";
    case "queued":
      return job.stage ?? "Queued";
    default:
      return job.stage ?? "Processing";
  }
}

const closeIcon = <X className="h-4 w-4" />;

// An icon-only X button, the shared shape of every cancel/dismiss action.
const xAction = (key: string, ariaLabel: string, run: () => void): TaskAction => ({
  key,
  icon: closeIcon,
  ariaLabel,
  run,
});

interface JobActions {
  cancel: (id: string) => void;
  dismiss: (id: string) => void;
}

function jobRow(job: JobView, { cancel, dismiss }: JobActions): TaskRow {
  const active = ACTIVE_JOB_STATUSES.has(job.status);
  const actions: TaskAction[] = active
    ? [xAction("cancel", "Cancel task", () => cancel(job.id))]
    : job.status === "failed"
      ? [xAction("dismiss", "Dismiss task", () => dismiss(job.id))]
      : [];

  return {
    id: job.id,
    order: job.created_at,
    title: job.title,
    statusText: jobStatusLabel(job),
    tone: active
      ? "active"
      : job.status === "succeeded"
        ? "success"
        : job.status === "failed"
          ? "error"
          : "muted",
    progress: active && job.progress && job.progress.total > 0 ? job.progress : null,
    actions,
  };
}

interface UploadActions {
  retry: (id: string) => void;
  cancel: (id: string) => void;
  dismiss: (id: string) => void;
}

function uploadRow(item: UploadItem, a: UploadActions): TaskRow {
  const failed = item.status === "failed";
  const retry: TaskAction = {
    key: "retry",
    label: "Retry",
    icon: <RotateCw className="h-3.5 w-3.5 mr-1" />,
    ariaLabel: "Retry",
    run: () => a.retry(item.id),
  };
  const dismiss = xAction("dismiss", "Dismiss upload", () => a.dismiss(item.id));
  const actions: TaskAction[] = failed
    ? item.retryable
      ? [retry, dismiss]
      : [dismiss]
    : [xAction("cancel", "Cancel upload", () => a.cancel(item.id))];

  return {
    id: item.id,
    order: item.createdAt,
    title: item.name,
    statusText: item.error ?? UPLOAD_STATUS_LABEL[item.status],
    tone: failed ? "error" : "active",
    progress: null,
    actions,
  };
}

function ToneIcon({ tone }: { tone: Tone }) {
  switch (tone) {
    case "active":
      return <Loader2 className="h-4 w-4 animate-spin text-primary" />;
    case "success":
      return <CheckCircle2 className="h-4 w-4 text-green-600" />;
    case "error":
      return <AlertCircle className="h-4 w-4 text-destructive" />;
    default:
      return <X className="h-4 w-4 text-muted-foreground" />;
  }
}

function TaskRowView({ row }: { row: TaskRow }) {
  return (
    <div className="flex items-start gap-2 px-3 py-2">
      <span className="mt-0.5 shrink-0">
        <ToneIcon tone={row.tone} />
      </span>
      <div className="min-w-0 flex-1">
        <p className="truncate text-sm font-medium">{row.title}</p>
        <p className={`truncate text-xs ${TONE_TEXT_CLASS[row.tone]}`}>{row.statusText}</p>
        {row.progress && (
          <div className="mt-1 flex items-center gap-2">
            <Progress
              value={(row.progress.current / row.progress.total) * 100}
              className="h-1 flex-1"
            />
            <span className="shrink-0 text-xs tabular-nums text-muted-foreground">
              {row.progress.current}/{row.progress.total}
            </span>
          </div>
        )}
      </div>
      {row.actions.length > 0 && (
        <div className="flex shrink-0 items-center gap-1">
          {row.actions.map((action) =>
            action.label ? (
              <Button
                key={action.key}
                variant="outline"
                size="sm"
                onClick={action.run}
                aria-label={action.ariaLabel}
              >
                {action.icon}
                {action.label}
              </Button>
            ) : (
              <Button
                key={action.key}
                variant="ghost"
                size="icon"
                className="h-6 w-6"
                onClick={action.run}
                aria-label={action.ariaLabel}
              >
                {action.icon}
              </Button>
            ),
          )}
        </div>
      )}
    </div>
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
      uploadRow(item, { retry, cancel: uploadCancel, dismiss: uploadDismiss }),
    );
    const jobRows = Object.values(jobsMap).map((job) =>
      jobRow(job, { cancel: (id) => void jobCancel(id), dismiss: jobDismiss }),
    );

    return [...uploadRows, ...jobRows].sort((a, b) => a.order - b.order);
  }, [uploadMap, jobsMap, retry, uploadCancel, uploadDismiss, jobCancel, jobDismiss]);

  if (rows.length === 0) return null;

  const activeCount = rows.filter((r) => r.tone === "active").length;
  const attentionCount = rows.filter((r) => r.tone === "error").length;

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <Button variant="ghost" size="sm" className="gap-2">
          {activeCount > 0 ? (
            <Loader2 className="h-4 w-4 animate-spin" />
          ) : attentionCount > 0 ? (
            <AlertCircle className="h-4 w-4 text-destructive" />
          ) : (
            <CheckCircle2 className="h-4 w-4 text-green-600" />
          )}
          <span className="hidden sm:inline">
            {activeCount > 0
              ? `Processing ${activeCount}`
              : attentionCount > 0
                ? `${attentionCount} need attention`
                : "Done"}
          </span>
        </Button>
      </PopoverTrigger>
      <PopoverContent align="end" className="w-80 p-0">
        <div className="border-b px-3 py-2 text-sm font-medium">Background tasks</div>
        <div className="max-h-80 divide-y overflow-y-auto">
          {rows.map((row) => (
            <TaskRowView key={row.id} row={row} />
          ))}
        </div>
      </PopoverContent>
    </Popover>
  );
}
