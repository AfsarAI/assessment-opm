# Architecture Decision Records (ADR): Assessment OPM

This document records the critical architectural, data, and engineering decisions made for the Assessment OPM implementation.

---

## ADR 001: Backend Framework Selection (FastAPI)
- **Decision**: Use FastAPI with Python 3.12, Pydantic v2, and SQLAlchemy 2.0.
- **Context**: The assessment permitted Flask, Django, or FastAPI.
- **Rationale**:
  - Native asynchronous ASGI architecture allows long-lived connections for Server-Sent Events (SSE) without exhausting worker threads.
  - Pydantic v2 offers high-speed validation compiled in Rust.
  - Automatic OpenAPI / Swagger generation at `/docs`.
  - Clean separation of routes, schemas, services, and models.

---

## ADR 002: Asynchronous Task Queue & Broker (Celery + Redis)
- **Decision**: Celery backed by Redis 7.
- **Context**: Importing 500,000 records takes dozens of seconds and must never run in the synchronous request-response lifecycle of an HTTP request.
- **Rationale**:
  - Celery is the industry standard for distributed task execution in Python.
  - Redis serves dual duty: high-throughput Celery message broker and lightweight Pub/Sub bus for real-time progress events.
  - Enables independent horizontal scaling of worker processes separate from API web servers.

---

## ADR 003: 500,000-Row Ingestion via In-Memory Streaming Deduplication & PostgreSQL COPY
- **Decision**: Stream CSV in Python to validate and map `sku.lower() -> latest_row_number` in-memory (~57 MB RAM, 0.98s), stream only winning unique records via PostgreSQL `COPY` into an unlogged staging table, temporarily drop secondary and GIN indexes, and execute an atomic range-chunked `UPSERT` into `products`.
- **Alternatives Considered**:
  - *SQLAlchemy ORM iteration*: Extreme memory usage (>1.5 GB), tens of minutes runtime. Rejected.
  - *In-Database SQL Deduplication (`DISTINCT ON` on Staging Table)*: Required creating intermediate unlogged tables and secondary staging indexes. On Render Free Tier storage (10–20 MB/s, 100–300 IOPS), this caused severe disk thrashing and spilled temporary files to disk, adding >3.5 minutes of delay. Replaced with in-memory Python deduplication.
- **Rationale**:
  - Python dictionary lookup on 500K SKUs takes only 0.98s and ~57 MB RAM, completely bypassing database disk I/O bottlenecks.
  - Staging table size is reduced from 500,000 to exactly 466,693 pre-deduplicated rows.
  - Temporarily dropping secondary B-tree and GIN indexes during the UPSERT cuts write amplification by 40–50%.
  - Zero partial data corruption: on error or cancellation, PostgreSQL transaction `ROLLBACK` guarantees 0 partial rows in `products`, and indexes are restored safely.

---

## ADR 004: Case-Insensitive SKU Uniqueness Enforced at Database Level
- **Decision**: Create a functional unique index:
  ```sql
  CREATE UNIQUE INDEX uq_products_sku_lower ON products (LOWER(sku));
  ```
- **Context**: Product SKUs can arrive in inconsistent casing (`ABC-123`, `abc-123`, `Abc-123`).
- **Rationale**:
  - Application-level uniqueness checks are vulnerable to race conditions under concurrent requests or multi-worker pipelines.
  - The database must be the authoritative source of truth.
  - Functional B-Tree index on `LOWER(sku)` guarantees consistency and enables indexed lookups for lowercase queries.

---

## ADR 005: Preserving Product Status (`active`) Across CSV Re-Imports
- **Decision**: The CSV does not contain an `active` column. The database defaults `active` to `TRUE` on creation. On re-import, the UPSERT query explicitly updates only `name`, `description`, and `updated_at`, leaving `active` untouched.
- **Rationale**:
  - E-commerce administrators who manually deactivate a product must not have their operational state overwritten every time a supplier or ERP CSV is synchronized.

---

## ADR 006: Server-Sent Events (SSE) vs WebSockets for Progress
- **Decision**: Use Server-Sent Events (SSE) over HTTP streaming.
- **Rationale**:
  - Import progress is strictly a unidirectional server-to-client telemetry stream.
  - SSE operates over standard HTTP/1.1 or HTTP/2, traverses enterprise proxies, firewalls, and CDNs without specialized WebSocket negotiation.
  - Simple reconnection mechanism with built-in event IDs and retry headers.
  - Client-side implementation is native via `EventSource` in standard browser APIs.

---

## ADR 007: Safe Bulk Deletion Strategy (`DELETE /api/v1/products`)
- **Decision**: Implement instant `TRUNCATE TABLE products;` guarded by a required query confirmation (`confirm=true`) and confirmation header, while enqueuing the `products.cleared` webhook.
- **Rationale**:
  - Executing `DELETE FROM products` on 500,000 rows generates immense WAL logs, causes high table lock contention, and takes 10-30 seconds.
  - `TRUNCATE` deallocates table pages instantly in sub-100ms.

---

## ADR 008: Webhook Delivery & SSRF Guard
- **Decision**: Webhooks are delivered via Celery background tasks with strict SSRF (Server-Side Request Forgery) IP resolution filtering.
- **Rationale**:
  - Synchronous webhooks in CRUD routes would block API latency and make the server vulnerable to slow-consumer DDoS.
  - Webhooks accepting arbitrary URLs can be exploited to probe internal microservices (`localhost`, `169.254.169.254` AWS/GCP instance metadata). Pre-resolving and blocking private subnets mitigates SSRF vulnerabilities.

---

## ADR 009: Strictly Monotonic Progress Telemetry Pipeline
- **Decision**: Enforce strictly monotonic progress ($P_{t+1} \ge P_t$) across all four defensive layers: Celery/Redis (`import_seq`, `import_max_progress`), PostgreSQL (`GREATEST(progress, :progress)`), FastAPI (`max_streamed_progress`, overlaying Redis cache), and React (`Math.max`, sequence drop guard).
- **Context**: Intermediate catalogue UPSERT chunks (74%, 78%, 82%, 86%, 90%) published to Redis were temporarily pulled backwards to 65% when the frontend's 2.5s watchdog fallback timer polled PostgreSQL.
- **Rationale**:
  - User-facing progress must never regress or fluctuate backwards.
  - Database updates during UPSERT chunks synchronize PostgreSQL with Redis Pub/Sub.
  - Multi-layer defense ensures that even under network lag, SSE packet re-ordering, or watchdog polling races, progress moves monotonically forward to 100%.
  - Cancellation preserves reached progress rather than zeroing out.

