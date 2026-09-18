from typing import Optional, Tuple, List
import math
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, text, delete
from sqlalchemy.exc import IntegrityError
from app.models.product import Product
from app.schemas.product import ProductCreate, ProductUpdate
from app.schemas.common import PaginationMetadata
from app.core.exceptions import (
    ProductNotFoundException,
    DuplicateSkuException,
)


class ProductService:
    @staticmethod
    async def get_by_id(db: AsyncSession, product_id: int) -> Product:
        query = select(Product).where(Product.id == product_id)
        result = await db.execute(query)
        product = result.scalar_one_or_none()
        if not product:
            raise ProductNotFoundException(product_id)
        return product

    @staticmethod
    async def get_by_sku_case_insensitive(db: AsyncSession, sku: str) -> Optional[Product]:
        query = select(Product).where(func.lower(Product.sku) == sku.strip().lower())
        result = await db.execute(query)
        return result.scalar_one_or_none()

    @staticmethod
    async def list_products(
        db: AsyncSession,
        page: int = 1,
        limit: int = 50,
        sku: Optional[str] = None,
        name: Optional[str] = None,
        description: Optional[str] = None,
        status: Optional[str] = None,
        sort_by: str = "created_at",
        sort_order: str = "desc",
    ) -> Tuple[List[Product], PaginationMetadata]:
        # Enforce bounds
        page = max(1, page)
        limit = min(max(1, limit), 100)
        offset = (page - 1) * limit

        # Base filter conditions with GIN trigram index utilization
        conditions = []
        if sku:
            conditions.append(Product.sku.ilike(f"%{sku.strip()}%"))
        if name:
            conditions.append(Product.name.ilike(f"%{name.strip()}%"))
        if description:
            conditions.append(Product.description.ilike(f"%{description.strip()}%"))
        if status and status.lower() in ("active", "true"):
            conditions.append(Product.active.is_(True))
        elif status and status.lower() in ("inactive", "false"):
            conditions.append(Product.active.is_(False))

        # Count total matching records
        count_stmt = select(func.count(Product.id))
        if conditions:
            count_stmt = count_stmt.where(*conditions)
        total_result = await db.execute(count_stmt)
        total = total_result.scalar_one()

        # Query products
        stmt = select(Product)
        if conditions:
            stmt = stmt.where(*conditions)

        # Safe Sorting with whitelisted columns
        allowed_sort_fields = {"id", "sku", "name", "active", "created_at", "updated_at"}
        safe_sort_by = sort_by if sort_by in allowed_sort_fields else "created_at"
        sort_column = getattr(Product, safe_sort_by, Product.created_at)
        if sort_order.lower() == "asc":
            stmt = stmt.order_by(sort_column.asc())
        else:
            stmt = stmt.order_by(sort_column.desc())

        # Pagination
        stmt = stmt.offset(offset).limit(limit)
        result = await db.execute(stmt)
        items = list(result.scalars().all())

        total_pages = math.ceil(total / limit) if total > 0 else 1
        pagination = PaginationMetadata(
            total=total,
            page=page,
            limit=limit,
            total_pages=total_pages,
            has_next=page < total_pages,
            has_prev=page > 1,
        )

        return items, pagination

    @staticmethod
    async def create_product(db: AsyncSession, product_in: ProductCreate) -> Product:
        # Check case-insensitive uniqueness before insert
        existing = await ProductService.get_by_sku_case_insensitive(db, product_in.sku)
        if existing:
            raise DuplicateSkuException(product_in.sku)

        product = Product(
            sku=product_in.sku,
            name=product_in.name,
            description=product_in.description,
            active=product_in.active,
        )
        db.add(product)
        try:
            await db.flush()
            await db.refresh(product)
        except IntegrityError:
            await db.rollback()
            raise DuplicateSkuException(product_in.sku)

        return product

    @staticmethod
    async def update_product(
        db: AsyncSession, product_id: int, product_in: ProductUpdate
    ) -> Product:
        product = await ProductService.get_by_id(db, product_id)

        update_data = product_in.model_dump(exclude_unset=True)
        if not update_data:
            return product

        # If updating SKU, ensure case-insensitive uniqueness against OTHER products
        if "sku" in update_data and update_data["sku"]:
            new_sku = update_data["sku"].strip()
            existing = await ProductService.get_by_sku_case_insensitive(db, new_sku)
            if existing and existing.id != product.id:
                raise DuplicateSkuException(new_sku)
            product.sku = new_sku

        if "name" in update_data and update_data["name"]:
            product.name = update_data["name"].strip()
        if "description" in update_data and update_data["description"] is not None:
            product.description = update_data["description"].strip()
        if "active" in update_data and update_data["active"] is not None:
            product.active = update_data["active"]

        try:
            await db.flush()
            await db.refresh(product)
        except IntegrityError:
            await db.rollback()
            raise DuplicateSkuException(product.sku)

        return product

    @staticmethod
    async def delete_product(db: AsyncSession, product_id: int) -> None:
        product = await ProductService.get_by_id(db, product_id)
        await db.delete(product)
        await db.flush()

    @staticmethod
    async def delete_all_products(db: AsyncSession) -> int:
        """Efficiently truncate products table."""
        count_res = await db.execute(select(func.count(Product.id)))
        count = count_res.scalar_one()

        # Fast TRUNCATE in PostgreSQL
        await db.execute(text("TRUNCATE TABLE products RESTART IDENTITY;"))
        await db.flush()
        return count
