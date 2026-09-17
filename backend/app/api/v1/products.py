from typing import Optional
from fastapi import APIRouter, Depends, Query, status, Response
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.database import get_async_db
from app.schemas.product import (
    ProductCreate,
    ProductUpdate,
    ProductResponse,
    ProductListResponse,
)
from app.schemas.common import SuccessResponse
from app.services.product_service import ProductService
from app.core.exceptions import ConfirmationRequiredException

router = APIRouter(prefix="/products", tags=["Products"])


@router.get("", response_model=ProductListResponse)
async def list_products(
    page: int = Query(1, ge=1, description="Page number"),
    limit: int = Query(50, ge=1, le=100, description="Items per page"),
    sku: Optional[str] = Query(None, description="Filter by SKU (case-insensitive substring)"),
    name: Optional[str] = Query(None, description="Filter by product name (case-insensitive substring)"),
    description: Optional[str] = Query(None, description="Filter by description"),
    status: Optional[str] = Query(None, description="Filter by status: 'active', 'inactive', or 'all'"),
    sort_by: str = Query("created_at", description="Sort field: 'created_at', 'sku', 'name'"),
    sort_order: str = Query("desc", description="Sort direction: 'asc' or 'desc'"),
    db: AsyncSession = Depends(get_async_db),
):
    """Retrieve a paginated list of products with multi-attribute filtering."""
    items, pagination = await ProductService.list_products(
        db=db,
        page=page,
        limit=limit,
        sku=sku,
        name=name,
        description=description,
        status=status,
        sort_by=sort_by,
        sort_order=sort_order,
    )
    return ProductListResponse(items=items, pagination=pagination)


@router.post("", response_model=ProductResponse, status_code=status.HTTP_201_CREATED)
async def create_product(
    product_in: ProductCreate,
    db: AsyncSession = Depends(get_async_db),
):
    """Create a new product with case-insensitive unique SKU enforcement."""
    product = await ProductService.create_product(db, product_in)
    
    # Asynchronously dispatch webhook event if configured
    try:
        from app.tasks.webhook_tasks import dispatch_webhook_event
        dispatch_webhook_event.delay(
            "product.created",
            {
                "id": product.id,
                "sku": product.sku,
                "name": product.name,
                "description": product.description,
                "active": product.active,
            }
        )
    except Exception:
        pass  # Webhook dispatch failure must never fail the product creation transaction

    return product


@router.get("/{product_id}", response_model=ProductResponse)
async def get_product(
    product_id: int,
    db: AsyncSession = Depends(get_async_db),
):
    """Get a single product by its primary key ID."""
    return await ProductService.get_by_id(db, product_id)


@router.patch("/{product_id}", response_model=ProductResponse)
async def update_product(
    product_id: int,
    product_in: ProductUpdate,
    db: AsyncSession = Depends(get_async_db),
):
    """Update product fields. Preserves active status if not explicitly provided."""
    product = await ProductService.update_product(db, product_id, product_in)

    # Asynchronously dispatch webhook event
    try:
        from app.tasks.webhook_tasks import dispatch_webhook_event
        dispatch_webhook_event.delay(
            "product.updated",
            {
                "id": product.id,
                "sku": product.sku,
                "name": product.name,
                "description": product.description,
                "active": product.active,
            }
        )
    except Exception:
        pass

    return product


@router.delete("/{product_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_product(
    product_id: int,
    db: AsyncSession = Depends(get_async_db),
):
    """Delete a single product with confirmation required on the client."""
    product = await ProductService.get_by_id(db, product_id)
    deleted_sku = product.sku
    await ProductService.delete_product(db, product_id)

    # Asynchronously dispatch webhook event
    try:
        from app.tasks.webhook_tasks import dispatch_webhook_event
        dispatch_webhook_event.delay(
            "product.deleted",
            {"id": product_id, "sku": deleted_sku}
        )
    except Exception:
        pass

    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete("", response_model=SuccessResponse)
async def delete_all_products(
    confirm: bool = Query(False, description="Must be true to permanently delete all products"),
    db: AsyncSession = Depends(get_async_db),
):
    """Permanently delete/truncate all products. Requires confirm=true query parameter."""
    if not confirm:
        raise ConfirmationRequiredException(
            "Deleting all products requires explicit confirmation parameter 'confirm=true'."
        )

    deleted_count = await ProductService.delete_all_products(db)

    # Asynchronously dispatch webhook event
    try:
        from app.tasks.webhook_tasks import dispatch_webhook_event
        dispatch_webhook_event.delay(
            "products.cleared",
            {"cleared_count": deleted_count}
        )
    except Exception:
        pass

    return SuccessResponse(
        success=True,
        message=f"Successfully truncated all products ({deleted_count:,} records removed).",
    )
