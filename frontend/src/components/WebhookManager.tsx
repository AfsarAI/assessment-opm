"use client";

import React, { useState, useEffect } from "react";
import {
  Webhook as WebhookIcon,
  Plus,
  Trash2,
  Edit2,
  Play,
  CheckCircle2,
  XCircle,
  Clock,
  ShieldCheck,
  RefreshCw,
  X,
} from "lucide-react";
import {
  getWebhooks,
  createWebhook,
  updateWebhook,
  deleteWebhook,
  testWebhook,
} from "@/lib/api";
import { Webhook, WebhookTestResult } from "@/types";

const ALL_EVENTS = [
  { id: "product.created", label: "Product Created", desc: "Fires when a new product is added" },
  { id: "product.updated", label: "Product Updated", desc: "Fires when product details or status change" },
  { id: "product.deleted", label: "Product Deleted", desc: "Fires when an individual product is deleted" },
  { id: "products.cleared", label: "Products Cleared", desc: "Fires on full catalogue bulk truncation" },
  { id: "import.completed", label: "Import Completed", desc: "Fires when a CSV import finishes successfully" },
  { id: "import.failed", label: "Import Failed", desc: "Fires if a CSV import encounters a fatal error" },
];

export const WebhookManager: React.FC = () => {
  const [webhooks, setWebhooks] = useState<Webhook[]>([]);
  const [loading, setLoading] = useState(false);

  // Modal states
  const [isModalOpen, setIsModalOpen] = useState(false);
  const [editingWebhook, setEditingWebhook] = useState<Webhook | null>(null);
  const [formUrl, setFormUrl] = useState("");
  const [formEvents, setFormEvents] = useState<string[]>(["product.created"]);
  const [formEnabled, setFormEnabled] = useState(true);
  const [formSecret, setFormSecret] = useState("");
  const [formError, setFormError] = useState<string | null>(null);
  const [actionLoading, setActionLoading] = useState(false);

  // Test modal state
  const [testResult, setTestResult] = useState<WebhookTestResult | null>(null);
  const [testingWebhookId, setTestingWebhookId] = useState<string | null>(null);

  const loadWebhooks = async () => {
    setLoading(true);
    try {
      const data = await getWebhooks();
      setWebhooks(data);
    } catch (err: any) {
      console.error("Failed to load webhooks:", err);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadWebhooks();
  }, []);

  const openCreateModal = () => {
    setEditingWebhook(null);
    setFormUrl("");
    setFormEvents(["product.created"]);
    setFormEnabled(true);
    setFormSecret("");
    setFormError(null);
    setIsModalOpen(true);
  };

  const openEditModal = (wh: Webhook) => {
    setEditingWebhook(wh);
    setFormUrl(wh.url);
    setFormEvents([...wh.events]);
    setFormEnabled(wh.enabled);
    setFormSecret(wh.secret || "");
    setFormError(null);
    setIsModalOpen(true);
  };

  const handleToggleEvent = (eventId: string) => {
    setFormEvents((prev) =>
      prev.includes(eventId) ? prev.filter((e) => e !== eventId) : [...prev, eventId]
    );
  };

  const handleSaveWebhook = async (e: React.FormEvent) => {
    e.preventDefault();
    setFormError(null);
    if (formEvents.length === 0) {
      setFormError("Select at least one event type.");
      return;
    }
    setActionLoading(true);

    try {
      if (editingWebhook) {
        const updated = await updateWebhook(editingWebhook.id, {
          url: formUrl,
          events: formEvents,
          enabled: formEnabled,
          secret: formSecret || undefined,
        });
        setWebhooks((prev) => prev.map((w) => (w.id === updated.id ? updated : w)));
      } else {
        const created = await createWebhook({
          url: formUrl,
          events: formEvents,
          enabled: formEnabled,
          secret: formSecret || undefined,
        });
        setWebhooks((prev) => [created, ...prev]);
      }
      setIsModalOpen(false);
    } catch (err: any) {
      setFormError(err.message || "Failed to save webhook");
    } finally {
      setActionLoading(false);
    }
  };

  const handleDelete = async (id: string) => {
    if (!confirm("Are you sure you want to delete this webhook subscription?")) return;
    try {
      await deleteWebhook(id);
      setWebhooks((prev) => prev.filter((w) => w.id !== id));
    } catch (err: any) {
      alert(`Failed to delete webhook: ${err.message}`);
    }
  };

  const handleTestWebhook = async (id: string) => {
    setTestingWebhookId(id);
    setTestResult(null);
    try {
      const result = await testWebhook(id);
      setTestResult(result);
    } catch (err: any) {
      setTestResult({
        success: false,
        status_code: 500,
        response_time_ms: 0,
        message: err.message || "Failed to execute webhook test",
      });
    } finally {
      setTestingWebhookId(null);
    }
  };

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex flex-col sm:flex-row items-start sm:items-center justify-between gap-4">
        <div>
          <h2 className="text-xl font-bold tracking-tight text-zinc-900 dark:text-white">
            Webhook Subscriptions
          </h2>
          <p className="text-sm text-zinc-500 dark:text-zinc-400">
            Configure external endpoints to receive real-time notifications for catalog and import events.
          </p>
        </div>

        <button
          onClick={openCreateModal}
          className="flex items-center gap-2 rounded-lg bg-indigo-600 px-4 py-2.5 text-sm font-semibold text-white shadow-sm hover:bg-indigo-500 transition-all"
        >
          <Plus className="h-4 w-4" /> Add Webhook
        </button>
      </div>

      {/* Webhook List */}
      {loading ? (
        <div className="py-12 text-center text-zinc-500">
          <RefreshCw className="inline h-5 w-5 animate-spin text-indigo-600 mb-2" />
          <p>Loading registered webhooks...</p>
        </div>
      ) : webhooks.length === 0 ? (
        <div className="rounded-xl border border-zinc-200 bg-white p-12 text-center shadow-sm dark:border-zinc-800 dark:bg-zinc-900">
          <WebhookIcon className="mx-auto h-12 w-12 text-zinc-400" />
          <h3 className="mt-3 text-base font-semibold text-zinc-900 dark:text-white">No webhooks configured</h3>
          <p className="mt-1 text-sm text-zinc-500">
            Add an endpoint to start receiving background webhook dispatches.
          </p>
          <button
            onClick={openCreateModal}
            className="mt-4 inline-flex items-center gap-2 rounded-lg bg-indigo-600 px-4 py-2 text-xs font-semibold text-white"
          >
            <Plus className="h-4 w-4" /> Register Endpoint
          </button>
        </div>
      ) : (
        <div className="grid grid-cols-1 gap-4">
          {webhooks.map((wh) => (
            <div
              key={wh.id}
              className="rounded-xl border border-zinc-200 bg-white p-5 shadow-sm dark:border-zinc-800 dark:bg-zinc-900 flex flex-col md:flex-row md:items-center justify-between gap-4"
            >
              <div className="space-y-2">
                <div className="flex items-center gap-3">
                  <span
                    className={`inline-flex items-center gap-1 rounded-full px-2.5 py-0.5 text-xs font-medium ${
                      wh.enabled
                        ? "bg-emerald-50 text-emerald-700 dark:bg-emerald-950/60 dark:text-emerald-400"
                        : "bg-zinc-100 text-zinc-600 dark:bg-zinc-800 dark:text-zinc-400"
                    }`}
                  >
                    <span className={`h-1.5 w-1.5 rounded-full ${wh.enabled ? "bg-emerald-500" : "bg-zinc-400"}`} />
                    {wh.enabled ? "Active" : "Disabled"}
                  </span>
                  <span className="font-mono text-sm font-semibold text-zinc-900 dark:text-white break-all">
                    {wh.url}
                  </span>
                </div>

                <div className="flex flex-wrap items-center gap-1.5 pt-1">
                  {wh.events.map((ev) => (
                    <span
                      key={ev}
                      className="rounded-md bg-indigo-50 px-2 py-0.5 text-xs font-medium text-indigo-700 dark:bg-indigo-950/60 dark:text-indigo-400"
                    >
                      {ev}
                    </span>
                  ))}
                </div>
              </div>

              <div className="flex items-center gap-2 shrink-0">
                <button
                  onClick={() => handleTestWebhook(wh.id)}
                  disabled={testingWebhookId === wh.id}
                  className="flex items-center gap-1.5 rounded-lg border border-indigo-200 bg-indigo-50 px-3 py-1.5 text-xs font-semibold text-indigo-700 hover:bg-indigo-100 dark:border-indigo-900/60 dark:bg-indigo-950/40 dark:text-indigo-300 transition-all disabled:opacity-50"
                >
                  {testingWebhookId === wh.id ? (
                    <RefreshCw className="h-3.5 w-3.5 animate-spin" />
                  ) : (
                    <Play className="h-3.5 w-3.5" />
                  )}
                  <span>Test Ping</span>
                </button>
                <button
                  onClick={() => openEditModal(wh)}
                  className="rounded-lg p-2 text-zinc-500 hover:bg-zinc-100 hover:text-zinc-900 dark:hover:bg-zinc-800 dark:hover:text-white"
                >
                  <Edit2 className="h-4 w-4" />
                </button>
                <button
                  onClick={() => handleDelete(wh.id)}
                  className="rounded-lg p-2 text-zinc-500 hover:bg-rose-50 hover:text-rose-600 dark:hover:bg-rose-950/50 dark:hover:text-rose-400"
                >
                  <Trash2 className="h-4 w-4" />
                </button>
              </div>
            </div>
          ))}
        </div>
      )}

      {/* Create / Edit Modal */}
      {isModalOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 backdrop-blur-xs p-4">
          <div className="w-full max-w-lg rounded-2xl border border-zinc-200 bg-white p-6 shadow-xl dark:border-zinc-800 dark:bg-zinc-900">
            <div className="flex items-center justify-between pb-4 border-b border-zinc-100 dark:border-zinc-800">
              <h3 className="text-base font-semibold text-zinc-900 dark:text-white">
                {editingWebhook ? "Edit Webhook" : "Register Webhook"}
              </h3>
              <button
                onClick={() => setIsModalOpen(false)}
                className="text-zinc-400 hover:text-zinc-600 dark:hover:text-zinc-200"
              >
                <X className="h-5 w-5" />
              </button>
            </div>

            <form onSubmit={handleSaveWebhook} className="mt-4 space-y-4">
              {formError && (
                <div className="rounded-lg bg-rose-50 p-3 text-xs text-rose-700 dark:bg-rose-950/50 dark:text-rose-400">
                  {formError}
                </div>
              )}

              <div>
                <label className="block text-xs font-medium text-zinc-700 dark:text-zinc-300 mb-1">
                  Destination URL (Public HTTP/HTTPS) *
                </label>
                <input
                  type="url"
                  required
                  value={formUrl}
                  onChange={(e) => setFormUrl(e.target.value)}
                  placeholder="https://your-domain.com/webhook"
                  className="w-full rounded-lg border border-zinc-200 px-3 py-2 text-sm dark:border-zinc-700 dark:bg-zinc-950 dark:text-white focus:outline-indigo-500 font-mono"
                />
                <p className="mt-1 text-xs text-zinc-500 flex items-center gap-1">
                  <ShieldCheck className="h-3.5 w-3.5 text-indigo-500" />
                  Protected against SSRF (private IPs and cloud metadata are blocked).
                </p>
              </div>

              <div>
                <label className="block text-xs font-medium text-zinc-700 dark:text-zinc-300 mb-2">
                  Subscribed Event Types *
                </label>
                <div className="space-y-2 max-h-48 overflow-y-auto pr-1">
                  {ALL_EVENTS.map((ev) => (
                    <label
                      key={ev.id}
                      className="flex items-start gap-3 rounded-lg border border-zinc-200 p-2.5 hover:bg-zinc-50 dark:border-zinc-800 dark:hover:bg-zinc-800/40 cursor-pointer"
                    >
                      <input
                        type="checkbox"
                        checked={formEvents.includes(ev.id)}
                        onChange={() => handleToggleEvent(ev.id)}
                        className="mt-0.5 h-4 w-4 rounded border-zinc-300 text-indigo-600"
                      />
                      <div>
                        <span className="font-mono text-xs font-semibold text-zinc-900 dark:text-white">
                          {ev.id}
                        </span>
                        <p className="text-xs text-zinc-500">{ev.desc}</p>
                      </div>
                    </label>
                  ))}
                </div>
              </div>

              <div className="flex items-center gap-2">
                <input
                  type="checkbox"
                  id="enabledCheck"
                  checked={formEnabled}
                  onChange={(e) => setFormEnabled(e.target.checked)}
                  className="h-4 w-4 rounded border-zinc-300 text-indigo-600"
                />
                <label htmlFor="enabledCheck" className="text-sm text-zinc-700 dark:text-zinc-300">
                  Enable this webhook
                </label>
              </div>

              <div className="flex justify-end gap-2 pt-4 border-t border-zinc-100 dark:border-zinc-800">
                <button
                  type="button"
                  onClick={() => setIsModalOpen(false)}
                  className="rounded-lg border border-zinc-200 px-4 py-2 text-xs font-medium text-zinc-700 hover:bg-zinc-50 dark:border-zinc-700 dark:text-zinc-300 dark:hover:bg-zinc-800"
                >
                  Cancel
                </button>
                <button
                  type="submit"
                  disabled={actionLoading}
                  className="flex items-center gap-1.5 rounded-lg bg-indigo-600 px-4 py-2 text-xs font-semibold text-white hover:bg-indigo-500 disabled:opacity-50"
                >
                  {actionLoading && <RefreshCw className="h-3 w-3 animate-spin" />}
                  <span>{editingWebhook ? "Save Changes" : "Create Webhook"}</span>
                </button>
              </div>
            </form>
          </div>
        </div>
      )}

      {/* Test Result Modal */}
      {testResult && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 backdrop-blur-xs p-4">
          <div className="w-full max-w-md rounded-2xl border border-zinc-200 bg-white p-6 shadow-xl dark:border-zinc-800 dark:bg-zinc-900">
            <div className="flex items-center gap-3">
              {testResult.success ? (
                <CheckCircle2 className="h-6 w-6 text-emerald-500" />
              ) : (
                <XCircle className="h-6 w-6 text-rose-500" />
              )}
              <h3 className="text-base font-semibold text-zinc-900 dark:text-white">
                {testResult.success ? "Webhook Test Successful" : "Webhook Test Failed"}
              </h3>
            </div>

            <div className="mt-4 space-y-3">
              <div className="grid grid-cols-2 gap-3 text-xs">
                <div className="rounded-lg bg-zinc-50 p-2.5 dark:bg-zinc-800/50">
                  <span className="text-zinc-500">Status Code</span>
                  <p className="mt-0.5 font-bold text-zinc-900 dark:text-white">
                    {testResult.status_code || "N/A"}
                  </p>
                </div>
                <div className="rounded-lg bg-zinc-50 p-2.5 dark:bg-zinc-800/50">
                  <span className="text-zinc-500">Response Time</span>
                  <p className="mt-0.5 font-bold text-zinc-900 dark:text-white">
                    {testResult.response_time_ms} ms
                  </p>
                </div>
              </div>

              <div className="rounded-lg bg-zinc-50 p-3 text-xs text-zinc-700 dark:bg-zinc-800/50 dark:text-zinc-300">
                <strong>Result:</strong> {testResult.message}
              </div>

              {testResult.response_body && (
                <div>
                  <span className="text-xs text-zinc-500">Response Snippet</span>
                  <pre className="mt-1 max-h-32 overflow-y-auto rounded-lg bg-zinc-950 p-2 text-xs font-mono text-zinc-300">
                    {testResult.response_body}
                  </pre>
                </div>
              )}
            </div>

            <div className="mt-6 flex justify-end">
              <button
                onClick={() => setTestResult(null)}
                className="rounded-lg bg-zinc-900 px-4 py-2 text-xs font-semibold text-white hover:bg-zinc-800 dark:bg-zinc-800 dark:hover:bg-zinc-700"
              >
                Close
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
};
