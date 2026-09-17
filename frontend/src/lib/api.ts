import {
  Product,
  ProductListResponse,
  ImportJob,
  Webhook,
  WebhookTestResult,
} from "@/types";

export function getApiBaseUrl(): string {
  let url = process.env.NEXT_PUBLIC_API_URL;
  if (url) {
    url = url.trim().replace(/\/+$/, "");
    if (!url.endsWith("/api/v1")) {
      url = `${url}/api/v1`;
    }
    return url;
  }
  if (typeof window !== "undefined") {
    return "/api/v1";
  }
  return "http://localhost:8000/api/v1";
}

const API_BASE_URL = getApiBaseUrl();

export class ApiError extends Error {
  code: string;
  status: number;

  constructor(message: string, code: string = "UNKNOWN_ERROR", status: number = 500) {
    super(message);
    this.name = "ApiError";
    this.code = code;
    this.status = status;
  }
}

async function handleResponse<T>(res: Response): Promise<T> {
  if (!res.ok) {
    let errorDetail = { code: "HTTP_ERROR", message: `Request failed with status ${res.status}` };
    try {
      const data = await res.json();
      if (data.error) {
        errorDetail = data.error;
      }
    } catch {
      // Non-JSON response
    }
    throw new ApiError(errorDetail.message, errorDetail.code, res.status);
  }
  if (res.status === 204) {
    return {} as T;
  }
  return res.json();
}

// ----------------------------------------------------
// Health Check
// ----------------------------------------------------
export async function getHealth(): Promise<{ status: string; database?: string; redis?: string }> {
  try {
    const rootUrl = API_BASE_URL.replace(/\/api\/v1\/?$/, "");
    const res = await fetch(`${rootUrl}/ready`, { cache: "no-store" });
    return handleResponse(res);
  } catch {
    return { status: "offline" };
  }
}

// ----------------------------------------------------
// Products API
// ----------------------------------------------------
export interface ProductFilterParams {
  page?: number;
  limit?: number;
  sku?: string;
  name?: string;
  description?: string;
  status?: string;
  sort_by?: string;
  sort_order?: string;
}

export async function getProducts(params: ProductFilterParams = {}): Promise<ProductListResponse> {
  const query = new URLSearchParams();
  if (params.page) query.set("page", params.page.toString());
  if (params.limit) query.set("limit", params.limit.toString());
  if (params.sku) query.set("sku", params.sku);
  if (params.name) query.set("name", params.name);
  if (params.description) query.set("description", params.description);
  if (params.status && params.status !== "all") query.set("status", params.status);
  if (params.sort_by) query.set("sort_by", params.sort_by);
  if (params.sort_order) query.set("sort_order", params.sort_order);

  const res = await fetch(`${API_BASE_URL}/products?${query.toString()}`, {
    cache: "no-store",
  });
  return handleResponse<ProductListResponse>(res);
}

export async function getProduct(id: number): Promise<Product> {
  const res = await fetch(`${API_BASE_URL}/products/${id}`, { cache: "no-store" });
  return handleResponse<Product>(res);
}

export async function createProduct(data: {
  sku: string;
  name: string;
  description?: string;
  active?: boolean;
}): Promise<Product> {
  const res = await fetch(`${API_BASE_URL}/products`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  });
  return handleResponse<Product>(res);
}

export async function updateProduct(
  id: number,
  data: Partial<Pick<Product, "sku" | "name" | "description" | "active">>
): Promise<Product> {
  const res = await fetch(`${API_BASE_URL}/products/${id}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  });
  return handleResponse<Product>(res);
}

export async function deleteProduct(id: number): Promise<void> {
  const res = await fetch(`${API_BASE_URL}/products/${id}`, {
    method: "DELETE",
  });
  return handleResponse<void>(res);
}

export async function deleteAllProducts(): Promise<{ success: boolean; message: string }> {
  const res = await fetch(`${API_BASE_URL}/products?confirm=true`, {
    method: "DELETE",
  });
  return handleResponse<{ success: boolean; message: string }>(res);
}

// ----------------------------------------------------
// Imports API
// ----------------------------------------------------
export async function uploadCsv(file: File): Promise<{ import_id: string; status: string; message: string }> {
  const formData = new FormData();
  formData.append("file", file);

  const res = await fetch(`${API_BASE_URL}/imports`, {
    method: "POST",
    body: formData,
  });
  return handleResponse<{ import_id: string; status: string; message: string }>(res);
}

export async function getImports(limit: number = 10): Promise<ImportJob[]> {
  const res = await fetch(`${API_BASE_URL}/imports?limit=${limit}`, {
    cache: "no-store",
  });
  return handleResponse<ImportJob[]>(res);
}

export async function getImport(id: string): Promise<ImportJob> {
  const res = await fetch(`${API_BASE_URL}/imports/${id}`, {
    cache: "no-store",
  });
  return handleResponse<ImportJob>(res);
}

export function getImportProgressUrl(id: string): string {
  return `${API_BASE_URL}/imports/${id}/progress`;
}

// ----------------------------------------------------
// Webhooks API
// ----------------------------------------------------
export async function getWebhooks(): Promise<Webhook[]> {
  const res = await fetch(`${API_BASE_URL}/webhooks`, { cache: "no-store" });
  return handleResponse<Webhook[]>(res);
}

export async function createWebhook(data: {
  url: string;
  events: string[];
  enabled?: boolean;
  secret?: string;
}): Promise<Webhook> {
  const res = await fetch(`${API_BASE_URL}/webhooks`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  });
  return handleResponse<Webhook>(res);
}

export async function updateWebhook(
  id: string,
  data: Partial<Pick<Webhook, "url" | "events" | "enabled" | "secret">>
): Promise<Webhook> {
  const res = await fetch(`${API_BASE_URL}/webhooks/${id}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  });
  return handleResponse<Webhook>(res);
}

export async function deleteWebhook(id: string): Promise<void> {
  const res = await fetch(`${API_BASE_URL}/webhooks/${id}`, {
    method: "DELETE",
  });
  return handleResponse<void>(res);
}

export async function testWebhook(id: string): Promise<WebhookTestResult> {
  const res = await fetch(`${API_BASE_URL}/webhooks/${id}/test`, {
    method: "POST",
  });
  return handleResponse<WebhookTestResult>(res);
}
