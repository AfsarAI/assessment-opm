import time
import uuid
from typing import List, Optional
import httpx
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.models.webhook import Webhook
from app.schemas.webhook import WebhookCreate, WebhookUpdate, WebhookTestResult
from app.core.exceptions import WebhookNotFoundException, AppException
from app.services.ssrf_validator import validate_webhook_url


class WebhookService:
    @staticmethod
    async def list_webhooks(db: AsyncSession) -> List[Webhook]:
        stmt = select(Webhook).order_by(Webhook.created_at.desc())
        result = await db.execute(stmt)
        return list(result.scalars().all())

    @staticmethod
    async def get_by_id(db: AsyncSession, webhook_id: uuid.UUID) -> Webhook:
        stmt = select(Webhook).where(Webhook.id == webhook_id)
        result = await db.execute(stmt)
        webhook = result.scalar_one_or_none()
        if not webhook:
            raise WebhookNotFoundException(str(webhook_id))
        return webhook

    @staticmethod
    async def create_webhook(db: AsyncSession, webhook_in: WebhookCreate) -> Webhook:
        # SSRF Pre-flight check
        is_safe, error_reason = validate_webhook_url(webhook_in.url)
        if not is_safe:
            raise AppException(
                status_code=400,
                code="INVALID_WEBHOOK_URL",
                message=f"SSRF validation failed: {error_reason}",
            )

        webhook = Webhook(
            url=webhook_in.url,
            events=webhook_in.events,
            enabled=webhook_in.enabled,
            secret=webhook_in.secret,
        )
        db.add(webhook)
        await db.flush()
        await db.refresh(webhook)
        return webhook

    @staticmethod
    async def update_webhook(
        db: AsyncSession, webhook_id: uuid.UUID, webhook_in: WebhookUpdate
    ) -> Webhook:
        webhook = await WebhookService.get_by_id(db, webhook_id)
        data = webhook_in.model_dump(exclude_unset=True)

        if "url" in data and data["url"]:
            is_safe, error_reason = validate_webhook_url(data["url"])
            if not is_safe:
                raise AppException(
                    status_code=400,
                    code="INVALID_WEBHOOK_URL",
                    message=f"SSRF validation failed: {error_reason}",
                )
            webhook.url = data["url"]

        if "events" in data and data["events"] is not None:
            webhook.events = data["events"]
        if "enabled" in data and data["enabled"] is not None:
            webhook.enabled = data["enabled"]
        if "secret" in data:
            webhook.secret = data["secret"]

        await db.flush()
        await db.refresh(webhook)
        return webhook

    @staticmethod
    async def delete_webhook(db: AsyncSession, webhook_id: uuid.UUID) -> None:
        webhook = await WebhookService.get_by_id(db, webhook_id)
        await db.delete(webhook)
        await db.flush()

    @staticmethod
    async def test_webhook(db: AsyncSession, webhook_id: uuid.UUID) -> WebhookTestResult:
        webhook = await WebhookService.get_by_id(db, webhook_id)

        # 1. SSRF Safety Verification
        is_safe, error_reason = validate_webhook_url(webhook.url)
        if not is_safe:
            return WebhookTestResult(
                success=False,
                status_code=None,
                response_time_ms=0.0,
                message=f"SSRF protection blocked request: {error_reason}",
            )

        # 2. Dispatch interactive test payload
        payload = {
            "event": "webhook.test",
            "timestamp": int(time.time()),
            "data": {
                "message": "Test ping from Assessment OPM Webhook System",
                "webhook_id": str(webhook.id),
                "subscribed_events": webhook.events,
            },
        }

        headers = {
            "User-Agent": "Assessment-OPM-Webhook/1.0",
            "Content-Type": "application/json",
            "X-Webhook-Event": "webhook.test",
        }

        start_time = time.perf_counter()
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.post(webhook.url, json=payload, headers=headers)
                elapsed_ms = round((time.perf_counter() - start_time) * 1000, 2)
                body_snippet = resp.text[:300] if resp.text else ""

                if 200 <= resp.status_code < 300:
                    return WebhookTestResult(
                        success=True,
                        status_code=resp.status_code,
                        response_time_ms=elapsed_ms,
                        message=f"Webhook responded with HTTP {resp.status_code} in {elapsed_ms}ms.",
                        response_body=body_snippet,
                    )
                else:
                    return WebhookTestResult(
                        success=False,
                        status_code=resp.status_code,
                        response_time_ms=elapsed_ms,
                        message=f"Webhook returned non-2xx status code: {resp.status_code}.",
                        response_body=body_snippet,
                    )

        except httpx.TimeoutException:
            elapsed_ms = round((time.perf_counter() - start_time) * 1000, 2)
            return WebhookTestResult(
                success=False,
                status_code=None,
                response_time_ms=elapsed_ms,
                message="Connection timed out after 5.0 seconds.",
            )
        except Exception as e:
            elapsed_ms = round((time.perf_counter() - start_time) * 1000, 2)
            return WebhookTestResult(
                success=False,
                status_code=None,
                response_time_ms=elapsed_ms,
                message=f"Connection error: {str(e)}",
            )
