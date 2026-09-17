# Assessment OPM: Deployment Platform Evaluation & Architecture Selection

## Executive Evaluation Summary

The goal of this evaluation is to select the most reliable, performant, and completely **Zero Cost ($0.00)** deployment architecture for Assessment OPM. The system must process a 500,000-row CSV (87.2 MB) asynchronously without blocking API operations, provide real-time Server-Sent Events (SSE) telemetry, and maintain persistent case-insensitive unique products in PostgreSQL.

---

## 1. Candidate Deployment Platforms Evaluation

| Criteria | Option A: Render Stack | Option B: Koyeb + Neon | Option C: Vercel + Render Backend | Option D: Hugging Face Spaces (Docker) | Option E: Railway Trial |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **Actual Cost** | **$0.00 / mo** (Free Tier) | **$0.00 / mo** (Free Eco) | **$0.00 / mo** (Free Tiers) | **$0.00 / mo** (Community Free) | $5 credit (Card required, expirations) |
| **PostgreSQL Support** | Native Free Managed PG (1 GB) | Neon Free PG (0.5 GB) | Neon / Render PG (0.5-1 GB) | Internal Postgres or External | Add-on PG (Trial limit) |
| **Redis Broker Support** | Local process or Upstash Free | Upstash Free Redis | Upstash / Local process | Local Redis process | Add-on Redis |
| **Worker Execution** | Multi-process supervisor | Multi-process supervisor | In backend container | Multi-process in container | Separate service (Trial burn) |
| **87 MB CSV Upload** | Supported (100 MB limit) | Supported | Direct to backend | Supported | Supported |
| **SSE Streaming** | Native HTTP/1.1 chunked | Supported | Direct to backend | Native HTTP streaming | Supported |
| **RAM Allowance** | 512 MB | 512 MB | 512 MB (backend) + Serverless Edge | **16 GB RAM (!)** | 512 MB - 1 GB |
| **CPU Allowance** | 0.1 vCPU (shared) | 0.1 vCPU | 0.1 vCPU + Vercel Edge | **2 vCPU (Dedicated)** | Shared vCPU |
| **Sleep / Spin-down** | Spins down after 15m | Always-on (1 service) | Frontend instant; Backend spins down | Sleeps after 48h idle | No spin-down (burns credit) |
| **Filesystem Sharing** | Unified container (Shared) | Unified container (Shared) | Unified backend container | Unified container (50 GB disk) | Volume mount required |

---

## 2. Deep Dive by Option

### Option A: Render Full Stack ($0.00)
- **Architecture**:
  - `opm-db`: Render Free PostgreSQL (1 GB storage, 50 connections).
  - `opm-backend`: Render Free Web Service running Docker multi-process:
    - FastAPI (ASGI via Uvicorn on `$PORT`)
    - Celery Worker (`-Q imports,webhooks,celery --concurrency=2`)
    - Embedded lightweight Redis server (for task queue broker and SSE Pub/Sub)
  - `opm-frontend`: Render Static Site / Web Service or Vercel Next.js.
- **Pros**:
  - Native GitHub integration (auto-deploys on `git push main`).
  - Native managed PostgreSQL included on free tier with 1 GB disk (comfortably accommodates the 194 MB database).
  - Unified container eliminates the shared-filesystem problem completely: uploaded CSV files in `/app/uploads` are immediately accessible to the Celery worker process on the same container.
  - Zero external credentials or complex object storage setup required.
- **Cons**:
  - Free web service spins down after 15 minutes of inactivity; cold start takes ~50s.

### Option B: Koyeb Free Eco + Neon PostgreSQL ($0.00)
- **Architecture**:
  - Koyeb Free Web Service running Docker container (FastAPI + Celery + Redis).
  - Neon Serverless PostgreSQL free tier.
- **Pros**:
  - Koyeb Free Eco tier does **not** spin down; always running.
  - Good global network latency.
- **Cons**:
  - Neon free tier storage limit is 500 MB (our 500K table + indexes is 194 MB, leaving ~300 MB headroom).
  - 512 MB RAM strict limit on Koyeb can be tight under 500K ingestion if concurrency is not tuned.

### Option C: Vercel (Frontend) + Render/Koyeb (Backend) ($0.00)
- **Architecture**:
  - Next.js 16 frontend hosted on Vercel (Edge CDN, instantaneous global loading, zero cold starts).
  - FastAPI + Celery backend hosted on Render or Koyeb.
  - PostgreSQL hosted on Render or Neon.
- **Pros**:
  - Best user experience for the dashboard UI (instant load via Vercel).
  - Clean separation of presentation and compute layers.
- **Cons**:
  - Requires setting `NEXT_PUBLIC_API_URL` to point to the Render backend URL.
  - Must configure CORS on the backend for the Vercel domain.

### Option D: Hugging Face Spaces (Docker SDK) ($0.00)
- **Architecture**:
  - A single dedicated Docker Space on Hugging Face.
  - Huge resource allocation: **2 vCPUs, 16 GB RAM, 50 GB persistent disk space** at $0 cost without credit card.
  - Runs PostgreSQL, Redis, Celery, FastAPI, and Next.js in a single super-fast container.
- **Pros**:
  - 16 GB RAM means the 500K ingestion runs at maximum local speed (no CPU/RAM throttling).
  - Exposes port 7860 with a free public HTTPS endpoint (`https://<username>-<spacename>.hf.space`).
  - Zero credit card required; never expires.
- **Cons**:
  - Runs on port 7860 instead of standard 80/443 (though reverse-proxied by HF).
  - UI is hosted on Hugging Face domain.

---

## 3. The File Storage Problem & Resolution

### The Cloud Ephemeral Filesystem Gotcha
In cloud architectures where backend and worker are separate container instances, a standard file upload:
1. User uploads 87 MB CSV to `POST /api/v1/imports`.
2. Backend container saves file to `/app/uploads/uuid.csv`.
3. Backend enqueues Celery task `process_csv_import.delay("uuid", "/app/uploads/uuid.csv")`.
4. Worker container running on another virtual host attempts to read `/app/uploads/uuid.csv` $\rightarrow$ **`FileNotFoundError`**!

### Solution Strategy Matrix

| Solution | Cost | Complexity | Speed | Recommendation |
| :--- | :--- | :--- | :--- | :--- |
| **1. Unified Process Container (Supervisord / Multi-process)** | **$0.00** | Low | Fastest (Direct local disk stream) | **PRIMARY RECOMMENDED** |
| **2. Cloudflare R2 / AWS S3 Object Storage** | Free tier available | Medium (Requires credentials, S3 client, presigned URLs) | Slower (2 network hops) | Viable fallback |
| **3. Shared Cloud Volume (NFS / EFS)** | Paid ($) | High | Fast | Not Zero-Cost |

**Selection**: Solution 1 (**Unified Process Container**) is the cleanest, most reliable, and 100% free solution. By running the FastAPI web server and the Celery worker process inside the same container under a lightweight supervisor (e.g. `supervisord`), both processes share the local filesystem `/app/uploads`, eliminating network transfer latency, object storage complexity, and third-party credential dependencies.

---

## 4. Final Deployment Architecture Selection

We choose a **dual deployment strategy**:
1. **Primary Production Deployment**: **Render Free Blueprint / Web Service** + **Vercel Frontend**.
   - Backend + Worker + Redis combined into a unified production Docker container with Supervisord, deployed on Render.
   - Frontend deployed on Vercel with `NEXT_PUBLIC_API_URL` pointing to the Render backend.
   - Database on Render Free PostgreSQL or Neon.
2. **Alternative One-Click Self-Contained Deployment**: **Render Unified Stack** (Next.js + FastAPI + Celery + Redis all in one Docker service) or **Hugging Face Docker Space** (for evaluators wanting 16 GB RAM high-speed demo).

This guarantees 100% Zero-Cost ($0.00), high resilience, and zero shared-filesystem breakage.
