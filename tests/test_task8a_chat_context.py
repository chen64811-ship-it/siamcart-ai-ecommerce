"""Task 8A — focused backend chat-context tests (chat context reuse, order
totals, purchased items, session endpoints, honest cancellation).

Covers the 28 Task 8A-2B backend requirements:

  1.  ChatRequest remains valid without order_id.
  2.  Request order_id=ORD-8001 stores active_order_id.
  3.  An explicit ORD-8001 inside the message stores active_order_id.
  4.  "Has it been paid?" reuses ORD-8001.
  5.  "ส่งของหรือยัง" reuses ORD-8001.
  6.  "คำสั่งซื้อนี้อยู่ที่ไหน" reuses ORD-8001.
  7.  English "it" follow-up reuses ORD-8001.
  8.  Explicit ORD-8002 overrides active ORD-8001.
  9.  Greeting does not clear active_order_id.
  10. Product price question is not hijacked by active order.
  11. "What is the total?" returns ORD-8001 total from SQLite.
  12. Thai total question returns the SQLite total.
  13. "What products did I buy?" returns every order item.
  14. Thai purchased-items question returns every order item.
  15. POST /api/chat/remove-context clears active_order_id.
  16. Remove-context preserves the same session.
  17. POST /api/chat/reset clears active_order_id, pending_intent,
      pending_subtype, missing_slots, collected_slots.
  18. Reset and remove-context handle unknown session IDs safely.
  19. Cancellation request reuses active order.
  20. Cancellation reply is Thai.
  21. Cancellation response does not say or imply request recorded /
      submitted / forwarded / escalated / order cancelled / refund
      initiated / 24-hour response.
  22. Cancellation metadata is exactly action_executed=false,
      escalation_created=false, cancellation_created=false.
  23. Unknown order returns an honest Thai error.
  24. New explicit order has priority over stale active order.
  25. Pending workflow order is not replaced by stale active order.
  26. Product-price / buying messages never trigger ORDER_TOTAL /
      PURCHASED_ITEMS.
  27. Every relevant customer-facing response remains Thai.
  28. Session state from one test cannot contaminate another test.

Test isolation:
- Temporary SQLite database initialised with the REAL production functions
  (orders schema + 5 sample orders + store schema + 12 products), then two
  Task 8A orders are seeded directly (ORD-8001, ORD-8002) with order_items.
- store_db.STORE_DB_PATH and the orchestrator db path are redirected to the
  temporary DB. The real data/orders.db size + mtime are recorded before and
  after the module and asserted unchanged.
- DeepSeek is disabled at module level AND openai.OpenAI is patched to raise,
  so any attempt to construct an external LLM client fails the test.
- The global SessionManager is replaced with a fresh instance before every
  test (session isolation). Unique session ids are used throughout.
- Policy retrieval (case 25) goes through the canned-evaluator pattern — no
  ChromaDB / SentenceTransformer / DeepSeek.
"""

import os
import re
import sqlite3
import tempfile

import pytest
from httpx import ASGITransport, AsyncClient

import app.agents.orchestrator as orch
import app.db.store as store_db
from app.agents import set_db_path
from app.agents.session_manager import SessionManager
from app.db.orders import init_database
from app.api.server import app

# Real database path — captured BEFORE the store module path is redirected.
REAL_DB = str(store_db.STORE_DB_PATH)

_THAI_RE = re.compile(r"[\u0E00-\u0E7F]")

# Cancellation claims that must NEVER appear (case 21).
_BANNED_CANCEL_CLAIMS = (
    "recorded",
    "submitted",
    "forwarded to staff",
    "forwarded",
    "escalat",
    "order cancelled",
    "order canceled",
    "refund initiated",
    "24-hour",
    "24 hours",
    "within 24",
)


# ── Canned policy evaluator result (Thai clauses; same pattern as the
#    existing test_session_refund_routing.py canned evaluator) ─────────────

CANNED_POLICY = {
    "intent": "STORE_POLICY",
    "agent": "store_policy_evaluator",
    "query": "refund return cancellation policy",
    "retrieved_clauses": [
        {
            "text": (
                "ลูกค้าสามารถขอคืนสินค้าได้ภายใน 14 วันนับจากวันที่ได้รับสินค้า "
                "สินค้าต้องอยู่ในสภาพเดิม ไม่มีการใช้งาน และมีบรรจุภัณฑ์ครบถ้วน"
            ),
            "language": "th",
            "policy_type": "return",
            "source_filename": "return_policy.md",
            "chunk_id": "return-001",
            "section_title": "Section 1: Standard Return Window",
            "similarity_score": 0.83,
        },
    ],
    "retrieval_success": True,
    "policy_sources": ["return_policy.md"],
    "retrieved_chunk_count": 1,
    "top_similarity_score": 0.83,
    "deterministic_response": (
        "ตามนโยบายการคืนสินค้าของ SiamCart: "
        "ลูกค้าสามารถขอคืนสินค้าได้ภายใน 14 วันนับจากวันที่ได้รับสินค้า"
    ),
    "requires_clarification": False,
    "simulated_human_review": False,
    "evidence": {
        "policy_type": "return",
        "primary_source": "return_policy.md",
        "retrieved_chunks": 1,
        "policy_sources": ["return_policy.md"],
        "retrieved_chunk_count": 1,
        "top_similarity_score": 0.83,
    },
    "response": (
        "ตามนโยบายการคืนสินค้าของ SiamCart: "
        "ลูกค้าสามารถขอคืนสินค้าได้ภายใน 14 วันนับจากวันที่ได้รับสินค้า"
    ),
    "response_source": "deterministic",
    "llm_enabled": False,
    "llm_fallback_used": False,
    "llm_latency_ms": 0.0,
    "llm_error_type": None,
    "grounding_validation_passed": None,
}


# ── helpers ───────────────────────────────────────────────────────────────


def _db_stat(path):
    try:
        st = os.stat(path)
        return (st.st_size, st.st_mtime_ns)
    except OSError:
        return None


def _assert_thai(text, label="response"):
    assert _THAI_RE.search(text or ""), f"{label} is not Thai: {text!r}"


async def _chat(client, message, session_id, order_id=None):
    payload = {"message": message, "session_id": session_id}
    if order_id is not None:
        payload["order_id"] = order_id
    return await client.post("/api/chat", json=payload)


async def _establish_active(client, session_id, order="ORD-8001"):
    """One explicit order turn — establishes the session active order."""
    resp = await _chat(client, f"Where is {order}?", session_id)
    assert resp.status_code == 200
    data = resp.json()
    assert data["active_order_id"] == order
    return data


def _seed_task8a_orders(db_path):
    """Seed ORD-8001 (paid/2 items/1980) and ORD-8002 (pending/1 item/1290)."""
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("PRAGMA foreign_keys=ON")
        # ORD-8001 — paid / processing / not_shipped, two products, total 1980
        conn.execute(
            """
            INSERT INTO orders (
                order_id, customer_name, product_name, order_status,
                payment_status, shipment_status, tracking_number,
                shipping_provider, purchase_date, estimated_delivery_date,
                total_amount, payment_method, paid_at, created_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                "ORD-8001", "Somchai Test",
                "Linen Everyday Blouse, Classic Denim Jacket",
                "processing", "paid", "not_shipped",
                "FLASH-TRK-8001", "Flash Express",
                "2026-08-01", "2026-08-05",
                1980.0, "card", "2026-08-01T09:30:00", "2026-08-01T09:00:00",
            ),
        )
        conn.execute(
            """
            INSERT INTO order_items (
                order_id, product_id, product_name, quantity, unit_price, line_total
            ) VALUES (?,?,?,?,?,?)
            """,
            ("ORD-8001", "PROD-001", "Linen Everyday Blouse", 1, 690.0, 690.0),
        )
        conn.execute(
            """
            INSERT INTO order_items (
                order_id, product_id, product_name, quantity, unit_price, line_total
            ) VALUES (?,?,?,?,?,?)
            """,
            ("ORD-8001", "PROD-002", "Classic Denim Jacket", 1, 1290.0, 1290.0),
        )
        # ORD-8002 — pending / processing / not_shipped, one product, total 1290
        conn.execute(
            """
            INSERT INTO orders (
                order_id, customer_name, product_name, order_status,
                payment_status, shipment_status, tracking_number,
                shipping_provider, purchase_date, estimated_delivery_date,
                total_amount, payment_method, created_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                "ORD-8002", "Somchai Test", "Classic Denim Jacket",
                "processing", "pending", "not_shipped", "", "",
                "2026-08-02", "2026-08-06",
                1290.0, "cash_on_delivery", "2026-08-02T10:00:00",
            ),
        )
        conn.execute(
            """
            INSERT INTO order_items (
                order_id, product_id, product_name, quantity, unit_price, line_total
            ) VALUES (?,?,?,?,?,?)
            """,
            ("ORD-8002", "PROD-002", "Classic Denim Jacket", 1, 1290.0, 1290.0),
        )
        conn.commit()
    finally:
        conn.close()


# ── fixtures ───────────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def tmp_db():
    """Temporary SQLite database, initialized with real production functions.

    Redirects store_db.STORE_DB_PATH and the orchestrator db path. Proves at
    teardown that the REAL data/orders.db (size + mtime) was never touched.
    """
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    init_database(path)  # orders schema + 5 sample orders + store schema + 12 products
    _seed_task8a_orders(path)

    real_before = _db_stat(REAL_DB)

    original_path = store_db.STORE_DB_PATH
    store_db.STORE_DB_PATH = path
    set_db_path(path)
    try:
        yield path
        # Isolation proof: the real database must be completely untouched.
        real_after = _db_stat(REAL_DB)
        assert real_after == real_before, (
            f"REAL database was modified by Task 8A tests: {REAL_DB} "
            f"before={real_before} after={real_after}"
        )
        print(
            f"\n[isolation] real DB before={real_before} after={real_after} — untouched"
        )
    finally:
        store_db.STORE_DB_PATH = original_path
        set_db_path(None)
        try:
            os.unlink(path)
        except OSError:
            pass


@pytest.fixture(scope="module")
def client(tmp_db):
    """Async HTTP client over the FastAPI app (no lifespan / no real DB init)."""
    transport = ASGITransport(app=app)
    c = AsyncClient(transport=transport, base_url="http://test")
    yield c


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    """Per-test isolation: fresh SessionManager + DeepSeek/LLM fully mocked.

    - Replaces the global orchestrator SessionManager so session state from
      one test can never contaminate another (case 28).
    - Disables DeepSeek in both orchestrator and llm_generator, and patches
      openai.OpenAI to raise — proving no external LLM call is ever made.
    - Neutralizes the experiment log writer so logs/experiment.jsonl is not
      mutated by the suite.
    """
    orch._SESSION_STORE = SessionManager()
    monkeypatch.setattr(orch, "DEEPSEEK_ENABLED", False)
    import app.agents.llm_generator as gen
    monkeypatch.setattr(gen, "DEEPSEEK_ENABLED", False)

    import openai

    def _no_client(*_a, **_k):
        raise AssertionError(
            "External LLM (OpenAI) client was constructed during Task 8A tests"
        )

    monkeypatch.setattr(openai, "OpenAI", _no_client)

    import app.api.server as server
    monkeypatch.setattr(server, "write_experiment_log", lambda *_a, **_k: None)
    yield


@pytest.fixture
def canned_policy(monkeypatch):
    """Route policy retrieval through a deterministic canned evaluator result."""
    monkeypatch.setattr(
        "app.agents.orchestrator.evaluate_policy_query",
        lambda query, top_k=3: dict(CANNED_POLICY, query=query),
    )
    return CANNED_POLICY


# ── 1-3. ChatRequest / active-order storage ───────────────────────────────


async def test_chat_request_valid_without_order_id(client):
    """1. ChatRequest remains valid without order_id (backward compatible)."""
    resp = await _chat(client, "สวัสดี", "s01")
    assert resp.status_code == 200
    data = resp.json()
    assert data["intent"] == "GREETING"
    assert data["session_id"] == "s01"
    assert data["active_order_id"] is None
    _assert_thai(data["response"])


async def test_request_order_id_stores_active_order(client):
    """2. Request order_id=ORD-8001 (Ask AI card) stores active_order_id."""
    resp = await _chat(client, "สวัสดี", "s02", order_id="ORD-8001")
    assert resp.status_code == 200
    data = resp.json()
    assert data["active_order_id"] == "ORD-8001"
    assert data["intent"] == "GREETING"
    _assert_thai(data["response"])


async def test_explicit_order_in_message_stores_active_order(client):
    """3. An explicit ORD-8001 inside the message stores active_order_id."""
    resp = await _chat(client, "Where is ORD-8001?", "s03")
    assert resp.status_code == 200
    data = resp.json()
    assert data["order_id"] == "ORD-8001"
    assert data["active_order_id"] == "ORD-8001"
    assert data["intent"] == "ORDER_STATUS"
    assert "ORD-8001" in data["response"]
    _assert_thai(data["response"])


# ── 4-7. Active-order reuse (follow-up / pronoun turns) ───────────────────


async def test_paid_followup_reuses_active_order(client):
    """4. 'Has it been paid?' reuses ORD-8001 and answers from SQLite."""
    await _establish_active(client, "s04")
    resp = await _chat(client, "Has it been paid?", "s04")
    assert resp.status_code == 200
    data = resp.json()
    assert data["intent"] == "PAYMENT_STATUS"
    assert data["active_order_id"] == "ORD-8001"
    assert data["order_id"] == "ORD-8001"
    assert "ORD-8001" in data["response"]
    assert "ได้รับชำระเงินแล้ว" in data["response"]
    _assert_thai(data["response"])


async def test_thai_shipment_followup_reuses_active_order(client):
    """5. 'ส่งของหรือยัง' reuses ORD-8001 (shipment status)."""
    await _establish_active(client, "s05")
    resp = await _chat(client, "ส่งของหรือยัง", "s05")
    assert resp.status_code == 200
    data = resp.json()
    assert data["intent"] == "SHIPMENT_STATUS"
    assert data["active_order_id"] == "ORD-8001"
    assert "ORD-8001" in data["response"]
    assert "ยังไม่ได้จัดส่ง" in data["response"]
    _assert_thai(data["response"])


async def test_thai_this_order_followup_reuses_active_order(client):
    """6. 'คำสั่งซื้อนี้อยู่ที่ไหน' reuses ORD-8001 (strong Thai referential)."""
    await _establish_active(client, "s06")
    resp = await _chat(client, "คำสั่งซื้อนี้อยู่ที่ไหน", "s06")
    assert resp.status_code == 200
    data = resp.json()
    assert data["intent"] == "ORDER_STATUS"
    assert data["active_order_id"] == "ORD-8001"
    assert "ORD-8001" in data["response"]
    _assert_thai(data["response"])


async def test_english_pronoun_followup_reuses_active_order(client):
    """7. English 'it' follow-up ('Where is it?') reuses ORD-8001."""
    await _establish_active(client, "s07")
    resp = await _chat(client, "Where is it?", "s07")
    assert resp.status_code == 200
    data = resp.json()
    assert data["intent"] == "ORDER_STATUS"
    assert data["active_order_id"] == "ORD-8001"
    assert "ORD-8001" in data["response"]
    _assert_thai(data["response"])


# ── 8-10. Override / survive / no-hijack rules ────────────────────────────


async def test_explicit_ord8002_overrides_active_ord8001(client):
    """8. Explicit ORD-8002 overrides the active ORD-8001."""
    await _establish_active(client, "s08", order="ORD-8001")
    resp = await _chat(client, "Where is ORD-8002?", "s08")
    assert resp.status_code == 200
    data = resp.json()
    assert data["order_id"] == "ORD-8002"
    assert data["active_order_id"] == "ORD-8002"
    assert "ORD-8002" in data["response"]
    assert "ORD-8001" not in data["response"]
    _assert_thai(data["response"])


async def test_greeting_does_not_clear_active_order(client):
    """9. A greeting never clears the session active order."""
    await _establish_active(client, "s09")
    resp = await _chat(client, "สวัสดี", "s09")
    assert resp.status_code == 200
    data = resp.json()
    assert data["intent"] == "GREETING"
    assert data["active_order_id"] == "ORD-8001"
    _assert_thai(data["response"])


async def test_product_price_question_not_hijacked_by_active_order(client):
    """10. A product-price question is never hijacked by the active order."""
    await _establish_active(client, "s10")
    resp = await _chat(client, "How much is this product?", "s10")
    assert resp.status_code == 200
    data = resp.json()
    assert data["intent"] not in ("ORDER_TOTAL", "PURCHASED_ITEMS", "ORDER_STATUS")
    assert "ยอดรวมของคำสั่งซื้อ" not in data["response"]
    assert data["active_order_id"] == "ORD-8001"  # survives, but unused
    _assert_thai(data["response"])


# ── 11-14. ORDER_TOTAL / PURCHASED_ITEMS from SQLite ──────────────────────


async def test_what_is_the_total_returns_sqlite_total(client):
    """11. 'What is the total?' returns ORD-8001's SQLite total (1980)."""
    await _establish_active(client, "s11")
    resp = await _chat(client, "What is the total?", "s11")
    assert resp.status_code == 200
    data = resp.json()
    assert data["intent"] == "ORDER_TOTAL"
    assert data["active_order_id"] == "ORD-8001"
    assert float(data["evidence"]["total_amount"]) == 1980.0
    assert "ORD-8001" in data["response"]
    assert "฿1,980" in data["response"]
    _assert_thai(data["response"])


async def test_thai_total_question_returns_sqlite_total(client):
    """12. Thai total question ('ยอดรวมเท่าไร') returns the SQLite total."""
    await _establish_active(client, "s12")
    resp = await _chat(client, "ยอดรวมเท่าไร", "s12")
    assert resp.status_code == 200
    data = resp.json()
    assert data["intent"] == "ORDER_TOTAL"
    assert float(data["evidence"]["total_amount"]) == 1980.0
    assert "฿1,980" in data["response"]
    _assert_thai(data["response"])


async def test_what_products_did_i_buy_returns_every_item(client):
    """13. 'What products did I buy?' returns every ORD-8001 order item."""
    await _establish_active(client, "s13")
    resp = await _chat(client, "What products did I buy?", "s13")
    assert resp.status_code == 200
    data = resp.json()
    assert data["intent"] == "PURCHASED_ITEMS"
    items = data["evidence"]["items"]
    assert len(items) == 2
    names = {i["product_name"] for i in items}
    assert names == {"Linen Everyday Blouse", "Classic Denim Jacket"}
    assert "2 รายการ" in data["response"]
    assert "Linen Everyday Blouse" in data["response"]
    assert "Classic Denim Jacket" in data["response"]
    _assert_thai(data["response"])


async def test_thai_purchased_items_question_returns_every_item(client):
    """14. Thai purchased-items question returns every order item."""
    await _establish_active(client, "s14")
    resp = await _chat(client, "ฉันซื้อสินค้าอะไรบ้าง", "s14")
    assert resp.status_code == 200
    data = resp.json()
    assert data["intent"] == "PURCHASED_ITEMS"
    items = data["evidence"]["items"]
    assert len(items) == 2
    names = {i["product_name"] for i in items}
    assert names == {"Linen Everyday Blouse", "Classic Denim Jacket"}
    assert "2 รายการ" in data["response"]
    _assert_thai(data["response"])


# ── 15-18. remove-context / reset endpoints ───────────────────────────────


async def test_remove_context_clears_active_order_id(client):
    """15. POST /api/chat/remove-context clears active_order_id.

    After removal a pronoun follow-up must no longer reuse the order — it
    asks for the order ID instead.
    """
    await _establish_active(client, "s15")
    resp = await client.post(
        "/api/chat/remove-context", json={"session_id": "s15"}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["active_order_id"] is None

    follow = await _chat(client, "Has it been paid?", "s15")
    data = follow.json()
    assert data["active_order_id"] is None
    assert "ORD-8001" not in data["response"]
    assert "หมายเลขคำสั่งซื้อ" in data["response"]
    _assert_thai(data["response"])


async def test_remove_context_preserves_same_session(client):
    """16. Remove-context preserves the same server-side session.

    Only the active order context is removed; the session object, its id and
    pending workflow state survive untouched.
    """
    await _chat(client, "สวัสดี", "s16")
    await _chat(client, "Where is ORD-8001?", "s16")
    orch._SESSION_STORE.update_state("s16", {"pending_intent": "RETURN_REFUND"})

    session_before = orch._SESSION_STORE.get_session("s16")

    resp = await client.post(
        "/api/chat/remove-context", json={"session_id": "s16"}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["session_id"] == "s16"
    assert body["active_order_id"] is None

    # The same server-side session object is preserved — not recreated.
    assert orch._SESSION_STORE.get_session("s16") is session_before
    # Only the order context is removed — pending workflow state is untouched.
    assert session_before["state"]["active_order_id"] is None
    assert session_before["state"]["pending_intent"] == "RETURN_REFUND"

    # The session keeps working with the same session_id.
    again = await _chat(client, "สวัสดี", "s16")
    assert again.status_code == 200
    assert again.json()["session_id"] == "s16"


async def test_reset_clears_all_session_state(client):
    """17. POST /api/chat/reset clears active_order_id, pending_intent,
    pending_subtype, missing_slots and collected_slots."""
    orch._SESSION_STORE.update_state(
        "s17",
        {
            "active_order_id": "ORD-8001",
            "pending_intent": "RETURN_REFUND",
            "pending_subtype": "cancel",
            "missing_slots": ["order_id"],
            "collected_slots": {"order_id": "ORD-8002"},
        },
    )
    resp = await client.post("/api/chat/reset", json={"session_id": "s17"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["active_order_id"] is None

    state = orch._SESSION_STORE.get_session("s17")["state"]
    assert state["active_order_id"] is None
    assert state["pending_intent"] is None
    assert state["pending_subtype"] is None
    assert state["missing_slots"] == []
    assert state["collected_slots"] == {}


async def test_reset_and_remove_context_unknown_session_safe(client):
    """18. Reset and remove-context handle unknown session IDs safely."""
    resp = await client.post("/api/chat/reset", json={"session_id": "ghost-reset"})
    assert resp.status_code == 200
    assert resp.json() == {"ok": True, "session_id": "ghost-reset", "active_order_id": None}

    resp = await client.post(
        "/api/chat/remove-context", json={"session_id": "ghost-remove"}
    )
    assert resp.status_code == 200
    assert resp.json() == {
        "ok": True,
        "session_id": "ghost-remove",
        "active_order_id": None,
    }


# ── 19-22. Honest cancellation guidance ───────────────────────────────────


async def test_cancellation_request_reuses_active_order(client):
    """19. A cancellation request reuses the active order.

    Demo-repair: a cancellable order (processing + not_shipped) now gets a
    Thai confirmation ask instead of the old "cannot cancel automatically"
    guidance; the order is NOT cancelled yet.
    """
    await _establish_active(client, "s19")
    resp = await _chat(client, "cancel my order", "s19")
    assert resp.status_code == 200
    data = resp.json()
    assert data["intent"] == "OUT_OF_SCOPE"
    assert data["active_order_id"] == "ORD-8001"
    assert data["evidence"].get("order_id") == "ORD-8001"
    assert "ORD-8001" in data["response"]
    assert "ต้องการยืนยันการยกเลิก" in data["response"]
    assert data["action_executed"] is False
    _assert_thai(data["response"])


async def test_cancellation_reply_is_thai(client):
    """20. The cancellation reply is Thai (Thai-dominant, not an English reply).

    The fixed UI menu name "My Orders" may appear inside an otherwise Thai
    sentence — the check is that Thai characters dominate the reply.
    """
    await _establish_active(client, "s20")
    resp = await _chat(client, "ยกเลิกคำสั่งซื้อ", "s20")
    assert resp.status_code == 200
    data = resp.json()
    thai_chars = len(_THAI_RE.findall(data["response"]))
    eng_chars = len(re.findall(r"[A-Za-z]", data["response"]))
    assert thai_chars > 0
    assert thai_chars > eng_chars  # Thai-dominant, never an English reply


async def test_cancellation_no_false_action_claims(client):
    """21. Cancellation guidance never claims an action happened.

    Must not say or imply: request recorded, request submitted, forwarded to
    staff, escalated, order cancelled, refund initiated, 24-hour response.
    """
    await _establish_active(client, "s21")
    resp = await _chat(client, "cancel my order", "s21")
    assert resp.status_code == 200
    data = resp.json()
    lower = data["response"].lower()
    for banned in _BANNED_CANCEL_CLAIMS:
        assert banned not in lower, f"cancellation claim leaked: {banned!r}"
    # The reply explicitly states the cancellation was NOT executed.
    assert "ยังไม่ได้ยกเลิก" in data["response"]
    _assert_thai(data["response"])


async def test_cancellation_metadata_exactly_false(client):
    """22. Cancellation metadata is exactly action_executed=false,
    escalation_created=false, cancellation_created=false."""
    await _establish_active(client, "s22")
    resp = await _chat(client, "cancel my order", "s22")
    assert resp.status_code == 200
    data = resp.json()
    assert data["action_executed"] is False
    assert data["escalation_created"] is False
    assert data["cancellation_created"] is False


# ── 23. Unknown order ─────────────────────────────────────────────────────


async def test_unknown_order_returns_honest_thai_error(client):
    """23. Unknown order returns an honest Thai error (no invented facts)."""
    resp = await _chat(client, "Where is ORD-9999?", "s23")
    assert resp.status_code == 200
    data = resp.json()
    assert data["intent"] == "ORDER_NOT_FOUND"
    assert data["evidence"] == {}
    assert "ไม่พบข้อมูลคำสั่งซื้อ ORD-9999" in data["response"]
    _assert_thai(data["response"])


# ── 24-25. Priority rules ─────────────────────────────────────────────────


async def test_new_explicit_order_priority_over_stale_active(client):
    """24. A new explicit order in the message wins over a stale active order
    (even when the request carries the stale order_id context)."""
    await _establish_active(client, "s24", order="ORD-8001")
    resp = await _chat(client, "Where is ORD-8002?", "s24", order_id="ORD-8001")
    assert resp.status_code == 200
    data = resp.json()
    assert data["order_id"] == "ORD-8002"
    assert data["active_order_id"] == "ORD-8002"
    assert "ORD-8002" in data["response"]
    assert "ORD-8001" not in data["response"]
    _assert_thai(data["response"])


async def test_pending_workflow_order_not_replaced_by_stale_active(client, canned_policy):
    """25. A pending workflow order (collected ORD-8002) is not replaced by a
    stale active order (ORD-8001) when the follow-up names no order."""
    orch._SESSION_STORE.update_state(
        "s25",
        {
            "active_order_id": "ORD-8001",  # stale active
            "pending_intent": "RETURN_REFUND",
            "pending_subtype": None,
            "missing_slots": [],
            "collected_slots": {"order_id": "ORD-8002", "reason": "i dont like"},
        },
    )
    resp = await _chat(client, "ไม่ชอบสินค้า", "s25")
    assert resp.status_code == 200
    data = resp.json()
    assert data["intent"] == "RETURN_REFUND"
    assert data["evidence"].get("order_id") == "ORD-8002"
    assert "ORD-8002" in data["response"]
    _assert_thai(data["response"])


# ── 26-27. No-hijack + Thai policy ────────────────────────────────────────


async def test_product_price_buy_messages_never_trigger_order_intents(client):
    """26. These messages must NOT trigger ORDER_TOTAL / PURCHASED_ITEMS:
    'How much is this product?', 'สินค้านี้ราคาเท่าไร', 'ฉันต้องการซื้อสินค้า'."""
    await _establish_active(client, "s26")
    for msg in (
        "How much is this product?",
        "สินค้านี้ราคาเท่าไร",
        "ฉันต้องการซื้อสินค้า",
    ):
        resp = await _chat(client, msg, "s26")
        assert resp.status_code == 200
        data = resp.json()
        assert data["intent"] not in ("ORDER_TOTAL", "PURCHASED_ITEMS"), msg
        assert "ยอดรวมของคำสั่งซื้อ" not in data["response"], msg
        _assert_thai(data["response"], label=f"response for {msg!r}")


async def test_all_customer_facing_responses_remain_thai(client):
    """27. Every relevant customer-facing response stays Thai across the
    whole Task 8A surface (greeting, status, total, items, cancel, error)."""
    # Greeting
    r = (await _chat(client, "สวัสดี", "s27")).json()
    _assert_thai(r["response"])
    # Establish active order, then a status follow-up
    await _establish_active(client, "s27")
    r = (await _chat(client, "Has it been paid?", "s27")).json()
    _assert_thai(r["response"])
    # Total
    r = (await _chat(client, "What is the total?", "s27")).json()
    _assert_thai(r["response"])
    # Purchased items
    r = (await _chat(client, "What products did I buy?", "s27")).json()
    _assert_thai(r["response"])
    # Cancellation
    r = (await _chat(client, "cancel my order", "s27")).json()
    _assert_thai(r["response"])
    # Unknown order
    r = (await _chat(client, "Where is ORD-9999?", "s27")).json()
    _assert_thai(r["response"])
    # No active order → clarification request
    r = (await _chat(client, "Has it been paid?", "s27-other")).json()
    _assert_thai(r["response"])


# ── 28. Session isolation ─────────────────────────────────────────────────


async def test_session_state_does_not_contaminate_other_sessions(client):
    """28. Active order set in one session never leaks into another session."""
    await _establish_active(client, "s28a")
    assert orch._SESSION_STORE.get_session("s28a")["state"]["active_order_id"] == "ORD-8001"

    # A different session has no active order and must ask for the order ID.
    other = await _chat(client, "Has it been paid?", "s28b")
    data = other.json()
    assert data["active_order_id"] is None
    assert "ORD-8001" not in data["response"]
    assert "หมายเลขคำสั่งซื้อ" in data["response"]
    _assert_thai(data["response"])
