"use client";

import React, { useState, useEffect, useCallback } from "react";
import {
  Search,
  Plus,
  Trash2,
  Edit2,
  AlertTriangle,
  CheckCircle2,
  ChevronLeft,
  ChevronRight,
  RefreshCw,
  X,
} from "lucide-react";
import {
  getProducts,
  createProduct,
  updateProduct,
  deleteProduct,
  deleteAllProducts,
} from "@/lib/api";
import { Product, PaginationMetadata } from "@/types";

export const ProductManager: React.FC = () => {
  const [products, setProducts] = useState<Product[]>([]);
  const [pagination, setPagination] = useState<PaginationMetadata>({
    total: 0,
    page: 1,
    limit: 50,
    total_pages: 1,
    has_next: false,
    has_prev: false,
  });

  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);

  // Filter states
  const [skuFilter, setSkuFilter] = useState("");
  const [nameFilter, setNameFilter] = useState("");
  const [descFilter, setDescFilter] = useState("");
  const [statusFilter, setStatusFilter] = useState("all");
  const [pageSize, setPageSize] = useState(50);
  const [currentPage, setCurrentPage] = useState(1);

  // Modals state
  const [isCreateOpen, setIsCreateOpen] = useState(false);
  const [editingProduct, setEditingProduct] = useState<Product | null>(null);
  const [deletingProduct, setDeletingProduct] = useState<Product | null>(null);
  const [isDeleteAllOpen, setIsDeleteAllOpen] = useState(false);
  const [deleteAllConfirmation, setDeleteAllConfirmation] = useState("");

  // Form states
  const [formSku, setFormSku] = useState("");
  const [formName, setFormName] = useState("");
  const [formDesc, setFormDesc] = useState("");
  const [formActive, setFormActive] = useState(true);
  const [formError, setFormError] = useState<string | null>(null);
  const [actionLoading, setActionLoading] = useState(false);

  const fetchProducts = useCallback(async () => {
    setLoading(true);
    try {
      setLoadError(null);
      const res = await getProducts({
        page: currentPage,
        limit: pageSize,
        sku: skuFilter || undefined,
        name: nameFilter || undefined,
        description: descFilter || undefined,
        status: statusFilter,
      });
      setProducts(res.items);
      setPagination(res.pagination);
    } catch (err: any) {
      console.error("Failed to load products:", err);
      setLoadError(err.message || "Failed to load products");
    } finally {
      setLoading(false);
    }
  }, [currentPage, pageSize, skuFilter, nameFilter, descFilter, statusFilter]);

  useEffect(() => {
    fetchProducts();
  }, [fetchProducts]);

  // Auto-reconnect if server was sleeping
  useEffect(() => {
    if (!loadError) return;
    const timer = setTimeout(() => {
      fetchProducts();
    }, 4000);
    return () => clearTimeout(timer);
  }, [loadError, fetchProducts]);

  const handleToggleStatus = async (product: Product) => {
    try {
      const updated = await updateProduct(product.id, { active: !product.active });
      setProducts((prev) => prev.map((p) => (p.id === updated.id ? updated : p)));
    } catch (err: any) {
      alert(`Failed to toggle status: ${err.message}`);
    }
  };

  const handleSaveProduct = async (e: React.FormEvent) => {
    e.preventDefault();
    setFormError(null);
    setActionLoading(true);

    try {
      if (editingProduct) {
        const updated = await updateProduct(editingProduct.id, {
          sku: formSku,
          name: formName,
          description: formDesc,
          active: formActive,
        });
        setProducts((prev) => prev.map((p) => (p.id === updated.id ? updated : p)));
        setEditingProduct(null);
      } else {
        await createProduct({
          sku: formSku,
          name: formName,
          description: formDesc,
          active: formActive,
        });
        setIsCreateOpen(false);
        fetchProducts();
      }
      setFormSku("");
      setFormName("");
      setFormDesc("");
      setFormActive(true);
    } catch (err: any) {
      setFormError(err.message || "Operation failed");
    } finally {
      setActionLoading(false);
    }
  };

  const handleDeleteSingle = async () => {
    if (!deletingProduct) return;
    setActionLoading(true);
    try {
      await deleteProduct(deletingProduct.id);
      setDeletingProduct(null);
      fetchProducts();
    } catch (err: any) {
      alert(`Failed to delete product: ${err.message}`);
    } finally {
      setActionLoading(false);
    }
  };

  const handleDeleteAll = async () => {
    if (deleteAllConfirmation !== "DELETE ALL") return;
    setActionLoading(true);
    try {
      await deleteAllProducts();
      setIsDeleteAllOpen(false);
      setDeleteAllConfirmation("");
      fetchProducts();
    } catch (err: any) {
      alert(`Failed to clear database: ${err.message}`);
    } finally {
      setActionLoading(false);
    }
  };

  const openEditModal = (p: Product) => {
    setEditingProduct(p);
    setFormSku(p.sku);
    setFormName(p.name);
    setFormDesc(p.description);
    setFormActive(p.active);
    setFormError(null);
  };

  return (
    <div className="space-y-6">
      {/* Header & Primary Actions */}
      <div className="flex flex-col sm:flex-row items-start sm:items-center justify-between gap-4">
        <div>
          <h2 className="text-xl font-bold tracking-tight text-zinc-900 dark:text-white">
            Product Catalogue
          </h2>
          <p className="text-sm text-zinc-500 dark:text-zinc-400">
            {pagination.total.toLocaleString()} total products indexed in PostgreSQL
          </p>
        </div>

        <div className="flex items-center gap-3">
          <button
            onClick={() => {
              setEditingProduct(null);
              setFormSku("");
              setFormName("");
              setFormDesc("");
              setFormActive(true);
              setFormError(null);
              setIsCreateOpen(true);
            }}
            className="flex items-center gap-2 rounded-lg bg-indigo-600 px-4 py-2.5 text-sm font-semibold text-white shadow-sm hover:bg-indigo-500 transition-all"
          >
            <Plus className="h-4 w-4" /> Add Product
          </button>
          <button
            onClick={() => {
              setDeleteAllConfirmation("");
              setIsDeleteAllOpen(true);
            }}
            className="flex items-center gap-2 rounded-lg border border-rose-200 bg-rose-50 px-4 py-2.5 text-sm font-semibold text-rose-700 hover:bg-rose-100 dark:border-rose-900/50 dark:bg-rose-950/40 dark:text-rose-400 dark:hover:bg-rose-950/60 transition-all"
          >
            <Trash2 className="h-4 w-4" /> Delete All
          </button>
        </div>
      </div>

      {/* Filter Toolbar */}
      <div className="rounded-xl border border-zinc-200 bg-white p-4 shadow-sm dark:border-zinc-800 dark:bg-zinc-900">
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-5">
          <div>
            <label className="block text-xs font-medium text-zinc-500 mb-1">SKU Search</label>
            <input
              type="text"
              placeholder="e.g. ABC-123..."
              value={skuFilter}
              onChange={(e) => {
                setSkuFilter(e.target.value);
                setCurrentPage(1);
              }}
              className="w-full rounded-lg border border-zinc-200 bg-zinc-50 px-3 py-1.5 text-sm dark:border-zinc-700 dark:bg-zinc-950 dark:text-white focus:outline-indigo-500"
            />
          </div>

          <div>
            <label className="block text-xs font-medium text-zinc-500 mb-1">Product Name</label>
            <input
              type="text"
              placeholder="Filter by name..."
              value={nameFilter}
              onChange={(e) => {
                setNameFilter(e.target.value);
                setCurrentPage(1);
              }}
              className="w-full rounded-lg border border-zinc-200 bg-zinc-50 px-3 py-1.5 text-sm dark:border-zinc-700 dark:bg-zinc-950 dark:text-white focus:outline-indigo-500"
            />
          </div>

          <div>
            <label className="block text-xs font-medium text-zinc-500 mb-1">Description</label>
            <input
              type="text"
              placeholder="Keyword in description..."
              value={descFilter}
              onChange={(e) => {
                setDescFilter(e.target.value);
                setCurrentPage(1);
              }}
              className="w-full rounded-lg border border-zinc-200 bg-zinc-50 px-3 py-1.5 text-sm dark:border-zinc-700 dark:bg-zinc-950 dark:text-white focus:outline-indigo-500"
            />
          </div>

          <div>
            <label className="block text-xs font-medium text-zinc-500 mb-1">Status</label>
            <select
              value={statusFilter}
              onChange={(e) => {
                setStatusFilter(e.target.value);
                setCurrentPage(1);
              }}
              className="w-full rounded-lg border border-zinc-200 bg-zinc-50 px-3 py-1.5 text-sm dark:border-zinc-700 dark:bg-zinc-950 dark:text-white focus:outline-indigo-500"
            >
              <option value="all">All Statuses</option>
              <option value="active">Active Only</option>
              <option value="inactive">Inactive Only</option>
            </select>
          </div>

          <div>
            <label className="block text-xs font-medium text-zinc-500 mb-1">Page Size</label>
            <select
              value={pageSize}
              onChange={(e) => {
                setPageSize(Number(e.target.value));
                setCurrentPage(1);
              }}
              className="w-full rounded-lg border border-zinc-200 bg-zinc-50 px-3 py-1.5 text-sm dark:border-zinc-700 dark:bg-zinc-950 dark:text-white focus:outline-indigo-500"
            >
              <option value={25}>25 per page</option>
              <option value={50}>50 per page</option>
              <option value={100}>100 per page</option>
            </select>
          </div>
        </div>
      </div>

      {/* Product Table */}
      <div className="rounded-xl border border-zinc-200 bg-white shadow-sm dark:border-zinc-800 dark:bg-zinc-900 overflow-hidden">
        <div className="overflow-x-auto">
          <table className="w-full text-left text-sm text-zinc-600 dark:text-zinc-400">
            <thead className="border-b border-zinc-200 bg-zinc-50/75 text-xs uppercase text-zinc-500 dark:border-zinc-800 dark:bg-zinc-950/50">
              <tr>
                <th className="py-3.5 px-4 font-semibold">SKU</th>
                <th className="py-3.5 px-4 font-semibold">Name</th>
                <th className="py-3.5 px-4 font-semibold">Description</th>
                <th className="py-3.5 px-4 font-semibold">Status</th>
                <th className="py-3.5 px-4 font-semibold text-right">Actions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-zinc-100 dark:divide-zinc-800/60">
              {loading ? (
                <tr>
                  <td colSpan={5} className="py-12 text-center text-zinc-500">
                    <RefreshCw className="inline h-5 w-5 animate-spin text-indigo-600 mb-2" />
                    <p>Loading products from PostgreSQL...</p>
                  </td>
                </tr>
              ) : loadError ? (
                <tr>
                  <td colSpan={5} className="py-12 text-center text-amber-800 dark:text-amber-200">
                    <AlertTriangle className="inline h-6 w-6 text-amber-500 mb-2" />
                    <p className="font-semibold">Backend server is spinning up or temporarily unavailable.</p>
                    <p className="text-xs text-zinc-500 mt-1">Render Free tier instances take ~50s on wake. Reconnecting automatically...</p>
                    <button
                      onClick={() => fetchProducts()}
                      className="mt-3 inline-flex items-center gap-1.5 rounded-md bg-amber-100 px-3 py-1.5 text-xs font-semibold text-amber-900 hover:bg-amber-200 transition-colors dark:bg-amber-950/60 dark:text-amber-300"
                    >
                      <RefreshCw className="h-3 w-3" /> Retry Now
                    </button>
                  </td>
                </tr>
              ) : products.length === 0 ? (
                <tr>
                  <td colSpan={5} className="py-12 text-center text-zinc-500">
                    No products matched your search criteria.
                  </td>
                </tr>
              ) : (
                products.map((product) => (
                  <tr key={product.id} className="hover:bg-zinc-50/50 dark:hover:bg-zinc-800/30 transition-colors">
                    <td className="py-3 px-4 font-mono font-bold text-zinc-900 dark:text-white">
                      {product.sku}
                    </td>
                    <td className="py-3 px-4 font-medium text-zinc-900 dark:text-zinc-100">
                      {product.name}
                    </td>
                    <td className="py-3 px-4 text-xs text-zinc-500 max-w-xs truncate" title={product.description}>
                      {product.description || "--"}
                    </td>
                    <td className="py-3 px-4">
                      <button
                        onClick={() => handleToggleStatus(product)}
                        title="Click to toggle status"
                        className={`inline-flex items-center gap-1.5 rounded-full px-2.5 py-0.5 text-xs font-medium cursor-pointer transition-colors ${
                          product.active
                            ? "bg-emerald-50 text-emerald-700 hover:bg-emerald-100 dark:bg-emerald-950/60 dark:text-emerald-400"
                            : "bg-zinc-100 text-zinc-600 hover:bg-zinc-200 dark:bg-zinc-800 dark:text-zinc-400"
                        }`}
                      >
                        <span className={`h-1.5 w-1.5 rounded-full ${product.active ? "bg-emerald-500" : "bg-zinc-400"}`} />
                        {product.active ? "Active" : "Inactive"}
                      </button>
                    </td>
                    <td className="py-3 px-4 text-right">
                      <div className="flex items-center justify-end gap-2">
                        <button
                          onClick={() => openEditModal(product)}
                          className="rounded-lg p-1.5 text-zinc-500 hover:bg-zinc-100 hover:text-zinc-900 dark:hover:bg-zinc-800 dark:hover:text-white"
                          title="Edit Product"
                        >
                          <Edit2 className="h-4 w-4" />
                        </button>
                        <button
                          onClick={() => setDeletingProduct(product)}
                          className="rounded-lg p-1.5 text-zinc-500 hover:bg-rose-50 hover:text-rose-600 dark:hover:bg-rose-950/50 dark:hover:text-rose-400"
                          title="Delete Product"
                        >
                          <Trash2 className="h-4 w-4" />
                        </button>
                      </div>
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>

        {/* Pagination Bar */}
        <div className="flex flex-col sm:flex-row items-center justify-between gap-4 border-t border-zinc-200 px-4 py-3 text-sm text-zinc-500 dark:border-zinc-800">
          <div>
            Showing{" "}
            <span className="font-semibold text-zinc-900 dark:text-white">
              {pagination.total > 0 ? (pagination.page - 1) * pagination.limit + 1 : 0}
            </span>{" "}
            to{" "}
            <span className="font-semibold text-zinc-900 dark:text-white">
              {Math.min(pagination.page * pagination.limit, pagination.total)}
            </span>{" "}
            of{" "}
            <span className="font-semibold text-zinc-900 dark:text-white">
              {pagination.total.toLocaleString()}
            </span>{" "}
            records
          </div>

          <div className="flex items-center gap-2">
            <button
              onClick={() => setCurrentPage((p) => Math.max(1, p - 1))}
              disabled={!pagination.has_prev || loading}
              className="flex items-center gap-1 rounded-lg border border-zinc-200 px-3 py-1.5 text-xs font-medium text-zinc-700 hover:bg-zinc-50 disabled:opacity-50 disabled:cursor-not-allowed dark:border-zinc-700 dark:text-zinc-300 dark:hover:bg-zinc-800"
            >
              <ChevronLeft className="h-3.5 w-3.5" /> Previous
            </button>
            <span className="text-xs font-medium px-2">
              Page {pagination.page} of {pagination.total_pages}
            </span>
            <button
              onClick={() => setCurrentPage((p) => p + 1)}
              disabled={!pagination.has_next || loading}
              className="flex items-center gap-1 rounded-lg border border-zinc-200 px-3 py-1.5 text-xs font-medium text-zinc-700 hover:bg-zinc-50 disabled:opacity-50 disabled:cursor-not-allowed dark:border-zinc-700 dark:text-zinc-300 dark:hover:bg-zinc-800"
            >
              Next <ChevronRight className="h-3.5 w-3.5" />
            </button>
          </div>
        </div>
      </div>

      {/* Create / Edit Modal */}
      {(isCreateOpen || editingProduct) && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 backdrop-blur-xs p-4">
          <div className="w-full max-w-md rounded-2xl border border-zinc-200 bg-white p-6 shadow-xl dark:border-zinc-800 dark:bg-zinc-900">
            <div className="flex items-center justify-between pb-4 border-b border-zinc-100 dark:border-zinc-800">
              <h3 className="text-base font-semibold text-zinc-900 dark:text-white">
                {editingProduct ? "Edit Product" : "Create New Product"}
              </h3>
              <button
                onClick={() => {
                  setIsCreateOpen(false);
                  setEditingProduct(null);
                }}
                className="text-zinc-400 hover:text-zinc-600 dark:hover:text-zinc-200"
              >
                <X className="h-5 w-5" />
              </button>
            </div>

            <form onSubmit={handleSaveProduct} className="mt-4 space-y-4">
              {formError && (
                <div className="rounded-lg bg-rose-50 p-3 text-xs text-rose-700 dark:bg-rose-950/50 dark:text-rose-400">
                  {formError}
                </div>
              )}

              <div>
                <label className="block text-xs font-medium text-zinc-700 dark:text-zinc-300 mb-1">
                  SKU (Case-Insensitive Unique) *
                </label>
                <input
                  type="text"
                  required
                  value={formSku}
                  onChange={(e) => setFormSku(e.target.value)}
                  placeholder="e.g. PROD-ABC-100"
                  className="w-full rounded-lg border border-zinc-200 px-3 py-2 text-sm dark:border-zinc-700 dark:bg-zinc-950 dark:text-white focus:outline-indigo-500"
                />
              </div>

              <div>
                <label className="block text-xs font-medium text-zinc-700 dark:text-zinc-300 mb-1">
                  Product Name *
                </label>
                <input
                  type="text"
                  required
                  value={formName}
                  onChange={(e) => setFormName(e.target.value)}
                  placeholder="e.g. Smart Watch Pro"
                  className="w-full rounded-lg border border-zinc-200 px-3 py-2 text-sm dark:border-zinc-700 dark:bg-zinc-950 dark:text-white focus:outline-indigo-500"
                />
              </div>

              <div>
                <label className="block text-xs font-medium text-zinc-700 dark:text-zinc-300 mb-1">
                  Description
                </label>
                <textarea
                  rows={3}
                  value={formDesc}
                  onChange={(e) => setFormDesc(e.target.value)}
                  placeholder="Product features and notes..."
                  className="w-full rounded-lg border border-zinc-200 px-3 py-2 text-sm dark:border-zinc-700 dark:bg-zinc-950 dark:text-white focus:outline-indigo-500"
                />
              </div>

              <div className="flex items-center gap-2">
                <input
                  type="checkbox"
                  id="activeCheck"
                  checked={formActive}
                  onChange={(e) => setFormActive(e.target.checked)}
                  className="h-4 w-4 rounded border-zinc-300 text-indigo-600 focus:ring-indigo-500"
                />
                <label htmlFor="activeCheck" className="text-sm text-zinc-700 dark:text-zinc-300">
                  Mark as Active Product
                </label>
              </div>

              <div className="flex justify-end gap-2 pt-4">
                <button
                  type="button"
                  onClick={() => {
                    setIsCreateOpen(false);
                    setEditingProduct(null);
                  }}
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
                  <span>{editingProduct ? "Save Changes" : "Create Product"}</span>
                </button>
              </div>
            </form>
          </div>
        </div>
      )}

      {/* Delete Single Product Modal */}
      {deletingProduct && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 backdrop-blur-xs p-4">
          <div className="w-full max-w-md rounded-2xl border border-zinc-200 bg-white p-6 shadow-xl dark:border-zinc-800 dark:bg-zinc-900">
            <div className="flex items-center gap-3 text-rose-600">
              <AlertTriangle className="h-6 w-6" />
              <h3 className="text-lg font-bold text-zinc-900 dark:text-white">Delete Product?</h3>
            </div>
            <p className="mt-3 text-sm text-zinc-500">
              Are you sure you want to delete{" "}
              <strong className="text-zinc-900 dark:text-white">{deletingProduct.name}</strong> (SKU:{" "}
              <code className="font-mono">{deletingProduct.sku}</code>)? This action cannot be undone.
            </p>
            <div className="mt-6 flex justify-end gap-3">
              <button
                onClick={() => setDeletingProduct(null)}
                className="rounded-lg border border-zinc-200 px-4 py-2 text-xs font-medium text-zinc-700 hover:bg-zinc-50 dark:border-zinc-700 dark:text-zinc-300 dark:hover:bg-zinc-800"
              >
                Cancel
              </button>
              <button
                onClick={handleDeleteSingle}
                disabled={actionLoading}
                className="rounded-lg bg-rose-600 px-4 py-2 text-xs font-semibold text-white hover:bg-rose-500 disabled:opacity-50"
              >
                {actionLoading ? "Deleting..." : "Permanently Delete"}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Delete All Danger Zone Modal */}
      {isDeleteAllOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 backdrop-blur-xs p-4">
          <div className="w-full max-w-md rounded-2xl border border-rose-200 bg-white p-6 shadow-2xl dark:border-rose-900/60 dark:bg-zinc-900">
            <div className="flex items-center gap-3 text-rose-600">
              <AlertTriangle className="h-6 w-6" />
              <h3 className="text-lg font-bold text-zinc-900 dark:text-white">Danger: Truncate All Products</h3>
            </div>
            <p className="mt-3 text-sm text-zinc-600 dark:text-zinc-400">
              This will permanently delete <strong>all {pagination.total.toLocaleString()} products</strong> from the database.
              This action cannot be undone.
            </p>
            <div className="mt-4">
              <label className="block text-xs font-medium text-zinc-500 mb-1">
                Type <strong className="text-rose-600">DELETE ALL</strong> below to confirm:
              </label>
              <input
                type="text"
                value={deleteAllConfirmation}
                onChange={(e) => setDeleteAllConfirmation(e.target.value)}
                placeholder="DELETE ALL"
                className="w-full rounded-lg border border-rose-300 bg-rose-50/50 px-3 py-2 text-sm font-mono text-rose-900 dark:border-rose-800 dark:bg-rose-950/20 dark:text-rose-200 focus:outline-rose-500"
              />
            </div>
            <div className="mt-6 flex justify-end gap-3">
              <button
                onClick={() => setIsDeleteAllOpen(false)}
                className="rounded-lg border border-zinc-200 px-4 py-2 text-xs font-medium text-zinc-700 hover:bg-zinc-50 dark:border-zinc-700 dark:text-zinc-300 dark:hover:bg-zinc-800"
              >
                Cancel
              </button>
              <button
                onClick={handleDeleteAll}
                disabled={deleteAllConfirmation !== "DELETE ALL" || actionLoading}
                className="rounded-lg bg-rose-600 px-4 py-2 text-xs font-bold text-white hover:bg-rose-500 disabled:opacity-40 disabled:cursor-not-allowed"
              >
                {actionLoading ? "Truncating..." : "Confirm Delete All"}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
};
