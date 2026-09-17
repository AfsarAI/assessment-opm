"use client";

import React, { useState } from "react";
import { Navbar } from "@/components/Navbar";
import { Dashboard } from "@/components/Dashboard";
import { ProductManager } from "@/components/ProductManager";
import { ImportManager } from "@/components/ImportManager";
import { WebhookManager } from "@/components/WebhookManager";

export default function Home() {
  const [activeTab, setActiveTab] = useState<"dashboard" | "products" | "imports" | "webhooks">("dashboard");

  return (
    <div className="min-h-screen bg-zinc-50 dark:bg-zinc-950">
      <Navbar activeTab={activeTab} onSelectTab={setActiveTab} />

      <main className="mx-auto max-w-7xl px-4 py-8 sm:px-6 lg:px-8">
        {activeTab === "dashboard" && <Dashboard onNavigate={setActiveTab} />}
        {activeTab === "products" && <ProductManager />}
        {activeTab === "imports" && <ImportManager />}
        {activeTab === "webhooks" && <WebhookManager />}
      </main>
    </div>
  );
}
