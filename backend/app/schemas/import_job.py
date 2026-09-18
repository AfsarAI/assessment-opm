import uuid
from datetime import datetime
from typing import Optional, List
from pydantic import BaseModel, ConfigDict, Field


class ImportErrorItem(BaseModel):
    row_number: int
    error_message: str
    raw_data: Optional[str] = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class ImportJobResponse(BaseModel):
    id: uuid.UUID
    filename: str
    status: str
    total_rows: int
    processed_rows: int
    successful_rows: int
    failed_rows: int
    progress: int
    stage_message: str
    error_message: Optional[str] = None
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class ImportJobDetailResponse(ImportJobResponse):
    errors: List[ImportErrorItem] = []


class ImportProgressPayload(BaseModel):
    import_id: str
    status: str
    progress: int
    processed_rows: int
    total_rows: int
    successful_rows: int
    failed_rows: int
    stage_message: str
    error_message: Optional[str] = None
    seq: Optional[int] = None
    timestamp: Optional[str] = None


class ImportCancelResponse(BaseModel):
    import_id: uuid.UUID
    status: str
    message: str

