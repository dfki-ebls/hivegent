import { AlertCircle, CheckCircle2, Loader2, X } from "lucide-react";
import { useEffect, useMemo } from "react";

import { ACTIVE_JOB_STATUSES, type JobView } from "../lib/types";
import { useJobsStore } from "../stores/jobs-store";
import { Button } from "./ui/button";
import { Popover, PopoverContent, PopoverTrigger } from "./ui/popover";
import { Progress } from "./ui/progress";

function statusLabel(job: JobView): string {
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

function JobIcon({ status }: { status: JobView["status"] }) {
  if (ACTIVE_JOB_STATUSES.has(status)) {
    return <Loader2 className="h-4 w-4 animate-spin text-primary" />;
  }
  if (status === "succeeded") {
    return <CheckCircle2 className="h-4 w-4 text-green-600" />;
  }
  if (status === "failed") {
    return <AlertCircle className="h-4 w-4 text-destructive" />;
  }
  return <X className="h-4 w-4 text-muted-foreground" />;
}

function JobRow({ job }: { job: JobView }) {
  const cancel = useJobsStore((s) => s.cancel);
  const dismiss = useJobsStore((s) => s.dismiss);
  const active = ACTIVE_JOB_STATUSES.has(job.status);
  const showProgress = active && job.progress !== null && job.progress.total > 0;

  return (
    <div className="flex items-start gap-2 px-3 py-2">
      <span className="mt-0.5 shrink-0">
        <JobIcon status={job.status} />
      </span>
      <div className="min-w-0 flex-1">
        <p className="truncate text-sm font-medium">{job.title}</p>
        <p
          className={`truncate text-xs ${
            job.status === "failed" ? "text-destructive" : "text-muted-foreground"
          }`}
        >
          {statusLabel(job)}
        </p>
        {showProgress && job.progress && (
          <div className="mt-1 flex items-center gap-2">
            <Progress
              value={(job.progress.current / job.progress.total) * 100}
              className="h-1 flex-1"
            />
            <span className="shrink-0 text-xs tabular-nums text-muted-foreground">
              {job.progress.current}/{job.progress.total}
            </span>
          </div>
        )}
      </div>
      {active ? (
        <Button
          variant="ghost"
          size="icon"
          className="h-6 w-6 shrink-0"
          onClick={() => void cancel(job.id)}
          aria-label="Cancel task"
        >
          <X className="h-4 w-4" />
        </Button>
      ) : (
        job.status === "failed" && (
          <Button
            variant="ghost"
            size="icon"
            className="h-6 w-6 shrink-0"
            onClick={() => dismiss(job.id)}
            aria-label="Dismiss task"
          >
            <X className="h-4 w-4" />
          </Button>
        )
      )}
    </div>
  );
}

/**
 * Header indicator and popover for background jobs. Generic over job kind:
 * it renders whatever the `/jobs` feed reports (document processing today),
 * lets the user cancel active work, and stays out of the way when idle.
 */
export function JobTray() {
  const start = useJobsStore((s) => s.start);
  const jobsMap = useJobsStore((s) => s.jobs);

  useEffect(() => {
    start();
  }, [start]);

  const jobs = useMemo(
    () => Object.values(jobsMap).sort((a, b) => a.created_at - b.created_at),
    [jobsMap],
  );

  if (jobs.length === 0) return null;

  const activeCount = jobs.filter((j) => ACTIVE_JOB_STATUSES.has(j.status)).length;
  const failedCount = jobs.filter((j) => j.status === "failed").length;

  return (
    <Popover>
      <PopoverTrigger asChild>
        <Button variant="ghost" size="sm" className="gap-2">
          {activeCount > 0 ? (
            <Loader2 className="h-4 w-4 animate-spin" />
          ) : failedCount > 0 ? (
            <AlertCircle className="h-4 w-4 text-destructive" />
          ) : (
            <CheckCircle2 className="h-4 w-4 text-green-600" />
          )}
          <span className="hidden sm:inline">
            {activeCount > 0
              ? `Processing ${activeCount}`
              : failedCount > 0
                ? `${failedCount} failed`
                : "Done"}
          </span>
        </Button>
      </PopoverTrigger>
      <PopoverContent align="end" className="w-80 p-0">
        <div className="border-b px-3 py-2 text-sm font-medium">Background tasks</div>
        <div className="max-h-80 divide-y overflow-y-auto">
          {jobs.map((job) => (
            <JobRow key={job.id} job={job} />
          ))}
        </div>
      </PopoverContent>
    </Popover>
  );
}
