# Assessment OPM: Production-Readiness & Hardening Plan

## Overview

This actionable plan outlines the exact engineering steps required to transition Assessment OPM from a local Docker Compose environment to a zero-cost, publicly deployed production system.

---

## 1. Production Architecture Hardening

### Step 1: Unified Production Container Entrypoint (`entrypoint.sh` / `supervisord.conf`)
- **Problem**: Cloud free tiers (Render, Koyeb) do not offer free background worker services. If backend and worker run on separate services, they do not share disk storage, breaking `/app/uploads/<job_id>.csv`.
- **Solution**:
  - Create a production multi-process entrypoint that can run:
    1. Database migration: `alembic upgrade head`
    2. Redis server (embedded lightweight `redis-server --daemonize yes` if external Redis URL not provided)
    3. Celery worker: `celery -A app.core.celery_app.celery_app worker --concurrency=2 -Q imports,webhooks,celery &`
    4. Uvicorn FastAPI server: `uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}`
  - Both web process and worker process run in the same container, sharing `/app/uploads` with zero object storage cost and zero latency.
  - Automatically respects the cloud platform's dynamic `$PORT`.

### Step 2: Next.js Dynamic API URL & Reverse Proxy
- **Problem**: Next.js inlines `NEXT_PUBLIC_API_URL` during `npm run build`.
- **Solution**:
  - Configure `frontend/next.config.ts` with API rewrites:
    ```ts
    async rewrites() {
      return [
        {
          source: "/api/v1/:path*",
          destination: `${process.env.BACKEND_INTERNAL_URL || "http://localhost:8000"}/api/v1/:path*`,
        },
      ];
    }
    ```
  - In `frontend/src/lib/api.ts`, default to relative `/api/v1` if running in the browser and no `NEXT_PUBLIC_API_URL` is set.
  - This allows the frontend to work automatically in any deployment scenario.

### Step 3: Webhook SSRF Hardening (Redirect Prevention)
- **Problem**: An attacker could provide a public URL that returns an HTTP 302 redirecting to `http://169.254.169.254`.
- **Solution**:
  - In `backend/app/services/webhook_service.py`, configure HTTP client with `follow_redirects=False` or re-validate redirect target URLs before following.

### Step 4: CORS Wildcard Handling for Cloud Domains
- **Problem**: Cloud hostnames (`*.onrender.com`, `*.vercel.app`) vary based on project naming.
- **Solution**:
  - In `backend/app/core/config.py`, support regex or wildcard matching for `CORS_ORIGINS` when in production.

### Step 5: Render & Vercel Automated Deployment Blueprints
- **Artifacts**:
  - `render.yaml`: Render Blueprint specifying:
    - PostgreSQL database (`opm-database`, free tier)
    - Unified Web service (`opm-backend`, Docker build, environment variables mapped)
  - `vercel.json`: Frontend deployment configuration.
  - `.github/workflows/ci.yml`: Automated GitHub Actions pipeline verifying migrations, unit tests, and frontend build on every pull request and push to `main`.

---

## 2. Implementation Checklist

- [ ] 1. Create `backend/entrypoint.sh` for dynamic `$PORT` and optional multi-process execution.
- [ ] 2. Update `backend/Dockerfile` to install `redis-server` (for standalone/single-container mode) and configure the unified entrypoint.
- [ ] 3. Update `backend/app/services/webhook_service.py` to prevent redirect-based SSRF.
- [ ] 4. Update `frontend/next.config.ts` and `frontend/src/lib/api.ts` for clean environment-agnostic API resolution.
- [ ] 5. Create `render.yaml` with zero-cost deployment definitions.
- [ ] 6. Create `.github/workflows/ci.yml` for continuous integration.
- [ ] 7. Verify local and container execution.
- [ ] 8. Commit and push changes to GitHub.
