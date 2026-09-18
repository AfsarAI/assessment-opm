# Assessment OPM: High-Performance 500K Product CSV Ingestion & Management System

[![FastAPI](https://img.shields.io/badge/FastAPI-0.115+-009688.svg?logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com)
[![Next.js](https://img.shields.io/badge/Next.js-16-black.svg?logo=next.js&logoColor=white)](https://nextjs.org)
[![PostgreSQL](https://img.shields.io/badge/PostgreSQL-16-336791.svg?logo=postgresql&logoColor=white)](https://www.postgresql.org)
[![Celery](https://img.shields.io/badge/Celery-5.4-37814A.svg?logo=celery&logoColor=white)](https://docs.celeryq.dev)
[![Redis](https://img.shields.io/badge/Redis-7-DC382D.svg?logo=redis&logoColor=white)](https://redis.io)
[![Docker](https://img.shields.io/badge/Docker_Compose-v2-2496ED.svg?logo=docker&logoColor=white)](https://www.docker.com)
[![Tests](https://img.shields.io/badge/Pytest-29%2F29_Passed-success.svg)](backend/tests)
[![Live Frontend](https://img.shields.io/badge/Vercel-Live_App-black.svg?logo=vercel&logoColor=white)](https://assessment-opm.vercel.app/)
[![Live Backend](https://img.shields.io/badge/Render-Live_API-46E3B7.svg?logo=render&logoColor=white)](https://opm-backend-rf77.onrender.com/docs)

A production-grade, asynchronous web application capable of streaming, validating, deduplicating, and importing a **500,000-row CSV file** (86.37 MB) into PostgreSQL without blocking the API server. Features real-time Server-Sent Events (SSE) telemetry with strictly monotonic progress guarantees, full product management (CRUD, case-insensitive SKU uniqueness, filtering, sorting, pagination), safe bulk clearing, and an asynchronous webhook delivery engine with HMAC signatures and SSRF protection.

---

## Table of Contents

1. [Project Overview](#1-project-overview)
2. [Key Features](#2-key-features)
3. [Architecture](#3-architecture)
4. [Tech Stack](#4-tech-stack)
5. [Repository Structure](#5-repository-structure)
6. [How the CSV Import Works](#6-how-the-csv-import-works)
7. [Progress Tracking & Monotonic SSE Telemetry](#7-progress-tracking--monotonic-sse-telemetry)
8. [Product Management](#8-product-management)
9. [Webhook System](#9-webhook-system)
10. [Local Setup & Developer Onboarding](#10-local-setup--developer-onboarding)
11. [Environment Variables](#11-environment-variables)
12. [Database Schema & Migrations](#12-database-schema--migrations)
13. [API Documentation](#13-api-documentation)
14. [Production Deployment](#14-production-deployment)
15. [Performance & Verified Benchmarks](#15-performance--verified-benchmarks)
16. [Automated Testing & Quality Assurance](#16-automated-testing--quality-assurance)
17. [Project Status](#17-project-status)

---

## 1. Project Overview

### The Problem
Importing hundreds of thousands of e-commerce records via conventional web architectures typically results in:
- **HTTP Request Timeouts**: Web servers freeze when processing large CSV files synchronously.
- **Out of Memory (OOM) Crashes**: Loading 500,000 rows into ORM models (`db.add()`) consumes gigabytes of RAM.
- **Database Write Contention**: High write-amplification against database indexes creates severe I/O bottlenecks.
- **Unreliable Progress Reporting**: Progress bars jump, freeze, or fluctuate backwards due to race conditions between background workers and database polling.
- **Data Corruption**: Partial batch failures leave half-imported catalogs in an inconsistent state.

### The Solution
**Assessment OPM** solves these challenges using a high-throughput, asynchronous pipeline:
- **Asynchronous Execution**: Upload returns an `HTTP 202 Accepted` response within milliseconds; Celery processes the dataset in the background.
- **Two-Pass Streaming Parser**: Validates and deduplicates 500,000 rows in Python in **0.98s** using only **57 MB RAM**, streaming winning records directly to PostgreSQL via binary `COPY ... FROM STDIN`.
- **Atomic Range-Chunked UPSERT**: Merges 466,693 unique products in a single database transaction, ensuring **zero partial data corruption** on failure or cancellation.
- **Strictly Monotonic Real-Time Telemetry**: Real-time Server-Sent Events (SSE) backed by Redis Pub/Sub deliver live progress that **never moves backwards** ($P_{t+1} \ge P_t$).

---

## 2. Key Features

- **500,000-Row CSV Ingestion**: High-throughput ingestion completed in **~21–24 seconds locally** (23,000+ rows/sec) and **~3.9 minutes in production** on throttled free-tier cloud storage.
- **Authoritative Case-Insensitive SKU Uniqueness**: Enforced via PostgreSQL functional unique B-tree index on `LOWER(sku)`.
- **Active State Preservation**: Pre-existing `active=false` flags are strictly preserved during re-imports; product status is never overwritten by supplier CSV files.
- **Deterministic Latest-Row Deduplication**: In-memory mapping guarantees that the latest occurrence of a duplicate SKU in the CSV wins.
- **Instant Non-Blocking API**: API response times remain sub-20ms under full 500K ingestion load.
- **Strictly Monotonic Progress Bar**: Multi-layer defense across Celery, Redis sequence numbers, PostgreSQL `GREATEST()`, FastAPI SSE, and React guarantees progress never regresses.
- **Full Product Management (CRUD)**: Server-side pagination, multi-attribute filtering, sorting, GIN trigram substring search, and safe bulk catalogue clear (`DELETE ALL` confirmation).
- **Asynchronous Webhooks**: Dispatches events (`product.created`, `product.updated`, `product.deleted`, `import.completed`, `products.cleared`) with exponential backoff retries, HMAC-SHA256 signatures, and strict SSRF protection against private subnets.
- **Interactive Webhook Testing UI**: Live ping trigger with real-time response code and round-trip latency metrics.
- **Zero Partial Imports**: Immediate cancellation via `pg_cancel_backend` and full transactional rollback.

---

## 3. Architecture

```
                             ┌───────────────────────────────────┐
                             │       Next.js 16 Frontend         │
                             │      TypeScript + Tailwind        │
                             │      (Vercel / Port 3000)         │
                             └─────────────────┬─────────────────┘
                                               │
                       HTTP REST (CRUD, Upload)│   SSE (/imports/{id}/progress)
                                               │
                             ┌─────────────────▼─────────────────┐
                             │       FastAPI ASGI Server         │
                             │      (Render / Port 8000)         │
                             └────────┬─────────────────┬────────┘
                                      │                 │
                            Database  │                 │ Enqueue Tasks /
                            Queries   │                 ▼ Subscribe Events
                                      │        ┌─────────────────┐
                                      │        │     Redis 7     │
                                      │        │  Broker & Cache │
                                      │        └────────┬────────┘
                                      │                 │
                                      │                 ▼
                                      │        ┌─────────────────┐
                                      │        │  Celery Worker  │
                                      │        └────────┬────────┘
                                      │                 │
                                      ▼                 ▼
                             ┌───────────────────────────────────┐
                             │          PostgreSQL 16            │
                             │                                   │
                             │  - products (UQ on LOWER(sku))    │
                             │  - import_jobs                    │
                             │  - import_errors                  │
                             │  - webhooks                       │
                             │  - unlogged staging_<id> (temp)   │
                             └───────────────────────────────────┘
```

---

## 4. Tech Stack

| Layer | Technology | Role & Justification |
| :--- | :--- | :--- |
| **Backend** | **Python 3.12 + FastAPI** | High-performance ASGI framework with native asynchronous support for long-lived SSE connections and Pydantic v2 validation. |
| **Database** | **PostgreSQL 16** | Relational engine with `pg_trgm` (trigram GIN search), functional unique index on `LOWER(sku)`, and raw `COPY FROM STDIN` streaming. |
| **Task Queue** | **Celery 5.4** | Distributed asynchronous task queue for CPU-intensive CSV parsing, batch UPSERTs, and webhook delivery. |
| **Message Broker** | **Redis 7** | In-memory message broker for Celery queues and low-latency Pub/Sub bus for real-time progress events. |
| **Frontend** | **Next.js 16 (Turbopack)** | React 19 framework using App Router, TypeScript, and Tailwind CSS for responsive, real-time UI. |
| **Database Access** | **SQLAlchemy 2.0 + Asyncpg / Psycopg** | Asynchronous ORM (`asyncpg`) for FastAPI endpoints; raw binary `psycopg` driver for Celery high-throughput `COPY`. |
| **Migrations** | **Alembic** | Version-controlled database schema migrations. |
| **Containerization** | **Docker & Docker Compose** | Reproducible multi-container local stack and unified multi-process container for free-tier cloud deployment. |

---

## 5. Repository Structure

```
assessment-opm/
├── .github/workflows/ci.yml   # Automated GitHub Actions CI (migrations, pytest, frontend build)
├── backend/                   # FastAPI backend application
│   ├── alembic/               # Database migrations & migration environment
│   │   └── versions/          # Versioned schema migration files
│   ├── app/
│   │   ├── api/               # API routes
│   │   │   ├── health.py      # /health and /ready endpoints
│   │   │   └── v1/
│   │   │       ├── imports.py # CSV upload, status, cancellation, and SSE progress stream
│   │   │       ├── products.py# Product CRUD, search, pagination, and bulk clear
│   │   │       └── webhooks.py# Webhook CRUD and interactive test trigger
│   │   ├── core/              # Core configuration, database sessions, and Celery setup
│   │   │   ├── celery_app.py  # Celery application & queue routing configuration
│   │   │   ├── config.py      # Pydantic v2 application settings from .env
│   │   │   └── database.py    # Async and Sync SQLAlchemy engine configurations
│   │   ├── models/            # SQLAlchemy database models
│   │   │   ├── import_job.py  # ImportJob and ImportErrorRecord models
│   │   │   ├── product.py     # Product model with LOWER(sku) functional index
│   │   │   └── webhook.py     # Webhook model with event subscriptions and secret
│   │   ├── schemas/           # Pydantic request/response schemas
│   │   ├── services/          # Core business logic services
│   │   │   ├── import_service.py # 5-pass streaming pipeline & monotonic progress
│   │   │   ├── product_service.py# Product queries, GIN search, and bulk operations
│   │   │   ├── ssrf_validator.py # Strict SSRF DNS pre-resolution security filter
│   │   │   └── webhook_service.py# Webhook dispatch, HMAC generation, and HTTP delivery
│   │   └── tasks/             # Celery background tasks
│   │       ├── import_tasks.py# Background CSV ingestion task
│   │       └── webhook_tasks.py # Background webhook delivery task with exponential backoff
│   ├── tests/                 # Comprehensive Pytest test suite (29 tests)
│   ├── Dockerfile             # Multi-stage production backend Dockerfile
│   ├── entrypoint.sh          # Container entrypoint supporting standalone and unified modes
│   └── requirements.txt       # Python dependencies
├── frontend/                  # Next.js 16 frontend application
│   ├── src/
│   │   ├── app/               # Next.js App Router (layout.tsx, page.tsx, globals.css)
│   │   ├── components/        # Interactive React UI components
│   │   │   ├── Dashboard.tsx      # Overview metrics & system health banner
│   │   │   ├── ImportManager.tsx  # CSV upload, monotonic SSE progress bar, job history
│   │   │   ├── ProductManager.tsx # Paginated table, search, CRUD modals, bulk clear
│   │   │   ├── WebhookManager.tsx # Webhook management & interactive test trigger
│   │   │   └── Navbar.tsx         # Navigation header
│   │   ├── lib/api.ts         # Strongly-typed Axios client with error handling
│   │   └── types/index.ts     # TypeScript interfaces matching backend schemas
│   ├── Dockerfile             # Multi-stage production frontend Dockerfile
│   ├── package.json           # Node.js dependencies and scripts
│   └── tsconfig.json          # TypeScript configuration
├── docs/                      # Architectural design & benchmark documentation
│   ├── architecture.md        # Detailed system design, data flow, and indexing topology
│   ├── benchmarks.md          # In-depth benchmark analysis: Local vs. Production
│   ├── decisions.md           # Architecture Decision Records (ADRs 001–009)
│   ├── deployment.md          # Zero-cost production deployment guide (Render & Vercel)
│   └── production-checklist.md# Requirements verification and production readiness matrix
├── scripts/                   # Evaluation, benchmark, and verification utilities
│   ├── benchmark_import.py    # Ingestion benchmark runner
│   ├── generate_sample_csvs.py# Deterministic generator for 100, 1K, 10K, 100K sample files
│   ├── measure_live_import.py # Full lifecycle measurement (T0-T17 timestamps & throughput)
│   ├── verify_monotonic_import.py # Automated monotonicity & cancellation verification
│   └── samples/               # Sample CSV datasets (100, 1K rows, edge cases)
├── products.csv               # Official 500,000-row benchmark dataset (86.37 MB)
├── docker-compose.yml         # Multi-container local deployment stack
├── render.yaml                # Render Blueprint infrastructure-as-code definition
└── .env.example               # Safe environment variable template with zero secrets
```

---

## 6. How the CSV Import Works

Importing 500,000 records row-by-row through an ORM would take over 20 minutes and crash server memory. Assessment OPM executes a **5-pass streaming pipeline**:

```
[CSV File on Disk (86.37 MB)]
       │
       ▼
Pass 1: Streaming Validation & In-Memory SKU Mapping (Python)
       • Validates format and mandatory fields ('name', 'sku')
       • Maps latest_sku_rows[sku.lower()] = row_index
       • Memory: ~57 MB RAM, Duration: ~0.98s for 500,000 rows
       • Produces winning_row_indices (466,693 unique rows)
       ▼
Pass 2: Streaming PostgreSQL Binary COPY
       • Streams ONLY the 466,693 unique winning records directly into an unlogged staging table
       • Batches of 25,000 rows via raw psycopg "COPY ... FROM STDIN"
       • Scales progress smoothly from 10% to 60%
       ▼
Pass 3: Index Maintenance Optimization (65%)
       • Temporarily drops secondary B-tree and GIN trigram indexes
       • Retains primary key and unique lower index uq_products_sku_lower
       • Cuts write amplification by 40–50%
       ▼
Pass 4: Atomic Range-Chunked Catalogue UPSERT Merge (65% -> 90%)
       • Merges staging records in 5 bounded chunks within ONE single atomic transaction:
             INSERT INTO products (sku, name, description, active, created_at, updated_at)
             SELECT sku, name, description, TRUE, NOW(), NOW()
             FROM staging_table
             WHERE chunk_id = :chunk_id
             ON CONFLICT (lower(sku)) DO UPDATE SET
                 name = EXCLUDED.name,
                 description = EXCLUDED.description,
                 updated_at = NOW();
       • active is intentionally excluded from UPDATE, preserving existing active=false states
       • Celery updates both Redis Pub/Sub and PostgreSQL on every chunk (74%, 78%, 82%, 86%, 90%)
       • Zero partial data corruption: on error or cancellation, ROLLBACK ensures 0 partial rows
       ▼
Pass 5: Background Index Rebuild & Finalization (92% -> 100%)
       • Rebuilds dropped indexes sequentially with maintenance_work_mem = '64MB'
       • Atomically marks status as COMPLETED (100%)
       • Asynchronously dispatches import.completed webhook to Celery
```

### Important Ingestion Behaviors:
- **Case-Insensitive SKU Uniqueness**: `PROD-001`, `prod-001`, and `Prod-001` are treated as the identical product.
- **Latest CSV Row Wins**: If duplicate SKUs appear in the file, the latest row in the CSV is the one committed to the catalogue.
- **Active State Preservation**: If an existing product was marked inactive (`active=false`), re-importing that SKU updates its name and description but keeps `active=false`.
- **Dynamic Cancellation**: Users can cancel an in-progress import at any time. The backend executes `SELECT pg_cancel_backend(pid)` to immediately terminate the PostgreSQL query, triggers `ROLLBACK`, restores all indexes, and cleanly preserves the highest reached progress without dropping to 0%.

---

## 7. Progress Tracking & Monotonic SSE Telemetry

### The Problem Solved
During multi-step imports, progress bars frequently fluctuate backwards (e.g. `60% → 65% → 74% → 65% → 78% → 65%`). This was caused by the frontend's 2.5s watchdog fallback poller reading a stale database row (frozen at 65%) and overwriting real-time SSE progress whenever stage messages differed.

### Monotonic Guarantee ($P_{t+1} \ge P_t$)
Monotonicity is enforced across **four independent defensive layers**:

```
[Layer 1: Celery & Redis]
  • Atomic sequence increment: import_seq:{job_id}
  • Monotonic progress tracker: safe_progress = max(import_max_progress, progress)
  • Caches highest progress in import_latest:{job_id}
        │
        ▼
[Layer 2: PostgreSQL Engine]
  • update_job_db SQL enforces:
    progress = GREATEST(progress, :progress)
    processed_rows = GREATEST(processed_rows, :processed)
  • Database updates executed on every UPSERT chunk (74%, 78%, 82%, 86%, 90%)
        │
        ▼
[Layer 3: FastAPI Endpoints]
  • GET /api/v1/imports/{id} overlays real-time Redis cached progress
  • SSE generator maintains max_streamed_progress state
        │
        ▼
[Layer 4: React / Next.js State]
  • Out-of-order SSE packets discarded using lastSeenSeqRef
  • Both SSE handler and Watchdog Poller enforce Math.max(prev.progress, current.progress)
  • COMPLETED status clamps to 100%; CANCELLED preserves reached progress
```

---

## 8. Product Management

Assessment OPM provides a full-featured product management dashboard:
- **Server-Side Pagination**: Bounded page sizes (10, 25, 50, 100) with total count and page controls.
- **Fuzzy Substring Search**: Real-time search across `sku` and `name` powered by PostgreSQL `pg_trgm` GIN indexes.
- **Status Filtering**: Filter by `All`, `Active`, or `Inactive`.
- **Column Sorting**: Sort by SKU, Name, Active status, or Creation date in ascending/descending order.
- **Single Product CRUD**:
  - **Create**: Validates required fields; duplicate case-insensitive SKUs return `409 Conflict`.
  - **Read**: Fetch product details with creation/update timestamps.
  - **Update**: Edit product name, description, or SKU.
  - **Delete**: Remove product; triggers `product.deleted` webhook.
  - **Toggle Status**: Instantly toggle `active` between true and false.
- **Safe Bulk Clear**: Truncates the entire product catalogue in sub-100ms using `DELETE /api/v1/products`, protected by a modal requiring the user to type `DELETE ALL`.

---

## 9. Webhook System

The webhook engine dispatches asynchronous HTTP POST notifications to external services when catalog events occur.

### Supported Events
- `product.created`: Fired when a new product is created via API.
- `product.updated`: Fired when a product's name, description, SKU, or active state is modified.
- `product.deleted`: Fired when an individual product is deleted.
- `products.cleared`: Fired when the product catalogue is bulk truncated.
- `import.completed`: Fired when a CSV import finishes successfully.

### Webhook Security & Delivery Guarantees
1. **HMAC-SHA256 Signatures**:
   Every webhook request includes an `X-Webhook-Signature` header computed as:
   $$\text{Signature} = \text{HMAC-SHA256}(\text{secret}, \text{raw\_json\_payload})$$
2. **Strict SSRF Protection**:
   Before any webhook request is dispatched, the URL hostname is resolved against DNS. Any IP address belonging to private or restricted subnets is immediately rejected with `SSRFBlockedException`:
   - Loopback (`127.0.0.0/8`, `::1`)
   - RFC 1918 Private networks (`10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16`)
   - Link-local and cloud metadata (`169.254.0.0/16`, `fe80::/10`)
   - Broadcast and reserved networks
   - Open redirect bypasses are disabled (`follow_redirects=False`).
3. **Exponential Backoff Retries**:
   Failed deliveries (HTTP 5xx, timeouts, connection errors) are retried up to 3 times with exponential backoff ($2^n$ seconds).
4. **Interactive Testing UI**:
   The Webhook Manager includes an instant **Test Ping** button (`POST /api/v1/webhooks/{id}/test`) that dispatches a simulated event and returns the target server's HTTP response code and round-trip latency in milliseconds.

---

## 10. Local Setup & Developer Onboarding

### Option A: Docker Compose (Recommended)

Start the complete multi-container stack with a single command:

```bash
# 1. Clone the repository
git clone https://github.com/AfsarAI/assessment-opm.git
cd assessment-opm

# 2. Start PostgreSQL, Redis, FastAPI Backend, Celery Worker, and Next.js Frontend
docker compose down -v && docker compose up -d --build
```

**Local Service URLs**:
- Frontend UI: [http://localhost:3000](http://localhost:3000)
- Backend Swagger API Docs: [http://localhost:8000/docs](http://localhost:8000/docs)
- Health Check: `curl http://localhost:8000/health`
- Readiness Check: `curl http://localhost:8000/ready`

---

### Option B: Native Step-by-Step Setup (Without Docker)

#### Prerequisites
- Python 3.12+
- Node.js 20+ LTS and npm 10+
- PostgreSQL 16 running on port 5432
- Redis 7 running on port 6379

#### 1. Configure Environment Variables
```bash
cp .env.example .env
```

#### 2. Set Up Database
```bash
# In PostgreSQL:
psql -U postgres -c "CREATE USER opm_user WITH PASSWORD 'opm_password';"
psql -U postgres -c "CREATE DATABASE opm_db OWNER opm_user;"
psql -U postgres -d opm_db -c "CREATE EXTENSION IF NOT EXISTS pg_trgm;"
```

#### 3. Set Up Backend & Run Migrations
```bash
cd backend

# Create and activate virtual environment
python3 -m venv .venv
source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Run Alembic migrations
alembic upgrade head

# Start FastAPI API server
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

#### 4. Start Celery Background Worker
In a new terminal window:
```bash
cd backend
source .venv/bin/activate
celery -A app.core.celery_app.celery_app worker --loglevel=info --concurrency=4 -Q imports,webhooks,celery
```

#### 5. Set Up & Start Frontend
In a new terminal window:
```bash
cd frontend

# Install Node dependencies
npm install

# Start Next.js development server
npm run dev
```

Open [http://localhost:3000](http://localhost:3000) in your browser.

---

## 11. Environment Variables

| Variable | Default | Purpose |
| :--- | :--- | :--- |
| `POSTGRES_USER` | `opm_user` | PostgreSQL username |
| `POSTGRES_PASSWORD` | `opm_password` | PostgreSQL password |
| `POSTGRES_DB` | `opm_db` | PostgreSQL database name |
| `POSTGRES_HOST` | `localhost` | PostgreSQL host (`db` in Docker) |
| `POSTGRES_PORT` | `5432` | PostgreSQL port |
| `DATABASE_URL` | `postgresql+asyncpg://...` | Asynchronous SQLAlchemy connection URL (used by FastAPI) |
| `SYNC_DATABASE_URL`| `postgresql+psycopg://...` | Synchronous connection URL (used by Celery & Alembic) |
| `REDIS_URL` | `redis://localhost:6379/0` | Redis connection URL for caching and Pub/Sub |
| `CELERY_BROKER_URL`| `redis://localhost:6379/0` | Celery task queue broker |
| `CELERY_RESULT_BACKEND` | `redis://localhost:6379/0` | Celery task result backend |
| `CORS_ORIGINS` | `["http://localhost:3000"]`| Allowed CORS origins (`*` in production) |
| `UPLOAD_DIR` | `uploads` | Local directory for temporary CSV staging |
| `MAX_UPLOAD_SIZE_MB`| `250` | Maximum allowed CSV file upload size |
| `WEBHOOK_TIMEOUT_SECONDS` | `5` | HTTP request timeout for webhook delivery |
| `WEBHOOK_MAX_RETRIES` | `3` | Maximum retry attempts for failed webhooks |
| `WEBHOOK_BACKOFF_FACTOR` | `2` | Exponential backoff multiplier |
| `NEXT_PUBLIC_API_URL` | `http://localhost:8000/api/v1` | Public API URL accessible by the browser |

---

## 12. Database Schema & Migrations

### Core Tables
1. **`products`**:
   - `id` (BIGSERIAL PRIMARY KEY)
   - `sku` (VARCHAR(100) NOT NULL)
   - `name` (VARCHAR(255) NOT NULL)
   - `description` (TEXT DEFAULT '')
   - `active` (BOOLEAN DEFAULT TRUE)
   - `created_at`, `updated_at` (TIMESTAMPTZ)
   - Indexes:
     - `uq_products_sku_lower`: Unique B-tree on `LOWER(sku)`
     - `ix_products_active`: B-tree on `active`
     - `ix_products_created_at`: B-tree on `created_at DESC`
     - `ix_products_name_trgm`: GIN trigram on `name gin_trgm_ops`
     - `ix_products_sku_trgm`: GIN trigram on `sku gin_trgm_ops`
2. **`import_jobs`**:
   - Tracks `id`, `filename`, `status`, `total_rows`, `processed_rows`, `successful_rows`, `failed_rows`, `progress`, `stage_message`, `started_at`, `completed_at`.
3. **`import_errors`**:
   - Records malformed or invalid CSV rows (`job_id`, `row_number`, `sku`, `error_reason`, `raw_data`).
4. **`webhooks`**:
   - Stores subscriptions (`id`, `url`, `events`, `secret`, `active`, `created_at`, `updated_at`).

### Migration Commands
```bash
cd backend
# Check current migration revision
alembic current

# Apply all pending migrations
alembic upgrade head

# Rollback one migration revision
alembic downgrade -1
```

---

## 13. API Documentation

Interactive Swagger documentation is available at `/docs` (e.g. [http://localhost:8000/docs](http://localhost:8000/docs) or [https://opm-backend-rf77.onrender.com/docs](https://opm-backend-rf77.onrender.com/docs)).

### Endpoint Reference

| Method | Path | Description | Status Code |
| :--- | :--- | :--- | :--- |
| `GET` | `/health` | Liveness health check | `200 OK` |
| `GET` | `/ready` | Readiness check (validates DB & Redis connections) | `200 OK` / `503` |
| `POST` | `/api/v1/imports` | Upload CSV and enqueue asynchronous ingestion | `202 Accepted` |
| `GET` | `/api/v1/imports` | List recent import jobs with pagination | `200 OK` |
| `GET` | `/api/v1/imports/{id}` | Get real-time status and error log of an import | `200 OK` |
| `GET` | `/api/v1/imports/{id}/progress` | Server-Sent Events (SSE) live progress stream | `200 OK (text/event-stream)` |
| `POST` | `/api/v1/imports/{id}/cancel` | Cancel in-progress import via `pg_cancel_backend` | `200 OK` |
| `GET` | `/api/v1/products` | Paginated product list with search, sort, and filter | `200 OK` |
| `POST` | `/api/v1/products` | Create a single product (enforces unique lower SKU) | `201 Created` / `409 Conflict` |
| `GET` | `/api/v1/products/{id}` | Get single product by ID | `200 OK` / `404` |
| `PATCH`| `/api/v1/products/{id}` | Partial update of a product | `200 OK` / `404` |
| `DELETE`| `/api/v1/products/{id}`| Delete a single product | `204 No Content` |
| `DELETE`| `/api/v1/products` | Bulk truncate product catalogue (`confirm=true` required) | `200 OK` |
| `GET` | `/api/v1/webhooks` | List all configured webhooks | `200 OK` |
| `POST` | `/api/v1/webhooks` | Register a new webhook with secret and event list | `201 Created` |
| `GET` | `/api/v1/webhooks/{id}` | Get webhook details | `200 OK` / `404` |
| `PATCH`| `/api/v1/webhooks/{id}` | Update webhook URL, events, or status | `200 OK` / `404` |
| `DELETE`| `/api/v1/webhooks/{id}`| Delete a webhook subscription | `204 No Content` |
| `POST` | `/api/v1/webhooks/{id}/test` | Trigger interactive test ping with latency reporting | `200 OK` |

---

## 14. Production Deployment

The application is deployed live in production at $0.00 cost:
- **Frontend (Vercel)**: [https://assessment-opm.vercel.app/](https://assessment-opm.vercel.app/)
- **Backend API (Render)**: [https://opm-backend-rf77.onrender.com/docs](https://opm-backend-rf77.onrender.com/docs)

### Deployment Architecture
- **Unified Multi-Process Container**: On Render's free tier, `entrypoint.sh` coordinates an embedded Redis 7 instance, Alembic database migrations, a background Celery worker, and the Uvicorn ASGI server within a single container, bypassing free-tier private networking and multi-service billing constraints.
- **Production Guide**: Step-by-step instructions for reproducing this setup on any new Render/Vercel account are documented in [`docs/deployment.md`](docs/deployment.md).

---

## 15. Performance & Verified Benchmarks

The 500,000-row `products.csv` file (86.37 MB) was empirically benchmarked on both local hardware and the live production environment.

### Benchmark Results

| Metric / Stage | Local Host (Docker NVMe) | Production (Render Free Tier) |
| :--- | :--- | :--- |
| **Dataset Size** | 500,000 rows (86.37 MB) | 500,000 rows (86.37 MB) |
| **Upload Duration** | **0.70s** (122.5 MB/s) | **38.44s** (2.25 MB/s over WAN) |
| **Ingestion Duration** | **21.21s** | **253.79s (~4.2 minutes)** |
| **Throughput** | **23,574 rows/sec** | **1,970 rows/sec** |
| **Unique Products Committed** | **466,693 records** | **466,693 records** |
| **Duplicate SKUs Handled** | **33,307 duplicates** | **33,307 duplicates** |
| **Active State Preservation** | **100% Preserved** | **100% Preserved** |
| **Backward Progress Events** | **0 (Zero)** | **0 (Zero)** |
| **API Responsiveness During Import** | Median 4.4ms, 0 errors | Median 20.4ms, 0 errors |

### Why Does Production Take ~4.2 min vs. ~21s Locally?
The codebase running locally and on production is **100% identical**. The timing difference is governed strictly by physical cloud hardware boundaries:
1. **Network-Attached Storage Throttling**: Render free tier uses virtualized shared network storage throttled to 10–20 MB/s and 100–300 IOPS (compared to 5,000 MB/s and 500,000 IOPS on a local NVMe SSD). Writing 466,693 product tuples and WAL logs physically requires ~180s on this storage tier.
2. **Fractional Shared vCPU**: Render free tier allocates ~0.1 to 0.25 shared vCPU, whereas local testing utilizes dedicated multi-core hardware threads.
3. **Memory Limits**: The unified container runs within a 512 MB RAM boundary with default PostgreSQL `work_mem = 4MB`.

See [`docs/benchmarks.md`](docs/benchmarks.md) for detailed hardware analysis and flame charts.

---

## 16. Automated Testing & Quality Assurance

### 1. Pytest Backend Test Suite (29 Tests)
The test suite validates health checks, product CRUD, case-insensitive SKU uniqueness, pagination, CSV import validation, error logging, and webhook delivery:

```bash
# Run pytest in local Docker container
docker exec -it opm_backend pytest -v
```
**Result**: `29 passed, 1 warning in 4.28s`.

### 2. Monotonicity Verification Across All Dataset Sizes
Run the automated verification script against any local or production target to assert that progress never moves backwards:

```bash
# Test 100 rows
python3 scripts/verify_monotonic_import.py http://localhost:8000 scripts/samples/products_100.csv

# Test 1,000 rows
python3 scripts/verify_monotonic_import.py http://localhost:8000 scripts/samples/products_1000.csv

# Test 10,000 rows
python3 scripts/verify_monotonic_import.py http://localhost:8000 scripts/samples/products_10000.csv

# Test 100,000 rows
python3 scripts/verify_monotonic_import.py http://localhost:8000 scripts/samples/products_100000.csv

# Test 500,000 rows
python3 scripts/verify_monotonic_import.py http://localhost:8000 products.csv

# Test cancellation (verifies progress is preserved and does not drop to 0%)
python3 scripts/verify_monotonic_import.py http://localhost:8000 scripts/samples/products_100000.csv cancel
```

### 3. Frontend Typecheck & Build
```bash
cd frontend
npx tsc --noEmit
npm run build
```
**Result**: Compiled successfully with Next.js Turbopack in 853ms.

---

## 17. Project Status

**PROJECT FINALIZED & FROZEN**:
- **Functionality**: Complete and operational across local and production environments.
- **Audit**: Zero leaked secrets, zero temporary files, clean repository structure.
- **Verification**: 29/29 Pytest tests passing, 500K benchmark verified, 0 backward progress events.
- **Documentation**: All architecture, deployment, benchmark, and ADR documents synchronized.
