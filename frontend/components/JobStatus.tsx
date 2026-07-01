'use client';

import { useEffect, useRef, useState } from "react";
import { Progress } from "@/components/ui/progress";
import { Badge } from "@/components/ui/badge";
import { getJob, cancelJob, resumeJob, exportUrl } from "@/lib/api";
import type { Job, Place } from "@/types";

interface Props {
  jobId: string;
  onComplete: (places: Place[]) => void;
  onPlacesUpdate?: (places: Place[]) => void;
}

export default function JobStatus({ jobId, onComplete, onPlacesUpdate }: Props) {
  const [job, setJob] = useState<Job | null>(null);
  const [showPending, setShowPending] = useState(false);
  const [showFailed, setShowFailed] = useState(false);
  const [showWarnings, setShowWarnings] = useState(true);
  const [pollTick, setPollTick] = useState(0);
  const completedRef = useRef(false);

  useEffect(() => {
    completedRef.current = false;
    let cancelled = false;
    let timerId: ReturnType<typeof setTimeout>;

    async function poll() {
      if (cancelled) return;
      try {
        const data = await getJob(jobId);
        if (cancelled) return;
        setJob(data);

        if (data.places.length > 0) {
          onPlacesUpdate?.(data.places);
        }

        if (data.status === "complete" || data.status === "complete_with_errors" || data.status === "cancelled") {
          if (!completedRef.current) {
            completedRef.current = true;
            onComplete(data.places);
          }
          return;
        }

        if (data.status === "paused") return;

        timerId = setTimeout(poll, 2000);
      } catch {
        if (!cancelled) timerId = setTimeout(poll, 3000);
      }
    }

    poll();
    return () => { cancelled = true; clearTimeout(timerId); };
  }, [jobId, onComplete, onPlacesUpdate, pollTick]);

  async function handleResume() {
    await resumeJob(jobId).catch((e) => console.error("Resume failed:", e));
    setPollTick((t) => t + 1);
  }

  async function handleCancel() {
    await cancelJob(jobId).catch((e) => console.error("Cancel failed:", e));
  }

  if (!job) {
    return (
      <div className="w-full max-w-lg space-y-2 animate-pulse">
        <div className="h-4 bg-zinc-200 rounded w-1/3" />
        <div className="h-2 bg-zinc-100 rounded" />
      </div>
    );
  }

  const pct = job.total_urls > 0 ? Math.round((job.processed / job.total_urls) * 100) : 0;
  const done = job.status === "complete" || job.status === "complete_with_errors" || job.status === "cancelled";
  const paused = job.status === "paused";
  const placeCount = job.places.length;
  const failCount = job.failed_urls.length;
  const pendingCount = job.pending_review.length;
  const warningCount = (job.warnings ?? []).length;

  return (
    <div className="w-full max-w-lg space-y-3">
      <div className="flex items-center justify-between text-sm">
        <span className="font-medium text-zinc-700">
          {job.status === "cancelled" ? "Job cancelled"
            : paused ? "Job paused"
            : done ? "Processing complete"
            : "Extracting places…"}
        </span>
        <div className="flex items-center gap-3">
          <span className="text-zinc-500 tabular-nums">
            {job.processed} / {job.total_urls}
          </span>
          {!done && !paused && (
            <button
              onClick={handleCancel}
              className="text-xs text-red-500 hover:text-red-700 border border-red-200 hover:border-red-400 rounded px-2 py-0.5 transition-colors"
            >
              Stop
            </button>
          )}
        </div>
      </div>

      <Progress value={pct} className="h-2" />

      {/* Current URL being processed */}
      {!done && job.current_url && (
        <p className="text-xs text-zinc-400 font-mono truncate">
          {job.current_url.replace("https://www.instagram.com/", "instagram.com/")}
        </p>
      )}

      {/* Live stats row */}
      <div className="flex items-center gap-2 flex-wrap text-xs">
        {placeCount > 0 && (
          <span className="text-green-700 font-medium">
            {placeCount} place{placeCount !== 1 ? "s" : ""} found
          </span>
        )}
        {failCount > 0 && (
          <span className="text-red-600 font-medium">
            · {failCount} failed
          </span>
        )}
        {pendingCount > 0 && (
          <span className="text-yellow-600 font-medium">
            · {pendingCount} need review
          </span>
        )}
        {warningCount > 0 && (
          <span className="text-orange-600 font-medium">
            · {warningCount} warning{warningCount !== 1 ? "s" : ""}
          </span>
        )}
      </div>

      {done && (
        <div className="flex items-center gap-3">
          <Badge variant={job.status === "complete" ? "default" : "destructive"}>
            {job.status === "cancelled" ? "Cancelled" : job.status === "complete" ? "Complete" : "Complete with errors"}
          </Badge>
          <a
            href={exportUrl(jobId)}
            download
            className="text-sm font-medium text-blue-600 hover:underline"
          >
            Download CSV
          </a>
        </div>
      )}

      {paused && (
        <div className="rounded-lg border border-amber-300 bg-amber-50 p-4 space-y-3">
          <p className="text-sm font-semibold text-amber-900">Extraction paused</p>
          {job.warnings.length > 0 && (
            <p className="text-xs text-amber-800">
              {job.warnings[job.warnings.length - 1].message}
            </p>
          )}
          <p className="text-xs text-amber-700">
            {job.remaining_posts.length} post{job.remaining_posts.length !== 1 ? "s" : ""} remaining.
            {job.paused_reason === "cdn_collision"
              ? " Resuming will continue without transcription (caption-only)."
              : " Resuming will retry with full capability."}
          </p>
          <div className="flex gap-2">
            <button
              onClick={handleResume}
              className="text-xs font-medium bg-amber-700 text-white hover:bg-amber-800 rounded px-3 py-1.5 transition-colors"
            >
              {job.paused_reason === "cdn_collision" ? "Continue (caption-only)" : "Resume"}
            </button>
            <button
              onClick={handleCancel}
              className="text-xs font-medium text-amber-800 hover:text-amber-900 border border-amber-300 hover:border-amber-400 rounded px-3 py-1.5 transition-colors"
            >
              Abandon
            </button>
          </div>
        </div>
      )}

      {warningCount > 0 && (
        <div className="rounded-lg border border-orange-200 bg-orange-50 p-3">
          <button
            className="text-sm font-medium text-orange-800 flex items-center gap-1"
            onClick={() => setShowWarnings((v) => !v)}
          >
            ⚡ {warningCount} warning{warningCount !== 1 ? "s" : ""}
            <span className="text-orange-400">{showWarnings ? "▲" : "▼"}</span>
          </button>
          {showWarnings && (
            <ul className="mt-2 space-y-1.5">
              {(job.warnings ?? []).map((w, i) => (
                <li key={i} className="text-xs text-orange-700">
                  <span className="font-mono bg-orange-100 px-1 rounded mr-1.5">{w.code}</span>
                  {w.message}
                </li>
              ))}
            </ul>
          )}
        </div>
      )}

      {pendingCount > 0 && (
        <div className="rounded-lg border border-yellow-200 bg-yellow-50 p-3">
          <button
            className="text-sm font-medium text-yellow-800 flex items-center gap-1"
            onClick={() => setShowPending((v) => !v)}
          >
            ⚠ {pendingCount} post{pendingCount > 1 ? "s" : ""} need review
            <span className="text-yellow-500">{showPending ? "▲" : "▼"}</span>
          </button>
          {showPending && (
            <ul className="mt-2 space-y-1">
              {job.pending_review.map((item, i) => (
                <li key={i} className="text-xs text-yellow-700 font-mono truncate">
                  {item.url} — {item.reason}
                </li>
              ))}
            </ul>
          )}
        </div>
      )}

      {failCount > 0 && (
        <div className="rounded-lg border border-red-200 bg-red-50 p-3">
          <button
            className="text-sm font-medium text-red-800 flex items-center gap-1"
            onClick={() => setShowFailed((v) => !v)}
          >
            ✕ {failCount} failure{failCount > 1 ? "s" : ""}
            <span className="text-red-500">{showFailed ? "▲" : "▼"}</span>
          </button>
          {showFailed && (
            <ul className="mt-2 space-y-1">
              {job.failed_urls.map((item, i) => (
                <li key={i} className="text-xs text-red-700 font-mono truncate">
                  {item.url} — {item.error}
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </div>
  );
}
