"""Focused tests for migrations, JWT ownership, and request tracing."""

import logging

import pytest
from httpx import ASGITransport, AsyncClient

from app import config
from app.api.server import app
from app.db import store as store_db
from app.db.migrations import upgrade_database
from app.db.orders import init_database


@pytest.fixture
def v2_database(tmp_path, monkeypatch):
    path = tmp_path / "backend-v2.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{path.as_posix()}")
    monkeypatch.setattr(store_db, "STORE_DB_PATH", str(path))
    monkeypatch.setattr(config, "AUTH_REQUIRED", True)
    monkeypatch.setattr(config, "JWT_SECRET_KEY", "test-secret-that-is-long-enough-for-ci")
    upgrade_database()
    init_database(str(path))
    return path


def test_programmatic_migration_preserves_application_logging(v2_database):
    root_logger = logging.getLogger()
    handlers_before = list(root_logger.handlers)
    level_before = root_logger.level

    upgrade_database()

    assert root_logger.handlers == handlers_before
    assert root_logger.level == level_before


@pytest.mark.asyncio
async def test_jwt_users_can_only_access_their_own_orders(v2_database):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        alice = await client.post(
            "/api/auth/register",
            json={"email": "alice@example.com", "password": "password-123", "display_name": "Alice"},
        )
        bob = await client.post(
            "/api/auth/register",
            json={"email": "bob@example.com", "password": "password-456", "display_name": "Bob"},
        )
        assert alice.status_code == bob.status_code == 201
        alice_headers = {"Authorization": f"Bearer {alice.json()['access_token']}"}
        bob_headers = {"Authorization": f"Bearer {bob.json()['access_token']}"}

        created = await client.post(
            "/api/orders",
            headers=alice_headers,
            json={
                "customer_name": "Alice",
                "customer_email": "alice@example.com",
                "customer_phone": "0800000000",
                "shipping_address": "Bangkok",
                "payment_method": "cash_on_delivery",
                "items": [{"product_id": "PROD-001", "quantity": 1}],
            },
        )
        assert created.status_code == 201
        order_id = created.json()["order_id"]
        assert (await client.get(f"/api/orders/{order_id}", headers=alice_headers)).status_code == 200
        assert (await client.get(f"/api/orders/{order_id}", headers=bob_headers)).status_code == 404
        assert (await client.get(f"/api/orders/{order_id}")).status_code == 401


@pytest.mark.asyncio
async def test_request_id_is_echoed_and_security_headers_are_set(monkeypatch):
    monkeypatch.setattr(config, "AUTH_REQUIRED", False)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/health", headers={"X-Request-ID": "ci-request-123"})
    assert response.headers["X-Request-ID"] == "ci-request-123"
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["X-Frame-Options"] == "DENY"
