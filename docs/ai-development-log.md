# AI-Assisted Development Log: Assessment OPM

This document transparently records the AI-assisted engineering workflow, design deliberations, prompt interactions, accepted suggestions, rejected proposals, and optimizations made throughout the development of Assessment OPM.

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

## 4. Security & Robustness Considerations
- Added DNS resolution and IP subnet validation to the webhook engine to prevent Server-Side Request Forgery (SSRF) against internal services and cloud instance metadata (`169.254.169.254`).
- Implemented file upload size checks and secure random UUID naming to eliminate path traversal vulnerabilities.
- Provided typed confirmation dialog for destructive bulk operations (`DELETE /api/v1/products`).
