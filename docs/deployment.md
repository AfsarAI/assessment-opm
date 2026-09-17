# Assessment OPM: Production Deployment Guide ($0.00 Zero-Cost Architecture)

This document provides step-by-step instructions to deploy Assessment OPM to public production infrastructure completely free of charge ($0.00/month).

---

## Architecture Overview

```
[ Vercel Global Edge CDN ] ──(REST / SSE)──> [ Render Free Web Service (Docker) ]
      (Next.js 16 UI)                                ├── FastAPI (Uvicorn on $PORT)
                                                     ├── Celery Worker (Concurrency 2)
                                                     └── Embedded Redis (Broker & SSE)
                                                                 │
                                                                 ▼
                                                    [ Render Free PostgreSQL 16 ]
                                                          (1 GB Storage)
```

---

## 1. Prerequisites

1. A GitHub account with this repository pushed: `https://github.com/AfsarAI/assessment-opm`.
2. A free account on [Render.com](https://render.com) (no credit card required).
3. A free account on [Vercel.com](https://vercel.com) (no credit card required).

---

## 2. Deploying Backend & Database on Render (Automated Blueprint)

The repository includes a ready-to-deploy Render Blueprint definition in `render.yaml`.

### Step-by-Step Deployment:
1. Log into your **Render Dashboard** ([dashboard.render.com](https://dashboard.render.com)).
2. Click **New +** in the top navigation and select **Blueprint**.
3. Connect your GitHub repository (`AfsarAI/assessment-opm`).
4. Render will detect `render.yaml` and display the planned resources:
   - **Database**: `opm-database` (PostgreSQL 16, Free plan, 1 GB storage).
   - **Web Service**: `opm-backend` (Docker, Free plan, unified FastAPI + Celery + Embedded Redis).
5. Click **Apply**:
   - Render automatically provisions the PostgreSQL instance.
   - Builds the Docker image from `backend/Dockerfile`.
   - Executes `entrypoint.sh`:
     1. Starts embedded Redis on `127.0.0.1:6379`.
     2. Runs database migrations: `alembic upgrade head`.
     3. Starts the Celery worker process.
     4. Binds Uvicorn to `${PORT}`.
6. Once deployed, note down your public backend URL:
   - Example: `https://opm-backend-xxxx.onrender.com`.
7. Verify backend health by visiting:
   - `https://<your-render-url>/health` $\rightarrow$ `{"status": "ok"}`
   - `https://<your-render-url>/docs` $\rightarrow$ Interactive Swagger UI.

---

## 3. Deploying Frontend on Vercel

### Step-by-Step Deployment:
1. Log into your **Vercel Dashboard** ([vercel.com](https://vercel.com)).
2. Click **Add New...** $\rightarrow$ **Project**.
3. Import your GitHub repository (`AfsarAI/assessment-opm`).
4. In the project configuration screen:
   - **Framework Preset**: Next.js (automatically detected).
   - **Root Directory**: Click **Edit** and select `frontend`.
5. Under **Environment Variables**, add:
   - `NEXT_PUBLIC_API_URL`: Set to your Render backend API URL (e.g. `https://opm-backend-xxxx.onrender.com/api/v1`).
   - `BACKEND_INTERNAL_URL`: Set to your Render backend root URL (e.g. `https://opm-backend-xxxx.onrender.com`).
6. Click **Deploy**:
   - Vercel builds the static and edge bundles in ~45 seconds.
   - Deploys to a global HTTPS domain: `https://assessment-opm-xxxx.vercel.app`.

---

## 4. Post-Deployment Verification

1. Open the Vercel frontend URL in your browser.
2. The top indicator should display **System Ready** (green).
3. Navigate to **Import CSV**, upload `scripts/samples/products_100.csv` or `products.csv`.
4. Observe the live animated progress bar via Server-Sent Events.
5. Search by SKU, test pagination, toggle product active state, and test webhooks.

---

## 5. Free-Tier Operational Characteristics

- **Inactivity Spin-down**: Render's free web service spins down after 15 minutes of inactivity. The first request after sleep takes ~30–50 seconds to boot.
- **SSE Persistence**: Active SSE streaming connections during an import keep the HTTP connection active, preventing spin-down mid-import.
- **Database Limits**: Render Free PostgreSQL provides 1 GB storage. The 500,000 product catalog uses 194 MB, leaving over 700 MB of free headroom.
