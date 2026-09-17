"use client";

import React, { useEffect, useState } from "react";
import {
  Box,
  CheckCircle2,
  XCircle,
  UploadCloud,
  Webhook,
  ArrowRight,
  Database,
  Cpu,
  Zap,
  RefreshCw,
} from "lucide-react";
import { getProducts, getImports, getWebhooks, getHealth } from "@/lib/api";

interface DashboardProps {
  onNavigate: (tab: "dashboard" | "products" | "imports" | "webhooks") => void;
}

export const Dashboard: React.FC<DashboardProps> = ({ onNavigate }) => {
  const [stats, setStats] = useState({
    total: 0,
    active: 0,
    inactive: 0,
    importsCount: 0,
    webhooksCount: 0,
  });
  const [loading, setLoading] = useState(true);
  const [isWaking, setIsWaking] = useState(false);

  const loadTelemetry = async () => {
    try {
      const [allRes, activeRes, inactiveRes, imports, webhooks] = await Promise.allSettled([
        getProducts({ limit: 1 }),
        getProducts({ limit: 1, status: "active" }),
        getProducts({ limit: 1, status: "inactive" }),
        getImports(10),
        getWebhooks(),
      ]);

      const allFailed = allRes.status === "rejected" && imports.status === "rejected";
      if (allFailed) {
        const health = await getHealth();
        if (health.status === "waking" || health.status === "offline") {
          setIsWaking(true);
        }
      } else {
        setIsWaking(false);
        const total = allRes.status === "fulfilled" ? allRes.value.pagination.total : 0;
        const active = activeRes.status === "fulfilled" ? activeRes.value.pagination.total : 0;
        const inactive = inactiveRes.status === "fulfilled" ? inactiveRes.value.pagination.total : 0;
        const importsCount = imports.status === "fulfilled" ? imports.value.length : 0;
        const webhooksCount = webhooks.status === "fulfilled" ? webhooks.value.filter((w) => w.enabled).length : 0;

        setStats({ total, active, inactive, importsCount, webhooksCount });
      }
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadTelemetry();
  }, []);

  // Automatic retry if backend was sleeping on initial visit
  useEffect(() => {
    if (!isWaking) return;
    const interval = setInterval(() => {
      loadTelemetry();
    }, 4000);
    return () => clearInterval(interval);
  }, [isWaking]);

  return (
    <div className="space-y-8">
      {/* Hero Welcome Banner */}
      <div className="relative overflow-hidden rounded-2xl bg-gradient-to-r from-indigo-900 via-indigo-800 to-indigo-950 p-8 text-white shadow-md">
        <div className="relative z-10 max-w-2xl">
          <span className="inline-flex items-center gap-1.5 rounded-full bg-indigo-500/20 px-3 py-1 text-xs font-semibold text-indigo-200 border border-indigo-400/20 backdrop-blur-xs">
            <Zap className="h-3.5 w-3.5 text-indigo-300" /> High-Throughput Ingestion Engine
          </span>
          <h1 className="mt-3 text-2xl font-bold tracking-tight sm:text-3xl">
            Assessment OPM Enterprise Dashboard
          </h1>
          <p className="mt-2 text-sm text-indigo-200 leading-relaxed">
            Asynchronous bulk CSV ingestion capable of processing 500,000 products into PostgreSQL using unlogged staging, streaming COPY, and case-insensitive deduplication without blocking the web server.
          </p>
          <div className="mt-6 flex flex-wrap gap-3">
            <button
              onClick={() => onNavigate("imports")}
              className="flex items-center gap-2 rounded-lg bg-white px-4 py-2.5 text-xs font-bold text-indigo-950 hover:bg-indigo-50 transition-all shadow-sm"
            >
              <UploadCloud className="h-4 w-4" /> Start Ingestion
            </button>
            <button
              onClick={() => onNavigate("products")}
              className="flex items-center gap-2 rounded-lg bg-indigo-700/60 px-4 py-2.5 text-xs font-semibold text-white hover:bg-indigo-700 transition-all border border-indigo-400/30"
            >
              <span>Explore Products</span> <ArrowRight className="h-4 w-4" />
            </button>
          </div>
        </div>
      </div>

      {isWaking && (
        <div className="flex items-center justify-between rounded-xl border border-amber-300 bg-amber-50 p-4 text-sm text-amber-900 dark:border-amber-800 dark:bg-amber-950/40 dark:text-amber-200">
          <div className="flex items-center gap-2.5">
            <RefreshCw className="h-4 w-4 animate-spin text-amber-600" />
            <span>Backend server is currently spinning up from sleep (~50s on Render Free tier). Telemetry and metrics will load automatically...</span>
          </div>
          <button
            onClick={() => loadTelemetry()}
            className="rounded-md bg-amber-200 px-3 py-1 text-xs font-semibold text-amber-900 hover:bg-amber-300 transition-colors"
          >
            Retry Now
          </button>
        </div>
      )}

      {/* Metric Cards Grid */}
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <div className="rounded-xl border border-zinc-200 bg-white p-5 shadow-xs dark:border-zinc-800 dark:bg-zinc-900">
          <div className="flex items-center justify-between">
            <span className="text-xs font-semibold uppercase text-zinc-500">Total Products</span>
            <div className="rounded-lg bg-indigo-50 p-2 text-indigo-600 dark:bg-indigo-950/50 dark:text-indigo-400">
              <Box className="h-5 w-5" />
            </div>
          </div>
          <p className="mt-4 text-2xl font-black text-zinc-900 dark:text-white">
            {loading ? "..." : stats.total.toLocaleString()}
          </p>
          <span className="text-xs text-zinc-500">Indexed in PostgreSQL database</span>
        </div>

        <div className="rounded-xl border border-zinc-200 bg-white p-5 shadow-xs dark:border-zinc-800 dark:bg-zinc-900">
          <div className="flex items-center justify-between">
            <span className="text-xs font-semibold uppercase text-zinc-500">Active Products</span>
            <div className="rounded-lg bg-emerald-50 p-2 text-emerald-600 dark:bg-emerald-950/50 dark:text-emerald-400">
              <CheckCircle2 className="h-5 w-5" />
            </div>
          </div>
          <p className="mt-4 text-2xl font-black text-emerald-600 dark:text-emerald-400">
            {loading ? "..." : stats.active.toLocaleString()}
          </p>
          <span className="text-xs text-zinc-500">Status preserved on CSV re-import</span>
        </div>

        <div className="rounded-xl border border-zinc-200 bg-white p-5 shadow-xs dark:border-zinc-800 dark:bg-zinc-900">
          <div className="flex items-center justify-between">
            <span className="text-xs font-semibold uppercase text-zinc-500">Inactive Products</span>
            <div className="rounded-lg bg-zinc-100 p-2 text-zinc-600 dark:bg-zinc-800 dark:text-zinc-400">
              <XCircle className="h-5 w-5" />
            </div>
          </div>
          <p className="mt-4 text-2xl font-black text-zinc-700 dark:text-zinc-300">
            {loading ? "..." : stats.inactive.toLocaleString()}
          </p>
          <span className="text-xs text-zinc-500">Temporarily deactivated catalogue items</span>
        </div>

        <div className="rounded-xl border border-zinc-200 bg-white p-5 shadow-xs dark:border-zinc-800 dark:bg-zinc-900">
          <div className="flex items-center justify-between">
            <span className="text-xs font-semibold uppercase text-zinc-500">Active Webhooks</span>
            <div className="rounded-lg bg-purple-50 p-2 text-purple-600 dark:bg-purple-950/50 dark:text-purple-400">
              <Webhook className="h-5 w-5" />
            </div>
          </div>
          <p className="mt-4 text-2xl font-black text-purple-600 dark:text-purple-400">
            {loading ? "..." : stats.webhooksCount}
          </p>
          <span className="text-xs text-zinc-500">Asynchronous Celery background delivery</span>
        </div>
      </div>

      {/* Architectural Engine Breakdown Card */}
      <div className="rounded-xl border border-zinc-200 bg-white p-6 shadow-xs dark:border-zinc-800 dark:bg-zinc-900">
        <h3 className="text-base font-semibold text-zinc-900 dark:text-white flex items-center gap-2">
          <Cpu className="h-5 w-5 text-indigo-600" />
          Ingestion Architecture Specifications
        </h3>

        <div className="mt-6 grid grid-cols-1 md:grid-cols-3 gap-6 text-sm">
          <div className="space-y-1.5 border-l-2 border-indigo-500 pl-4">
            <h4 className="font-semibold text-zinc-900 dark:text-white">PostgreSQL COPY Staging</h4>
            <p className="text-xs text-zinc-500 dark:text-zinc-400 leading-relaxed">
              Streams validated rows into an unlogged staging table directly via raw binary COPY protocol, bypassing WAL logs and avoiding ORM instantiation overhead.
            </p>
          </div>

          <div className="space-y-1.5 border-l-2 border-emerald-500 pl-4">
            <h4 className="font-semibold text-zinc-900 dark:text-white">Case-Insensitive Deduplication</h4>
            <p className="text-xs text-zinc-500 dark:text-zinc-400 leading-relaxed">
              Enforced by database-level functional unique index on <code className="font-mono">lower(sku)</code>. In-CSV duplicates deterministically resolve with later row replacing earlier.
            </p>
          </div>

          <div className="space-y-1.5 border-l-2 border-purple-500 pl-4">
            <h4 className="font-semibold text-zinc-900 dark:text-white">Real-Time SSE Telemetry</h4>
            <p className="text-xs text-zinc-500 dark:text-zinc-400 leading-relaxed">
              Celery workers publish progress checkpoints to Redis Pub/Sub, streamed via FastAPI Server-Sent Events with automatic keepalive heartbeat.
            </p>
          </div>
        </div>
      </div>
    </div>
  );
};
