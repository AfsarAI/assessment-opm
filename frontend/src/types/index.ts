export interface Product {
  id: number;
  sku: string;
  name: string;
  description: string;
  active: boolean;
  created_at: string;
  updated_at: string;
}

export interface PaginationMetadata {
  total: number;
  page: number;
  limit: number;
  total_pages: number;
  has_next: boolean;
  has_prev: boolean;
}

export interface ProductListResponse {
  items: Product[];
  pagination: PaginationMetadata;
}

export interface ImportJob {
  id: string;
  filename: string;
  status: "QUEUED" | "PARSING" | "VALIDATING" | "IMPORTING" | "COMPLETED" | "COMPLETED_WITH_ERRORS" | "FAILED";
  total_rows: number;
  processed_rows: number;
  successful_rows: number;
  failed_rows: number;
  progress: number;
  stage_message: string;
  error_message?: string | null;
  started_at?: string | null;
  completed_at?: string | null;
  created_at: string;
  updated_at: string;
  errors?: ImportErrorItem[];
}

export interface ImportErrorItem {
  row_number: number;
  error_message: string;
  raw_data?: string | null;
  created_at: string;
}

export interface ImportProgressEvent {
  import_id: string;
  status: "QUEUED" | "PARSING" | "VALIDATING" | "IMPORTING" | "COMPLETED" | "COMPLETED_WITH_ERRORS" | "FAILED";
  progress: number;
  processed_rows: number;
  total_rows: number;
  successful_rows: number;
  failed_rows: number;
  stage_message: string;
  error_message?: string | null;
}

export interface Webhook {
  id: string;
  url: string;
  events: string[];
  enabled: boolean;
  secret?: string | null;
  created_at: string;
  updated_at: string;
}

export interface WebhookTestResult {
  success: boolean;
  status_code?: number | null;
  response_time_ms: number;
  message: string;
  response_body?: string | null;
}
