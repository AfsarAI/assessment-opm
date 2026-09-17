import logging
from app.core.celery_app import celery_app
from app.core.database import get_sync_db
from sqlalchemy import text

logger = logging.getLogger("webhook_tasks")


@celery_app.task(bind=True, name="app.tasks.webhook_tasks.dispatch_webhook_event")
def dispatch_webhook_event(self, event_type: str, payload: dict):
    """Find all enabled webhooks subscribed to event_type and trigger delivery."""
    logger.info(f"Dispatching webhook event '{event_type}'")
    try:
        with get_sync_db() as db:
            result = db.execute(
                text(
                    """
                    SELECT id, url, secret
                    FROM webhooks
                    WHERE enabled = TRUE AND :event = ANY(events);
                    """
                ),
                {"event": event_type},
            )
            matching_webhooks = result.fetchall()

        for wh in matching_webhooks:
            deliver_webhook.delay(str(wh.id), wh.url, event_type, payload, wh.secret)

    except Exception as e:
        logger.warning(f"Error finding webhooks for event {event_type}: {e}")


@celery_app.task(
    bind=True,
    name="app.tasks.webhook_tasks.deliver_webhook",
    max_retries=3,
    default_retry_delay=5,
)
def deliver_webhook(self, webhook_id: str, url: str, event_type: str, payload: dict, secret: str | None = None):
    import time
    import httpx
    from app.services.ssrf_validator import validate_webhook_url

    # Check SSRF safety
    is_safe, error_reason = validate_webhook_url(url)
    if not is_safe:
        logger.error(f"SSRF violation for webhook {webhook_id} (URL: {url}): {error_reason}")
        return {"status": "failed", "error": f"SSRF violation: {error_reason}"}

    headers = {
        "User-Agent": "Assessment-OPM-Webhook/1.0",
        "Content-Type": "application/json",
        "X-Webhook-Event": event_type,
        "X-Webhook-Delivery-Id": str(self.request.id),
        "X-Webhook-Timestamp": str(int(time.time())),
    }

    body_obj = {"event": event_type, "payload": payload}
    if secret:
        import hmac
        import hashlib
        import json
        body_bytes = json.dumps(body_obj).encode("utf-8")
        signature = hmac.new(secret.encode("utf-8"), body_bytes, hashlib.sha256).hexdigest()
        headers["X-Webhook-Signature"] = f"sha256={signature}"

    try:
        with httpx.Client(timeout=5.0, follow_redirects=False) as client:
            resp = client.post(url, json=body_obj, headers=headers)
            resp.raise_for_status()
            logger.info(f"Webhook {webhook_id} delivered successfully with status {resp.status_code}")
            return {"status": "success", "status_code": resp.status_code}
    except Exception as exc:
        logger.warning(f"Webhook delivery attempt {self.request.retries + 1} failed for {url}: {exc}")
        # Exponential backoff retry: 5s, 15s, 45s
        backoff = 5 * (3 ** self.request.retries)
        raise self.retry(exc=exc, countdown=backoff)
