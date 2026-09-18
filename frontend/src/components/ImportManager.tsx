"use client";

import React, { useState, useEffect, useRef } from "react";
import {
  UploadCloud,
  CheckCircle2,
  AlertTriangle,
  Clock,
  RefreshCw,
  FileText,
  XCircle,
  StopCircle,
  X,
  ShieldAlert,
} from "lucide-react";
import { uploadCsv, cancelImport, getImports, getImport, getImportProgressUrl } from "@/lib/api";
import { ImportJob, ImportProgressEvent } from "@/types";

export const ImportManager: React.FC = () => {
  const [file, setFile] = useState<File | null>(null);
  const [isUploading, setIsUploading] = useState(false);
  const [uploadProgress, setUploadProgress] = useState<{ percent: number; loaded: number; total: number } | null>(null);
  const [activeJob, setActiveJob] = useState<ImportJob | null>(null);
  const [recentJobs, setRecentJobs] = useState<ImportJob[]>([]);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const [isCancelling, setIsCancelling] = useState(false);
  const [showCancelModal, setShowCancelModal] = useState(false);

  const eventSourceRef = useRef<EventSource | null>(null);
  const fallbackPollRef = useRef<NodeJS.Timeout | null>(null);
  const currentXhrRef = useRef<XMLHttpRequest | null>(null);
  const lastSeenSeqRef = useRef<number>(0);

  const loadRecentJobs = async () => {
    try {
      const jobs = await getImports(10);
      setRecentJobs(jobs);

      // Auto-reconnect if any background job is actively processing (excluding stale zombie jobs)
      const active = jobs.find((j) => {
        if (!["QUEUED", "PARSING", "VALIDATING", "IMPORTING"].includes(j.status)) return false;
        const updatedTime = new Date(j.updated_at).getTime();
        // If an in-progress job hasn't updated in 15 minutes, treat it as dead/stale
        return Date.now() - updatedTime < 15 * 60 * 1000;
      });

      if (active) {
        setActiveJob(active);
        connectSse(active.id);
      }
    } catch {
      // Ignored
    }
  };

  useEffect(() => {
    loadRecentJobs();
    return () => {
      if (eventSourceRef.current) {
        eventSourceRef.current.close();
      }
      if (fallbackPollRef.current) {
        clearInterval(fallbackPollRef.current);
      }
      if (currentXhrRef.current) {
        currentXhrRef.current.abort();
      }
    };
  }, []);

  const connectSse = (jobId: string) => {
    if (eventSourceRef.current) {
      eventSourceRef.current.close();
      eventSourceRef.current = null;
    }
    if (fallbackPollRef.current) {
      clearInterval(fallbackPollRef.current);
      fallbackPollRef.current = null;
    }

    lastSeenSeqRef.current = 0;
    const sseUrl = getImportProgressUrl(jobId);
    const es = new EventSource(sseUrl);
    eventSourceRef.current = es;

    const handleTerminalStatus = () => {
      if (eventSourceRef.current) {
        eventSourceRef.current.close();
        eventSourceRef.current = null;
      }
      if (fallbackPollRef.current) {
        clearInterval(fallbackPollRef.current);
        fallbackPollRef.current = null;
      }
      loadRecentJobs();
    };

    es.addEventListener("progress", (event) => {
      try {
        const data: ImportProgressEvent = JSON.parse(event.data);

        // Sequence number check: ignore stale out-of-order events
        if (data.seq !== undefined && data.seq < lastSeenSeqRef.current) {
          return;
        }
        if (data.seq !== undefined) {
          lastSeenSeqRef.current = data.seq;
        }

        setActiveJob((prev) => {
          if (!prev) return null;
          // Invariant: displayed progress must NEVER decrease for the same import
          const safeProgress = data.status === "COMPLETED" 
            ? 100 
            : Math.max(prev.progress, data.progress);

          return {
            ...prev,
            status: data.status,
            progress: safeProgress,
            processed_rows: Math.max(prev.processed_rows, data.processed_rows),
            total_rows: data.total_rows > 0 ? data.total_rows : prev.total_rows,
            successful_rows: data.successful_rows,
            failed_rows: data.failed_rows,
            stage_message: data.stage_message || prev.stage_message,
            error_message: data.error_message,
          };
        });

        // Sync history table in real time monotonically
        setRecentJobs((prev) =>
          prev.map((j) =>
            j.id === jobId
              ? {
                  ...j,
                  status: data.status,
                  progress: data.status === "COMPLETED" ? 100 : Math.max(j.progress, data.progress),
                  processed_rows: Math.max(j.processed_rows, data.processed_rows),
                  total_rows: data.total_rows > 0 ? data.total_rows : j.total_rows,
                  successful_rows: data.successful_rows,
                  failed_rows: data.failed_rows,
                  stage_message: data.stage_message || j.stage_message,
                }
              : j
          )
        );

        if (["COMPLETED", "COMPLETED_WITH_ERRORS", "FAILED", "CANCELLED"].includes(data.status)) {
          handleTerminalStatus();
        }
      } catch (err) {
        console.error("Failed to parse SSE event:", err);
      }
    });

    es.onerror = () => {
      // SSE connection dropped; watchdog polling will maintain state
    };

    // Authoritative background watchdog poll every 2.5s:
    // Guarantees UI completion even if reverse proxies drop SSE or network fluctuates
    fallbackPollRef.current = setInterval(async () => {
      try {
        const current = await getImport(jobId);
        if (["COMPLETED", "COMPLETED_WITH_ERRORS", "FAILED", "CANCELLED"].includes(current.status)) {
          setActiveJob((prev) => {
            const finalProgress = current.status === "COMPLETED" 
              ? 100 
              : Math.max(prev?.progress ?? 0, current.progress);
            return {
              ...(prev || current),
              ...current,
              progress: finalProgress,
            };
          });
          setRecentJobs((prev) =>
            prev.map((j) =>
              j.id === jobId
                ? {
                    ...j,
                    ...current,
                    progress: current.status === "COMPLETED" ? 100 : Math.max(j.progress, current.progress),
                  }
                : j
            )
          );
          handleTerminalStatus();
        } else {
          setActiveJob((prev) => {
            if (!prev) return current;
            // Invariant: watchdog polling must NEVER pull progress or stage backwards!
            const safeProgress = Math.max(prev.progress, current.progress);
            const safeProcessed = Math.max(prev.processed_rows, current.processed_rows);
            return {
              ...prev,
              ...current,
              progress: safeProgress,
              processed_rows: safeProcessed,
              total_rows: current.total_rows > 0 ? current.total_rows : prev.total_rows,
              stage_message: current.progress >= prev.progress ? current.stage_message : prev.stage_message,
            };
          });
        }
      } catch {}
    }, 2500);
  };

  const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    if (e.target.files && e.target.files[0]) {
      setFile(e.target.files[0]);
      setUploadError(null);
    }
  };

  const handleUpload = async () => {
    if (!file) return;
    setIsUploading(true);
    setUploadError(null);
    setUploadProgress({ percent: 0, loaded: 0, total: file.size });

    try {
      const res = await uploadCsv(
        file,
        (percent, loaded, total) => {
          setUploadProgress({ percent, loaded, total });
        },
        (xhr) => {
          currentXhrRef.current = xhr;
        }
      );

      currentXhrRef.current = null;

      const newJob: ImportJob = {
        id: res.import_id,
        filename: file.name,
        status: "QUEUED",
        total_rows: 0,
        processed_rows: 0,
        successful_rows: 0,
        failed_rows: 0,
        progress: 0,
        stage_message: "Import job queued for background processing",
        created_at: new Date().toISOString(),
        updated_at: new Date().toISOString(),
      };

      setRecentJobs((prev) => [newJob, ...prev.filter((j) => j.id !== newJob.id)]);
      setActiveJob(newJob);
      setFile(null);
      setUploadProgress(null);
      // Connect to live SSE progress stream
      connectSse(res.import_id);
    } catch (err: any) {
      if (err.code === "CANCELLED") {
        setUploadError("Upload was cancelled.");
      } else {
        setUploadError(err.message || "Failed to initiate upload");
      }
      setUploadProgress(null);
    } finally {
      setIsUploading(false);
      currentXhrRef.current = null;
    }
  };

  const handleTriggerCancel = () => {
    setShowCancelModal(true);
  };

  const handleConfirmCancel = async () => {
    setShowCancelModal(false);
    setIsCancelling(true);

    try {
      // 1. If currently uploading file over HTTP, abort XHR
      if (isUploading && currentXhrRef.current) {
        currentXhrRef.current.abort();
        currentXhrRef.current = null;
        setIsUploading(false);
        setUploadProgress(null);
        setIsCancelling(false);
        return;
      }

      // 2. If active job is queued or processing on backend, call cancellation endpoint
      if (activeJob) {
        const res = await cancelImport(activeJob.id);
        setActiveJob((prev) =>
          prev
            ? {
                ...prev,
                status: "CANCELLED",
                stage_message: res.message || "Import cancelled by user",
                successful_rows: 0,
              }
            : null
        );

        if (eventSourceRef.current) {
          eventSourceRef.current.close();
          eventSourceRef.current = null;
        }
        if (fallbackPollRef.current) {
          clearInterval(fallbackPollRef.current);
          fallbackPollRef.current = null;
        }

        await loadRecentJobs();
      }
    } catch (err: any) {
      console.error("Cancellation error:", err);
    } finally {
      setIsCancelling(false);
    }
  };

  const getStatusBadge = (status: ImportJob["status"]) => {
    switch (status) {
      case "COMPLETED":
        return (
          <span className="inline-flex items-center gap-1 rounded-full bg-emerald-100 px-2.5 py-0.5 text-xs font-semibold text-emerald-800 dark:bg-emerald-950/60 dark:text-emerald-400">
            <CheckCircle2 className="h-3.5 w-3.5" /> Completed
          </span>
        );
      case "COMPLETED_WITH_ERRORS":
        return (
          <span className="inline-flex items-center gap-1 rounded-full bg-amber-100 px-2.5 py-0.5 text-xs font-semibold text-amber-800 dark:bg-amber-950/60 dark:text-amber-400">
            <AlertTriangle className="h-3.5 w-3.5" /> With Warnings
          </span>
        );
      case "CANCELLED":
        return (
          <span className="inline-flex items-center gap-1 rounded-full bg-zinc-200 px-2.5 py-0.5 text-xs font-semibold text-zinc-800 dark:bg-zinc-800 dark:text-zinc-300">
            <StopCircle className="h-3.5 w-3.5 text-zinc-600 dark:text-zinc-400" /> Cancelled
          </span>
        );
      case "FAILED":
        return (
          <span className="inline-flex items-center gap-1 rounded-full bg-rose-100 px-2.5 py-0.5 text-xs font-semibold text-rose-800 dark:bg-rose-950/60 dark:text-rose-400">
            <XCircle className="h-3.5 w-3.5" /> Failed
          </span>
        );
      default:
        return (
          <span className="inline-flex items-center gap-1 rounded-full bg-indigo-100 px-2.5 py-0.5 text-xs font-semibold text-indigo-800 dark:bg-indigo-950/60 dark:text-indigo-400 animate-pulse">
            <Clock className="h-3.5 w-3.5" /> {status}
          </span>
        );
    }
  };

  const isJobInProgress = Boolean(
    isUploading || (activeJob && ["QUEUED", "PARSING", "VALIDATING", "IMPORTING"].includes(activeJob.status))
  );

  return (
    <div className="space-y-8">
      {/* Upload Box */}
      <div className="rounded-xl border border-zinc-200 bg-white p-6 shadow-sm dark:border-zinc-800 dark:bg-zinc-900">
        <div className="flex items-center justify-between">
          <div>
            <h2 className="text-lg font-semibold text-zinc-900 dark:text-white">Import Products CSV</h2>
            <p className="mt-1 text-sm text-zinc-500 dark:text-zinc-400">
              Upload up to 500,000 product records. Ingestion executes asynchronously via PostgreSQL streaming COPY and range-chunked merge.
            </p>
          </div>
          {isJobInProgress && (
            <button
              onClick={handleTriggerCancel}
              disabled={isCancelling}
              className="flex items-center gap-1.5 rounded-lg border border-rose-300 bg-rose-50 px-3.5 py-2 text-xs font-semibold text-rose-700 hover:bg-rose-100 dark:border-rose-900/60 dark:bg-rose-950/40 dark:text-rose-300 dark:hover:bg-rose-950/70 transition-colors shadow-xs"
            >
              <StopCircle className="h-4 w-4" />
              <span>{isCancelling ? "Cancelling..." : "Cancel Import"}</span>
            </button>
          )}
        </div>

        <div className="mt-6">
          <label className="flex flex-col items-center justify-center rounded-xl border-2 border-dashed border-zinc-300 p-8 text-center hover:border-indigo-500 dark:border-zinc-700 dark:hover:border-indigo-400 cursor-pointer transition-colors bg-zinc-50/50 dark:bg-zinc-950/30">
            <UploadCloud className="h-10 w-10 text-zinc-400" />
            <span className="mt-3 text-sm font-medium text-zinc-700 dark:text-zinc-300">
              {file ? file.name : "Choose CSV file or drag and drop here"}
            </span>
            <span className="mt-1 text-xs text-zinc-500">
              {file ? `${(file.size / (1024 * 1024)).toFixed(2)} MB` : "Columns: name, sku, description (up to 250MB)"}
            </span>
            <input
              type="file"
              accept=".csv"
              onChange={handleFileChange}
              className="hidden"
              disabled={isJobInProgress}
            />
          </label>
        </div>

        {uploadError && (
          <div className="mt-4 flex items-center gap-2 rounded-lg bg-rose-50 p-3 text-sm text-rose-700 dark:bg-rose-950/50 dark:text-rose-400">
            <AlertTriangle className="h-4 w-4 shrink-0" />
            <span>{uploadError}</span>
          </div>
        )}

        {uploadProgress && isUploading && (
          <div className="mt-4 rounded-xl border border-indigo-200 bg-indigo-50/50 p-4 dark:border-indigo-900/50 dark:bg-indigo-950/20">
            <div className="flex justify-between text-xs font-semibold text-indigo-900 dark:text-indigo-200 mb-1.5">
              <span className="flex items-center gap-2">
                <RefreshCw className="h-3.5 w-3.5 animate-spin text-indigo-600" />
                Uploading to server: {(uploadProgress.loaded / (1024 * 1024)).toFixed(1)} MB / {(uploadProgress.total / (1024 * 1024)).toFixed(1)} MB
              </span>
              <span>{uploadProgress.percent}%</span>
            </div>
            <div className="h-2 w-full overflow-hidden rounded-full bg-zinc-200 dark:bg-zinc-800">
              <div
                className="h-full bg-indigo-600 transition-all duration-150 ease-out"
                style={{ width: `${Math.max(uploadProgress.percent, 2)}%` }}
              />
            </div>
            <div className="mt-2 flex items-center justify-between text-xs text-zinc-500 dark:text-zinc-400">
              <span>Streaming file directly to server disk...</span>
              <button
                onClick={handleTriggerCancel}
                className="font-medium text-rose-600 hover:text-rose-700 dark:text-rose-400"
              >
                Abort Upload
              </button>
            </div>
          </div>
        )}

        <div className="mt-6 flex items-center justify-end gap-3">
          {isJobInProgress && (
            <button
              onClick={handleTriggerCancel}
              disabled={isCancelling}
              className="flex items-center gap-2 rounded-lg border border-rose-300 bg-rose-50 px-4 py-2.5 text-sm font-semibold text-rose-700 hover:bg-rose-100 dark:border-rose-900/60 dark:bg-rose-950/40 dark:text-rose-300 dark:hover:bg-rose-950/70 transition-all"
            >
              <StopCircle className="h-4 w-4" />
              <span>{isCancelling ? "Cancelling..." : "Cancel Import"}</span>
            </button>
          )}
          <button
            onClick={handleUpload}
            disabled={!file || isJobInProgress}
            className="flex items-center gap-2 rounded-lg bg-indigo-600 px-5 py-2.5 text-sm font-semibold text-white shadow-sm hover:bg-indigo-500 disabled:opacity-50 disabled:cursor-not-allowed transition-all"
          >
            {isUploading ? (
              <>
                <RefreshCw className="h-4 w-4 animate-spin" />
                <span>Uploading ({uploadProgress ? `${uploadProgress.percent}%` : "..."})</span>
              </>
            ) : isJobInProgress ? (
              <>
                <RefreshCw className="h-4 w-4 animate-spin" />
                <span>Ingestion in Progress...</span>
              </>
            ) : (
              <span>Start Ingestion</span>
            )}
          </button>
        </div>
      </div>

      {/* Active Live Progress Card */}
      {activeJob && (
        <div className="rounded-xl border border-indigo-200 bg-indigo-50/30 p-6 shadow-sm dark:border-indigo-900/50 dark:bg-indigo-950/10">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-3">
              <FileText className="h-5 w-5 text-indigo-600" />
              <div>
                <h3 className="text-base font-semibold text-zinc-900 dark:text-white">{activeJob.filename}</h3>
                <p className="text-xs text-zinc-500 font-mono">Job ID: {activeJob.id}</p>
              </div>
            </div>
            <div className="flex items-center gap-3">
              {getStatusBadge(activeJob.status)}
              {isJobInProgress ? (
                <button
                  onClick={handleTriggerCancel}
                  disabled={isCancelling}
                  className="flex items-center gap-1 rounded-md border border-rose-300 bg-white px-2.5 py-1 text-xs font-semibold text-rose-700 hover:bg-rose-50 dark:border-rose-900/60 dark:bg-zinc-900 dark:text-rose-300 dark:hover:bg-rose-950/50 transition-colors shadow-xs"
                >
                  <StopCircle className="h-3.5 w-3.5" />
                  <span>{isCancelling ? "Cancelling..." : "Cancel"}</span>
                </button>
              ) : (
                <button
                  onClick={() => setActiveJob(null)}
                  className="p-1 text-zinc-400 hover:text-zinc-600 dark:hover:text-zinc-200 transition-colors"
                  title="Dismiss view"
                >
                  <X className="h-4 w-4" />
                </button>
              )}
            </div>
          </div>

          <div className="mt-6">
            <div className="flex justify-between text-sm font-medium text-zinc-700 dark:text-zinc-300 mb-2">
              <span className="flex items-center gap-2">
                {["PARSING", "VALIDATING", "IMPORTING"].includes(activeJob.status) && (
                  <RefreshCw className="h-3.5 w-3.5 animate-spin text-indigo-600 dark:text-indigo-400" />
                )}
                {activeJob.stage_message}
              </span>
              <span className="font-semibold">{activeJob.progress}%</span>
            </div>
            <div className="h-3 w-full overflow-hidden rounded-full bg-zinc-200 dark:bg-zinc-800">
              <div
                className={`h-full transition-all duration-300 ease-out ${
                  activeJob.status === "COMPLETED"
                    ? "bg-emerald-500"
                    : activeJob.status === "FAILED"
                    ? "bg-rose-500"
                    : activeJob.status === "CANCELLED"
                    ? "bg-zinc-500"
                    : ["PARSING", "VALIDATING", "IMPORTING"].includes(activeJob.status)
                    ? "bg-indigo-600 animate-pulse"
                    : "bg-indigo-600"
                }`}
                style={{ width: `${Math.max(activeJob.progress, 3)}%` }}
              />
            </div>
          </div>

          <div className="mt-6 grid grid-cols-2 gap-4 sm:grid-cols-4">
            <div className="rounded-lg bg-white p-3 shadow-xs dark:bg-zinc-900">
              <span className="text-xs text-zinc-500">Processed</span>
              <p className="mt-1 text-lg font-bold text-zinc-900 dark:text-white">
                {activeJob.processed_rows.toLocaleString()}
              </p>
            </div>
            <div className="rounded-lg bg-white p-3 shadow-xs dark:bg-zinc-900">
              <span className="text-xs text-zinc-500">Total Rows</span>
              <p className="mt-1 text-lg font-bold text-zinc-900 dark:text-white">
                {activeJob.total_rows > 0 ? activeJob.total_rows.toLocaleString() : "--"}
              </p>
            </div>
            <div className="rounded-lg bg-white p-3 shadow-xs dark:bg-zinc-900">
              <span className="text-xs text-zinc-500">Committed / Succeeded</span>
              <p className="mt-1 text-lg font-bold text-emerald-600 dark:text-emerald-400">
                {activeJob.status === "COMPLETED" || activeJob.status === "COMPLETED_WITH_ERRORS"
                  ? activeJob.successful_rows.toLocaleString()
                  : activeJob.status === "CANCELLED" || activeJob.status === "FAILED"
                  ? "0"
                  : activeJob.successful_rows > 0
                  ? activeJob.successful_rows.toLocaleString()
                  : "--"}
              </p>
            </div>
            <div className="rounded-lg bg-white p-3 shadow-xs dark:bg-zinc-900">
              <span className="text-xs text-zinc-500">Failed / Malformed</span>
              <p className="mt-1 text-lg font-bold text-rose-600 dark:text-rose-400">
                {activeJob.failed_rows.toLocaleString()}
              </p>
            </div>
          </div>

          {activeJob.error_message && (
            <div className="mt-4 rounded-lg bg-rose-50 p-3 text-sm text-rose-800 dark:bg-rose-950/60 dark:text-rose-300">
              <strong>Error:</strong> {activeJob.error_message}
            </div>
          )}
        </div>
      )}

      {/* Confirmation Modal for Cancel Import */}
      {showCancelModal && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4 backdrop-blur-xs animate-in fade-in duration-200">
          <div className="w-full max-w-md rounded-2xl border border-zinc-200 bg-white p-6 shadow-xl dark:border-zinc-800 dark:bg-zinc-900">
            <div className="flex items-center gap-3 text-rose-600 dark:text-rose-400">
              <ShieldAlert className="h-6 w-6" />
              <h3 className="text-lg font-bold text-zinc-900 dark:text-white">Cancel Import?</h3>
            </div>
            <p className="mt-3 text-sm text-zinc-600 dark:text-zinc-300">
              Are you sure you want to cancel this CSV import?
            </p>
            <ul className="mt-3 space-y-1.5 text-xs text-zinc-500 dark:text-zinc-400 list-disc list-inside">
              <li>Active database operations will be interrupted.</li>
              <li>Uncommitted data will be rolled back completely.</li>
              <li>Staging tables and temporary files will be cleaned up.</li>
              <li>Zero partial or corrupted records will remain in the catalogue.</li>
            </ul>

            <div className="mt-6 flex justify-end gap-3">
              <button
                onClick={() => setShowCancelModal(false)}
                className="rounded-lg border border-zinc-300 px-4 py-2 text-xs font-semibold text-zinc-700 hover:bg-zinc-100 dark:border-zinc-700 dark:text-zinc-300 dark:hover:bg-zinc-800 transition-colors"
              >
                Keep Importing
              </button>
              <button
                onClick={handleConfirmCancel}
                className="rounded-lg bg-rose-600 px-4 py-2 text-xs font-semibold text-white shadow-sm hover:bg-rose-500 transition-colors"
              >
                Yes, Cancel Import
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Recent Imports History */}
      <div className="rounded-xl border border-zinc-200 bg-white p-6 shadow-sm dark:border-zinc-800 dark:bg-zinc-900">
        <div className="flex items-center justify-between mb-4">
          <h2 className="text-lg font-semibold text-zinc-900 dark:text-white">Recent Imports</h2>
          <button
            onClick={loadRecentJobs}
            className="flex items-center gap-1.5 text-xs font-medium text-zinc-500 hover:text-zinc-900 dark:hover:text-white transition-colors"
          >
            <RefreshCw className="h-3.5 w-3.5" /> Refresh
          </button>
        </div>

        {recentJobs.length === 0 ? (
          <p className="text-sm text-zinc-500 py-6 text-center">No previous import jobs found.</p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-left text-sm text-zinc-600 dark:text-zinc-400">
              <thead className="border-b border-zinc-200 text-xs uppercase text-zinc-500 dark:border-zinc-800">
                <tr>
                  <th className="py-3 px-4">Filename</th>
                  <th className="py-3 px-4">Status</th>
                  <th className="py-3 px-4">Rows (Succeeded / Total)</th>
                  <th className="py-3 px-4">Failed</th>
                  <th className="py-3 px-4">Date</th>
                  <th className="py-3 px-4 text-right">Action</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-zinc-100 dark:divide-zinc-800/50">
                {recentJobs.map((job) => (
                  <tr key={job.id} className="hover:bg-zinc-50/50 dark:hover:bg-zinc-800/20 transition-colors">
                    <td className="py-3.5 px-4 font-medium text-zinc-900 dark:text-white">{job.filename}</td>
                    <td className="py-3.5 px-4">{getStatusBadge(job.status)}</td>
                    <td className="py-3.5 px-4">
                      {job.successful_rows.toLocaleString()} / {job.total_rows.toLocaleString()}
                    </td>
                    <td className="py-3.5 px-4">
                      {job.failed_rows > 0 ? (
                        <span className="font-semibold text-rose-600">{job.failed_rows.toLocaleString()}</span>
                      ) : (
                        "0"
                      )}
                    </td>
                    <td className="py-3.5 px-4 text-xs text-zinc-500">
                      {new Date(job.created_at).toLocaleString()}
                    </td>
                    <td className="py-3.5 px-4 text-right">
                      <div className="flex items-center justify-end gap-2">
                        {["QUEUED", "PARSING", "VALIDATING", "IMPORTING"].includes(job.status) && (
                          <button
                            onClick={async () => {
                              try {
                                await cancelImport(job.id);
                                loadRecentJobs();
                              } catch (e) {
                                console.error(e);
                              }
                            }}
                            className="text-xs font-semibold text-rose-600 hover:text-rose-500 dark:text-rose-400"
                          >
                            Cancel
                          </button>
                        )}
                        <button
                          onClick={() => {
                            setActiveJob(job);
                            if (["QUEUED", "PARSING", "VALIDATING", "IMPORTING"].includes(job.status)) {
                              connectSse(job.id);
                            }
                          }}
                          className="text-xs font-semibold text-indigo-600 hover:text-indigo-500 dark:text-indigo-400"
                        >
                          Inspect
                        </button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
};
