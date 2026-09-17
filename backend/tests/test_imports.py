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
    from unittest.mock import patch

    csv_content = "name,sku,description\nTest Product,TEST-SKU-1,A test description\n"
    files = {"file": ("products.csv", csv_content.encode("utf-8"), "text/csv")}

    with patch("app.api.v1.imports.process_csv_import.delay") as mock_delay:
        response = await client.post("/api/v1/imports", files=files)
        assert response.status_code == 202
        data = response.json()
        assert data["status"] == "QUEUED"
        assert "import_id" in data
        mock_delay.assert_called_once()

    # Verify record in database
    job_uuid = uuid.UUID(data["import_id"])
    res = await db_session.execute(select(ImportJob).where(ImportJob.id == job_uuid))
    job = res.scalar_one_or_none()
    assert job is not None
    assert job.status == "QUEUED"


@pytest.mark.asyncio
async def test_execute_import_pipeline_end_to_end(client: AsyncClient, db_session: AsyncSession, tmp_path):
    from app.core.database import sync_engine
    with sync_engine.connect() as conn:
        with conn.begin():
            conn.execute(text("TRUNCATE TABLE products RESTART IDENTITY;"))

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


@pytest.mark.asyncio
async def test_case_insensitive_latest_wins_deduplication(client: AsyncClient, db_session: AsyncSession, tmp_path):
    await db_session.execute(text("TRUNCATE TABLE products RESTART IDENTITY;"))
    await db_session.commit()

    # Create CSV with 5 occurrences of the same SKU with varying casing
    csv_file = tmp_path / "case_dedup.csv"
    content = (
        "name,sku,description\n"
        "V1,ABC-999,First occurrence\n"
        "V2,abc-999,Second occurrence\n"
        "V3,Abc-999,Third occurrence\n"
        "V4,aBc-999,Fourth occurrence\n"
        "V5 WINNER,ABc-999,Final winner description\n"
    )
    csv_file.write_text(content, encoding="utf-8")

    job_id = uuid.uuid4()
    job = ImportJob(id=job_id, filename="case_dedup.csv", status="QUEUED")
    db_session.add(job)
    await db_session.commit()

    execute_import_pipeline(str(job_id), str(csv_file))

    db_session.expire_all()
    res = await db_session.execute(select(Product))
    products = list(res.scalars().all())

    # Must collapse to exactly 1 product
    assert len(products) == 1
    assert products[0].name == "V5 WINNER"
    assert products[0].description == "Final winner description"
    assert products[0].sku == "ABc-999"


@pytest.mark.asyncio
async def test_failed_import_does_not_report_staged_as_succeeded(client: AsyncClient, db_session: AsyncSession, tmp_path):
    from unittest.mock import patch

    csv_file = tmp_path / "faulty_import.csv"
    csv_file.write_text(
        "name,sku,description\n"
        "Item 1,SKU-1,Desc 1\n"
        "Item 2,SKU-2,Desc 2\n",
        encoding="utf-8"
    )

    job_id = uuid.uuid4()
    job = ImportJob(id=job_id, filename="faulty_import.csv", status="QUEUED")
    db_session.add(job)
    await db_session.commit()

    # Simulate an error specifically during catalogue merge after staging
    from app.services import import_service
    original_connect = import_service.sync_engine.connect

    class FailOnMergeConnection:
        def __init__(self, real_conn):
            self._real = real_conn
        def __getattr__(self, name):
            return getattr(self._real, name)
        def __enter__(self):
            self._real.__enter__()
            return self
        def __exit__(self, *args):
            return self._real.__exit__(*args)
        def execute(self, statement, *args, **kwargs):
            if "INSERT INTO products" in str(statement):
                raise Exception("Simulated SSL EOF / DB disconnect during merge")
            return self._real.execute(statement, *args, **kwargs)

    def mock_connect(*args, **kwargs):
        return FailOnMergeConnection(original_connect(*args, **kwargs))

    with pytest.raises(Exception):
        with patch.object(import_service.sync_engine, "connect", side_effect=mock_connect):
            execute_import_pipeline(str(job_id), str(csv_file))

    db_session.expire_all()
    res = await db_session.execute(select(ImportJob).where(ImportJob.id == job_id))
    failed_job = res.scalar_one()
    assert failed_job.status == "FAILED"
    # CRITICAL: successful_rows must be 0, NOT 2!
    assert failed_job.successful_rows == 0
    assert failed_job.error_message is not None


@pytest.mark.asyncio
async def test_cancel_import_endpoint_success(client: AsyncClient, db_session: AsyncSession):
    job_id = uuid.uuid4()
    job = ImportJob(id=job_id, filename="cancel_test.csv", status="QUEUED")
    db_session.add(job)
    await db_session.commit()

    response = await client.post(f"/api/v1/imports/{job_id}/cancel")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "CANCELLED"
    assert data["import_id"] == str(job_id)

    db_session.expire_all()
    res = await db_session.execute(select(ImportJob).where(ImportJob.id == job_id))
    cancelled_job = res.scalar_one()
    assert cancelled_job.status == "CANCELLED"
    assert cancelled_job.stage_message == "Import cancelled by user"
    assert cancelled_job.successful_rows == 0


@pytest.mark.asyncio
async def test_cancel_import_endpoint_idempotent(client: AsyncClient, db_session: AsyncSession):
    job_id = uuid.uuid4()
    job = ImportJob(id=job_id, filename="idempotent_cancel.csv", status="CANCELLED")
    db_session.add(job)
    await db_session.commit()

    # Second cancel call should return 200
    response = await client.post(f"/api/v1/imports/{job_id}/cancel")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "CANCELLED"
    assert "already cancelled" in data["message"].lower()


@pytest.mark.asyncio
async def test_cancel_import_endpoint_cannot_cancel_completed(client: AsyncClient, db_session: AsyncSession):
    job_id = uuid.uuid4()
    job = ImportJob(id=job_id, filename="done.csv", status="COMPLETED")
    db_session.add(job)
    await db_session.commit()

    response = await client.post(f"/api/v1/imports/{job_id}/cancel")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "COMPLETED"
    assert "already reached terminal status" in data["message"].lower()


@pytest.mark.asyncio
async def test_cancellation_during_execution_leaves_no_partial_data(client: AsyncClient, db_session: AsyncSession, tmp_path):
    from app.core.database import sync_engine
    with sync_engine.connect() as conn:
        with conn.begin():
            conn.execute(text("TRUNCATE TABLE products RESTART IDENTITY;"))

    csv_file = tmp_path / "cancel_run.csv"
    csv_file.write_text(
        "name,sku,description\n"
        "Cancel Item 1,CANCEL-01,Desc 1\n"
        "Cancel Item 2,CANCEL-02,Desc 2\n",
        encoding="utf-8"
    )

    job_id = uuid.uuid4()
    job = ImportJob(id=job_id, filename="cancel_run.csv", status="QUEUED")
    db_session.add(job)
    await db_session.commit()

    # Set cancellation flag in Redis directly
    from app.services.import_service import get_redis_client
    r = get_redis_client()
    r.setex(f"import_cancel:{job_id}", 60, "1")

    # Run pipeline
    result = execute_import_pipeline(str(job_id), str(csv_file))
    assert result == {"status": "CANCELLED"}

    # Verify job status in DB
    db_session.expire_all()
    res = await db_session.execute(select(ImportJob).where(ImportJob.id == job_id))
    cancelled_job = res.scalar_one()
    assert cancelled_job.status == "CANCELLED"
    assert cancelled_job.successful_rows == 0

    # Verify ZERO products were inserted
    prod_res = await db_session.execute(select(Product))
    products = list(prod_res.scalars().all())
    assert len(products) == 0

    # Verify staging and dedup tables do not exist
    clean_id = str(job_id).replace("-", "")
    with sync_engine.connect() as conn:
        stg_exists = conn.execute(text(f"SELECT to_regclass('staging_{clean_id}');")).scalar()
        ddp_exists = conn.execute(text(f"SELECT to_regclass('dedup_{clean_id}');")).scalar()
        assert stg_exists is None
        assert ddp_exists is None


@pytest.mark.asyncio
async def test_reimport_after_cancel_succeeds(client: AsyncClient, db_session: AsyncSession, tmp_path):
    csv_file = tmp_path / "valid_reimport.csv"
    csv_file.write_text(
        "name,sku,description\n"
        "Valid Product After Cancel,VAC-01,Description for valid product\n",
        encoding="utf-8"
    )

    job_id = uuid.uuid4()
    job = ImportJob(id=job_id, filename="valid_reimport.csv", status="QUEUED")
    db_session.add(job)
    await db_session.commit()

    execute_import_pipeline(str(job_id), str(csv_file))

    db_session.expire_all()
    res = await db_session.execute(select(ImportJob).where(ImportJob.id == job_id))
    completed_job = res.scalar_one()
    assert completed_job.status == "COMPLETED"
    assert completed_job.successful_rows == 1


