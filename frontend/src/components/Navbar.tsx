"use client";

import React, { useEffect, useState } from "react";
import { getHealth } from "@/lib/api";
import { Database, UploadCloud, Webhook, Box, BarChart3, Activity } from "lucide-react";

interface NavbarProps {
  activeTab: "dashboard" | "products" | "imports" | "webhooks";
  onSelectTab: (tab: "dashboard" | "products" | "imports" | "webhooks") => void;
}

export const Navbar: React.FC<NavbarProps> = ({ activeTab, onSelectTab }) => {
  const [healthStatus, setHealthStatus] = useState<"checking" | "online" | "offline">("checking");

  useEffect(() => {
    const check = async () => {
      const res = await getHealth();
      setHealthStatus(res.status === "ok" ? "online" : "offline");
    };
    check();
    const interval = setInterval(check, 30000);
    return () => clearInterval(interval);
  }, []);

  const navItems = [
    { id: "dashboard", label: "Dashboard", icon: BarChart3 },
    { id: "products", label: "Products", icon: Box },
    { id: "imports", label: "CSV Ingestion", icon: UploadCloud },
    { id: "webhooks", label: "Webhooks", icon: Webhook },
  ] as const;

  return (
    <header className="sticky top-0 z-40 w-full border-b border-zinc-200 bg-white/95 backdrop-blur dark:border-zinc-800 dark:bg-zinc-950/95">
      <div className="mx-auto flex h-16 max-w-7xl items-center justify-between px-4 sm:px-6 lg:px-8">
        <div className="flex items-center gap-3">
          <div className="flex h-9 w-9 items-center justify-center rounded-lg bg-indigo-600 text-white shadow-sm">
            <Database className="h-5 w-5" />
          </div>
          <div>
            <span className="font-bold tracking-tight text-zinc-900 dark:text-white">Assessment OPM</span>
            <span className="ml-2 rounded-full bg-indigo-50 px-2 py-0.5 text-xs font-semibold text-indigo-700 dark:bg-indigo-950/50 dark:text-indigo-400">
              500K Engine
            </span>
          </div>
        </div>

        <nav className="flex items-center gap-1">
          {navItems.map((item) => {
            const Icon = item.icon;
            const isActive = activeTab === item.id;
            return (
              <button
                key={item.id}
                onClick={() => onSelectTab(item.id)}
                className={`flex items-center gap-2 rounded-lg px-3.5 py-2 text-sm font-medium transition-all ${
                  isActive
                    ? "bg-indigo-50 text-indigo-700 dark:bg-indigo-950/60 dark:text-indigo-300"
                    : "text-zinc-600 hover:bg-zinc-100 hover:text-zinc-900 dark:text-zinc-400 dark:hover:bg-zinc-900 dark:hover:text-zinc-200"
                }`}
              >
                <Icon className="h-4 w-4" />
                <span>{item.label}</span>
              </button>
            );
          })}
        </nav>

        <div className="flex items-center gap-3">
          <div
            className={`flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-medium ${
              healthStatus === "online"
                ? "bg-emerald-50 text-emerald-700 dark:bg-emerald-950/50 dark:text-emerald-400"
                : healthStatus === "checking"
                ? "bg-amber-50 text-amber-700 dark:bg-amber-950/50 dark:text-amber-400"
                : "bg-rose-50 text-rose-700 dark:bg-rose-950/50 dark:text-rose-400"
            }`}
          >
            <span
              className={`h-2 w-2 rounded-full ${
                healthStatus === "online"
                  ? "bg-emerald-500 animate-pulse"
                  : healthStatus === "checking"
                  ? "bg-amber-500"
                  : "bg-rose-500"
              }`}
            />
            <span>{healthStatus === "online" ? "API Online" : healthStatus === "checking" ? "Checking..." : "Offline"}</span>
          </div>
        </div>
      </div>
    </header>
  );
};
