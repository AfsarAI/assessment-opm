# AI-Assisted Development Log: Assessment OPM

This document records the AI-assisted engineering workflow, design deliberations, prompt interactions, accepted suggestions, rejected proposals, and optimizations made throughout the development of Assessment OPM.

---

## 1. Initial Prompt & Problem Decomposition

### Human Intent & Requirements
- **Goal**: Ingest up to 500,000 product records from CSV into a PostgreSQL database with real-time SSE progress, case-insensitive SKU deduplication, CRUD, filtering, pagination, bulk deletion, and asynchronous webhooks.
- **Key Constraints**:
  - The API server must remain responsive during 500K ingestion.
  - Active status is application-owned and not in the CSV; it must survive re-imports.
  - Case-insensitive SKU uniqueness must be strictly enforced at the database level.
  - Full observability: real progress (no artificial timer), clean Git history, comprehensive testing, Docker Compose.

---

## 2. Phase 0: Empirical Data Inspection & AI Suggestions

### Investigation
- The actual `products.csv` was analyzed via `scripts/inspect_csv.py` (streaming parser).
- **Findings**: Exactly 500,000 rows, 3 columns (`name`, `sku`, `description`), no malformed lines, no null fields, max lengths (30, 19, 199).
- **Critical Finding**: 33,307 duplicate SKU rows in the source CSV (466,693 unique SKUs).

### AI Suggestions & Architectural Evaluation

| AI Proposal | Decision | Rationale |
| :--- | :--- | :--- |
| **Use pandas / dask for CSV ingestion** | **REJECTED** | Adding heavy binary dependencies (Pandas, NumPy) adds hundreds of MB to image size and does not solve PostgreSQL COPY streaming. Standard Python `csv.reader` + streaming TSV COPY uses <35 MB RAM. |
| **Use Celery chunking with individual task per 1,000 rows** | **REJECTED** | Enqueuing 500 tasks into Redis creates unnecessary queue serialization overhead and concurrent transaction contention on the `products` table. Single worker task streaming COPY into an unlogged staging table is vastly faster. |
| **PostgreSQL UNLOGGED Staging Table + COPY + SQL UPSERT** | **ACCEPTED** | Extreme throughput, sub-60 second execution for 500K records, bypasses WAL on transient data, and set-based deduplication (`DISTINCT ON`) in SQL runs in seconds. |
| **WebSockets for Progress Tracking** | **REJECTED** | WebSockets introduce bidirectional connection state management, reconnection complexity, and reverse-proxy upgrade configuration. Progress tracking is strictly unidirectional server-to-client telemetry: SSE is vastly cleaner and more robust. |
| **Server-Sent Events (SSE) via Redis Pub/Sub** | **ACCEPTED** | Clean unidirectional streaming over HTTP/1.1 or HTTP/2, easy client `EventSource` integration, and native support in FastAPI. |

---

## 3. Database & Deduplication Strategy

### Human & AI Alignment on SKU Semantics
- **Rule**: "If duplicates are found, they are replaced based on SKU, treating it without case sensitivity."
- **Implementation**:
  1. Staging table stores `(row_number INT, name TEXT, sku TEXT, description TEXT)`.
  2. SQL deduplication:
     ```sql
     SELECT DISTINCT ON (LOWER(sku))
         name, sku, description
     FROM staging_<job_id>
     ORDER BY LOWER(sku), row_number DESC;
     ```
  3. This guarantees that higher `row_number` (later occurrence in the CSV) wins deterministically.
  4. Upsert into `products`:
     ```sql
     ON CONFLICT (LOWER(sku)) DO UPDATE SET
         name = EXCLUDED.name,
         description = EXCLUDED.description,
         updated_at = NOW();
     ```
     `active` is omitted from `UPDATE`, ensuring existing active/inactive statuses persist.

---

## 4. Key Debugging & Quality Assurance Iterations

Throughout development, several subtle challenges were caught and resolved:

1. **AsyncPG Event-Loop Sharing in Pytest**:
   - *Problem*: Pytest-asyncio creates per-test event loops, causing standard AsyncEngine connection pool reuse across tests to fail with `Attached to a different loop`.
   - *Fix*: Configured `poolclass=NullPool` for the test engine in `tests/conftest.py`.

2. **Async Relationship Lazy Loading (MissingGreenlet)**:
   - *Problem*: In `get_import_job`, accessing `job.errors` triggered greenlet errors in async SQLAlchemy.
   - *Fix*: Applied eager loading via `.options(selectinload(ImportJob.errors))`.

3. **Celery Queue Partitioning**:
   - *Problem*: Heavy 500K ingestion tasks could starve lightweight webhook delivery tasks if on a single shared queue.
   - *Fix*: Partitioned tasks into separate queues (`imports` and `webhooks`), with worker listening on both (`-Q imports,webhooks,celery`).

4. **Active Filter Parameter Harmonization**:
   - *Problem*: API consumers requested filtering either via `status=active|inactive` or `active=true|false`.
   - *Fix*: Accepted both in `app/api/v1/products.py` with automatic fallback and documentation.

5. **SSRF Webhook Protection**:
   - *Problem*: User-specified webhook URLs could be pointed to internal services (e.g. AWS/GCP metadata `169.254.169.254` or Docker internal IP ranges `172.16.0.0/12`).
   - *Fix*: Implemented strict DNS resolution validation rejecting all RFC 1918, RFC 3927 (link-local), and loopback subnets.

---

## 5. Phase-by-Phase Implementation Chronology

- **Phase 0: Inspection**: Empirically analyzed `products.csv` (87.2 MB, 500K rows, 466,693 unique SKUs).
- **Phase 1: Architecture & Planning**: Established project structure, implementation plan, ADRs, and sample scripts.
- **Phase 2: Database Layer**: SQLAlchemy 2.0 async models, Alembic migrations, PostgreSQL functional index on `LOWER(sku)`.
- **Phase 3: Product CRUD API**: Endpoints for pagination, sorting, search, single product fetch/patch/delete, and bulk truncate.
- **Phase 4 & 5: Celery Pipeline & SSE**: Asynchronous `UNLOGGED` staging table + streaming COPY + SQL deduplication + Redis Pub/Sub SSE telemetry.
- **Phase 6: Webhook System**: Asynchronous delivery engine, HMAC-SHA256 signatures, SSRF validation, and interactive test trigger endpoint.
- **Phase 7: Frontend Application**: Next.js 16 App Router UI with stats cards, CSV upload with real-time SSE progress bar, products table with search/filtering, and webhook manager.
- **Phase 8: Benchmarking**: Executed 500,000-row ingestion benchmark in **24.38 seconds** (35,339 rows/sec) and verified active flag preservation.

---

## 6. Official Benchmark Metrics

- **Total Ingestion Duration**: 24.38 seconds
- **File Upload Duration**: 0.78 seconds
- **Total Throughput**: 35,339 rows/sec
- **Unique Products Saved**: 466,693
- **Duplicate Rows Handled**: 33,307
- **Pytest Suite**: 22 passed, 0 failed (2.80s)
- **Active Preservation Test**: Verified (product `a-ability-see-gun` remained `active: false` after subsequent re-import).
