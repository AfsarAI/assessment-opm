import uuid
from typing import List
from fastapi import APIRouter, Depends, status, Response
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.database import get_async_db
from app.schemas.webhook import (
    WebhookCreate,
    WebhookUpdate,
    WebhookResponse,
    WebhookTestResult,
)
from app.services.webhook_service import WebhookService

router = APIRouter(prefix="/webhooks", tags=["Webhooks"])


@router.get("", response_model=List[WebhookResponse])
async def list_webhooks(db: AsyncSession = Depends(get_async_db)):
    """List all registered webhook subscriptions."""
    return await WebhookService.list_webhooks(db)


@router.post("", response_model=WebhookResponse, status_code=status.HTTP_201_CREATED)
async def create_webhook(
    webhook_in: WebhookCreate,
    db: AsyncSession = Depends(get_async_db),
):
    """Register a new webhook subscription with SSRF validation."""
    return await WebhookService.create_webhook(db, webhook_in)


@router.get("/{webhook_id}", response_model=WebhookResponse)
async def get_webhook(
    webhook_id: uuid.UUID,
    db: AsyncSession = Depends(get_async_db),
):
    """Retrieve details for a specific webhook."""
    return await WebhookService.get_by_id(db, webhook_id)


@router.patch("/{webhook_id}", response_model=WebhookResponse)
async def update_webhook(
    webhook_id: uuid.UUID,
    webhook_in: WebhookUpdate,
    db: AsyncSession = Depends(get_async_db),
):
    """Update webhook configuration, active state, or subscribed events."""
    return await WebhookService.update_webhook(db, webhook_id, webhook_in)


@router.delete("/{webhook_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_webhook(
    webhook_id: uuid.UUID,
    db: AsyncSession = Depends(get_async_db),
):
    """Delete a webhook subscription."""
    await WebhookService.delete_webhook(db, webhook_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{webhook_id}/test", response_model=WebhookTestResult)
async def test_webhook(
    webhook_id: uuid.UUID,
    db: AsyncSession = Depends(get_async_db),
):
    """
    Execute an interactive webhook test. Sends a test payload, measures latency in milliseconds,
    and returns HTTP status code and response summary. Protected against SSRF.
    """
    return await WebhookService.test_webhook(db, webhook_id)
