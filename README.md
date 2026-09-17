# Assessment OPM: High-Performance 500K Product CSV Ingestion & Management System

[![FastAPI](https://img.shields.io/badge/FastAPI-0.115+-009688.svg?logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com)
[![Next.js](https://img.shields.io/badge/Next.js-15-black.svg?logo=next.js&logoColor=white)](https://nextjs.org)
[![PostgreSQL](https://img.shields.io/badge/PostgreSQL-16-336791.svg?logo=postgresql&logoColor=white)](https://www.postgresql.org)
[![Celery](https://img.shields.io/badge/Celery-5.4-37814A.svg?logo=celery&logoColor=white)](https://docs.celeryq.dev)
[![Redis](https://img.shields.io/badge/Redis-7-DC382D.svg?logo=redis&logoColor=white)](https://redis.io)
[![Docker](https://img.shields.io/badge/Docker_Compose-v2-2496ED.svg?logo=docker&logoColor=white)](https://www.docker.com)

A production-grade, asynchronous web application capable of streaming, validating, deduplicating, and importing a **500,000-row CSV file** into PostgreSQL without blocking the API server. Features real-time Server-Sent Events (SSE) progress tracking, product management (CRUD, case-insensitive SKU uniqueness, filtering, pagination), bulk truncation with safety confirmations, and an asynchronous webhook delivery engine with interactive testing and SSRF protection.

---

## System Architecture

```
                         ┌─────────────────────────┐
                         │   Next.js 15 (App)      │
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

## Key Features

1. **High-Performance 500K CSV Ingestion**:
   - Streaming validation $\rightarrow$ PostgreSQL `UNLOGGED` staging $\rightarrow$ streaming `COPY` $\rightarrow$ SQL deduplication $\rightarrow$ atomic `UPSERT`.
   - Ingests 500,000 records in seconds with low RAM overhead (<35 MB in Python).
2. **Case-Insensitive SKU Uniqueness**:
   - Enforced at PostgreSQL level: `CREATE UNIQUE INDEX uq_products_sku_lower ON products (LOWER(sku));`.
   - Handles concurrent writes safely and rejects duplicate SKUs with `409 Conflict`.
3. **Deterministic CSV Deduplication**:
   - Duplicate SKUs within the CSV (33,307 duplicates present in `products.csv`) are deterministically resolved: later occurrences replace earlier occurrences.
4. **Application-Owned Active Status**:
   - CSV contains no active column. Status defaults to `TRUE` and is **strictly preserved** upon CSV re-imports.
5. **Real-Time Progress Tracking (SSE)**:
   - Live telemetry through Server-Sent Events (`PARSING` $\rightarrow$ `VALIDATING` $\rightarrow$ `IMPORTING` $\rightarrow$ `COMPLETED`).
   - Powered by Redis Pub/Sub with automatic reconnection and heartbeat.
6. **Product CRUD & Server-Side Filtering / Pagination**:
   - Filter by SKU, Name, Status, and Description with pagination and sorting.
7. **Safe Bulk Deletion**:
   - Instant table truncation (`DELETE /api/v1/products`) protected by typed confirmation (`DELETE ALL`).
8. **Asynchronous Webhook Engine**:
   - Celery background delivery with 3 retries, exponential backoff, and strict SSRF protection against internal IP ranges.
   - Interactive webhook tester measuring round-trip latency and HTTP status code.

---

## Documentation

- [Implementation Plan](docs/implementation-plan.md)
- [System Architecture](docs/architecture.md)
- [Architecture Decision Records (ADR)](docs/decisions.md)
- [AI-Assisted Development Log](docs/ai-development-log.md)

---

## Quick Start with Docker Compose

### Prerequisites
- Docker Engine 24+ and Docker Compose v2.

### 1. Clone & Configure
```bash
git clone https://github.com/AfsarAI/assessment-opm.git
cd assessment-opm
cp .env.example .env
```

### 2. Start Full Stack
```bash
docker compose up --build
```

Services will be available at:
- **Frontend UI**: [http://localhost:3000](http://localhost:3000)
- **FastAPI Backend API**: [http://localhost:8000](http://localhost:8000)
- **Interactive Swagger Docs**: [http://localhost:8000/docs](http://localhost:8000/docs)
- **PostgreSQL**: `localhost:5432`
- **Redis**: `localhost:6379`

---

## Local Development (Without Docker)

### 1. Backend Setup
```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Run migrations
alembic upgrade head

# Start FastAPI server
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

# Start Celery worker (in a separate terminal)
celery -A app.core.celery_app.celery_app worker --loglevel=info
```

### 2. Frontend Setup
```bash
cd frontend
npm install
npm run dev
```

---

## Running Automated Tests & 500K Benchmark

```bash
# Run backend pytest suite
cd backend
pytest -v

# Run 500K CSV ingestion benchmark
python scripts/benchmark_import.py
```
