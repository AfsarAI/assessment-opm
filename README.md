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

A production-grade, asynchronous web application capable of streaming, validating, deduplicating, and importing a **500,000-row CSV file** (86.37 MB) into PostgreSQL without blocking the API server. Features real-time Server-Sent Events (SSE) telemetry, full product management (CRUD, case-insensitive SKU uniqueness, filtering, sorting, pagination), safe bulk clearing, and an asynchronous webhook delivery engine with interactive testing and SSRF protection.

---

## Environments & Live URLs

### 1. Local Environment (Recommended for High-Speed Evaluation)
> [!TIP]
> **For the fastest evaluation experience (< 25s for the entire 500K dataset)**, run the project locally via Docker Compose. Local NVMe SSD and multi-core CPU process the entire 500,000-row pipeline in **~21–24 seconds**.

- **Frontend Application**: [http://localhost:3000/](http://localhost:3000/)
- **Backend API & Swagger Docs**: [http://localhost:8000/docs](http://localhost:8000/docs)
- **Health Check**: [http://localhost:8000/health](http://localhost:8000/health)
- **Readiness Check**: [http://localhost:8000/ready](http://localhost:8000/ready)

### 2. Live Public Production Environment
- **Frontend Application (Vercel)**: [https://assessment-opm.vercel.app/](https://assessment-opm.vercel.app/)
- **Backend API & Swagger Docs (Render)**: [https://opm-backend-rf77.onrender.com/docs](https://opm-backend-rf77.onrender.com/docs)
- **Production Health Check**: [https://opm-backend-rf77.onrender.com/health](https://opm-backend-rf77.onrender.com/health)
- **Production Readiness Check**: [https://opm-backend-rf77.onrender.com/ready](https://opm-backend-rf77.onrender.com/ready)

---

## Performance Summary: Local vs. Production

| Metric / Stage | Local Host (Docker) | Production (Render Free Tier) | Status |
| :--- | :--- | :--- | :--- |
| **500K File Upload (86.37 MB)** | **0.70s** (122.5 MB/s) | **40.43s** (2.14 MB/s) | Network WAN transfer |
| **500K Ingestion Duration** | **21.21 seconds** | **236.66 seconds (~3.9 min)** | 100% completed |
| **Throughput** | **23,574 rows/sec** | **2,113 rows/sec** | Non-blocking Celery |
| **Unique Products Committed** | **466,693 records** | **466,693 records** | 100% Accurate |
| **Duplicate SKUs Handled** | **33,307 duplicates** | **33,307 duplicates** | Latest CSV row wins |
| **Active State Preservation** | **100% Preserved** | **100% Preserved** | `active=false` preserved |
| **UI Telemetry** | **Real-Time SSE** | **Real-Time SSE** | Zero page refresh |

---

## Why Does Production Take ~236.66s (~3.9 min) vs. ~21s Locally?

The codebase running locally and on production is **100% identical**. The timing difference is governed strictly by physical cloud hardware boundaries:

1. **Throttled Network-Attached Disk Write Bandwidth (10–20 MB/s, 100–300 IOPS)**:
   - **Locally**: PCIe 4.0 NVMe SSD delivers **500,000 IOPS** and **5,000 MB/s**, absorbing all database writes instantly.
   - **Render Free Tier**: Persistent storage is shared virtualized network-attached disk throttled to ~10–20 MB/s and 100–300 IOPS. Inserting 466,693 product tuples and writing corresponding Write-Ahead Logs (WAL) physically requires ~180 seconds of disk write time on this storage class.
2. **Fractional Shared vCPU vs. Dedicated Multi-Core**:
   - **Locally**: 16–32 dedicated CPU hardware threads.
   - **Render Free Tier**: Allocates ~0.1 to 0.25 shared vCPU. After an initial CPU burst credit, the hypervisor throttles compute, extending string hashing and trigram index generation.
3. **Container Memory Limits (512 MB RAM)**:
   - **Locally**: 32 GB RAM allows PostgreSQL and Celery to cache intermediate tables and indexes without disk paging.
   - **Render Free Tier**: The entire backend container (FastAPI + Celery Worker + Redis) runs within a 512 MB RAM limit, with PostgreSQL default `work_mem = 4MB`.
4. **Public WAN Internet Transit**:
   - Uploading an 86.37 MB file locally takes **0.70s**, while streaming across public internet to Oregon takes **~40s**.

---

## System Architecture

```
                         ┌─────────────────────────┐
                         │    Next.js 16 (UI)      │
                         │   TypeScript + Tailwind │
                         └────────────┬────────────┘
                                      │
                         HTTP REST    │   SSE (/imports/{id}/progress)
                                      │
                         ┌────────────▼────────────┐
                         │     FastAPI ASGI        │
                         └──────┬────────────┬─────┘
                                │            │
                      Product   │            │ Enqueue Job / Publish Event
                      CRUD / SSE│            ▼
                                │     ┌──────────────┐
                                │     │  Redis 7     │
                                │     │  Pub/Sub &   │
                                │     │  Broker      │
                                │     └──────┬───────┘
                                │            │
                                │            ▼
                                │     ┌──────────────┐
                                │     │ Celery Worker│
                                │     └──────┬───────┘
                                │            │ Streaming COPY,
                                ▼            ▼ UPSERT, Webhook HTTP
                         ┌───────────────────────────┐
                         │      PostgreSQL 16        │
                         │                           │
                         │  - products               │
                         │  - import_jobs            │
                         │  - import_errors          │
                         │  - webhooks               │
                         │  - staging_<id> (temp)    │
                         └───────────────────────────┘
```

---

## Ingestion Pipeline Mechanics

1. **Pass 1: Streaming Validation & In-Memory Deduplication**:
   - Evaluates row format, checks required fields (`name`, `sku`), logs malformed rows to `import_errors`, and maps `latest_sku_rows[sku.lower()] = row_idx`.
   - On 500,000 rows, this takes **0.98s** and consumes only **57 MB RAM**.
   - Extracts `winning_row_indices = set(latest_sku_rows.values())` and reclaims dictionary memory immediately.
2. **Pass 2: Streaming PostgreSQL Binary COPY**:
   - Streams only the 466,693 unique winning records directly into an ephemeral unlogged staging table via raw `psycopg` `COPY ... FROM STDIN`.
   - Pre-assigns bounded `chunk_id` (1..5) to avoid secondary index scans.
   - Completely eliminates disk-thrashing staging indexes or intermediate deduplication tables.
3. **Pass 3: Index Maintenance Optimization**:
   - Temporarily drops secondary B-tree indexes (`ix_products_created_at`, `ix_products_active`) alongside GIN trigram indexes (`ix_products_name_trgm`, `ix_products_sku_trgm`) before the UPSERT.
   - Retains `products_pkey` and `uq_products_sku_lower` at all times.
   - Cuts write amplification by 40–50%.
4. **Pass 4: Atomic Range-Chunked Catalogue UPSERT**:
   - Merges inside a single atomic transaction:
     ```sql
     INSERT INTO products (sku, name, description, active, created_at, updated_at)
     SELECT sku, name, description, TRUE, NOW(), NOW()
     FROM staging_table
     WHERE chunk_id = :chunk_id
     ON CONFLICT (lower(sku)) DO UPDATE SET
         name = EXCLUDED.name,
         description = EXCLUDED.description,
         updated_at = NOW();
     ```
   - `active` is intentionally excluded from the update clause, guaranteeing pre-existing `active=false` flags are never overwritten.
   - Zero partial data corruption: on error or cancellation, `ROLLBACK` ensures 0 partial rows in `products`.
5. **Pass 5: Background Index Rebuild**:
   - Rebuilds dropped indexes sequentially post-commit with `maintenance_work_mem = '64MB'`.
   - Marks status as `COMPLETED` (100%) and dispatches `import.completed` webhook asynchronously.

---

## Quickstart: Running Locally

### Prerequisites
- Docker and Docker Compose (v2+)
- Python 3.12+ (optional, for running benchmarks locally)

### 1. Start All Services
```bash
# Clone the repository
git clone https://github.com/AfsarAI/assessment-opm.git
cd assessment-opm

# Start database, redis, backend, worker, and frontend containers cleanly
docker compose down -v && docker compose up -d --build
```

### 2. Verify Services
- Frontend: `http://localhost:3000`
- Backend Swagger: `http://localhost:8000/docs`
- Readiness check: `curl http://localhost:8000/ready`

### 3. Run the 500K Import Benchmark
```bash
# Run automated benchmark on products.csv
python3 scripts/measure_live_import.py products.csv http://localhost:8000
```

### 4. Run Test Suite
```bash
# Execute backend pytest suite (29 tests)
docker exec -i opm_backend pytest
```

---

## Core Documentation

All in-depth documentation is organized inside [`docs/`](docs/):

- [Architecture & System Design](docs/architecture.md): Detailed pipeline mechanics, database schema, and telemetry.
- [Performance Benchmarks & Hardware Analysis](docs/benchmarks.md): Local vs. Production measurements and hardware bottleneck analysis.
- [Architecture Decision Records (ADRs)](docs/decisions.md): Technical decisions and tradeoffs made during implementation.
- [Production Deployment Guide](docs/deployment.md): Zero-cost ($0.00) deployment instructions for Render and Vercel.
- [Production Compliance Checklist](docs/production-checklist.md): Comprehensive assessment requirement verification matrix.
