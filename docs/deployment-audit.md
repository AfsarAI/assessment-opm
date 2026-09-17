# Assessment OPM: Comprehensive Technical Deployment Audit

## Executive Summary

This technical audit provides a full architectural, reliability, security, and deployment assessment of **Assessment OPM** prior to public production deployment. The system is designed to ingest and process a **500,000-row CSV file (87.2 MB)** without blocking API operations, providing real-time Server-Sent Events (SSE) telemetry, case-insensitive SKU deduplication, application-owned active status preservation, product CRUD, bulk deletion, and an SSRF-protected asynchronous webhook engine.

---

## A. Current Architecture

Assessment OPM follows an asynchronous decoupled micro-service architecture:

```
[ Next.js 16 UI ] ──(REST / SSE)──> [ FastAPI API Gateway ] ──(Enqueue)──> [ Redis 7 Broker ]
                                            │                                      │
                                    (Read/Write CRUD)                              ▼
                                            │                            [ Celery 5.4 Worker ]
                                            ▼                                      │
                                   [ PostgreSQL 16 ] <──(COPY / UPSERT / Truncate)─┘
```

- **Frontend Tier**: Next.js 16 (App Router) with React 19, TypeScript, Tailwind CSS v4, Lucide icons.
- **API Gateway Tier**: FastAPI (ASGI) with Uvicorn, AsyncPG connection pooling, Redis Pub/Sub for SSE streaming, Pydantic v2 schemas.
- **Worker Tier**: Celery 5.4 with Prefork concurrency, partitioned queues (`imports`, `webhooks`, `celery`).
- **Data Tier**: PostgreSQL 16 Alpine with `pg_trgm`, `LOWER(sku)` unique functional index, Alembic migration management.
- **Message Broker & Cache**: Redis 7 Alpine handling Celery task queues and real-time Pub/Sub progress telemetry.

---

## B. Current Services

| Service Name | Technology | Internal Port | External Port | Role |
| :--- | :--- | :--- | :--- | :--- |
| `opm_frontend` | Next.js 16 (Node 20 Alpine) | 3000 | 3000 | User dashboard, real-time import monitor, product CRUD, webhook UI |
| `opm_backend` | FastAPI 0.115 (Python 3.12 Slim) | 8000 | 8000 | REST API, SSE streaming generator, upload receiver, validation |
| `opm_worker` | Celery 5.4 (Python 3.12 Slim) | N/A | N/A | Asynchronous bulk ingestion, SQL deduplication, webhook dispatch & retries |
| `opm_postgres`| PostgreSQL 16 Alpine | 5432 | 5432 | Relational datastore, functional indexes, unlogged staging tables |
| `opm_redis` | Redis 7 Alpine | 6379 | 6379 | Message broker, task queue backend, SSE Pub/Sub channel |

---

## C. Current Data Flow

1. **Upload Request**: Client submits `multipart/form-data` with CSV file to `POST /api/v1/imports`.
2. **Immediate Acknowledgment**: FastAPI streams upload chunks to local disk (`uploads/<job_id>.csv`), writes a `QUEUED` record to PostgreSQL `import_jobs`, dispatches Celery task `process_csv_import.delay(job_id, path)`, and returns `HTTP 202 Accepted` within 15 ms.
3. **Background Ingestion**: Celery worker picks up task from `imports` queue, creates an `UNLOGGED` staging table, streams COPY in 25K chunks, performs set-based deduplication, and atomic `UPSERT` into `products`.
4. **Real-time Telemetry**: Worker publishes progress payloads to Redis channel `import_progress:<job_id>`.
5. **Client SSE Stream**: Client connects to `GET /api/v1/imports/<job_id>/progress`; FastAPI subscribes to Redis channel and yields event messages to client.
6. **Completion & Webhook Trigger**: Worker marks job `COMPLETED`, enqueues `dispatch_webhook_event`, which dispatches HMAC-signed HTTP POST requests to registered webhook URLs with exponential backoff.

---

## D. Current CSV Import Flow

```
[CSV on Disk] ──(csv.reader stream)──> [Staging Validator (25K rows)]
                                                  │
                                       (PostgreSQL Binary COPY)
                                                  ▼
                                    [UNLOGGED staging_<job_id>]
                                                  │
                                (CREATE INDEX on lower(sku), row_number)
                                                  ▼
                          [SELECT DISTINCT ON (lower(sku)) ... ORDER BY row_number DESC]
                                                  │
                                           (Atomic UPSERT)
                                                  ▼
                                      [products (Production)]
                                 (active flag untouched on conflict)
```

- **Chunk Size**: 25,000 rows.
- **Memory Overhead**: < 35 MB in Python heap (generator-based streaming).
- **WAL Bypass**: Staging table is declared `UNLOGGED`, completely bypassing Write-Ahead Logging.
- **Deterministic Deduplication**: Higher `row_number` wins, guaranteeing later CSV occurrences replace earlier ones.
- **Active Preservation**: SQL `ON CONFLICT (LOWER(sku)) DO UPDATE SET` specifically omits `active`, ensuring user toggles are never overwritten.

---

## E. Current Background-Job Architecture

- **Task Queues**:
  - `imports`: Dedicated to heavy CSV parsing, COPY, and UPSERT workloads.
  - `webhooks`: Dedicated to fast, lightweight outbound HTTP deliveries.
  - `celery`: Default fallback queue.
- **Queue Partitioning**: Prevents 500K ingestion tasks from starving outbound webhook deliveries.
- **Worker Configuration**: Prefork pool with 4 worker processes (`--concurrency=4 -Q imports,webhooks,celery`).

---

## F. Current Progress Architecture

- **Protocol**: Server-Sent Events (SSE) via `text/event-stream`.
- **Decoupling**: Progress is published to Redis (`r.publish`) rather than polled from PostgreSQL.
- **Persistence**: Latest state is cached in Redis with a 1-hour TTL (`r.setex`) and stored in PostgreSQL `import_jobs`.
- **Disconnect Handling**: If client disconnects or refreshes, worker continues unhindered. Upon reconnect, endpoint fetches current state from DB/Redis and resumes streaming.
- **Heartbeat**: Yields `: ping\n\n` comments every 15 seconds to prevent reverse proxy (Nginx/Cloudflare) timeouts.

---

## G. Current Database Design

### Tables & Relationships
1. **`products`**:
   - `id`: BIGSERIAL Primary Key
   - `sku`: VARCHAR(100) NOT NULL
   - `name`: VARCHAR(255) NOT NULL
   - `description`: TEXT NULL
   - `active`: BOOLEAN NOT NULL DEFAULT TRUE
   - `created_at`: TIMESTAMPTZ NOT NULL DEFAULT NOW()
   - `updated_at`: TIMESTAMPTZ NOT NULL DEFAULT NOW()
   - **Indexes**:
     - `uq_products_sku_lower`: Unique functional index on `LOWER(sku)`
     - `idx_products_active`: B-tree index on `active`
     - `idx_products_created_at`: Descending B-tree index on `created_at`
     - `idx_products_name_trgm`: Trigram GIN index on `name` (`gin_trgm_ops`)
2. **`import_jobs`**:
   - `id`: UUID Primary Key
   - `filename`: VARCHAR(255) NOT NULL
   - `status`: VARCHAR(50) NOT NULL (`QUEUED`, `PARSING`, `VALIDATING`, `IMPORTING`, `COMPLETED`, `COMPLETED_WITH_ERRORS`, `FAILED`)
   - `total_rows`, `processed_rows`, `successful_rows`, `failed_rows`: INTEGER DEFAULT 0
   - `progress`: INTEGER DEFAULT 0
   - `stage_message`: VARCHAR(255)
   - `created_at`, `updated_at`, `completed_at`: TIMESTAMPTZ
3. **`import_errors`**:
   - `id`: BIGSERIAL Primary Key
   - `job_id`: UUID Foreign Key referencing `import_jobs.id` (ON DELETE CASCADE)
   - `row_number`: INTEGER NOT NULL
   - `sku`, `raw_data`: TEXT
   - `error_reason`: VARCHAR(500) NOT NULL
4. **`webhooks`**:
   - `id`: UUID Primary Key
   - `url`: VARCHAR(2048) NOT NULL
   - `events`: ARRAY of VARCHAR(50) (`import.completed`, `product.created`, etc.)
   - `secret`: VARCHAR(255)
   - `enabled`: BOOLEAN NOT NULL DEFAULT TRUE
   - `created_at`, `updated_at`: TIMESTAMPTZ

---

## H. Current Webhook Architecture

- **Security (SSRF Protection)**: Target URLs are parsed and resolved via DNS; all private subnets (`10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16`, `127.0.0.0/8`, `169.254.0.0/16`, `::1`) are strictly rejected.
- **Cryptographic Authentication**: Requests contain `X-Webhook-Signature` (`sha256=<hmac_digest>`) and `X-Webhook-Timestamp`.
- **Reliability**: Celery retries up to 3 times with exponential backoff (`factor=2`) and a 5-second HTTP timeout.
- **Testing**: Dedicated endpoint `POST /api/v1/webhooks/{id}/test` performs an immediate test ping and returns exact round-trip response time and status code.

---

## I. Current Frontend Architecture

- **Framework**: Next.js 16 (App Router), React 19, TypeScript.
- **Styling**: Tailwind CSS v4 with custom dark mode glassmorphism UI.
- **State Management**: React hooks (`useState`, `useEffect`, `useCallback`) with server-side pagination and debounced search.
- **SSE Client**: Native browser `EventSource` with lifecycle listener and automatic UI state transition to completed.
- **Components**:
  - `Dashboard.tsx`: Top metric cards (total, active, inactive, imports) + active tab switch.
  - `ImportManager.tsx`: Drag-and-drop CSV uploader, animated progress bar, real-time stage display, recent imports list.
  - `ProductManager.tsx`: Data table, search inputs, status filter buttons, Create Product modal, Delete All confirmation.
  - `WebhookManager.tsx`: Endpoint registry table, event toggles, secret generator, interactive test ping runner.

---

## J. Current Docker Setup

- **Multi-container Compose**:
  - `opm_backend`: Builds from `backend/Dockerfile`, runs FastAPI via Uvicorn.
  - `opm_worker`: Builds from `backend/Dockerfile`, runs Celery worker with concurrency 4.
  - `opm_frontend`: Builds from `frontend/Dockerfile` (multi-stage Alpine Node 20), runs Next.js on port 3000.
  - `opm_postgres`: PostgreSQL 16 Alpine with health check.
  - `opm_redis`: Redis 7 Alpine with health check.
- **Local Volume Sharing**:
  - `./backend/uploads:/app/uploads` is mounted into both `opm_backend` and `opm_worker`.

---

## K. Current Environment Configuration

- Controlled via `.env` (derived from `.env.example`).
- Standard variables:
  - `DATABASE_URL`, `SYNC_DATABASE_URL`
  - `REDIS_URL`, `CELERY_BROKER_URL`, `CELERY_RESULT_BACKEND`
  - `CORS_ORIGINS`
  - `NEXT_PUBLIC_API_URL`
  - `MAX_UPLOAD_SIZE_MB`
  - `LOG_LEVEL`

---

## L. Current Testing Strategy

- **Pytest Suite**: 22 automated tests spanning:
  - Liveness & Readiness checks (`/health`, `/ready`).
  - Product CRUD, case-insensitive duplicate SKU rejection (`409 Conflict`), filtering, pagination.
  - CSV upload validation (file extensions, size bounds).
  - Synchronous pipeline execution, staging, deduplication, error logging.
  - Active status preservation across re-imports.
  - SSRF protection (private IP blocking, loopback blocking, cloud metadata blocking).
  - Webhook delivery, retries, and test ping mocking.
- **Execution Speed**: 22/22 passed in **2.80 seconds**.

---

## M. Current Benchmark & Responsiveness Results

### 1. Ingestion Throughput (500,000 Rows / 87.2 MB)
- **Upload Duration**: 0.78s - 1.29s
- **Server Processing Duration**: **22.61s - 24.38s**
- **Throughput**: **35,339 - 38,115 rows/second**
- **Deduplication**: 33,307 duplicate rows resolved
- **Total Unique Stored**: 466,693 products

### 2. API Responsiveness Under Active 500K Import Load (Empirically Measured)
During active 500K ingestion, 1,492 concurrent API requests were executed:
- `GET /health` (825 requests): **Median 4.4 ms**, p95 11.1 ms, p99 18.1 ms.
- `GET /ready` (380 requests): **Median 20.4 ms**, p95 34.7 ms, p99 44.7 ms.
- `GET /api/v1/products` (198 requests): **Median 117.8 ms**, p95 270.9 ms.
- `POST /api/v1/products` (89 concurrent writes): **Median 25.4 ms**, p95 46.8 ms.
- **Total Failures / 5xx Errors**: **0 (Zero)**.

---

## N. Current Deployment Blockers & Critical Risks

### 1. Shared Filesystem Dependency (CRITICAL)
- **Current State**: FastAPI writes the uploaded CSV to `uploads/<job_id>.csv` on local disk, and passes the path `uploads/<job_id>.csv` to Celery via Redis.
- **Cloud Reality**: In serverless, containerized, or multi-service deployments (e.g. Render, Railway, Fly.io, Vercel), `backend` and `worker` run as separate containers with **separate, isolated ephemeral filesystems**. The worker will fail with `FileNotFoundError` because it cannot see files written to the backend container's disk!
- **Solution Requirement**:
  - Option 1: Combine FastAPI and Celery worker inside a single unified container using a multi-process supervisor (e.g., Supervisord or Python sub-process runner). This guarantees that backend and worker share the same local `/app/uploads` filesystem with **zero cloud storage cost** and **zero extra network hops**.
  - Option 2: Upload to external S3-compatible Object Storage (Cloudflare R2, AWS S3, Supabase Storage). While robust, this introduces external credentials, upload latency, and potential free tier bandwidth limits.
  - **Verdict**: Option 1 (Unified container for Free Tier deployments, or shared volume) is vastly simpler, 100% free, and completely eliminates the filesystem disconnect.

### 2. Next.js Build-Time `NEXT_PUBLIC_API_URL` Inlining
- **Current State**: `NEXT_PUBLIC_API_URL` defaults to `http://localhost:8000/api/v1`.
- **Cloud Reality**: In Next.js client components, `NEXT_PUBLIC_*` variables are compiled and baked into static JS at `npm run build` time. If built without the cloud backend URL, the user's browser will try to connect to `localhost:8000`.
- **Solution Requirement**:
  - Configure the public backend URL in the deployment environment prior to frontend build, OR
  - Provide a dynamic Next.js API rewrite proxy (`/api/v1/:path*` -> `${BACKEND_INTERNAL_URL}/api/v1/:path*`), allowing relative API URLs (`/api/v1`) that work in any environment automatically.

### 3. Dynamic `$PORT` Binding
- **Current State**: `uvicorn` is hardcoded to port `8000` in `backend/Dockerfile`.
- **Cloud Reality**: Platforms like Render, Koyeb, Railway assign dynamic `$PORT` environment variables (e.g. `10000`).
- **Solution Requirement**: Update entrypoint to `uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}`.

### 4. Database Free Tier Storage Limits
- **Measurement**: 500,000 products occupy **194 MB** in PostgreSQL (108 MB data + 86 MB indexes), plus ~100 MB staging during import.
- **Cloud Reality**: Free databases must provide at least 350 MB to 500 MB storage. Neon (500 MB) and Render PostgreSQL (1 GB) accommodate this comfortably.

---

## O. Security & Vulnerability Audit

1. **SSRF**: Comprehensive IP subnet checking is active. To further harden, ensure DNS rebinding mitigation and prevent redirects to internal subnets (`follow_redirects=False` in HTTP client).
2. **CORS**: Currently supports comma-separated list or JSON array. Must ensure production origins (e.g. `https://*.onrender.com`, `https://*.vercel.app`) are permitted.
3. **Secret Hygiene**: Checked git history; zero live production credentials committed. `.env.example` contains only template placeholders.
4. **Bulk Deletion**: Protected by confirmation requirement (`confirm=true` query param or `DELETE ALL` confirmation in UI).

---

## P. Recommended Deployment Architecture

To satisfy the **Zero Cost ($0)** requirement with high reliability and responsiveness:

### Primary Production Recommendation: Render + Supabase/Neon + Vercel
1. **Database**: Render Free Managed PostgreSQL (1 GB storage, 50 connections) OR Neon Serverless PostgreSQL (0.5 GB storage).
2. **Backend + Celery Worker + Redis**:
   - Deployed on a single Render Free Web Service running a multi-process supervisor (FastAPI on `$PORT` + Celery Worker listening to `imports,webhooks` + internal lightweight Redis server or managed Redis).
   - This keeps the entire backend, worker, and file uploads within the same container, eliminating cloud object storage costs and solving the shared-filesystem problem completely for free!
3. **Frontend**:
   - Next.js 16 deployed to **Vercel** (Global Edge CDN, 100% free) or served directly alongside backend on Render/Docker.
