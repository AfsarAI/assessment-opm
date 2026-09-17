"use client";

import React, { useState, useEffect, useRef } from "react";
import { UploadCloud, CheckCircle2, AlertTriangle, Clock, RefreshCw, FileText, XCircle } from "lucide-react";
import { uploadCsv, getImports, getImportProgressUrl } from "@/lib/api";
import { ImportJob, ImportProgressEvent } from "@/types";

export const ImportManager: React.FC = () => {
  const [file, setFile] = useState<File | null>(null);
  const [isUploading, setIsUploading] = useState(false);
  const [activeJob, setActiveJob] = useState<ImportJob | null>(null);
  const [recentJobs, setRecentJobs] = useState<ImportJob[]>([]);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const eventSourceRef = useRef<EventSource | null>(null);

  const loadRecentJobs = async () => {
    try {
      const jobs = await getImports(10);
      setRecentJobs(jobs);
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
    };
  }, []);

  const connectSse = (jobId: string) => {
    if (eventSourceRef.current) {
      eventSourceRef.current.close();
    }

    const sseUrl = getImportProgressUrl(jobId);
    const es = new EventSource(sseUrl);
    eventSourceRef.current = es;

    es.addEventListener("progress", (event) => {
      try {
        const data: ImportProgressEvent = JSON.parse(event.data);
        setActiveJob((prev) => {
          if (!prev) return null;
          return {
            ...prev,
            status: data.status,
            progress: data.progress,
            processed_rows: data.processed_rows,
            total_rows: data.total_rows,
            successful_rows: data.successful_rows,
            failed_rows: data.failed_rows,
            stage_message: data.stage_message,
            error_message: data.error_message,
          };
        });

        if (data.status === "COMPLETED" || data.status === "COMPLETED_WITH_ERRORS" || data.status === "FAILED") {
          es.close();
          loadRecentJobs();
        }
      } catch (err) {
        console.error("Failed to parse SSE event:", err);
      }
    });

    es.onerror = () => {
      es.close();
    };
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

    try {
      const res = await uploadCsv(file);
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
      setActiveJob(newJob);
      setFile(null);
      // Connect to live SSE progress stream
      connectSse(res.import_id);
    } catch (err: any) {
      setUploadError(err.message || "Failed to initiate upload");
    } finally {
      setIsUploading(false);
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

  return (
    <div className="space-y-8">
      {/* Upload Box */}
      <div className="rounded-xl border border-zinc-200 bg-white p-6 shadow-sm dark:border-zinc-800 dark:bg-zinc-900">
        <h2 className="text-lg font-semibold text-zinc-900 dark:text-white">Import Products CSV</h2>
        <p className="mt-1 text-sm text-zinc-500 dark:text-zinc-400">
          Upload up to 500,000 product records. Ingestion executes asynchronously in the background via Celery and PostgreSQL COPY.
        </p>

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
            />
          </label>
        </div>

        {uploadError && (
          <div className="mt-4 flex items-center gap-2 rounded-lg bg-rose-50 p-3 text-sm text-rose-700 dark:bg-rose-950/50 dark:text-rose-400">
            <AlertTriangle className="h-4 w-4" />
            <span>{uploadError}</span>
          </div>
        )}

        <div className="mt-6 flex justify-end">
          <button
            onClick={handleUpload}
            disabled={!file || isUploading}
            className="flex items-center gap-2 rounded-lg bg-indigo-600 px-5 py-2.5 text-sm font-semibold text-white shadow-sm hover:bg-indigo-500 disabled:opacity-50 disabled:cursor-not-allowed transition-all"
          >
            {isUploading ? (
              <>
                <RefreshCw className="h-4 w-4 animate-spin" />
                <span>Uploading...</span>
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
            {getStatusBadge(activeJob.status)}
          </div>

          <div className="mt-6">
            <div className="flex justify-between text-sm font-medium text-zinc-700 dark:text-zinc-300 mb-2">
              <span>{activeJob.stage_message}</span>
              <span>{activeJob.progress}%</span>
            </div>
            <div className="h-3 w-full overflow-hidden rounded-full bg-zinc-200 dark:bg-zinc-800">
              <div
                className="h-full bg-indigo-600 transition-all duration-300 ease-out"
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
              <span className="text-xs text-zinc-500">Succeeded</span>
              <p className="mt-1 text-lg font-bold text-emerald-600 dark:text-emerald-400">
                {activeJob.successful_rows.toLocaleString()}
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

      {/* Recent Imports History */}
      <div className="rounded-xl border border-zinc-200 bg-white p-6 shadow-sm dark:border-zinc-800 dark:bg-zinc-900">
        <div className="flex items-center justify-between mb-4">
          <h2 className="text-lg font-semibold text-zinc-900 dark:text-white">Recent Imports</h2>
          <button
            onClick={loadRecentJobs}
            className="flex items-center gap-1.5 text-xs font-medium text-zinc-500 hover:text-zinc-900 dark:hover:text-white"
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
                  <tr key={job.id} className="hover:bg-zinc-50/50 dark:hover:bg-zinc-800/20">
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
                      <button
                        onClick={() => {
                          setActiveJob(job);
                          if (job.status !== "COMPLETED" && job.status !== "FAILED") {
                            connectSse(job.id);
                          }
                        }}
                        className="text-xs font-semibold text-indigo-600 hover:text-indigo-500 dark:text-indigo-400"
                      >
                        Inspect
                      </button>
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
