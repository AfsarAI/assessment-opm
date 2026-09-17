from typing import Any, Dict, Optional
from fastapi import HTTPException, status


class AppException(HTTPException):
    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        headers: Optional[Dict[str, Any]] = None,
    ):
        super().__init__(status_code=status_code, detail=message, headers=headers)
        self.code = code
        self.message = message


class ProductNotFoundException(AppException):
    def __init__(self, product_id: int):
        super().__init__(
            status_code=status.HTTP_404_NOT_FOUND,
            code="PRODUCT_NOT_FOUND",
            message=f"Product with ID {product_id} was not found.",
        )


class DuplicateSkuException(AppException):
    def __init__(self, sku: str):
        super().__init__(
            status_code=status.HTTP_409_CONFLICT,
            code="DUPLICATE_SKU",
            message=f"A product with SKU '{sku}' already exists (SKU comparison is case-insensitive).",
        )


class InvalidProductException(AppException):
    def __init__(self, message: str):
        super().__init__(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            code="INVALID_PRODUCT",
            message=message,
        )


class ConfirmationRequiredException(AppException):
    def __init__(self, message: str = "Explicit confirmation query parameter 'confirm=true' is required."):
        super().__init__(
            status_code=status.HTTP_400_BAD_REQUEST,
            code="CONFIRMATION_REQUIRED",
            message=message,
        )


class ImportNotFoundException(AppException):
    def __init__(self, import_id: str):
        super().__init__(
            status_code=status.HTTP_404_NOT_FOUND,
            code="IMPORT_NOT_FOUND",
            message=f"Import job with ID {import_id} was not found.",
        )


class InvalidCsvException(AppException):
    def __init__(self, message: str):
        super().__init__(
            status_code=status.HTTP_400_BAD_REQUEST,
            code="INVALID_CSV",
            message=message,
        )


class WebhookNotFoundException(AppException):
    def __init__(self, webhook_id: str):
        super().__init__(
            status_code=status.HTTP_404_NOT_FOUND,
            code="WEBHOOK_NOT_FOUND",
            message=f"Webhook with ID {webhook_id} was not found.",
        )
