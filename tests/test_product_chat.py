"""Focused tests for Product Catalog Lookup (Task 5D-1).

Covers POST /api/chat with optional product_id context:

- backward compatibility without product_id
- deterministic SQLite-backed product facts (name, price, stock, description,
  features, discount) with response_source=product_catalog_deterministic
- HTTP 404 for unknown product
- no invented unsupported information (sizes / colors / materials)
- no DeepSeek / ChromaDB / SentenceTransformer initialization for product facts
- order queries with product context still use Transaction Tracker
- policy queries with product context still follow the Store Policy Evaluator
- research metadata marks the handler as a demo extension, not an evaluated agent
- existing POST /api/chat request format still works

Isolation guarantees:
- The real data/orders.db is never written. store_db.STORE_DB_PATH is redirected
  to a temporary database initialized with the REAL production initialization
  functions (orders schema + 5 sample orders + store schema + 12 products).
- The orchestrator DB path is redirected to the same temp DB so Transaction
  Tracker reads from it too.
- A teardown stat check proves the real database file is untouched.
- Policy route is exercised with a mocked evaluator (no ChromaDB / LLM).
"""

import os
import sqlite3
import sys
import tempfile
import types

import pytest
from httpx import ASGITransport, AsyncClient

import app.db.store as store_db
from app.agents import set_db_path
from app.db.orders import init_database
from app.api.server import app

# Real database path — captured BEFORE the store module path is redirected.
REAL_DB = str(store_db.STORE_DB_PATH)


# ── helpers ────────────────────────────────────────────────────────────

def _db_stat(path):
    try:
        st = os.stat(path)
        return (st.st_size, st.st_mtime_ns)
    except OSError:
        return None


def _price(db_path, product_id):
    conn = sqlite3.connect(db_path)
    try:
        return conn.execute(
            "SELECT price FROM products WHERE product_id = ?", (product_id,)
        ).fetchone()[0]
    finally:
        conn.close()


def _stock(db_path, product_id):
    conn = sqlite3.connect(db_path)
    try:
        return conn.execute(
            "SELECT stock_quantity FROM products WHERE product_id = ?", (product_id,)
        ).fetchone()[0]
    finally:
        conn.close()


async def _chat(client, message, session_id, product_id=None):
    payload = {"message": message, "session_id": session_id}
    if product_id is not None:
        payload["product_id"] = product_id
    return await client.post("/api/chat", json=payload)


# ── fixtures ───────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def tmp_db():
    """Temporary SQLite database, initialized with real production functions.

    Redirects store_db.STORE_DB_PATH (read at request time by /api/chat) and
    the orchestrator DB path so both Product Catalog Lookup and Transaction
    Tracker operate on the temp DB.
    """
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    init_database(path)  # real init: orders schema + 5 sample orders + store schema + 12 products

    real_before = _db_stat(REAL_DB)

    original_path = store_db.STORE_DB_PATH
    store_db.STORE_DB_PATH = path
    set_db_path(path)
    try:
        yield path
        # Isolation proof: the real database must be completely untouched.
        assert _db_stat(REAL_DB) == real_before, (
            f"REAL database was modified by product chat tests: {REAL_DB}"
        )
    finally:
        store_db.STORE_DB_PATH = original_path
        set_db_path(None)
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


# ── 1. Backward compatibility ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_chat_without_product_id_remains_backward_compatible(client):
    """1. Chat without product_id keeps the existing order route."""
    resp = await _chat(client, "Where is order ORD-1001?", "t1")
    assert resp.status_code == 200
    data = resp.json()
    assert data["intent"] == "ORDER_STATUS"
    assert data["agent"] == "transaction_tracker"
    assert data["order_id"] == "ORD-1001"
    assert data["product_id"] is None
    assert data["demo_extension"] is None


# ── 2. Valid product_id loads the product from SQLite ─────────────────


@pytest.mark.asyncio
async def test_valid_product_id_loads_product_from_sqlite(client):
    """2. Valid product_id loads the CURRENT product record from SQLite."""
    resp = await _chat(client, "What can you tell me about this product?", "t2", "PROD-008")
    assert resp.status_code == 200
    data = resp.json()
    assert data["agent"] == "product_catalog_lookup"
    assert data["product_id"] == "PROD-008"
    assert "CloudStep Casual Sneakers" in data["response"]


# ── 3-7. Deterministic product facts ──────────────────────────────────


@pytest.mark.asyncio
async def test_price_question_returns_sqlite_price(client, tmp_db):
    """3. Price question returns the SQLite price."""
    price = _price(tmp_db, "PROD-008")
    resp = await _chat(client, "How much does it cost?", "t3", "PROD-008")
    assert resp.status_code == 200
    data = resp.json()
    assert f"{price:,.0f}" in data["response"]
    assert data["evidence"]["price"] == price
    assert data["latency_ms"] < 1000  # Objective 6: local millisecond-level


@pytest.mark.asyncio
async def test_stock_question_returns_current_sqlite_stock(client, tmp_db):
    """4. Stock question returns current SQLite stock."""
    stock = _stock(tmp_db, "PROD-008")
    resp = await _chat(client, "Is this product in stock?", "t4", "PROD-008")
    assert resp.status_code == 200
    data = resp.json()
    assert str(stock) in data["response"]
    assert data["evidence"]["stock_quantity"] == stock
    assert data["evidence"]["availability"] == "in_stock"


@pytest.mark.asyncio
async def test_description_question_returns_catalogue_information(client):
    """5. Description question returns catalogue information."""
    resp = await _chat(client, "Tell me about this product.", "t5", "PROD-008")
    assert resp.status_code == 200
    data = resp.json()
    assert "cushioned comfort" in data["response"].lower()


@pytest.mark.asyncio
async def test_feature_question_returns_stored_features(client):
    """6. Feature question returns stored features."""
    resp = await _chat(client, "What are its main features?", "t6", "PROD-008")
    assert resp.status_code == 200
    data = resp.json()
    assert "clean white leather upper" in data["response"].lower()


@pytest.mark.asyncio
async def test_discount_question_uses_current_and_original_prices(client, tmp_db):
    """7. Discount question uses current and original prices."""
    price = _price(tmp_db, "PROD-008")
    resp = await _chat(client, "Is it discounted?", "t7", "PROD-008")
    assert resp.status_code == 200
    data = resp.json()
    assert f"{price:,.0f}" in data["response"]
    assert "1,390" in data["response"]  # original price 1390 formatted
    assert data["evidence"]["original_price"] == 1390
    assert data["evidence"]["discount"] == 400


# ── 8. Unknown product ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_unknown_product_returns_clear_error(client):
    """8. Unknown product returns a clear chat error (HTTP 404)."""
    resp = await _chat(client, "Is this in stock?", "t8", "PROD-999")
    assert resp.status_code == 404
    assert "PROD-999" in resp.json()["detail"]


# ── 9. Unsupported information is not invented ────────────────────────


@pytest.mark.asyncio
async def test_unsupported_size_question_does_not_invent_data(client):
    """9. Unsupported size/color/material questions do not invent data."""
    for question in ("What sizes are available?", "What color is it?", "What material is it made of?"):
        resp = await _chat(client, question, "t9", "PROD-008")
        assert resp.status_code == 200
        data = resp.json()
        assert "does not provide" in data["response"].lower(), question
        assert data["response_source"] == "product_catalog_deterministic"


# ── 10-11. No heavy initialization for product facts ──────────────────


@pytest.mark.asyncio
async def test_product_facts_do_not_initialize_deepseek(client):
    """10. Product facts never initialize a DeepSeek (OpenAI) client."""
    import openai

    original = openai.OpenAI

    def _no_client(*_a, **_k):
        raise AssertionError("Product Catalog Lookup initialized a DeepSeek (OpenAI) client")

    openai.OpenAI = _no_client
    try:
        resp = await _chat(client, "How much does it cost?", "t10", "PROD-008")
        assert resp.status_code == 200
        data = resp.json()
        assert data["response_source"] == "product_catalog_deterministic"
    finally:
        openai.OpenAI = original


@pytest.mark.asyncio
async def test_product_facts_do_not_initialize_chromadb_or_sentence_transformer(client):
    """11. Product facts never initialize ChromaDB or SentenceTransformer."""
    HEAVY = ("chromadb", "sentence_transformers")
    installed = {}
    for name in HEAVY:
        if name not in sys.modules:
            mod = types.ModuleType(name)

            def _raiser(*_a, **_k):
                raise AssertionError(f"Product Catalog Lookup initialized '{name}'")

            mod.__getattr__ = _raiser  # type: ignore[attr-defined]
            sys.modules[name] = mod
            installed[name] = mod

    try:
        resp = await _chat(client, "What are its main features?", "t11", "PROD-008")
        assert resp.status_code == 200
        assert resp.json()["agent"] == "product_catalog_lookup"
        for name, mod in installed.items():
            assert sys.modules.get(name) is mod, (
                f"'{name}' was re-imported during Product Catalog Lookup"
            )
    finally:
        for name, mod in installed.items():
            if sys.modules.get(name) is mod:
                del sys.modules[name]


# ── 12-13. Routing preservation ───────────────────────────────────────


@pytest.mark.asyncio
async def test_order_query_with_product_context_uses_transaction_tracker(client):
    """12. Order queries still use Transaction Tracker with product context active."""
    resp = await _chat(client, "Where is order ORD-1001?", "t12", "PROD-008")
    assert resp.status_code == 200
    data = resp.json()
    assert data["agent"] == "transaction_tracker"
    assert data["intent"] == "ORDER_STATUS"
    assert data["order_id"] == "ORD-1001"
    assert data["response_source"] != "product_catalog_deterministic"
    assert data["demo_extension"] is None


@pytest.mark.asyncio
async def test_policy_query_with_product_context_follows_policy_route(client, monkeypatch):
    """13. Policy queries still follow the existing Store Policy Evaluator route."""
    canned = {
        "intent": "STORE_POLICY",
        "agent": "store_policy_evaluator",
        "query": "Can I return this product?",
        "response": "ตามนโยบายการคืนสินค้าของ SiamCart: สามารถคืนสินค้าได้ภายใน 30 วันนับจากวันที่ได้รับสินค้า",
        "retrieved_clauses": [],
        "retrieval_success": True,
        "policy_sources": ["return_policy.pdf"],
        "retrieved_chunk_count": 1,
        "top_similarity_score": 0.65,
        "deterministic_response": "ตามนโยบายการคืนสินค้าของ SiamCart: สามารถคืนสินค้าได้ภายใน 30 วันนับจากวันที่ได้รับสินค้า",
        "requires_clarification": False,
        "simulated_human_review": False,
        "evidence": {"policy_type": "return", "primary_source": "return_policy.pdf"},
        "response_source": "deepseek",
        "llm_enabled": True,
        "llm_fallback_used": False,
        "llm_latency_ms": 10.0,
        "llm_error_type": None,
        "grounding_validation_passed": True,
    }
    monkeypatch.setattr(
        "app.agents.orchestrator.evaluate_policy_query",
        lambda query, top_k=3: canned,
    )

    resp = await _chat(client, "Can I return this product?", "t13", "PROD-008")
    assert resp.status_code == 200
    data = resp.json()
    assert data["agent"] == "store_policy_evaluator"
    # The router keeps its own intent label (RETURN_REFUND); the agent field
    # reflects the preserved Store Policy Evaluator route.
    assert data["intent"] == "RETURN_REFUND"
    assert data["response"] == canned["deterministic_response"]
    # Safe product context: only verified name, ID and category (Objective 3).
    assert data["evidence"]["product_context"] == {
        "product_id": "PROD-008",
        "name": "CloudStep Casual Sneakers",
        "category": "Footwear",
    }
    assert data["product_id"] == "PROD-008"
    # Catalogue facts must NOT appear as policy evidence.
    assert "price" not in data["evidence"]["product_context"]


# ── 14. Research metadata: demo extension, not an evaluated agent ─────


@pytest.mark.asyncio
async def test_catalog_metadata_marks_demo_extension_not_evaluated_agent(client):
    """14. Product response metadata identifies a demo extension."""
    resp = await _chat(client, "How many are available?", "t14", "PROD-008")
    assert resp.status_code == 200
    data = resp.json()
    assert data["agent"] == "product_catalog_lookup"
    assert data["agent"] not in (
        "transaction_tracker", "store_policy_evaluator", "general_response",
    )
    assert data["response_source"] == "product_catalog_deterministic"
    assert data["database_source"] == "SQLite"
    assert data["demo_extension"] is True
    assert data["product_id"] == "PROD-008"
    assert data["evidence"]["demo_extension"] is True
    assert data["policy_evidence"] == {}


# ── 15. Existing request format still works ───────────────────────────


@pytest.mark.asyncio
async def test_existing_chat_request_format_still_works(client):
    """15. Existing POST /api/chat request format (message + session_id) works."""
    resp = await _chat(client, "Hello", "t15")
    assert resp.status_code == 200
    data = resp.json()
    assert data["intent"] == "GREETING"
    assert data["agent"] == "general_response"
    assert data["response"]
    assert data["product_id"] is None
    assert data["demo_extension"] is None
