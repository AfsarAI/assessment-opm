import uuid
import pytest
from httpx import AsyncClient
from unittest.mock import patch, AsyncMock
import httpx

from app.services.ssrf_validator import validate_webhook_url


@pytest.mark.asyncio
async def test_ssrf_validator_blocks_internal_ips():
    # Loopback
    is_safe, msg = validate_webhook_url("http://127.0.0.1:8080/hook")
    assert is_safe is False
    assert "SSRF protection" in msg or "restricted" in msg

    is_safe, msg = validate_webhook_url("http://localhost:3000/api")
    assert is_safe is False

    # Cloud metadata (AWS/GCP)
    is_safe, msg = validate_webhook_url("http://169.254.169.254/latest/meta-data")
    assert is_safe is False

    # RFC 1918 Private networks
    is_safe, msg = validate_webhook_url("http://10.0.0.1/notify")
    assert is_safe is False

    is_safe, msg = validate_webhook_url("http://192.168.1.100:5000/webhook")
    assert is_safe is False

    is_safe, msg = validate_webhook_url("http://172.16.0.5/hook")
    assert is_safe is False

    # Invalid scheme
    is_safe, msg = validate_webhook_url("ftp://example.com/file")
    assert is_safe is False
    assert "scheme" in msg


@pytest.mark.asyncio
async def test_create_webhook_success(client: AsyncClient):
    payload = {
        "url": "https://httpbin.org/post",
        "events": ["product.created", "product.deleted"],
        "enabled": True,
        "secret": "my-secret-key-123",
    }
    res = await client.post("/api/v1/webhooks", json=payload)
    assert res.status_code == 201
    data = res.json()
    assert data["url"] == "https://httpbin.org/post"
    assert set(data["events"]) == {"product.created", "product.deleted"}
    assert data["enabled"] is True
    assert "id" in data


@pytest.mark.asyncio
async def test_create_webhook_invalid_event(client: AsyncClient):
    payload = {
        "url": "https://example.com/webhook",
        "events": ["invalid.fake.event"],
    }
    res = await client.post("/api/v1/webhooks", json=payload)
    assert res.status_code == 422
    assert "Unsupported event" in res.text


@pytest.mark.asyncio
async def test_create_webhook_ssrf_blocked(client: AsyncClient):
    payload = {
        "url": "http://127.0.0.1:8000/internal",
        "events": ["product.created"],
    }
    res = await client.post("/api/v1/webhooks", json=payload)
    assert res.status_code == 400
    assert res.json()["error"]["code"] == "INVALID_WEBHOOK_URL"


@pytest.mark.asyncio
async def test_get_and_update_webhook(client: AsyncClient):
    create_res = await client.post(
        "/api/v1/webhooks",
        json={"url": "https://httpbin.org/post", "events": ["import.completed"]},
    )
    wh_id = create_res.json()["id"]

    # GET
    get_res = await client.get(f"/api/v1/webhooks/{wh_id}")
    assert get_res.status_code == 200
    assert get_res.json()["id"] == wh_id

    # PATCH
    patch_res = await client.patch(
        f"/api/v1/webhooks/{wh_id}",
        json={"enabled": False, "events": ["import.completed", "import.failed"]},
    )
    assert patch_res.status_code == 200
    assert patch_res.json()["enabled"] is False
    assert len(patch_res.json()["events"]) == 2


@pytest.mark.asyncio
async def test_delete_webhook(client: AsyncClient):
    create_res = await client.post(
        "/api/v1/webhooks",
        json={"url": "https://httpbin.org/post", "events": ["products.cleared"]},
    )
    wh_id = create_res.json()["id"]

    del_res = await client.delete(f"/api/v1/webhooks/{wh_id}")
    assert del_res.status_code == 204

    get_res = await client.get(f"/api/v1/webhooks/{wh_id}")
    assert get_res.status_code == 404


@pytest.mark.asyncio
async def test_webhook_test_endpoint_mocked(client: AsyncClient):
    create_res = await client.post(
        "/api/v1/webhooks",
        json={"url": "https://httpbin.org/post", "events": ["product.created"]},
    )
    wh_id = create_res.json()["id"]

    # Mock external HTTP call in webhook_service specifically
    mock_instance = AsyncMock()
    mock_instance.post.return_value = httpx.Response(
        status_code=200,
        text='{"status": "received"}',
        request=httpx.Request("POST", "https://httpbin.org/post"),
    )
    mock_instance.__aenter__.return_value = mock_instance
    mock_instance.__aexit__.return_value = None

    with patch("app.services.webhook_service.httpx.AsyncClient", return_value=mock_instance):
        test_res = await client.post(f"/api/v1/webhooks/{wh_id}/test")
        assert test_res.status_code == 200
        data = test_res.json()
        assert data["success"] is True
        assert data["status_code"] == 200
        assert data["response_time_ms"] >= 0
