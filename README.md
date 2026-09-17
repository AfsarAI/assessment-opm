# Assessment OPM: High-Performance 500K Product CSV Ingestion & Management System

[![FastAPI](https://img.shields.io/badge/FastAPI-0.115+-009688.svg?logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com)
[![Next.js](https://img.shields.io/badge/Next.js-16-black.svg?logo=next.js&logoColor=white)](https://nextjs.org)
[![PostgreSQL](https://img.shields.io/badge/PostgreSQL-16-336791.svg?logo=postgresql&logoColor=white)](https://www.postgresql.org)
[![Celery](https://img.shields.io/badge/Celery-5.4-37814A.svg?logo=celery&logoColor=white)](https://docs.celeryq.dev)
[![Redis](https://img.shields.io/badge/Redis-7-DC382D.svg?logo=redis&logoColor=white)](https://redis.io)
[![Docker](https://img.shields.io/badge/Docker_Compose-v2-2496ED.svg?logo=docker&logoColor=white)](https://www.docker.com)
[![Tests](https://img.shields.io/badge/Pytest-22%2F22_Passed-success.svg)](backend/tests)
[![Live Frontend](https://img.shields.io/badge/Vercel-Live_App-black.svg?logo=vercel&logoColor=white)](https://assessment-opm.vercel.app/)
[![Live Backend](https://img.shields.io/badge/Render-Live_API-46E3B7.svg?logo=render&logoColor=white)](https://opm-backend-rf77.onrender.com/docs)

A production-grade, asynchronous web application capable of streaming, validating, deduplicating, and importing a **500,000-row CSV file** into PostgreSQL without blocking the API server. Features real-time Server-Sent Events (SSE) progress tracking, product management (CRUD, case-insensitive SKU uniqueness, filtering, pagination), bulk truncation with safety confirmations, and an asynchronous webhook delivery engine with interactive testing and SSRF protection.

---

## Live Production URLs

- **Frontend Application (Vercel)**: [https://assessment-opm.vercel.app/](https://assessment-opm.vercel.app/)
- **Backend API & Swagger Docs (Render)**: [https://opm-backend-rf77.onrender.com/docs](https://opm-backend-rf77.onrender.com/docs)
- **API Readiness Check**: [https://opm-backend-rf77.onrender.com/ready](https://opm-backend-rf77.onrender.com/ready)
- **API Liveness Check**: [https://opm-backend-rf77.onrender.com/health](https://opm-backend-rf77.onrender.com/health)

---

## Benchmark Highlights (Full 500,000-row Dataset)

| Metric | Result | Target / Requirement | Status |
| :--- | :--- | :--- | :--- |
| **500,000-Row Ingestion Time** | **24.38 seconds** | < 60 seconds | **35,339 rows/sec** |
| **Unique Products Stored** | **466,693 records** | 466,693 unique SKUs | **100% Accurate** |
| **Duplicate SKUs Deduplicated** | **33,307 duplicates** | Later row wins | **Deterministic** |
| **Active Status Preservation** | **100% Preserved** | `active=false` preserved on re-import | **Verified** |
| **API Response Time** | **< 15 ms** | Immediate HTTP 202 Accepted | **Non-blocking** |
| **Worker Peak RAM (RSS)** | **< 220 MB** | Streaming chunking | **Low Footprint** |

Detailed benchmark methodology, live production telemetry, and optimization analyses can be found in:
- [Production Performance Audit & Optimization](docs/production-performance-audit.md) *(Live 500k Render & Vercel measurements)*
- [Benchmark Results](docs/benchmark-results.md) *(Local & containerized benchmarks)*
- [Final Deployment Report](docs/final-deployment-report.md) *(End-to-end verification)*

---

## System Architecture

```
                         ┌─────────────────────────┐
                         │   Next.js 16 (App)      │
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
                                │            │ Bulk COPY,
                                ▼            ▼ UPSERT, Webhook HTTP
                         ┌───────────────────────────┐
                         │      PostgreSQL 16        │
                         │                           │
                         │  - products               │
                         │  - import_jobs            │
                         │  - import_errors          │
                         │  - webhooks               │
                         │  - staging_imports (temp) │
                         └───────────────────────────┘
```

---

## Key Features & Production Engineering

1. **High-Performance 500K CSV Ingestion Pipeline**:
   - Streaming validation $\rightarrow$ PostgreSQL `UNLOGGED` staging $\rightarrow$ streaming `COPY` $\rightarrow$ SQL deduplication $\rightarrow$ atomic `UPSERT`.
   - Ingests 500,000 records in 24 seconds with low RAM overhead (<35 MB in Python, streaming 25,000-row chunks).
2. **Case-Insensitive SKU Uniqueness**:
   - Enforced at PostgreSQL level: `CREATE UNIQUE INDEX uq_products_sku_lower ON products (LOWER(sku));`.
   - Handles concurrent writes safely and rejects duplicate SKUs with `409 Conflict`.
3. **Deterministic CSV Deduplication**:
   - Duplicate SKUs within the CSV (33,307 duplicates present in `products.csv`) are deterministically resolved: later occurrences replace earlier occurrences via `ORDER BY lower(sku), row_number DESC`.
4. **Application-Owned Active Status**:
   - CSV contains no active column. Status defaults to `TRUE` and is **strictly preserved** upon CSV re-imports via selective `DO UPDATE SET`.
5. **Real-Time Progress Tracking (SSE)**:
   - Live telemetry through Server-Sent Events (`PARSING` $\rightarrow$ `VALIDATING` $\rightarrow$ `IMPORTING` $\rightarrow$ `COMPLETED`).
   - Powered by Redis Pub/Sub with automatic reconnection and fallback.
6. **Product CRUD & Server-Side Filtering / Pagination**:
   - Filter by SKU, Name, Status (`active` / `inactive`), and Description with pagination and sorting.
7. **Safe Bulk Deletion**:
   - Instant table truncation (`DELETE /api/v1/products`) protected by typed confirmation (`DELETE ALL` in UI or `confirm=true` in API).
8. **Asynchronous Webhook Engine**:
   - Celery background delivery with 3 retries, exponential backoff, HMAC-SHA256 signature header, and strict SSRF protection against private/loopback/cloud metadata IP ranges.
   - Interactive webhook tester measuring round-trip latency and HTTP status code.

---

## Documentation Index

- [Implementation Plan](docs/implementation-plan.md) — Phased architecture roadmap and task breakdown.
- [System Architecture](docs/architecture.md) — Comprehensive technical architecture, database schemas, and data flow.
- [Architecture Decision Records (ADR)](docs/decisions.md) — Rationale for COPY vs ORM, unlogged staging, SSRF defense, and queues.
- [Benchmark Results](docs/benchmark-results.md) — Empirical timing, memory footprint, and validation logs.
- [AI-Assisted Development Log](docs/ai-development-log.md) — Audit trail of AI-assisted engineering iterations and prompts.

---

## Quick Start with Docker Compose

### Prerequisites
- Docker Engine 24+ and Docker Compose v2.

### 1. Clone & Start
```bash
git clone https://github.com/AfsarAI/assessment-opm.git
cd assessment-opm
cp .env.example .env

# Build and start all 5 containers (DB, Redis, Backend, Celery Worker, Frontend)
docker compose up -d --build
```

Services will be ready at:
- **Frontend Dashboard**: [http://localhost:3000](http://localhost:3000)
- **FastAPI Backend API**: [http://localhost:8000](http://localhost:8000)
- **Interactive Swagger Docs**: [http://localhost:8000/docs](http://localhost:8000/docs)
- **Health Check**: [http://localhost:8000/health](http://localhost:8000/health) or [http://localhost:8000/ready](http://localhost:8000/ready)

---

## API Endpoints Reference

### Products API (`/api/v1/products`)
| Method | Endpoint | Description |
| :--- | :--- | :--- |
| `GET` | `/api/v1/products` | Paginated product list with `sku`, `name`, `status`, `sort_by` filters |
| `POST` | `/api/v1/products` | Create product with case-insensitive unique SKU enforcement |
| `GET` | `/api/v1/products/{id}` | Get product by primary key ID |
| `PATCH`| `/api/v1/products/{id}` | Update product fields (including `active` toggle) |
| `DELETE`| `/api/v1/products/{id}`| Soft or hard delete single product |
| `DELETE`| `/api/v1/products?confirm=true`| Bulk truncate all products (requires confirmation) |

### Imports API (`/api/v1/imports`)
| Method | Endpoint | Description |
| :--- | :--- | :--- |
| `POST` | `/api/v1/imports` | Upload CSV multipart file; returns `import_id` immediately (HTTP 202) |
| `GET` | `/api/v1/imports` | List recent import jobs and metadata |
| `GET` | `/api/v1/imports/{id}` | Inspect specific import job details and validation error records |
| `GET` | `/api/v1/imports/{id}/progress` | Server-Sent Events (SSE) real-time streaming progress |

### Webhooks API (`/api/v1/webhooks`)
| Method | Endpoint | Description |
| :--- | :--- | :--- |
| `GET` | `/api/v1/webhooks` | List registered webhook endpoints |
| `POST` | `/api/v1/webhooks` | Register webhook endpoint (SSRF validated) |
| `GET` | `/api/v1/webhooks/{id}` | Get webhook details |
| `PUT` | `/api/v1/webhooks/{id}` | Update webhook URL, secret, or subscribed events |
| `DELETE`| `/api/v1/webhooks/{id}`| Delete webhook endpoint |
| `POST` | `/api/v1/webhooks/{id}/test` | Trigger live test ping and measure round-trip latency |

---

## Running Automated Tests & Benchmarks

### 1. Execute Backend Pytest Suite (22 Tests)
```bash
docker exec opm_backend pytest tests/ -v
```

### 2. Run the 500,000-Row CSV Benchmark
```bash
python3 scripts/benchmark_import.py products.csv
```
Expected output:
- Upload duration: ~0.8s
- Ingestion duration: ~24s
- Throughput: ~35,000 rows/sec
- Database total: 466,693 products (with all 33,307 duplicate SKUs resolved)

---

## Production Security & Best Practices
- **SSRF Protection**: Webhook URLs are resolved via DNS and checked against RFC 1918 private subnets, loopback addresses (`127.0.0.1`, `::1`), and cloud metadata IP (`169.254.169.254`).
- **Cryptographic Signatures**: Webhook requests include `X-Webhook-Signature` (`sha256=<hmac_hex>`) using per-webhook secrets.
- **PostgreSQL Connection Pooling**: Configured with AsyncPG and Psycopg3 connection pools, health checks, and automatic schema migrations via Alembic.
