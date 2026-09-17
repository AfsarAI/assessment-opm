import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_create_product_success(client: AsyncClient):
    payload = {
        "sku": "PROD-001",
        "name": "Wireless Mechanical Keyboard",
        "description": "RGB backlight with blue switches",
        "active": True,
    }
    response = await client.post("/api/v1/products", json=payload)
    assert response.status_code == 201
    data = response.json()
    assert data["sku"] == "PROD-001"
    assert data["name"] == "Wireless Mechanical Keyboard"
    assert data["active"] is True
    assert "id" in data


@pytest.mark.asyncio
async def test_create_product_case_insensitive_duplicate_sku(client: AsyncClient):
    # 1. Create product with UPPERCASE SKU
    res1 = await client.post(
        "/api/v1/products",
        json={"sku": "KEYBOARD-RGB", "name": "RGB Keyboard", "description": "Desc 1"},
    )
    assert res1.status_code == 201

    # 2. Attempt to create product with lowercase SKU
    res2 = await client.post(
        "/api/v1/products",
        json={"sku": "keyboard-rgb", "name": "Another Keyboard", "description": "Desc 2"},
    )
    assert res2.status_code == 409
    err = res2.json()["error"]
    assert err["code"] == "DUPLICATE_SKU"

    # 3. Attempt with mixed case
    res3 = await client.post(
        "/api/v1/products",
        json={"sku": "KeyBoard-RGB", "name": "Mixed Keyboard", "description": "Desc 3"},
    )
    assert res3.status_code == 409
    assert res3.json()["error"]["code"] == "DUPLICATE_SKU"


@pytest.mark.asyncio
async def test_get_product_by_id(client: AsyncClient):
    res = await client.post(
        "/api/v1/products",
        json={"sku": "MOUSE-01", "name": "Ergonomic Mouse", "description": "Laser sensor"},
    )
    product_id = res.json()["id"]

    get_res = await client.get(f"/api/v1/products/{product_id}")
    assert get_res.status_code == 200
    assert get_res.json()["sku"] == "MOUSE-01"

    # Non-existent
    not_found = await client.get("/api/v1/products/999999")
    assert not_found.status_code == 404
    assert not_found.json()["error"]["code"] == "PRODUCT_NOT_FOUND"


@pytest.mark.asyncio
async def test_update_product(client: AsyncClient):
    res = await client.post(
        "/api/v1/products",
        json={"sku": "MONITOR-4K", "name": "4K Display", "description": "60Hz IPS", "active": True},
    )
    product_id = res.json()["id"]

    # Update name and deactivate
    patch_res = await client.patch(
        f"/api/v1/products/{product_id}",
        json={"name": "4K Ultra Display", "active": False},
    )
    assert patch_res.status_code == 200
    updated = patch_res.json()
    assert updated["name"] == "4K Ultra Display"
    assert updated["active"] is False
    assert updated["sku"] == "MONITOR-4K"  # Unchanged


@pytest.mark.asyncio
async def test_update_product_duplicate_sku_conflict(client: AsyncClient):
    await client.post("/api/v1/products", json={"sku": "SKU-A", "name": "Product A"})
    p2 = await client.post("/api/v1/products", json={"sku": "SKU-B", "name": "Product B"})
    p2_id = p2.json()["id"]

    # Try renaming Product B to SKU-A (lowercase)
    patch_res = await client.patch(f"/api/v1/products/{p2_id}", json={"sku": "sku-a"})
    assert patch_res.status_code == 409
    assert patch_res.json()["error"]["code"] == "DUPLICATE_SKU"


@pytest.mark.asyncio
async def test_delete_product(client: AsyncClient):
    res = await client.post("/api/v1/products", json={"sku": "TO-DELETE", "name": "Delete Me"})
    product_id = res.json()["id"]

    del_res = await client.delete(f"/api/v1/products/{product_id}")
    assert del_res.status_code == 204

    get_res = await client.get(f"/api/v1/products/{product_id}")
    assert get_res.status_code == 404


@pytest.mark.asyncio
async def test_product_filtering_and_pagination(client: AsyncClient):
    # Seed test products
    products_to_seed = [
        {"sku": "LAPTOP-PRO-1", "name": "Apple MacBook Pro", "description": "M3 chip", "active": True},
        {"sku": "LAPTOP-AIR-1", "name": "Apple MacBook Air", "description": "M2 chip", "active": True},
        {"sku": "PHONE-IPHONE", "name": "Apple iPhone 15", "description": "OLED screen", "active": False},
        {"sku": "HEADPHONES-01", "name": "Sony WH-1000XM5", "description": "Noise cancelling", "active": True},
    ]
    for p in products_to_seed:
        await client.post("/api/v1/products", json=p)

    # Filter by name "Apple"
    res_apple = await client.get("/api/v1/products?name=apple")
    assert res_apple.status_code == 200
    data = res_apple.json()
    assert data["pagination"]["total"] == 3
    assert len(data["items"]) == 3

    # Filter by status "active"
    res_active = await client.get("/api/v1/products?status=active")
    assert res_active.status_code == 200
    assert res_active.json()["pagination"]["total"] == 3

    # Filter by status "inactive"
    res_inactive = await client.get("/api/v1/products?status=inactive")
    assert res_inactive.status_code == 200
    assert res_inactive.json()["pagination"]["total"] == 1
    assert res_inactive.json()["items"][0]["sku"] == "PHONE-IPHONE"

    # Pagination: limit=2, page=1
    res_page1 = await client.get("/api/v1/products?limit=2&page=1")
    assert res_page1.status_code == 200
    page1_data = res_page1.json()
    assert len(page1_data["items"]) == 2
    assert page1_data["pagination"]["has_next"] is True
    assert page1_data["pagination"]["total"] == 4


@pytest.mark.asyncio
async def test_delete_all_products(client: AsyncClient):
    await client.post("/api/v1/products", json={"sku": "P1", "name": "Item 1"})
    await client.post("/api/v1/products", json={"sku": "P2", "name": "Item 2"})

    # Without confirmation -> 400
    res_no_confirm = await client.delete("/api/v1/products")
    assert res_no_confirm.status_code == 400
    assert res_no_confirm.json()["error"]["code"] == "CONFIRMATION_REQUIRED"

    # With confirm=true -> 200
    res_confirm = await client.delete("/api/v1/products?confirm=true")
    assert res_confirm.status_code == 200
    assert res_confirm.json()["success"] is True

    # Verify table is now empty
    list_res = await client.get("/api/v1/products")
    assert list_res.json()["pagination"]["total"] == 0
