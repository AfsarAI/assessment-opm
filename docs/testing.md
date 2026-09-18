# Testing & Benchmarking Guide: Assessment OPM

This guide provides end-to-end instructions for testing, benchmarking, and verifying the **Assessment OPM** system across local and production environments.

---

## 1. Overview of Verification Suites

| Test Category | Target Component | Tool / Script | Key Assertions |
| :--- | :--- | :--- | :--- |
| **Backend Unit & Integration** | FastAPI, Celery, PostgreSQL | `pytest` (29 tests) | Status codes, schema validation, SKU uniqueness, active preservation, SSRF blocks |
| **Frontend Verification** | Next.js 16, TypeScript | `tsc`, `next build` | Type safety, zero bundle warnings, Turbopack compilation |
| **Monotonicity Telemetry** | Celery, Redis, SSE, Poller | `verify_monotonic_import.py` | Zero backward progress events ($P_{t+1} \ge P_t$), cancellation preservation |
| **Scale & Ingestion Benchmark** | Celery Worker & PostgreSQL | `benchmark_import.py` | End-to-end duration, upload latency, rows/sec throughput |
| **API Responsiveness** | FastAPI ASGI Server | `test_responsiveness.py` | Sub-20ms latency for `/health` and `/products` under 500K load |
| **Multi-Tier Benchmark** | Ingestion Pipeline | `benchmark_suite.py` | Multi-dataset scale comparison (100 to 100,000 rows) |

---

## 2. Backend Automated Test Suite (Pytest)

The backend test suite contains **29 comprehensive unit and integration tests** covering:
- `/health` and `/ready` service probes
- Product CRUD (Create, Read, Update, Delete)
- Authoritative case-insensitive SKU uniqueness (`uq_products_sku_lower`)
- Trigram substring search and multi-column sorting
- CSV validation, malformed row error logging, and streaming deduplication
- Active status preservation on re-imports
- SSRF private IP validation for webhooks
- HMAC-SHA256 webhook signature computation and dispatch

### Running with Docker Compose (Recommended)
```bash
docker exec -it opm_backend pytest -v
```

### Running Natively (Without Docker)
```bash
cd backend
source .venv/bin/activate
pytest -v
```

**Expected Output**:
```text
============================== 29 passed in 4.28s ==============================
```

---

## 3. Frontend Static Analysis & Production Build

Verify TypeScript compilation and Next.js Turbopack production bundle creation:

```bash
cd frontend

# 1. Type check
npx tsc --noEmit

# 2. Production build
npm run build
```

**Expected Output**:
```text
✓ Compiled successfully in ~850ms
✓ Generating static pages
✓ Finalizing page optimization
```

---

## 4. Monotonic Progress Verification (`verify_monotonic_import.py`)

During large CSV imports, progress bars in many systems flicker or move backwards due to race conditions between background workers and database polling. Assessment OPM implements a 4-layer defense guaranteeing **strictly monotonic progress** ($P_{t+1} \ge P_t$).

`scripts/verify_monotonic_import.py` connects to the SSE progress stream while simultaneously executing watchdog polling to assert that **zero** backward events ever occur.

### Running Against Localhost
```bash
# Smoke test (100 rows)
python3 scripts/verify_monotonic_import.py http://localhost:8000 scripts/samples/products_100.csv

# Integration test (1,000 rows)
python3 scripts/verify_monotonic_import.py http://localhost:8000 scripts/samples/products_1000.csv

# Medium stress test (10,000 rows)
python3 scripts/verify_monotonic_import.py http://localhost:8000 scripts/samples/products_10000.csv

# Large stress test (100,000 rows)
python3 scripts/verify_monotonic_import.py http://localhost:8000 scripts/samples/products_100000.csv

# Full benchmark dataset (500,000 rows)
python3 scripts/verify_monotonic_import.py http://localhost:8000 products.csv
```

### Cancellation Preservation Test
Verifies that cancelling an in-flight import triggers `pg_cancel_backend`, rolls back database changes, and preserves the highest reached progress (does not drop to 0%):
```bash
python3 scripts/verify_monotonic_import.py http://localhost:8000 scripts/samples/products_100000.csv cancel
```

### Running Against Production Render Backend
```bash
python3 scripts/verify_monotonic_import.py https://opm-backend-rf77.onrender.com scripts/samples/products_100.csv
```

---

## 5. End-to-End Ingestion Benchmark (`benchmark_import.py`)

Benchmarks the complete 500,000-row ingestion lifecycle:
1. Upload duration & bandwidth (MB/s)
2. Asynchronous job enqueue
3. Real-time SSE stage transitions
4. Ingestion throughput (rows/second)
5. Final committed records vs. deduplicated count

### Command
```bash
# Benchmark against local stack
python3 scripts/benchmark_import.py products.csv

# Or benchmark against custom endpoint
API_BASE=https://opm-backend-rf77.onrender.com/api/v1 python3 scripts/benchmark_import.py scripts/samples/products_1000.csv
```

**Sample Output (Local Docker)**:
```text
✓ Backend health check passed.
[1/3] Uploading products.csv to http://localhost:8000/api/v1/imports...
✓ Upload succeeded in 0.70s.
[2/3] Listening to real-time SSE progress stream...
  [IMPORTING] 20% | Processed: 100,000 / 500,000 rows | Streaming unique rows via PostgreSQL COPY...
  [IMPORTING] 60% | Processed: 466,693 / 500,000 rows | Unique rows staged. Dropping secondary indexes...
  [IMPORTING] 74% | Processed: 466,693 / 500,000 rows | Merged chunk 1/5 into catalogue...
  [IMPORTING] 90% | Processed: 466,693 / 500,000 rows | Merged chunk 5/5 into catalogue...
  [COMPLETED] 100% | Processed: 466,693 / 500,000 rows | Import completed successfully.

[3/3] Import finished with terminal status: COMPLETED
  Server Processing Duration: 21.21s
  Successful rows: 466,693
  Failed rows: 0
  Throughput: 23,574 rows/sec
```

---

## 6. High-Concurrency API Responsiveness Benchmark (`test_responsiveness.py`)

Verifies that the FastAPI web server remains completely responsive (< 20 ms latency) even while Celery and PostgreSQL are actively processing the 500,000-row dataset.

```bash
python3 scripts/test_responsiveness.py
```

### Metrics Measured
- `GET /health` (liveness probe)
- `GET /ready` (readiness with DB & Redis verification)
- `GET /api/v1/products?limit=10` (active database read queries)
- `POST /api/v1/products` (concurrent single inserts during bulk UPSERT)

---

## 7. Multi-Tier Scale Benchmark Suite (`benchmark_suite.py`)

Automates sequential benchmarking across four file tiers (100, 1,000, 10,000, and 100,000 rows) and exports results to `docs/benchmark_results.json`:

```bash
# Local benchmark
API_BASE=http://localhost:8000 python3 scripts/benchmark_suite.py

# Production benchmark
API_BASE=https://opm-backend-rf77.onrender.com python3 scripts/benchmark_suite.py
```

---

## 8. Sample Dataset Generator (`generate_sample_csvs.py`)

To regenerate or customize test CSV slices from `products.csv`:

```bash
python3 scripts/generate_sample_csvs.py
```

This generates:
- `scripts/samples/products_100.csv` (100 rows, for fast unit tests)
- `scripts/samples/products_1000.csv` (1,000 rows, for integration tests)
- `scripts/samples/products_10000.csv` (10,000 rows, medium benchmark)
- `scripts/samples/products_100000.csv` (100,000 rows, stress test)
- `scripts/samples/edge_cases.csv` (Mixed case SKUs, empty fields, special characters)
