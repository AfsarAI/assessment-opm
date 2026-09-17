#!/bin/bash
set -e

echo "=== Assessment OPM Production Entrypoint Starting ==="

PORT="${PORT:-8000}"
SERVICE_TYPE="${SERVICE_TYPE:-all}" # 'all', 'web', 'worker'

# 1. Start embedded Redis if REDIS_URL targets localhost or 127.0.0.1
if [[ "$REDIS_URL" == *"localhost"* ]] || [[ "$REDIS_URL" == *"127.0.0.1"* ]] || [[ -z "$REDIS_URL" ]]; then
    if command -v redis-server >/dev/null 2>&1; then
        echo "Starting embedded Redis server on 127.0.0.1:6379..."
        redis-server --daemonize yes --bind 127.0.0.1 --port 6379 --save "" --appendonly no
        # Wait for Redis ready
        until redis-cli -h 127.0.0.1 -p 6379 ping >/dev/null 2>&1; do
            echo "Waiting for Redis to accept connections..."
            sleep 0.2
        done
        echo "✓ Embedded Redis server is ready."
    else
        echo "Warning: redis-server binary not found, relying on external REDIS_URL."
    fi
fi

# 2. Run Database Migrations (only from web or all)
if [ "$SERVICE_TYPE" = "all" ] || [ "$SERVICE_TYPE" = "web" ]; then
    echo "Running Alembic database migrations..."
    alembic upgrade head
    echo "✓ Database migrations applied."
fi

# 3. Handle service types
if [ "$SERVICE_TYPE" = "worker" ]; then
    echo "Starting standalone Celery worker..."
    exec celery -A app.core.celery_app.celery_app worker --loglevel=info --concurrency=1 -Q imports,webhooks,celery
elif [ "$SERVICE_TYPE" = "web" ]; then
    echo "Starting standalone Uvicorn API server on port ${PORT}..."
    exec uvicorn app.main:app --host 0.0.0.0 --port "${PORT}"
else
    # Unified 'all' mode: Run Celery worker in background, Uvicorn in foreground
    echo "Starting Celery worker in background (concurrency=1)..."
    celery -A app.core.celery_app.celery_app worker --loglevel=info --concurrency=1 -Q imports,webhooks,celery &
    WORKER_PID=$!
    echo "✓ Celery worker started with PID ${WORKER_PID}."

    # Trap termination signals to gracefully shut down both worker and web server
    cleanup() {
        echo "Received termination signal. Shutting down worker PID ${WORKER_PID}..."
        kill -TERM "$WORKER_PID" 2>/dev/null || true
        wait "$WORKER_PID" 2>/dev/null || true
        echo "All processes terminated cleanly."
    }
    trap cleanup SIGTERM SIGINT

    echo "Starting Uvicorn API server on port ${PORT}..."
    uvicorn app.main:app --host 0.0.0.0 --port "${PORT}" &
    UVICORN_PID=$!

    # Wait for any process to exit
    wait -n "$UVICORN_PID" "$WORKER_PID"
    cleanup
fi
