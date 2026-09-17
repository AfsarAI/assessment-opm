import uuid
from datetime import datetime
from typing import List, Optional
from pydantic import BaseModel, ConfigDict, Field, field_validator
from app.models.webhook import SUPPORTED_WEBHOOK_EVENTS


class WebhookBase(BaseModel):
    url: str = Field(..., description="Target destination HTTP/HTTPS endpoint")
    events: List[str] = Field(..., description="List of subscribed event names")
    enabled: bool = Field(default=True, description="Whether the webhook is active")
    secret: Optional[str] = Field(default=None, description="Optional secret key for signature headers")

    @field_validator("events")
    @classmethod
    def validate_events(cls, events: List[str]) -> List[str]:
        if not events:
            raise ValueError("At least one subscribed event is required.")
        for ev in events:
            if ev not in SUPPORTED_WEBHOOK_EVENTS:
                raise ValueError(
                    f"Unsupported event '{ev}'. Supported events are: {', '.join(SUPPORTED_WEBHOOK_EVENTS)}"
                )
        return list(set(events))


class WebhookCreate(WebhookBase):
    pass


class WebhookUpdate(BaseModel):
    url: Optional[str] = None
    events: Optional[List[str]] = None
    enabled: Optional[bool] = None
    secret: Optional[str] = None

    @field_validator("events")
    @classmethod
    def validate_events(cls, events: Optional[List[str]]) -> Optional[List[str]]:
        if events is not None:
            if not events:
                raise ValueError("At least one subscribed event is required.")
            for ev in events:
                if ev not in SUPPORTED_WEBHOOK_EVENTS:
                    raise ValueError(
                        f"Unsupported event '{ev}'. Supported events are: {', '.join(SUPPORTED_WEBHOOK_EVENTS)}"
                    )
            return list(set(events))
        return events


class WebhookResponse(WebhookBase):
    id: uuid.UUID
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class WebhookTestResult(BaseModel):
    success: bool
    status_code: Optional[int] = None
    response_time_ms: float
    message: str
    response_body: Optional[str] = None
