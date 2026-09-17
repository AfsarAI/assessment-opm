# Assessment OPM: Implementation Plan

## 1. Executive Summary & Objective

Assessment OPM requires building a resilient, asynchronous web application capable of ingesting a 500,000-row CSV file into a SQL database without blocking the API server, providing real-time import progress tracking, product management (CRUD with case-insensitive SKU uniqueness, filtering, pagination, and status management), safe bulk deletion, and an asynchronous webhook delivery engine with interactive testing and SSRF protection.

The solution is architected with:
- **Backend**: FastAPI (Python 3.12) with Pydantic v2 validation and SQLAlchemy 2.0 ORM / Core.
- **Database**: PostgreSQL 16 with case-insensitive unique indexes and Trigram search.
- **Async Workers**: Celery backed by Redis 7 for message brokering and Pub/Sub progress events.
- **Frontend**: Next.js 15 (App Router), TypeScript, and Tailwind CSS.
- **Real-Time Streaming**: Server-Sent Events (SSE) via Redis Pub/Sub.
- **Containerization**: Docker Compose development and production-aligned multi-container stack.

---

## 2. Phase 0 Empirical Inspection Findings

Prior to system design, the actual dataset `products.csv` was subjected to a streaming byte-level and row-level diagnostic (`scripts/inspect_csv.py`):

| Property | Measured Value | Architectural Implication |
| :--- | :--- | :--- |
| **Total Rows** | 500,000 data rows (excluding header) | Bulk COPY & unlogged staging required; ORM iteration strictly prohibited. |
| **Columns** | `name`, `sku`, `description` | Exactly 3 columns; active status is application-owned and not present in CSV. |
| **File Size** | 87,272,324 bytes (~87.3 MB) | Manageable locally; direct stream or chunked upload required to avoid RAM spikes. |
| **Encoding** | ASCII / UTF-8, CRLF (`\r\n`) | Must parse with UTF-8 and strip carriage returns cleanly. |
| **Empty Values** | 0 missing fields | All fields guaranteed non-empty in baseline; validation still enforces non-empty constraints. |
| **Max Field Lengths** | `name`: 30, `sku`: 19, `description`: 199 | Optimal database sizing: `VARCHAR(255)` for name, `VARCHAR(100)` for SKU, `TEXT` for description. |
| **Unique SKUs** | 466,693 unique values | 33,307 duplicate SKU rows exist within the CSV itself. |
| **CSV Duplicates** | 33,307 rows | Later occurrences must replace earlier occurrences in a deterministic pipeline. |

---

## 3. Core Database Design

### 3.1 Schema & Migrations

- **`products` Table**:
  - `id`: `BIGSERIAL` (Primary Key).
  - `sku`: `VARCHAR(100) NOT NULL`.
  - `name`: `VARCHAR(255) NOT NULL`.
  - `description`: `TEXT NOT NULL DEFAULT ''`.
  - `active`: `BOOLEAN NOT NULL DEFAULT TRUE`.
  - `created_at`: `TIMESTAMPTZ NOT NULL DEFAULT NOW()`.
  - `updated_at`: `TIMESTAMPTZ NOT NULL DEFAULT NOW()`.
  - **Index**: `CREATE UNIQUE INDEX uq_products_sku_lower ON products (LOWER(sku));`
  - **Trigram Index**: `CREATE INDEX ix_products_name_trgm ON products USING gin (name gin_trgm_ops);`
  - **Status Index**: `CREATE INDEX ix_products_active ON products (active);`

- **`import_jobs` Table**:
  - `id`: `UUID` (Primary Key).
  - `filename`: `VARCHAR(255) NOT NULL`.
  - `status`: `VARCHAR(50) NOT NULL` (`QUEUED`, `PARSING`, `VALIDATING`, `IMPORTING`, `COMPLETED`, `COMPLETED_WITH_ERRORS`, `FAILED`).
  - `total_rows`: `INTEGER NOT NULL DEFAULT 0`.
  - `processed_rows`: `INTEGER NOT NULL DEFAULT 0`.
  - `successful_rows`: `INTEGER NOT NULL DEFAULT 0`.
  - `failed_rows`: `INTEGER NOT NULL DEFAULT 0`.
  - `progress`: `INTEGER NOT NULL DEFAULT 0` (0-100%).
  - `stage_message`: `VARCHAR(255) NULL`.
  - `error_message`: `TEXT NULL`.
  - `started_at`: `TIMESTAMPTZ NULL`.
  - `completed_at`: `TIMESTAMPTZ NULL`.
  - `created_at`: `TIMESTAMPTZ NOT NULL DEFAULT NOW()`.
  - `updated_at`: `TIMESTAMPTZ NOT NULL DEFAULT NOW()`.

- **`import_errors` Table**:
  - `id`: `BIGSERIAL` (Primary Key).
  - `import_id`: `UUID NOT NULL REFERENCES import_jobs(id) ON DELETE CASCADE`.
  - `row_number`: `INTEGER NOT NULL`.
  - `error_message`: `TEXT NOT NULL`.
  - `raw_data`: `TEXT NULL`.
  - `created_at`: `TIMESTAMPTZ NOT NULL DEFAULT NOW()`.
  - Capped at 1,000 errors per import to prevent database exhaustion.

- **`webhooks` Table**:
  - `id`: `UUID` (Primary Key).
  - `url`: `VARCHAR(2048) NOT NULL`.
  - `events`: `VARCHAR(50)[] NOT NULL` (e.g. `product.created`, `product.updated`, `product.deleted`, `products.cleared`, `import.completed`, `import.failed`).
  - `enabled`: `BOOLEAN NOT NULL DEFAULT TRUE`.
  - `secret`: `VARCHAR(255) NULL`.
  - `created_at`: `TIMESTAMPTZ NOT NULL DEFAULT NOW()`.
  - `updated_at`: `TIMESTAMPTZ NOT NULL DEFAULT NOW()`.

---

## 4. Ingestion Pipeline Architecture (500,000 Rows)

### 4.1 Pipeline Stages
1. **Upload & Enqueue**:
   - `POST /api/v1/imports` accepts multipart file, writes to disk with a UUID, creates an `import_jobs` record (`status=QUEUED`), and enqueues `process_csv_import.delay(str(job_id), file_path)` in Celery.
   - Responds in <50ms with `202 Accepted` and `{ "import_id": "...", "status": "QUEUED" }`.
2. **Streaming Parse & Validation**:
   - Worker reads the CSV using Python's streaming `csv.reader` in memory-bounded batches (25,000 rows).
   - Validates column count, header integrity, non-empty SKU and Name.
   - Invalid rows are buffered into `import_errors`.
3. **High-Speed Staging with PostgreSQL COPY**:
   - Worker creates an ephemeral unlogged staging table:
     ```sql
     CREATE UNLOGGED TABLE staging_<job_id> (
         row_num INT,
         name VARCHAR(255),
         sku VARCHAR(100),
         description TEXT
     );
     ```
   - Valid rows are streamed directly into PostgreSQL via binary or formatted TSV `COPY` via `psycopg` / raw connection cursor.
4. **SQL-Level Deduplication (Later Occurrence Replaces Earlier)**:
   - PostgreSQL executes deterministic deduplication using `DISTINCT ON`:
     ```sql
     CREATE UNLOGGED TABLE deduplicated_<job_id> AS
     SELECT DISTINCT ON (LOWER(sku))
         name, sku, description
     FROM staging_<job_id>
     ORDER BY LOWER(sku), row_num DESC;
     ```
5. **Atomic UPSERT with Active Preservation**:
   - Records are merged into `products`:
     ```sql
     INSERT INTO products (sku, name, description, active, created_at, updated_at)
     SELECT sku, name, description, TRUE, NOW(), NOW()
     FROM deduplicated_<job_id>
     ON CONFLICT (LOWER(sku)) DO UPDATE SET
         name = EXCLUDED.name,
         description = EXCLUDED.description,
         updated_at = NOW();
     ```
   - *Crucial*: `active` is intentionally excluded from `DO UPDATE SET`, preserving existing active/inactive statuses.
   - Drop staging tables.
   - Job marked `COMPLETED` (or `COMPLETED_WITH_ERRORS`), and `import.completed` webhook event is dispatched.

---

## 5. Real-Time Progress via Server-Sent Events (SSE)

1. **Publishing**:
   - Worker updates `import_jobs` in PostgreSQL and publishes state payloads to Redis channel `import_progress:{import_id}` at regular increments.
2. **Streaming Endpoint (`GET /api/v1/imports/{import_id}/progress`)**:
   - Fast startup: queries current job status from PostgreSQL immediately to render current state without waiting for the next tick.
   - Listens to Redis Pub/Sub async generator.
   - Emits SSE message chunks formatted with `event: progress`, `data: { ... }`.
   - Sends `:ping\n\n` comments every 15s to keep connections alive through reverse proxies.
   - Closes automatically upon terminal states (`COMPLETED`, `FAILED`).

---

## 6. Product CRUD, Filtering & Pagination

- `GET /api/v1/products`:
  - Query parameters: `page`, `limit` (max 100), `sku`, `name`, `status` (`all` | `active` | `inactive`), `description`, `sort_by`, `sort_order`.
  - Database-level pagination using `LIMIT` and `OFFSET` with optimized index scans.
- `POST /api/v1/products`: Single product creation; enforces case-insensitive uniqueness; triggers `product.created` webhook.
- `GET /api/v1/products/{id}`: Single product retrieval.
- `PATCH /api/v1/products/{id}`: Partial update; triggers `product.updated` webhook.
- `DELETE /api/v1/products/{id}`: Single deletion; triggers `product.deleted` webhook.
- `DELETE /api/v1/products`: Bulk truncation; requires `confirm=true`; triggers `products.cleared` webhook.

---

## 7. Webhook Engine & SSRF Protection

- **SSRF Defense**: Validates URL before delivery. Rejects private RFC 1918 subnets (`10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16`), loopback (`127.0.0.0/8`, `::1`), cloud metadata (`169.254.169.254`), and local link addresses.
- **Asynchronous Worker Delivery**: Enqueued in Celery queue `webhooks`.
- **Retries & Timeouts**: 5-second HTTP timeout with 3 exponential backoff attempts (5s, 15s, 45s).
- **Interactive Test (`POST /api/v1/webhooks/{id}/test`)**: Sends a mock event and returns round-trip duration in ms, HTTP response code, and response snippet.

---

## 8. Next.js Frontend Architecture

- **Dashboard**: High-level overview cards (total products, active/inactive counts, active webhooks, last import metrics).
- **Import Hub**: Drag-and-drop CSV uploader, file size validator, animated live progress bar with stage indicators (`PARSING`, `VALIDATING`, `IMPORTING`, `COMPLETED`), error summary card.
- **Product Management Table**: Search filters (SKU, Name, Description, Status), inline status toggle, Edit modal, Delete modal with confirmation.
- **Danger Zone**: "Delete All Products" modal requiring typing `DELETE ALL`.
- **Webhook Manager**: List, Create/Edit with event checkboxes, enable/disable toggle, and live "Test Webhook" modal with response status code and latency display.

---

## 9. Benchmark & Verification Plan

1. **Pytest Suite**: Backend unit and integration tests covering CRUD, case-insensitive collision, pagination, CSV validation, and webhook delivery.
2. **Benchmark Execution (`scripts/benchmark_import.py`)**:
   - Ingest 100 rows, 10,000 rows, 100,000 rows, and the full 500,000 rows.
   - Record total execution time, rows/second throughput, peak RAM usage, and database row consistency.
