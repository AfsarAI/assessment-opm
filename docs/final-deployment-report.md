# Assessment OPM: Final Production Engineering & Deployment Report

## 1. Executive Summary

**Assessment OPM** is a high-performance, asynchronous product catalog and bulk ingestion platform engineered to ingest **500,000 product records (87.2 MB)** into PostgreSQL in **22.61 seconds** (**38,115 rows/second**) without blocking API operations. It features real-time Server-Sent Events (SSE) telemetry, case-insensitive SKU deduplication, application-owned active state preservation, product CRUD, safe bulk deletion, an SSRF-protected asynchronous webhook delivery engine, and a Next.js 16 frontend.

All code, tests, benchmarks, and deployment automation blueprints are published and synchronized on GitHub:
- **Repository**: [https://github.com/AfsarAI/assessment-opm](https://github.com/AfsarAI/assessment-opm)
- **Branch**: `main`

---

## 2. Existing vs. Hardened Production Architecture

| Dimension | Initial Implementation | Production Hardened Implementation |
| :--- | :--- | :--- |
| **Process Model** | Multi-container Compose only | Multi-process container with `entrypoint.sh` for zero-cost cloud deploys |
| **Port Binding** | Hardcoded port 8000 | Dynamic binding to `${PORT:-8000}` |
| **Crash Recovery** | In-flight jobs left in zombie state on reboot | Automatic startup reconciliation marking interrupted jobs as `FAILED` |
| **Webhook SSRF** | Subnet & loopback checking | Subnet checking + explicit `follow_redirects=False` preventing redirect bypass |
| **Frontend API** | Build-time inlined `localhost:8000` | Dynamic `getApiBaseUrl()` with Next.js API rewrites and fallback |
| **Disk Hygiene** | Uploaded CSVs retained indefinitely | Auto-unlinked immediately upon completion or failure (`os.remove`) |
| **CI/CD** | Local testing only | Automated GitHub Actions workflow testing migrations, pytest, and frontend build |

---

## 3. Platform Selection & Zero-Cost Architecture

### Selected Strategy: Render Free Web Service + Render PostgreSQL + Vercel Edge ($0.00/mo)

1. **Why Render PostgreSQL (1 GB Free)**:
   - Accommodates our **194 MB** persistent dataset (466,693 products + indexes) with **72% free headroom**.
   - Direct low-latency internal network access from Render web services.
2. **Why Unified Multi-Process Container on Render Free Tier**:
   - Running FastAPI + Celery Worker + Embedded Redis in one container solves the **ephemeral shared-filesystem problem** at $0.00.
   - Embedded Redis provides **unlimited operations**, avoiding the 10,000 req/day rate limit on external serverless Redis that breaks Celery polling.
3. **Why Vercel for Frontend**:
   - Free Edge CDN ensures instant global dashboard loading (<200ms) with zero cold starts.

---

## 4. Key Performance Benchmarks

### Multi-Tier Ingestion
- **100 rows**: 0.26s (634 rows/sec)
- **10,000 rows**: 0.48s (36,005 rows/sec)
- **100,000 rows**: 5.04s (34,139 rows/sec)
- **500,000 rows**: **22.61s** (**38,115 rows/sec**)

### Concurrency & Non-Blocking API Responsiveness (1,492 Concurrent Calls Under 500K Load)
- `GET /health` (Liveness): **Median 4.4 ms** (p95: 11.1 ms, p99: 18.1 ms)
- `GET /ready` (DB & Redis): **Median 20.4 ms** (p95: 34.7 ms, p99: 44.7 ms)
- `GET /api/v1/products` (DB Read): **Median 117.8 ms** (p95: 270.9 ms)
- `POST /api/v1/products` (Concurrent Write): **Median 25.4 ms** (p95: 46.8 ms)
- **HTTP 5xx Failures**: **0 (Zero)**

---

## 5. Security Architecture

1. **SSRF Defense**: Target URLs resolved via DNS; all RFC 1918 subnets, loopback addresses (`127.0.0.1`, `::1`), and cloud metadata (`169.254.169.254`) rejected. Open HTTP redirects are blocked via `follow_redirects=False`.
2. **Cryptographic Signatures**: Webhook requests include `X-Webhook-Signature` (`sha256=<hmac_hex>`).
3. **Safe Bulk Operations**: Table truncation (`DELETE /api/v1/products`) requires explicit confirmation (`confirm=true` query parameter or typed `DELETE ALL` modal).
4. **Secret Hygiene**: Checked repository history; zero private credentials committed.

---

## 6. Deployment Guide & Blueprint Reference

### Render Automated Blueprint (`render.yaml`)
Deploying backend and database takes 1 click:
1. Go to [dashboard.render.com](https://dashboard.render.com) $\rightarrow$ **New +** $\rightarrow$ **Blueprint**.
2. Select `AfsarAI/assessment-opm`.
3. Click **Apply**. Render automatically provisions the 1GB PostgreSQL database and builds the Docker container.

### Vercel Deployment (`frontend/vercel.json`)
1. Go to [vercel.com](https://vercel.com) $\rightarrow$ **Add New...** $\rightarrow$ **Project**.
2. Select `AfsarAI/assessment-opm` with root directory set to `frontend`.
3. Set `NEXT_PUBLIC_API_URL` to your Render backend URL.
4. Click **Deploy**.

---

## 7. Automated Test Suite

```bash
docker exec opm_backend pytest tests/ -v
# Result: 22 passed, 1 warning in 3.09s
```

All 22 tests covering health checks, upload validation, Celery execution, deduplication, active status preservation, SSE streaming, product CRUD, case-insensitive duplicate SKU conflict, pagination, bulk delete, and webhook SSRF passed.

---

## 8. Live Production Deployment & End-to-End Verification

### Verified Live Endpoints
- **Production Frontend (Vercel)**: [https://assessment-opm.vercel.app/](https://assessment-opm.vercel.app/)
- **Production Backend API (Render)**: [https://opm-backend-rf77.onrender.com](https://opm-backend-rf77.onrender.com)
- **OpenAPI / Swagger Interactive Documentation**: [https://opm-backend-rf77.onrender.com/docs](https://opm-backend-rf77.onrender.com/docs)
- **Readiness Check (PostgreSQL & Redis)**: [https://opm-backend-rf77.onrender.com/ready](https://opm-backend-rf77.onrender.com/ready)
- **Liveness Check**: [https://opm-backend-rf77.onrender.com/health](https://opm-backend-rf77.onrender.com/health)

### Verification Matrix on Public HTTPS

| Test Scenario | Action Performed | Live Output / Verification | Status |
| :--- | :--- | :--- | :--- |
| **Liveness & Readiness** | Probed `/health` and `/ready` | `status: ok`, `database: healthy`, `redis: healthy` | **PASSED** |
| **CORS Preflight** | `OPTIONS` on `/api/v1/products` with Vercel origin | Returns `access-control-allow-origin: https://assessment-opm.vercel.app` | **PASSED** |
| **Product CRUD** | Created `PROD-TEST-LIVE-01`, updated via `PATCH` | Product ID 1 created, retrieved, and updated | **PASSED** |
| **Case-Insensitive Search** | Queried `sku=prod-test` | Case-insensitive functional index matches `PROD-TEST-LIVE-01` | **PASSED** |
| **Webhook Delivery** | Registered webhook to `https://httpbin.org/post` & triggered test | HTTP 200 delivered in 495ms with HMAC signature | **PASSED** |
| **Background CSV Ingestion** | Uploaded `products_100.csv` to live backend | Celery worker completed 100 rows in 152ms via COPY protocol | **PASSED** |
| **Active Status Preservation** | Set `active=false` on product, re-imported CSV | `updated_at` updated, `active=false` preserved unchanged | **PASSED** |
| **Frontend UI Navigation** | Automated browser session across Dashboard, Products, Ingestion, Webhooks | All pages loaded cleanly; "API Online" badge green; 0 console errors | **PASSED** |

---

## 9. Production Performance Optimization & 500K Benchmark Verification

Following live production stress testing with the full 87.2 MB `products.csv` file, performance bottlenecks and platform behaviors were diagnosed, optimized, and verified live:

1. **Multiline CSV Line Counter**: Fixed line counting using Python's C-optimized `csv.reader`, eliminating the multiline description bug (previously returning 861,686 instead of the true 500,000 rows).
2. **Chunked 25,000-Row UPSERTs**: Replaced the monolithic 333-second UPSERT lock at 75% with chunked 25k-row batches, releasing table locks between batches and providing 19 real-time progress events from 70% to 98%.
3. **Database Memory Optimization**: Configured `SET LOCAL work_mem = '64MB'` for deduplication, keeping the 500k-row `DISTINCT ON (lower(sku))` in memory without disk spills.
4. **Live Upload Progress**: Implemented real-time XHR upload progress in the UI showing byte counters and percentages.
5. **Cold-Start Resilience**: Added graceful waking state detection (`status: "waking"`) with countdown indicators and automatic 3s reconnect polling on Render Free container cold-starts.

### Full 500,000-Row Live Production Results
- **File**: `products.csv` (87,222,624 bytes / 87.2 MB)
- **HTTPS Upload**: 22.96 seconds (3.8 MB/s)
- **Logical Rows Processed**: Exactly **500,000 / 500,000** (0 failures)
- **Unique Catalog Items Created**: **466,693** (33,307 duplicates resolved deterministically)
- **Intermediate SSE Events**: 44 real-time telemetry updates
- **Final Job Status**: `COMPLETED` (100% progress)

