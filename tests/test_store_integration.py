"""Focused SQLite store integration tests (Task 5C-2A).

Covers GET /api/products, GET /api/products/{id}, POST /api/orders and
GET /api/orders/{order_id} against a TEMPORARY SQLite database.

Isolation guarantees:
- The real data/orders.db is never written. store_db.STORE_DB_PATH is
  redirected to a temp file for the whole module; the Transaction Tracker
  is called with an explicit temp db_path.
- The temp database is initialized with the REAL production initialization
  functions (app.db.orders.init_database -> app.db.store.init_store_database),
  which preserve the real schema and seed the real 12 products (PROD-001 …
  PROD-012) plus the 5 sample orders (ORD-1001 … ORD-1005).
- A teardown stat check proves the real database file is untouched.
- No DeepSeek / ChromaDB / SentenceTransformer initialization is performed
  (verified by an explicit test), no browser automation, no JS/CSS edits.

Order creation and stock updates are NEVER mocked — they run against the
real production transaction logic.
"""

import os
import sqlite3
import sys
import tempfile
import types
from urllib.parse import quote

import pytest
from httpx import ASGITransport, AsyncClient

import app.db.store as store_db
from app.db.orders import init_database
from app.agents.transaction_tracker import fetch_transaction_evidence
from app.api.server import app

# Real database path — captured BEFORE the store module path is redirected.
REAL_DB = str(store_db.STORE_DB_PATH)

# Order IDs created by the suite (for the run report).
CREATED_ORDER_IDS = []


# ── helpers ────────────────────────────────────────────────────────────

VALID_CUSTOMER = {
    "customer_name": "Somchai Test",
    "customer_email": "somchai.test@example.com",
    "customer_phone": "081-234-5678",
    "shipping_address": "123 Sukhumvit Rd",
    "district": "Watthana",
    "province": "Bangkok",
    "postal_code": "10110",
    "payment_method": "cash_on_delivery",
}


def _order_payload(items, **overrides):
    payload = dict(VALID_CUSTOMER)
    payload.update(overrides)
    payload["items"] = items
    return payload


async def _post_order(client, items, **overrides):
    resp = await client.post("/api/orders", json=_order_payload(items, **overrides))
    if resp.status_code == 201:
        CREATED_ORDER_IDS.append(resp.json()["order_id"])
    return resp


def _db_stat(path):
    try:
        st = os.stat(path)
        return (st.st_size, st.st_mtime_ns)
    except OSError:
        return None


def _stock(db_path, product_id):
    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute(
            "SELECT stock_quantity FROM products WHERE product_id = ?", (product_id,)
        ).fetchone()
        return row[0]
    finally:
        conn.close()


def _price(db_path, product_id):
    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute(
            "SELECT price FROM products WHERE product_id = ?", (product_id,)
        ).fetchone()
        return row[0]
    finally:
        conn.close()


def _order_count(db_path):
    conn = sqlite3.connect(db_path)
    try:
        return conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0]
    finally:
        conn.close()


def _contains_card(data):
    """Recursively look for any card-like key/value in an API response."""
    if isinstance(data, dict):
        for k, v in data.items():
            if "card" in str(k).lower():
                return True
            if _contains_card(v):
                return True
    elif isinstance(data, list):
        return any(_contains_card(i) for i in data)
    return False


# ── fixtures ───────────────────────────────────────────────────────────


@pytest.fixture(scope="module", autouse=True)
def tmp_db():
    """Temporary SQLite database, initialized with real production functions."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    init_database(path)  # real init: orders schema + 5 sample orders + store schema + 12 products

    real_before = _db_stat(REAL_DB)

    original_path = store_db.STORE_DB_PATH
    store_db.STORE_DB_PATH = path
    try:
        yield path
        # Isolation proof: the real database must be completely untouched.
        assert _db_stat(REAL_DB) == real_before, (
            f"REAL database was modified by store integration tests: {REAL_DB}"
        )
    finally:
        store_db.STORE_DB_PATH = original_path
        try:
            os.unlink(path)
        except OSError:
            pass


@pytest.fixture(scope="module")
def client():
    """Async HTTP client over the FastAPI app (no lifespan / no real DB init)."""
    transport = ASGITransport(app=app)
    c = AsyncClient(transport=transport, base_url="http://test")
    yield c


# ── Products ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_products_returns_12_active(client):
    """1. GET /api/products returns exactly 12 active products."""
    resp = await client.get("/api/products")
    assert resp.status_code == 200
    products = resp.json()
    assert isinstance(products, list)
    assert len(products) == 12
    assert all(p["active"] == 1 for p in products)


@pytest.mark.asyncio
async def test_products_include_required_fields(client):
    """2. Products carry product_id, name, price, category, image_url, stock_quantity."""
    resp = await client.get("/api/products")
    products = resp.json()
    required = {"product_id", "name", "price", "category", "image_url", "stock_quantity"}
    for p in products:
        assert required.issubset(p.keys()), f"missing fields in {p['product_id']}"
        assert isinstance(p["price"], (int, float))
        assert isinstance(p["stock_quantity"], int)


@pytest.mark.asyncio
async def test_search_filtering_works(client):
    """3. Search filtering narrows results."""
    resp = await client.get("/api/products", params={"search": "sneaker"})
    assert resp.status_code == 200
    products = resp.json()
    assert len(products) == 2
    assert {p["product_id"] for p in products} == {"PROD-007", "PROD-008"}

    resp = await client.get("/api/products", params={"search": "zzzz-no-match"})
    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.asyncio
async def test_category_filtering_works(client):
    """4. Category filtering returns only products of that category."""
    resp = await client.get("/api/products", params={"category": "Fashion"})
    assert resp.status_code == 200
    products = resp.json()
    assert len(products) == 3
    assert {p["product_id"] for p in products} == {"PROD-001", "PROD-002", "PROD-003"}
    assert all(p["category"] == "Fashion" for p in products)


@pytest.mark.asyncio
async def test_price_asc_sorting_works(client):
    """5. sort=price_asc returns products from cheapest to most expensive."""
    resp = await client.get("/api/products", params={"sort": "price_asc"})
    assert resp.status_code == 200
    prices = [p["price"] for p in resp.json()]
    assert prices == sorted(prices)


@pytest.mark.asyncio
async def test_price_desc_sorting_works(client):
    """6. sort=price_desc returns products from most to least expensive."""
    resp = await client.get("/api/products", params={"sort": "price_desc"})
    assert resp.status_code == 200
    prices = [p["price"] for p in resp.json()]
    assert prices == sorted(prices, reverse=True)


@pytest.mark.asyncio
async def test_get_product_by_id_succeeds(client):
    """7. GET /api/products/PROD-001 returns the product."""
    resp = await client.get("/api/products/PROD-001")
    assert resp.status_code == 200
    data = resp.json()
    assert data["product_id"] == "PROD-001"
    assert data["name"] == "Linen Everyday Blouse"
    assert data["price"] == 690


@pytest.mark.asyncio
async def test_get_unknown_product_returns_404(client):
    """8. Unknown product id returns 404."""
    resp = await client.get("/api/products/PROD-999")
    assert resp.status_code == 404


# ── Orders ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_post_valid_order_returns_201(client):
    """9. Valid POST /api/orders returns 201 with a new ORD-xxxx id."""
    resp = await _post_order(client, [{"product_id": "PROD-001", "quantity": 1}])
    assert resp.status_code == 201
    data = resp.json()
    assert data["order_id"].startswith("ORD-")
    assert data["customer_name"] == VALID_CUSTOMER["customer_name"]


@pytest.mark.asyncio
async def test_server_reads_price_and_calculates_total(client, tmp_db):
    """10. Price is read from SQLite; total_amount is computed server-side."""
    price = _price(tmp_db, "PROD-001")
    resp = await _post_order(client, [{"product_id": "PROD-001", "quantity": 2}])
    assert resp.status_code == 201
    data = resp.json()
    assert data["total_amount"] == round(price * 2, 2)
    assert data["items"][0]["unit_price"] == price
    assert data["items"][0]["line_total"] == round(price * 2, 2)


@pytest.mark.asyncio
async def test_created_order_contains_order_items(client, tmp_db):
    """11. Created order contains order_items."""
    price = _price(tmp_db, "PROD-002")
    resp = await _post_order(client, [{"product_id": "PROD-002", "quantity": 1}])
    assert resp.status_code == 201
    data = resp.json()
    assert isinstance(data["items"], list) and len(data["items"]) == 1
    item = data["items"][0]
    assert item["order_id"] == data["order_id"]
    assert item["product_id"] == "PROD-002"
    assert item["product_name"]
    assert item["quantity"] == 1
    assert item["unit_price"] == price
    assert item["line_total"] == round(price, 2)


@pytest.mark.asyncio
async def test_default_statuses(client):
    """12. Default statuses are processing / pending / not_shipped."""
    resp = await _post_order(client, [{"product_id": "PROD-003", "quantity": 1}])
    assert resp.status_code == 201
    data = resp.json()
    assert data["order_status"] == "processing"
    assert data["payment_status"] == "pending"
    assert data["shipment_status"] == "not_shipped"


@pytest.mark.asyncio
async def test_purchased_stock_is_reduced(client, tmp_db):
    """13. Stock is reduced by exactly the purchased quantity."""
    pid = "PROD-001"
    before = _stock(tmp_db, pid)
    resp = await _post_order(client, [{"product_id": pid, "quantity": 2}])
    assert resp.status_code == 201
    assert _stock(tmp_db, pid) == before - 2


@pytest.mark.asyncio
async def test_get_order_returns_created_order(client):
    """14. GET /api/orders/{order_id} returns the created order."""
    resp = await _post_order(client, [{"product_id": "PROD-004", "quantity": 1}])
    assert resp.status_code == 201
    oid = resp.json()["order_id"]

    got = await client.get(f"/api/orders/{oid}")
    assert got.status_code == 200
    data = got.json()
    assert data["order_id"] == oid
    assert data["customer_name"] == VALID_CUSTOMER["customer_name"]
    assert len(data["items"]) == 1


@pytest.mark.asyncio
async def test_order_id_lookup_normalized_lowercase_whitespace(client):
    """15. Lowercase and whitespace order-id lookup is normalized to ORD-xxxx."""
    for lookup in ("ord-1001", quote(" ord-1001 ")):
        resp = await client.get(f"/api/orders/{lookup}")
        assert resp.status_code == 200, f"lookup {lookup!r} failed"
        assert resp.json()["order_id"] == "ORD-1001"


@pytest.mark.asyncio
async def test_new_order_queryable_via_transaction_tracker(client, tmp_db):
    """16. A newly created order is queryable through the Transaction Tracker."""
    resp = await _post_order(client, [{"product_id": "PROD-005", "quantity": 1}])
    assert resp.status_code == 201
    oid = resp.json()["order_id"]

    result = fetch_transaction_evidence(f"{oid} status", db_path=tmp_db)
    assert result["order_found"] is True
    assert result["order_id"] == oid
    assert result["evidence"]["order_id"] == oid
    assert result["intent"] == "ORDER_STATUS"
    assert result["requires_clarification"] is False


@pytest.mark.asyncio
async def test_seeded_ord1001_remains_queryable(client, tmp_db):
    """17. Existing seeded order ORD-1001 remains queryable after store ops."""
    result = fetch_transaction_evidence("ORD-1001 ถึงไหน", db_path=tmp_db)
    assert result["order_found"] is True
    assert result["evidence"]["order_status"] == "shipped"


@pytest.mark.asyncio
async def test_unknown_product_rejected(client):
    """18. Unknown product is rejected with 400."""
    resp = await client.post(
        "/api/orders", json=_order_payload([{"product_id": "PROD-999", "quantity": 1}])
    )
    assert resp.status_code == 400
    assert "PROD-999" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_zero_and_negative_quantities_rejected(client):
    """19. Zero and negative quantities are rejected (422 validation / 400 business)."""
    for qty in (0, -1):
        resp = await client.post(
            "/api/orders", json=_order_payload([{"product_id": "PROD-001", "quantity": qty}])
        )
        assert resp.status_code in (400, 422), f"qty={qty} -> {resp.status_code}"


@pytest.mark.asyncio
async def test_insufficient_stock_rolls_back(client, tmp_db):
    """20. Insufficient stock rolls back order insertion AND stock change."""
    pid = "PROD-009"  # seeded stock 8
    stock_before = _stock(tmp_db, pid)
    orders_before = _order_count(tmp_db)

    resp = await client.post(
        "/api/orders",
        json=_order_payload([{"product_id": pid, "quantity": stock_before + 1}]),
    )
    assert resp.status_code == 400
    assert _stock(tmp_db, pid) == stock_before, "stock changed after rollback"
    assert _order_count(tmp_db) == orders_before, "order row inserted after rollback"


@pytest.mark.asyncio
async def test_duplicate_product_ids_combined(client, tmp_db):
    """21. Duplicate product IDs are combined into one line (case-insensitive)."""
    price = _price(tmp_db, "PROD-001")
    stock_before = _stock(tmp_db, "PROD-001")

    resp = await client.post(
        "/api/orders",
        json=_order_payload(
            [
                {"product_id": "prod-001", "quantity": 2},
                {"product_id": "PROD-001", "quantity": 3},
            ]
        ),
    )
    assert resp.status_code == 201
    data = resp.json()
    assert len(data["items"]) == 1, "duplicates must collapse into one order_items row"
    assert data["items"][0]["product_id"] == "PROD-001"
    assert data["items"][0]["quantity"] == 5
    assert data["total_amount"] == round(price * 5, 2)
    assert _stock(tmp_db, "PROD-001") == stock_before - 5

    conn = sqlite3.connect(tmp_db)
    try:
        rows = conn.execute(
            "SELECT quantity FROM order_items WHERE order_id = ?", (data["order_id"],)
        ).fetchall()
    finally:
        conn.close()
    assert len(rows) == 1 and rows[0][0] == 5


# ── Safety guarantees ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_store_api_does_not_initialize_heavy_deps(client, tmp_db):
    """22. Store API operations never initialize DeepSeek, ChromaDB or SentenceTransformer."""
    import openai

    HEAVY = ("chromadb", "sentence_transformers")
    installed = {}
    for name in HEAVY:
        if name not in sys.modules:
            mod = types.ModuleType(name)

            def _raiser(*_a, **_k):
                raise AssertionError(f"Store API operation initialized '{name}'")

            mod.__getattr__ = _raiser  # type: ignore[attr-defined]
            sys.modules[name] = mod
            installed[name] = mod

    original_openai = openai.OpenAI

    def _no_client(*_a, **_k):
        raise AssertionError("Store API operation initialized a DeepSeek (OpenAI) client")

    openai.OpenAI = _no_client
    try:
        r = await client.get("/api/products")
        assert r.status_code == 200
        assert len(r.json()) == 12

        r = await _post_order(client, [{"product_id": "PROD-012", "quantity": 1}])
        assert r.status_code == 201

        r = await client.get(f"/api/orders/{r.json()['order_id']}")
        assert r.status_code == 200

        for name, mod in installed.items():
            assert sys.modules.get(name) is mod, (
                f"'{name}' was re-imported during store API calls"
            )
    finally:
        openai.OpenAI = original_openai
        for name, mod in installed.items():
            if sys.modules.get(name) is mod:
                del sys.modules[name]


@pytest.mark.asyncio
async def test_no_card_number_accepted_or_stored(client, tmp_db):
    """23. No card-number field is accepted or stored."""
    from app.models.store import OrderCreateRequest, OrderItemRequest

    # The schema defines no card fields at all.
    for model in (OrderCreateRequest, OrderItemRequest):
        fields = set(model.model_fields)
        assert not any("card" in f.lower() for f in fields), f"card field found in {model.__name__}"

    # Even if a client sends card data, it must not be accepted/stored.
    card_number = "4111111111111111"
    resp = await client.post(
        "/api/orders",
        json=_order_payload(
            [{"product_id": "PROD-002", "quantity": 1}],
            card_number=card_number,
            card_cvv="123",
            card_expiry="12/29",
        ),
    )
    # pydantic ignores unknown fields -> 201; extra='forbid' config -> 422. Either way no storage.
    assert resp.status_code in (201, 422)
    if resp.status_code == 201:
        data = resp.json()
        assert not _contains_card(data), "card data leaked into the API response"

        conn = sqlite3.connect(tmp_db)
        try:
            columns = [
                r[1] for r in conn.execute("PRAGMA table_info(orders)").fetchall()
            ]
            assert not any("card" in c.lower() for c in columns)
            row = conn.execute(
                "SELECT * FROM orders WHERE order_id = ?", (data["order_id"],)
            ).fetchone()
            values = " ".join(str(v) for v in row)
            assert card_number not in values, "card number was stored in the orders table"
        finally:
            conn.close()
