import io
import uuid
import pytest
from httpx import AsyncClient
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.import_job import ImportJob
from app.models.product import Product
from app.services.import_service import execute_import_pipeline


@pytest.mark.asyncio
async def test_upload_invalid_file_extension(client: AsyncClient):
    files = {"file": ("test.txt", b"not a csv content", "text/plain")}
    response = await client.post("/api/v1/imports", files=files)
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "INVALID_CSV"


@pytest.mark.asyncio
async def test_upload_csv_creates_queued_job(client: AsyncClient, db_session: AsyncSession):
    csv_content = "name,sku,description\nTest Product,TEST-SKU-1,A test description\n"
    files = {"file": ("products.csv", csv_content.encode("utf-8"), "text/csv")}

    response = await client.post("/api/v1/imports", files=files)
    assert response.status_code == 202
    data = response.json()
    assert data["status"] == "QUEUED"
    assert "import_id" in data

    # Verify record in database
    job_uuid = uuid.UUID(data["import_id"])
    res = await db_session.execute(select(ImportJob).where(ImportJob.id == job_uuid))
    job = res.scalar_one_or_none()
    assert job is not None
    assert job.status == "QUEUED"


@pytest.mark.asyncio
async def test_execute_import_pipeline_end_to_end(client: AsyncClient, db_session: AsyncSession, tmp_path):
    # 1. Create temporary CSV with duplicates and case variants
    csv_file = tmp_path / "test_import.csv"
    content = (
        "name,sku,description\n"
        "Alpha Initial,ALPHA-01,Alpha initial description\n"
        "Alpha Updated,alpha-01,Alpha updated description (later row should win)\n"
        "Beta Product,BETA-01,Beta product description\n"
        "Malformed Row Missing Column\n"
    )
    csv_file.write_text(content, encoding="utf-8")

    # 2. Create job in DB
    job_id = uuid.uuid4()
    job = ImportJob(
        id=job_id,
        filename="test_import.csv",
        status="QUEUED",
    )
    db_session.add(job)
    await db_session.commit()

    # 3. Execute pipeline synchronously
    execute_import_pipeline(str(job_id), str(csv_file))

    # 4. Verify job status and counts
    db_session.expire_all()
    res = await db_session.execute(select(ImportJob).where(ImportJob.id == job_id))
    completed_job = res.scalar_one()
    assert completed_job.status in ("COMPLETED", "COMPLETED_WITH_ERRORS")
    assert completed_job.progress == 100
    assert completed_job.total_rows == 4
    assert completed_job.failed_rows == 1  # 1 malformed row

    # 5. Verify database products
    prod_res = await db_session.execute(select(Product).order_by(Product.sku))
    products = list(prod_res.scalars().all())

    # Should have exactly 2 products: alpha-01 (case deduplicated) and BETA-01
    assert len(products) == 2
    
    alpha = next(p for p in products if p.sku.lower() == "alpha-01")
    assert alpha.name == "Alpha Updated"
    assert "later row should win" in alpha.description
    assert alpha.active is True


@pytest.mark.asyncio
async def test_active_status_preserved_on_reimport(client: AsyncClient, db_session: AsyncSession, tmp_path):
    # 1. Manually create product with active = False
    existing = Product(
        sku="INACTIVE-SKU-99",
        name="Old Inactive Name",
        description="Old description",
        active=False,
    )
    db_session.add(existing)
    await db_session.commit()

    # 2. Re-import CSV containing the same SKU (with different casing)
    csv_file = tmp_path / "reimport.csv"
    csv_file.write_text(
        "name,sku,description\n"
        "New Name From CSV,inactive-sku-99,Updated description from supplier CSV\n",
        encoding="utf-8"
    )

    job_id = uuid.uuid4()
    job = ImportJob(id=job_id, filename="reimport.csv", status="QUEUED")
    db_session.add(job)
    await db_session.commit()

    execute_import_pipeline(str(job_id), str(csv_file))

    # 3. Verify product was updated in name & description, but active status remains False
    db_session.expire_all()
    res = await db_session.execute(select(Product).where(Product.sku.ilike("inactive-sku-99")))
    updated = res.scalar_one()
    assert updated.name == "New Name From CSV"
    assert updated.description == "Updated description from supplier CSV"
    assert updated.active is False  # CRITICAL: Preserved!


@pytest.mark.asyncio
async def test_get_import_job_and_progress_sse(client: AsyncClient, db_session: AsyncSession):
    job_id = uuid.uuid4()
    job = ImportJob(
        id=job_id,
        filename="batch.csv",
        status="COMPLETED",
        progress=100,
        total_rows=1000,
        processed_rows=1000,
        successful_rows=1000,
        failed_rows=0,
        stage_message="Import complete",
    )
    db_session.add(job)
    await db_session.commit()

    # GET details
    res = await client.get(f"/api/v1/imports/{job_id}")
    assert res.status_code == 200
    assert res.json()["status"] == "COMPLETED"
    assert res.json()["progress"] == 100

    # GET SSE progress
    sse_res = await client.get(f"/api/v1/imports/{job_id}/progress")
    assert sse_res.status_code == 200
    assert "text/event-stream" in sse_res.headers["content-type"]
    assert "event: progress" in sse_res.text
    assert '"status": "COMPLETED"' in sse_res.text
