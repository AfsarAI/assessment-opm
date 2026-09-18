# Assessment OPM: Frontend Application

A responsive, real-time web interface for **Assessment OPM**, built with **Next.js 16 (App Router)**, **TypeScript**, and **Tailwind CSS**.

---

## Key Features

1. **Interactive Dashboard (`Dashboard.tsx`)**:
   - Real-time catalog metrics (total products, active ratio, recent imports).
   - System health status indicator communicating directly with backend `/health` and `/ready` endpoints.
2. **CSV Import Manager (`ImportManager.tsx`)**:
   - Drag-and-drop CSV file uploader supporting up to 250 MB.
   - Real-time progress bar powered by **Server-Sent Events (SSE)** with monotonic progress guarantees ($P_{t+1} \ge P_t$) and sequence number tracking.
   - Stage indicators: *Parsing CSV*, *Validating & Staging*, *Catalogue UPSERT*, *Rebuilding Indexes*, and *Completed*.
   - Dynamic import cancellation with instant backend process termination (`pg_cancel_backend`).
   - Recent import jobs table with real-time status updates and modal error inspector.
3. **Product Catalog Manager (`ProductManager.tsx`)**:
   - Server-side paginated product table (10, 25, 50, 100 per page).
   - Real-time search by SKU or Name powered by PostgreSQL `pg_trgm` GIN indexes.
   - Status filtering (All, Active, Inactive) and multi-column sorting.
   - Single-product CRUD modals with client & server validation (409 Conflict handling for duplicate SKUs).
   - Instant active/inactive status toggle.
   - Safe bulk catalogue clear with typed confirmation (`DELETE ALL`).
4. **Webhook Manager (`WebhookManager.tsx`)**:
   - Create, edit, and delete webhook subscriptions for event notifications (`product.created`, `product.updated`, `product.deleted`, `import.completed`, `products.cleared`).
   - Secret key generator and display for HMAC-SHA256 signature verification.
   - Interactive webhook test trigger with real-time HTTP response status and latency reporting.

---

## Architecture & State Management

- **Next.js App Router**: Optimized layout and page structure located in `src/app/`.
- **Server-Sent Events (`EventSource`)**: Unidirectional real-time telemetry stream from `GET /api/v1/imports/{id}/progress`.
- **Monotonic Progress Engine**: Out-of-order SSE events are discarded using sequence numbers (`seq`). Both SSE and fallback polling enforce `Math.max(prev, current)` so the progress bar never regresses.
- **API Client (`src/lib/api.ts`)**: Strongly typed Axios client with centralized error handling and dynamic base URL resolution.

---

## Local Development

### Prerequisites
- Node.js 20+ LTS
- npm 10+

### Installation & Running
```bash
# 1. Install dependencies
npm install

# 2. Configure environment (optional, defaults to http://localhost:8000/api/v1)
cp .env.example .env.local

# 3. Start development server with Turbopack
npm run dev
```

Visit [http://localhost:3000](http://localhost:3000) in your browser.

---

## Build & Quality Checks

```bash
# Run TypeScript type check
npx tsc --noEmit

# Build production bundle with Turbopack
npm run build

# Start production server
npm start
```

---

## Environment Variables

| Variable | Description | Default |
| :--- | :--- | :--- |
| `NEXT_PUBLIC_API_URL` | Public backend API URL accessible by the browser | `http://localhost:8000/api/v1` |
| `PORT` | Local dev server port | `3000` |
