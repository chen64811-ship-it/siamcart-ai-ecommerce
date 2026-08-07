"""Focused session-aware refund routing tests (Task 5D-5).

Covers:
  1.  Fresh "hai" routes to GREETING.
  2.  "this product" does not trigger greeting through the substring "hi".
  3.  "I want a refund" creates pending RETURN_REFUND state.
  4.  Follow-up "ORD-1029, i dont like" preserves RETURN_REFUND.
  5.  order_id is extracted correctly.
  6.  reason is extracted correctly.
  7.  Transaction Tracker evidence is loaded.
  8.  Refund policy evidence is retrieved.
  9.  Result is not classified as ORDER_STATUS.
  10. "Where is order ORD-1029?" still routes to ORDER_STATUS.
  11. "Has ORD-1029 been paid?" still routes to PAYMENT_STATUS.
  12. "Cancel ORD-1029" does not route to generic ORDER_STATUS.
  13. Bare ORD-1029 follows the current pending intent.
  14. Bare ORD-1029 without pending context may use the existing order lookup.
  15. No order facts are invented by an LLM.
  16. Session state clears after refund guidance is completed.
  17. Existing product-context routing remains functional.

Isolation:
- Temporary SQLite database initialized with the REAL production functions
  (orders schema + 5 sample orders + store schema + 12 products + ORD-1029
  inserted as the ORD-1029-style COD fixture). The real data/orders.db is
  proven untouched at teardown.
- Policy retrieval is exercised through the established canned-evaluator
  pattern (same as test_product_chat.py) — no ChromaDB / SentenceTransformer.
- No DeepSeek client is ever constructed for order facts.
"""

import os
import sqlite3
import tempfile

import pytest
from httpx import ASGITransport, AsyncClient

import app.db.store as store_db
from app.agents import set_db_path
from app.db.orders import init_database
from app.api.server import app

# Real database path — captured BEFORE the store module path is redirected.
REAL_DB = str(store_db.STORE_DB_PATH)


# ── Canned policy evaluator result (same pattern as test_product_chat.py) ──

CANNED_POLICY = {
    "intent": "STORE_POLICY",
    "agent": "store_policy_evaluator",
    "query": "refund return cancellation policy",
    "retrieved_clauses": [
        {
            "text": (
                "Customers may request a return within 14 calendar days from "
                "the date of delivery. Items must be in original condition, "
                "unused, and include all original packaging and accessories."
            ),
            "policy_type": "return",
            "source_filename": "return_policy.md",
            "chunk_id": "return-001",
            "section_title": "Section 1: Standard Return Window",
            "similarity_score": 0.83,
        },
        {
            "text": (
                "After receiving and inspecting the returned item, the store "
                "will process the refund within 5-7 business days. The refund "
                "will be issued to the original payment method."
            ),
            "policy_type": "refund",
            "source_filename": "refund_policy.md",
            "chunk_id": "refund-001",
            "section_title": "Section 1: Refund Processing Timeline",
            "similarity_score": 0.79,
        },
    ],
    "retrieval_success": True,
    "policy_sources": ["return_policy.md", "refund_policy.md"],
    "retrieved_chunk_count": 2,
    "top_similarity_score": 0.83,
    "deterministic_response": (
        "ตามนโยบายการคืนสินค้าของ SiamCart: Customers may request a return "
        "within 14 calendar days from the date of delivery."
    ),
    "requires_clarification": False,
    "simulated_human_review": False,
    "evidence": {
        "policy_type": "return",
        "primary_source": "return_policy.md",
        "retrieved_chunks": 2,
        "policy_sources": ["return_policy.md", "refund_policy.md"],
        "retrieved_chunk_count": 2,
        "top_similarity_score": 0.83,
    },
    "response": (
        "ตามนโยบายการคืนสินค้าของ SiamCart: Customers may request a return "
        "within 14 calendar days from the date of delivery."
    ),
    "response_source": "deterministic",
    "llm_enabled": False,
    "llm_fallback_used": False,
    "llm_latency_ms": 0.0,
    "llm_error_type": None,
    "grounding_validation_passed": None,
}


# ── helpers ────────────────────────────────────────────────────────────


def _db_stat(path):
    try:
        st = os.stat(path)
        return (st.st_size, st.st_mtime_ns)
    except OSError:
        return None


async def _chat(client, message, session_id, product_id=None):
    payload = {"message": message, "session_id": session_id}
    if product_id is not None:
        payload["product_id"] = product_id
    return await client.post("/api/chat", json=payload)


def _insert_ord1029(db_path):
    """Insert the ORD-1029-style Cash on Delivery fixture (pending/processing/not shipped)."""
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            "INSERT OR REPLACE INTO orders ("
            "order_id, customer_name, product_name, order_status, payment_status,"
            "shipment_status, tracking_number, shipping_provider, purchase_date,"
            "estimated_delivery_date, payment_method, paid_at"
            ") VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "ORD-1029", "xingyu chen", "Aura Wireless Headphones",
                "processing", "pending", "not_shipped", "", "",
                "2026-08-02", "", "COD", None,
            ),
        )
        conn.commit()
    finally:
        conn.close()


# ── fixtures ───────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def tmp_db():
    """Temporary SQLite database, initialized with real production functions."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    init_database(path)  # orders schema + 5 sample orders + store schema + 12 products
    _insert_ord1029(path)

    real_before = _db_stat(REAL_DB)

    original_path = store_db.STORE_DB_PATH
    store_db.STORE_DB_PATH = path
    set_db_path(path)
    try:
        yield path
        # Isolation proof: the real database must be completely untouched.
        assert _db_stat(REAL_DB) == real_before, (
            f"REAL database was modified by session refund tests: {REAL_DB}"
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


@pytest.fixture(scope="module", autouse=True)
def deepseek_off():
    """Keep the suite offline and deterministic (repo convention — conftest
    states no real DeepSeek API calls are made in tests)."""
    import app.agents.orchestrator as orch
    import app.agents.llm_generator as gen

    orig_orch = orch.DEEPSEEK_ENABLED
    orig_gen = gen.DEEPSEEK_ENABLED
    orch.DEEPSEEK_ENABLED = False
    gen.DEEPSEEK_ENABLED = False
    yield
    orch.DEEPSEEK_ENABLED = orig_orch
    gen.DEEPSEEK_ENABLED = orig_gen


@pytest.fixture
def canned_policy(monkeypatch):
    """Route policy retrieval through a deterministic canned evaluator result."""
    monkeypatch.setattr(
        "app.agents.orchestrator.evaluate_policy_query",
        lambda query, top_k=3: dict(CANNED_POLICY, query=query),
    )
    return CANNED_POLICY


# ── 1 + 2. Greeting typos ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_fresh_hai_routes_to_greeting(client):
    """1. A fresh 'hai' is a GREETING and never asks for an order ID."""
    resp = await _chat(client, "hai", "s1")
    assert resp.status_code == 200
    data = resp.json()
    assert data["intent"] == "GREETING"
    assert data["agent"] == "general_response"
    assert "order ID" not in data["response"].lower()
    assert "order" not in data["response"].lower()


@pytest.mark.asyncio
async def test_this_product_does_not_trigger_greeting(client):
    """2. 'this product' must not match the greeting via the substring 'hi'."""
    resp = await _chat(client, "this product", "s2")
    assert resp.status_code == 200
    data = resp.json()
    assert data["intent"] != "GREETING"
    assert data["intent"] == "UNKNOWN"


# ── 3. Pending RETURN_REFUND state ────────────────────────────────────


@pytest.mark.asyncio
async def test_i_want_a_refund_creates_pending_state(client):
    """3. 'I want a refund' starts the refund flow and asks for ID + reason."""
    resp = await _chat(client, "I want a refund", "s3")
    assert resp.status_code == 200
    data = resp.json()
    assert data["intent"] == "RETURN_REFUND"
    assert data["agent"] == "store_policy_evaluator"
    assert data["requires_clarification"] is True
    # Task 5D-7: clarification is Thai.
    assert "หมายเลขคำสั่งซื้อ" in data["response"]
    assert "เหตุผล" in data["response"]


# ── 4-9. Refund follow-up flow ────────────────────────────────────────


async def _complete_refund(client, session_id, first="I want a refund",
                           followup="ORD-1029, i dont like"):
    r1 = await _chat(client, first, session_id)
    assert r1.status_code == 200
    r2 = await _chat(client, followup, session_id)
    assert r2.status_code == 200
    return r1.json(), r2.json()


@pytest.mark.asyncio
async def test_followup_ord1029_i_dont_like_preserves_return_refund(client, canned_policy):
    """4. The ORD-1029 follow-up stays RETURN_REFUND, never ORDER_STATUS."""
    _, data = await _complete_refund(client, "s4")
    assert data["intent"] == "RETURN_REFUND"
    assert data["agent"] == "store_policy_evaluator"
    assert "ORD-1029" in data["response"]
    assert "Cash on Delivery" in data["response"]
    # Task 5D-7: refund guidance is Thai.
    assert "อยู่ระหว่างดำเนินการ" in data["response"]
    assert "ยังไม่ได้จัดส่ง" in data["response"]
    assert "return within 14 calendar days" in data["response"]
    assert "ยังไม่มีการอนุมัติคืนเงินหรือยกเลิก" in data["response"]


@pytest.mark.asyncio
async def test_order_id_extracted_correctly(client, canned_policy):
    """5. ORD-1029 is extracted from the follow-up message."""
    _, data = await _complete_refund(client, "s5")
    assert data["order_id"] == "ORD-1029"


@pytest.mark.asyncio
async def test_reason_extracted_correctly(client, canned_policy):
    """6. The refund reason is captured from the follow-up message."""
    _, data = await _complete_refund(client, "s6")
    assert data["refund_reason"] == "i dont like"
    # Task 5D-7: the customer-facing acknowledgment is Thai.
    assert "ไม่ชอบสินค้า" in data["response"]


@pytest.mark.asyncio
async def test_transaction_tracker_evidence_loaded(client, canned_policy):
    """7. Deterministic SQLite order evidence is attached to the result."""
    _, data = await _complete_refund(client, "s7")
    ev = data["evidence"]
    assert ev["order_id"] == "ORD-1029"
    assert ev["payment_method"] == "COD"
    assert ev["payment_status"] == "pending"
    assert ev["order_status"] == "processing"
    assert ev["shipment_status"] == "not_shipped"
    assert data["order_evidence_source"] == "SQLite"


@pytest.mark.asyncio
async def test_refund_policy_evidence_retrieved(client, canned_policy):
    """8. Refund/return policy evidence is retrieved and exposed."""
    _, data = await _complete_refund(client, "s8")
    assert data["policy_evidence"]["retrieval_success"] is True
    assert data["policy_evidence"]["retrieved_chunk_count"] == 2
    assert data["retrieved_chunks"] == 2
    assert data["policy_source"] == "return_policy.md"
    assert "return_policy.md" in data["policy_evidence"]["policy_sources"]


@pytest.mark.asyncio
async def test_result_is_not_classified_as_order_status(client, canned_policy):
    """9. The completed refund request is never labelled ORDER_STATUS."""
    _, data = await _complete_refund(client, "s9")
    assert data["intent"] == "RETURN_REFUND"
    assert data["intent"] != "ORDER_STATUS"
    assert data["response_source"] == "deterministic"
    assert data["llm_used"] is False
    assert data["fallback_reason"] == "deepseek_disabled"


# ── 10-12. Routing regressions ────────────────────────────────────────


@pytest.mark.asyncio
async def test_where_is_order_routes_to_order_status(client):
    """10. A fresh order-status question still routes to transaction_tracker."""
    resp = await _chat(client, "Where is order ORD-1029?", "s10")
    assert resp.status_code == 200
    data = resp.json()
    assert data["intent"] == "ORDER_STATUS"
    assert data["agent"] == "transaction_tracker"
    assert data["order_id"] == "ORD-1029"
    assert data["response_source"] == "deterministic"


@pytest.mark.asyncio
async def test_has_ord1029_been_paid_routes_to_payment_status(client):
    """11. 'paid' is an explicit intent word, not hijacked by the order ID."""
    resp = await _chat(client, "Has ORD-1029 been paid?", "s11")
    assert resp.status_code == 200
    data = resp.json()
    assert data["intent"] == "PAYMENT_STATUS"
    assert data["agent"] == "transaction_tracker"
    assert data["order_id"] == "ORD-1029"


@pytest.mark.asyncio
async def test_cancel_ord1029_not_generic_order_status(client):
    """12. 'Cancel ORD-1029' keeps the existing cancel model, not ORDER_STATUS."""
    resp = await _chat(client, "Cancel ORD-1029", "s12")
    assert resp.status_code == 200
    data = resp.json()
    assert data["intent"] != "ORDER_STATUS"
    assert data["intent"] == "OUT_OF_SCOPE"
    assert data["simulated_human_review"] is True


# ── 13-14. Bare order ID behavior ─────────────────────────────────────


@pytest.mark.asyncio
async def test_bare_ord_follows_pending_intent(client):
    """13. A bare ORD-1029 continues the pending refund, asking for the reason."""
    r1 = await _chat(client, "I want a refund", "s13")
    assert r1.json()["intent"] == "RETURN_REFUND"
    r2 = await _chat(client, "ORD-1029", "s13")
    data = r2.json()
    assert data["intent"] == "RETURN_REFUND"
    assert data["order_id"] == "ORD-1029"
    assert data["requires_clarification"] is True
    # Task 5D-7: the missing-reason request is Thai.
    assert "เหตุผล" in data["response"]


@pytest.mark.asyncio
async def test_bare_ord_without_pending_uses_order_lookup(client):
    """14. A bare ORD-1029 without pending context keeps the order lookup."""
    resp = await _chat(client, "ORD-1029", "s14")
    assert resp.status_code == 200
    data = resp.json()
    assert data["intent"] == "ORDER_STATUS"
    assert data["agent"] == "transaction_tracker"
    assert data["order_id"] == "ORD-1029"


# ── 15. No invented order facts ───────────────────────────────────────


@pytest.mark.asyncio
async def test_no_order_facts_invented_by_llm(client, canned_policy, tmp_db):
    """15. Order facts come from SQLite; DeepSeek is never used for them."""
    import openai

    original = openai.OpenAI

    def _no_client(*_a, **_k):
        raise AssertionError("DeepSeek (OpenAI) client constructed for order facts")

    openai.OpenAI = _no_client
    try:
        _, data = await _complete_refund(client, "s15")
    finally:
        openai.OpenAI = original

    assert data["response_source"] == "deterministic"
    assert data["llm_used"] is False
    assert data["order_evidence_source"] == "SQLite"

    # Cross-check every status fact against the temp SQLite database.
    conn = sqlite3.connect(tmp_db)
    try:
        row = conn.execute(
            "SELECT payment_method, payment_status, order_status, shipment_status "
            "FROM orders WHERE order_id = 'ORD-1029'"
        ).fetchone()
    finally:
        conn.close()
    assert data["evidence"]["payment_method"] == row[0]
    assert data["evidence"]["payment_status"] == row[1]
    assert data["evidence"]["order_status"] == row[2]
    assert data["evidence"]["shipment_status"] == row[3]
    # Task 5D-7: the completion disclaimer is Thai.
    assert "ยังไม่มีการอนุมัติคืนเงินหรือยกเลิก" in data["response"]


# ── 16. Session state clears ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_session_state_clears_after_completion(client, canned_policy):
    """16. Pending state is cleared once the refund guidance is completed."""
    _, data = await _complete_refund(client, "s16")
    assert data["intent"] == "RETURN_REFUND"

    # Same session, same follow-up message: without pending state the bare
    # order ID now falls back to the existing order lookup behavior.
    r3 = await _chat(client, "ORD-1029, i dont like", "s16")
    data3 = r3.json()
    assert data3["intent"] == "ORDER_STATUS"
    assert data3["agent"] == "transaction_tracker"


# ── 17. Product-context routing regression ────────────────────────────


@pytest.mark.asyncio
async def test_product_context_routing_remains_functional(client, canned_policy):
    """17. Policy questions with product context still follow the policy route."""
    resp = await _chat(client, "Can I return this product?", "s17", "PROD-008")
    assert resp.status_code == 200
    data = resp.json()
    assert data["agent"] == "store_policy_evaluator"
    assert data["intent"] == "RETURN_REFUND"
    assert data["response"] == CANNED_POLICY["deterministic_response"]
    assert data["evidence"]["product_context"] == {
        "product_id": "PROD-008",
        "name": "CloudStep Casual Sneakers",
        "category": "Footwear",
    }
    assert data["product_id"] == "PROD-008"
    assert data["demo_extension"] is None
