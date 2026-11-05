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
async def test_order_list_isolated_and_guest_payment_requires_login(v2_database):
    """P1-1 — logged-in users only list their own orders; guests must log in
    before the (demo) payment step."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        alice = await client.post(
            "/api/auth/register",
            json={"email": "alice-list@example.com", "password": "password-123", "display_name": "Alice"},
        )
        bob = await client.post(
            "/api/auth/register",
            json={"email": "bob-list@example.com", "password": "password-456", "display_name": "Bob"},
        )
        assert alice.status_code == bob.status_code == 201
        alice_headers = {"Authorization": f"Bearer {alice.json()['access_token']}"}
        bob_headers = {"Authorization": f"Bearer {bob.json()['access_token']}"}

        payload = {
            "customer_name": "Alice",
            "customer_email": "alice-list@example.com",
            "customer_phone": "0800000000",
            "shipping_address": "Bangkok",
            "payment_method": "bank_transfer",
            "items": [{"product_id": "PROD-001", "quantity": 1}],
        }
        a1 = await client.post("/api/orders", headers=alice_headers, json=payload)
        a2 = await client.post("/api/orders", headers=alice_headers, json=payload)
        assert a1.status_code == a2.status_code == 201
        alice_order_ids = {a1.json()["order_id"], a2.json()["order_id"]}

        bob_payload = dict(payload, customer_email="bob-list@example.com", customer_name="Bob")
        b1 = await client.post("/api/orders", headers=bob_headers, json=bob_payload)
        assert b1.status_code == 201
        bob_order_id = b1.json()["order_id"]

        # Alice's list contains ONLY her orders (never Bob's).
        alice_list = await client.get("/api/orders", headers=alice_headers)
        assert alice_list.status_code == 200
        listed_ids = {o["order_id"] for o in alice_list.json()["orders"]}
        assert alice_order_ids.issubset(listed_ids)
        assert bob_order_id not in listed_ids

        # A guest cannot reach the payment step (401) when auth is required.
        guest_payment = await client.post(
            f"/api/orders/{a1.json()['order_id']}/demo-payment",
            json={"confirm_demo_payment": True},
        )
        assert guest_payment.status_code == 401


@pytest.mark.asyncio
async def test_demo_login_is_explicit_and_reuses_labelled_account(v2_database, monkeypatch):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        monkeypatch.setattr(config, "DEMO_LOGIN_ENABLED", False)
        disabled_config = await client.get("/api/auth/config")
        assert disabled_config.status_code == 200
        assert disabled_config.json() == {
            "auth_required": True,
            "demo_login_enabled": False,
        }
        assert (await client.post("/api/auth/demo")).status_code == 404

        monkeypatch.setattr(config, "DEMO_LOGIN_ENABLED", True)
        first = await client.post("/api/auth/demo")
        second = await client.post("/api/auth/demo")
        assert first.status_code == second.status_code == 200
        assert first.json()["user"]["email"] == "demo@siamcart.app"
        assert first.json()["user"]["display_name"] == "SiamCart Demo Shopper"
        assert first.json()["user"]["user_id"] == second.json()["user"]["user_id"]

        headers = {"Authorization": f"Bearer {first.json()['access_token']}"}
        me = await client.get("/api/auth/me", headers=headers)
        assert me.status_code == 200
        assert me.json()["user_id"] == first.json()["user"]["user_id"]


@pytest.mark.asyncio
async def test_request_id_is_echoed_and_security_headers_are_set(monkeypatch):
    monkeypatch.setattr(config, "AUTH_REQUIRED", False)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/health", headers={"X-Request-ID": "ci-request-123"})
    assert response.headers["X-Request-ID"] == "ci-request-123"
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["X-Frame-Options"] == "DENY"
