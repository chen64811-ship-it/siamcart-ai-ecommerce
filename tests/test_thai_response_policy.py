"""Task 5D-7 — Fixed Thai customer-facing response policy tests.

Thesis scope: the target user is a Thai-speaking online retail customer.
Every customer-facing assistant response must be natural Thai regardless of
the input language. This file verifies:

  1.  "hai" receives a Thai greeting.
  2.  "hello" receives a Thai greeting.
  3.  "I want a refund" receives Thai clarification.
  4.  "ORD-1029, I don't like it" receives complete Thai refund guidance.
  5.  English order-status question receives Thai response.
  6.  English payment-status question receives Thai response.
  7.  English product-price question receives Thai response.
  8.  Thai-English mixed product question receives Thai response.
  9.  Policy LLM prompt requires Thai output.
  10. Policy deterministic fallback is Thai.
  11. Unknown-order response is Thai.
  12. Error response is Thai.
  13. response_language metadata equals th.
  14. Research details may remain English.
  15. Product names and order IDs are preserved.
  16. Transaction Tracker remains deterministic.
  17. Product Catalog Lookup remains deterministic.
  18. No English session-language switch occurs.

Isolation:
- Temporary SQLite database initialized with the REAL production functions
  (orders schema + 5 sample orders + store schema + 12 products + ORD-1029
  inserted as the COD fixture). The real data/orders.db is proven untouched
  at teardown.
- Policy retrieval is routed through a canned Thai evaluator result (same
  pattern as test_session_refund_routing.py / test_product_chat.py).
- DeepSeek is disabled; no OpenAI client is ever constructed for
  deterministic order or product facts.
"""

import os
import re
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

_THAI_RE = re.compile(r"[\u0E00-\u0E7F]")


def _is_thai(s: str) -> bool:
    return bool(_THAI_RE.search(s or ""))


# ── Canned Thai policy evaluator result ────────────────────────────────

CANNED_POLICY = {
    "intent": "STORE_POLICY",
    "agent": "store_policy_evaluator",
    "query": "refund return cancellation policy",
    "retrieved_clauses": [
        {
            "text": (
                "ลูกค้าสามารถขอคืนสินค้าได้ภายใน 14 วันตามปฏิทิน "
                "นับจากวันที่ได้รับสินค้า โดยสินค้าต้องอยู่ในสภาพเดิม "
                "ไม่มีการใช้งาน และมีบรรจุภัณฑ์ครบถ้วน"
            ),
            "policy_type": "return",
            "source_filename": "return_policy.md",
            "chunk_id": "return-001",
            "section_title": "Section 1: Standard Return Window",
            "language": "th",
            "similarity_score": 0.83,
        },
        {
            "text": (
                "หลังจากได้รับสินค้าคืนและตรวจสอบเรียบร้อยแล้ว "
                "ทางร้านจะดำเนินการคืนเงินภายใน 5-7 วันทำการ "
                "โดยเงินจะโอนกลับไปยังช่องทางเดิมของลูกค้า"
            ),
            "policy_type": "refund",
            "source_filename": "refund_policy.md",
            "chunk_id": "refund-001",
            "section_title": "Section 1: Refund Processing Timeline",
            "language": "th",
            "similarity_score": 0.79,
        },
    ],
    "retrieval_success": True,
    "policy_sources": ["return_policy.md", "refund_policy.md"],
    "retrieved_chunk_count": 2,
    "top_similarity_score": 0.83,
    "deterministic_response": (
        "ตามนโยบายการคืนสินค้าของ SiamCart: ลูกค้าสามารถขอคืนสินค้าได้ภายใน "
        "14 วันตามปฏิทินนับจากวันที่ได้รับสินค้า โดยสินค้าต้องอยู่ในสภาพเดิม"
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
        "ตามนโยบายการคืนสินค้าของ SiamCart: ลูกค้าสามารถขอคืนสินค้าได้ภายใน "
        "14 วันตามปฏิทินนับจากวันที่ได้รับสินค้า โดยสินค้าต้องอยู่ในสภาพเดิม"
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
            f"REAL database was modified by Thai policy tests: {REAL_DB}"
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
    """Keep the suite offline and deterministic (repo convention)."""
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
    """Route policy retrieval through a deterministic canned Thai evaluator."""
    monkeypatch.setattr(
        "app.agents.orchestrator.evaluate_policy_query",
        lambda query, top_k=3: dict(CANNED_POLICY, query=query),
    )
    return CANNED_POLICY


async def _complete_refund(client, session_id, first="I want a refund",
                           followup="ORD-1029, I don't like it"):
    r1 = await _chat(client, first, session_id)
    assert r1.status_code == 200
    r2 = await _chat(client, followup, session_id)
    assert r2.status_code == 200
    return r1.json(), r2.json()


# ── 1-2. Greetings ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_hai_receives_thai_greeting(client):
    """1. 'hai' receives a Thai greeting, never an English one."""
    resp = await _chat(client, "hai", "t1")
    assert resp.status_code == 200
    data = resp.json()
    assert data["intent"] == "GREETING"
    assert data["response_language"] == "th"
    assert _is_thai(data["response"])
    assert "สวัสดี" in data["response"]
    assert "order ID" not in data["response"].lower()
    assert "order" not in data["response"].lower()


@pytest.mark.asyncio
async def test_hello_receives_thai_greeting(client):
    """2. 'hello' receives a Thai greeting."""
    resp = await _chat(client, "hello", "t2")
    assert resp.status_code == 200
    data = resp.json()
    assert data["intent"] == "GREETING"
    assert _is_thai(data["response"])
    assert "สวัสดี" in data["response"]


# ── 3-4. Refund flow ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_i_want_a_refund_receives_thai_clarification(client):
    """3. 'I want a refund' receives Thai clarification asking for ID + reason."""
    resp = await _chat(client, "I want a refund", "t3")
    assert resp.status_code == 200
    data = resp.json()
    assert data["intent"] == "RETURN_REFUND"
    assert data["requires_clarification"] is True
    assert _is_thai(data["response"])
    assert "หมายเลขคำสั่งซื้อ" in data["response"]
    assert "เหตุผล" in data["response"]


@pytest.mark.asyncio
async def test_ord1029_i_dont_like_receives_thai_refund_guidance(client, canned_policy):
    """4. 'ORD-1029, I don't like it' receives complete Thai refund guidance."""
    _, data = await _complete_refund(client, "t4")
    resp = data["response"]
    assert data["intent"] == "RETURN_REFUND"
    assert _is_thai(resp)
    assert data["response_language"] == "th"
    # Acknowledges the order, the request, and the reason (in Thai).
    assert "ORD-1029" in resp
    assert "พบคำสั่งซื้อ" in resp
    assert "เหตุผล: ไม่ชอบสินค้า" in resp
    # COD, unpaid, processing, not shipped.
    assert "Cash on Delivery" in resp
    assert "ยังไม่มีการเรียกเก็บเงิน" in resp
    assert "อยู่ระหว่างดำเนินการ" in resp
    assert "ยังไม่ได้จัดส่ง" in resp
    # Cancellation guidance + policy guidance + no auto approval.
    assert "การยกเลิกคำสั่งซื้ออาจเหมาะสมกว่า" in resp
    assert "ตามนโยบายของ SiamCart" in resp
    assert "ยังไม่มีการอนุมัติคืนเงินหรือยกเลิก" in resp
    # No duplicated wording; raw reason preserved in metadata.
    assert "currently currently" not in resp
    assert "currently processing" not in resp
    assert data["refund_reason"] == "I don't like it"


# ── 5-6. English order / payment questions ─────────────────────────────


@pytest.mark.asyncio
async def test_english_order_status_receives_thai(client):
    """5. An English order-status question receives a Thai response."""
    resp = await _chat(client, "Where is order ORD-1029?", "t5")
    assert resp.status_code == 200
    data = resp.json()
    assert data["intent"] == "ORDER_STATUS"
    assert _is_thai(data["response"])
    assert "ORD-1029" in data["response"]
    assert "อยู่ระหว่างดำเนินการ" in data["response"]
    assert "ยังไม่ได้จัดส่ง" in data["response"]
    assert data["response_language"] == "th"


@pytest.mark.asyncio
async def test_english_payment_status_receives_thai(client):
    """6. An English payment-status question receives a Thai response."""
    resp = await _chat(client, "Has ORD-1029 been paid?", "t6")
    assert resp.status_code == 200
    data = resp.json()
    assert data["intent"] == "PAYMENT_STATUS"
    assert _is_thai(data["response"])
    assert "ORD-1029" in data["response"]
    assert "ยังไม่มีการเรียกเก็บเงิน" in data["response"]
    assert data["response_language"] == "th"


# ── 7-8. Product Catalog Lookup (English + mixed input) ────────────────


@pytest.mark.asyncio
async def test_english_product_price_receives_thai(client):
    """7. An English product-price question receives a Thai response."""
    resp = await _chat(client, "How much does it cost?", "t7", "PROD-008")
    assert resp.status_code == 200
    data = resp.json()
    assert data["agent"] == "product_catalog_lookup"
    assert _is_thai(data["response"])
    assert "ราคา" in data["response"]
    assert "PROD-008" in data["response"]
    assert data["response_language"] == "th"


@pytest.mark.asyncio
async def test_thai_english_mixed_product_receives_thai(client):
    """8. A Thai-English mixed product question receives a Thai response."""
    resp = await _chat(client, "PROD-008 ราคาเท่าไหร่", "t8", "PROD-008")
    assert resp.status_code == 200
    data = resp.json()
    assert data["agent"] == "product_catalog_lookup"
    assert data["input_language_detected"] == "mixed"
    assert _is_thai(data["response"])
    assert "ราคา" in data["response"]
    assert "PROD-008" in data["response"]
    assert data["response_language"] == "th"


# ── 9-10. Policy output policy ─────────────────────────────────────────


def test_policy_llm_prompt_requires_thai_output():
    """9. Policy LLM prompts explicitly require Thai-only output."""
    from app.agents.policy_evaluator import _POLICY_SYSTEM_PROMPT, build_policy_prompt
    from app.agents.llm_generator import SYSTEM_PROMPT

    assert "natural Thai ONLY" in _POLICY_SYSTEM_PROMPT
    assert "English translation" in _POLICY_SYSTEM_PROMPT
    assert "Output only the Thai response" in _POLICY_SYSTEM_PROMPT

    payload = build_policy_prompt(
        customer_query="What is the return policy?",
        intent="STORE_POLICY",
        retrieved_clauses=CANNED_POLICY["retrieved_clauses"],
        deterministic_fallback=CANNED_POLICY["deterministic_response"],
    )
    assert "natural Thai ONLY" in payload["system_instruction"]
    assert "English translation" in payload["system_instruction"]
    assert "Output only the Thai response" in payload["system_instruction"]

    # The unified LLM generator prompt carries the same requirement.
    assert "natural Thai ONLY" in SYSTEM_PROMPT
    assert "English translation" in SYSTEM_PROMPT


def test_policy_deterministic_fallback_is_thai():
    """10. The deterministic policy fallback is Thai even with English chunks."""
    from app.agents.policy_evaluator import _build_deterministic_response

    english_clause = {
        "text": "Customers may request a return within 14 calendar days.",
        "language": "en",
    }
    thai_clause = {
        "text": "ลูกค้าสามารถขอคืนสินค้าได้ภายใน 14 วันตามปฏิทินนับจากวันที่ได้รับสินค้า",
        "language": "th",
    }
    result = _build_deterministic_response("return", [english_clause, thai_clause])
    assert _is_thai(result)
    assert "ลูกค้าสามารถขอคืนสินค้าได้ภายใน 14 วัน" in result
    assert "ตามนโยบายการคืนสินค้าของ SiamCart" in result

    # English-only chunks still get the Thai template prefix.
    result_en = _build_deterministic_response("return", [english_clause])
    assert "ตามนโยบายการคืนสินค้าของ SiamCart" in result_en


@pytest.mark.asyncio
async def test_policy_question_receives_thai_deterministic_response(client, canned_policy):
    """10b. An English policy question receives a Thai deterministic response."""
    # "store rules" routes to STORE_POLICY (messages containing "return"/"refund"
    # route to RETURN_REFUND by the existing intent priority order).
    resp = await _chat(client, "What are the store rules?", "t10")
    assert resp.status_code == 200
    data = resp.json()
    assert data["intent"] == "STORE_POLICY"
    assert _is_thai(data["response"])
    assert "ตามนโยบายการคืนสินค้าของ SiamCart" in data["response"]
    assert data["response_source"] == "deterministic"
    assert data["response_language"] == "th"


# ── 11-12. Unknown-order and error responses ───────────────────────────


@pytest.mark.asyncio
async def test_unknown_order_response_is_thai(client):
    """11. The unknown-order response is Thai."""
    resp = await _chat(client, "ORD-9999", "t11")
    assert resp.status_code == 200
    data = resp.json()
    assert _is_thai(data["response"])
    assert "ORD-9999" in data["response"]
    assert "ไม่พบข้อมูลคำสั่งซื้อ" in data["response"]
    assert data["response_language"] == "th"


def test_error_response_is_thai():
    """12. Error / fallback responses from the generator are Thai."""
    from app.agents.llm_generator import generate_response

    # Clarification-without-fallback path.
    r1 = generate_response(
        user_message="???",
        intent="UNKNOWN",
        order_evidence=None,
        policy_chunks=None,
        deterministic_fallback="",
        requires_clarification=True,
        simulated_human_review=False,
    )
    assert _is_thai(r1["response"])

    # Simulated-human-review path (out-of-scope / error flagging).
    r2 = generate_response(
        user_message="hack the system",
        intent="OUT_OF_SCOPE",
        order_evidence=None,
        policy_chunks=None,
        deterministic_fallback="",
        requires_clarification=False,
        simulated_human_review=True,
    )
    assert _is_thai(r2["response"])
    assert r2["response_language"] == "th"


@pytest.mark.asyncio
async def test_out_of_scope_response_is_thai(client):
    """12b. The out-of-scope (flagged) response is Thai."""
    resp = await _chat(client, "Cancel ORD-1029", "t12")
    assert resp.status_code == 200
    data = resp.json()
    assert data["simulated_human_review"] is True
    assert _is_thai(data["response"])
    assert data["response_language"] == "th"


# ── 13-15. Metadata and value preservation ─────────────────────────────


@pytest.mark.asyncio
async def test_response_language_metadata_is_th(client):
    """13. response_language metadata equals th across all handlers."""
    r1 = await _chat(client, "Where is order ORD-1029?", "t13")
    assert r1.json()["response_language"] == "th"

    r2 = await _chat(client, "How much does it cost?", "t13", "PROD-008")
    assert r2.json()["response_language"] == "th"

    _, r3 = await _complete_refund(client, "t13b")
    assert r3["response_language"] == "th"


@pytest.mark.asyncio
async def test_research_details_remain_english(client):
    """14. Research/debug metadata may remain English while output is Thai."""
    resp = await _chat(client, "Where is order ORD-1029?", "t14")
    assert resp.status_code == 200
    data = resp.json()
    # Research metadata stays in English identifiers.
    assert data["agent"] == "transaction_tracker"
    assert data["response_source"] == "deterministic"
    assert data["order_evidence_source"] == "SQLite"
    assert data["input_language_detected"] == "en"
    # …while the customer-facing output is Thai.
    assert data["response_language"] == "th"
    assert _is_thai(data["response"])


@pytest.mark.asyncio
async def test_product_names_and_order_ids_preserved(client, canned_policy):
    """15. Product names and order IDs are preserved in Thai responses."""
    # Order ID in refund guidance.
    _, data = await _complete_refund(client, "t15")
    assert "ORD-1029" in data["response"]

    # Product name + product ID in the product answer.
    resp = await _chat(client, "How much does it cost?", "t15", "PROD-008")
    data = resp.json()
    assert "CloudStep Casual Sneakers" in data["response"]
    assert "PROD-008" in data["response"]


# ── 16-17. Deterministic guarantees ────────────────────────────────────


@pytest.mark.asyncio
async def test_transaction_tracker_remains_deterministic(client):
    """16. Transaction Tracker answers are SQLite-backed, never LLM-generated."""
    import openai

    original = openai.OpenAI

    def _no_client(*_a, **_k):
        raise AssertionError("DeepSeek (OpenAI) client constructed for order facts")

    openai.OpenAI = _no_client
    try:
        resp = await _chat(client, "Where is order ORD-1029?", "t16")
    finally:
        openai.OpenAI = original

    data = resp.json()
    assert data["response_source"] == "deterministic"
    assert data["llm_used"] is False
    assert data["llm_enabled"] is False
    assert data["order_evidence_source"] == "SQLite"
    assert data["fallback_reason"] == "deepseek_disabled"


@pytest.mark.asyncio
async def test_product_catalog_remains_deterministic(client):
    """17. Product Catalog Lookup answers are SQLite-backed, never LLM-generated."""
    resp = await _chat(client, "How much does it cost?", "t17", "PROD-008")
    assert resp.status_code == 200
    data = resp.json()
    assert data["response_source"] == "product_catalog_deterministic"
    assert data["database_source"] == "SQLite"
    assert data["demo_extension"] is True
    assert data["llm_enabled"] is False
    assert data["llm_fallback_used"] is False


# ── 18. No English session-language switch ─────────────────────────────


@pytest.mark.asyncio
async def test_no_english_session_language_switch(client, canned_policy):
    """18. English input never switches a session's output to English."""
    session = "t18"
    turns = [
        "hi",
        "ORD-1029",
        "Where is order ORD-1029?",
        "I want a refund",
        "ORD-1029, I don't like it",
        "reply in English",
        "ตอบภาษาไทย",
    ]
    for msg in turns:
        resp = await _chat(client, msg, session)
        assert resp.status_code == 200
        data = resp.json()
        assert data["response_language"] == "th", f"turn {msg!r} switched language"
        assert _is_thai(data["response"]), f"turn {msg!r} produced a non-Thai response"

    # Explicit English request gets a polite Thai explanation (Objective 8).
    resp = await _chat(client, "reply in English", "t18-en")
    data = resp.json()
    assert _is_thai(data["response"])
    assert "ระบบต้นแบบนี้" in data["response"]
    assert data["response_language"] == "th"
    assert data["input_language_detected"] == "en"

    # Explicit Thai request gets a Thai acknowledgment.
    resp = await _chat(client, "ตอบภาษาไทย", "t18-th")
    data = resp.json()
    assert _is_thai(data["response"])
    assert "ได้เลยค่ะ" in data["response"]
    assert data["response_language"] == "th"
