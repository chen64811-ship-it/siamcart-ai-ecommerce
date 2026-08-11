"""Demo shipment notification tests — Task 8C.

Covers the deterministic shipment service, the /ship-demo API, the Order
Details Mark as Shipped (Demo) UI contract, and the Transaction Tracker
answering tracking / shipped questions:

  1. generate_tracking_number produces realistic per-provider formats;
  2. a processing order ships and PERSISTS shipment_status='shipped',
     tracking_number, shipping_provider, notification_sent=1 and
     notification_sent_at;
  3. confirm_demo_shipment=false changes nothing (400, no DB write);
  4. a second ship is idempotent — tracking number, provider and
     notification_sent_at are never regenerated (action_executed=false);
  5. delivered / shipped / cancelled orders are rejected (NOT_SHIPPABLE);
  6. unknown orders are rejected;
  7. malformed order ids are rejected;
  8. AI answers "What is my tracking number for ORD-XXXX?" with the parcel
     number (TRACKING_NUMBER intent);
  9. AI answers "Has order ORD-XXXX been shipped?" with the shipment
     status (SHIPMENT_STATUS intent) — before and after shipping;
 10. AI honestly says there is no tracking number yet before shipping;
 11. every customer-facing AI reply is Thai;
 12. Order Details UI contract: Mark as Shipped (Demo) button only for
     processing orders, shipment facts rows, Copy Tracking Number button,
     and the confirm/copy wiring in orders.js.

Isolation guarantees (same pattern as the cancellation test module):
- The real data/orders.db is never written. store_db.STORE_DB_PATH and the
  orchestrator db path are redirected to a temporary file for the whole
  module.
- The temp database is initialized with the REAL production initialization
  functions (app.db.orders.init_database -> app.db.store.init_store_database),
  then Task orders are seeded directly.
- A teardown stat check proves the real database file is untouched.
- No DeepSeek / ChromaDB / SentenceTransformer initialization; no browser.
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
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_THAI_RE = re.compile(r"[\u0E00-\u0E7F]")

# Provider formats from app/db/store.py (Task 8C requirement 3).
_TRACKING_FORMATS = {
    "Thailand Post": re.compile(r"^TH\d{9}TH$"),
    "Flash Express": re.compile(r"^FD\d{10}$"),
    "J&T Express": re.compile(r"^JT\d{12}$"),
}

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


# ── helpers ────────────────────────────────────────────────────────────


def _db_stat(path):
    try:
        st = os.stat(path)
        return (st.st_size, st.st_mtime_ns)
    except OSError:
        return None


def _read(rel):
    with open(os.path.join(REPO, rel), encoding="utf-8") as f:
        return f.read()


def _assert_thai_dominant(text, label="response"):
    thai = len(_THAI_RE.findall(text or ""))
    eng = len(re.findall(r"[A-Za-z]", text or ""))
    assert thai > 0, f"{label} has no Thai: {text!r}"
    assert thai > eng, f"{label} is not Thai-dominant: {text!r}"


def _order_payload(items, **overrides):
    payload = dict(VALID_CUSTOMER)
    payload.update(overrides)
    payload["items"] = items
    return payload


async def _post_order(client, items, **overrides):
    resp = await client.post("/api/orders", json=_order_payload(items, **overrides))
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _ship(client, order_id, confirm=True):
    resp = await client.post(
        "/api/orders/" + order_id + "/ship-demo",
        json={"confirm_demo_shipment": confirm},
    )
    return resp


async def _chat(client, message, session_id, order_id=None):
    payload = {"message": message, "session_id": session_id}
    if order_id is not None:
        payload["order_id"] = order_id
    return await client.post("/api/chat", json=payload)


def _order_row(db_path, order_id):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT * FROM orders WHERE order_id = ?", (order_id,)
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def _seed_task_orders(db_path):
    """Seed Task 8C fixtures: processing orders + non-shippable states."""
    conn = sqlite3.connect(db_path)
    try:
        # ORD-8101 — processing / paid / not_shipped (AI tracking question).
        conn.execute(
            "INSERT INTO orders ("
            "order_id, customer_name, product_name, order_status, payment_status,"
            "shipment_status, tracking_number, shipping_provider, purchase_date,"
            "estimated_delivery_date, total_amount, payment_method, paid_at, created_at"
            ") VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "ORD-8101", "Somchai Test", "Linen Everyday Blouse",
                "processing", "paid", "not_shipped", "", "", "2026-08-01", "2026-08-05",
                690.0, "bank_transfer", "2026-08-01T09:30:00", "2026-08-01T09:00:00",
            ),
        )
        # ORD-8102 — processing / pending / not_shipped (AI shipped question).
        conn.execute(
            "INSERT INTO orders ("
            "order_id, customer_name, product_name, order_status, payment_status,"
            "shipment_status, tracking_number, shipping_provider, purchase_date,"
            "estimated_delivery_date, total_amount, payment_method, created_at"
            ") VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "ORD-8102", "Somchai Test", "Classic Denim Jacket",
                "processing", "pending", "not_shipped", "", "", "2026-08-02", "2026-08-06",
                1290.0, "cash_on_delivery", "2026-08-02T10:00:00",
            ),
        )
        # ORD-8103 — cancelled (must NOT be shippable).
        conn.execute(
            "INSERT INTO orders ("
            "order_id, customer_name, product_name, order_status, payment_status,"
            "shipment_status, tracking_number, shipping_provider, purchase_date,"
            "estimated_delivery_date, total_amount, payment_method, created_at"
            ") VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "ORD-8103", "Somchai Test", "Wireless Earbuds",
                "cancelled", "refunded", "not_shipped", "", "", "2026-08-03", "2026-08-07",
                890.0, "bank_transfer", "2026-08-03T11:00:00",
            ),
        )
        # ORD-8104 — processing / not_shipped, NEVER shipped by any test
        # (the honest no-tracking-yet answer must stay deterministic even
        # though ORD-8101/8102 get shipped by earlier tests).
        conn.execute(
            "INSERT INTO orders ("
            "order_id, customer_name, product_name, order_status, payment_status,"
            "shipment_status, tracking_number, shipping_provider, purchase_date,"
            "estimated_delivery_date, total_amount, payment_method, created_at"
            ") VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "ORD-8104", "Somchai Test", "Running Shoes",
                "processing", "pending", "not_shipped", "", "", "2026-08-04", "2026-08-08",
                1590.0, "cash_on_delivery", "2026-08-04T12:00:00",
            ),
        )
        conn.commit()
    finally:
        conn.close()


# ── fixtures ───────────────────────────────────────────────────────────


@pytest.fixture(scope="module", autouse=True)
def tmp_db():
    """Temporary SQLite database, initialized with real production functions."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    init_database(path)  # orders schema + 5 sample orders + store schema + 12 products
    _seed_task_orders(path)

    real_before = _db_stat(REAL_DB)

    original_path = store_db.STORE_DB_PATH
    store_db.STORE_DB_PATH = path
    set_db_path(path)
    try:
        yield path
        # Isolation proof: the real database must be completely untouched.
        real_after = _db_stat(REAL_DB)
        assert real_after == real_before, (
            f"REAL database was modified by Task 8C tests: {REAL_DB} "
            f"before={real_before} after={real_after}"
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
    """Per-test isolation: fresh SessionManager + DeepSeek/LLM fully mocked."""
    orch._SESSION_STORE = SessionManager()
    monkeypatch.setattr(orch, "DEEPSEEK_ENABLED", False)
    import app.agents.llm_generator as gen

    monkeypatch.setattr(gen, "DEEPSEEK_ENABLED", False)

    import openai

    def _no_client(*_a, **_k):
        raise AssertionError(
            "External LLM (OpenAI) client was constructed during Task 8C tests"
        )

    monkeypatch.setattr(openai, "OpenAI", _no_client)

    import app.api.server as server

    monkeypatch.setattr(server, "write_experiment_log", lambda *_a, **_k: None)
    yield


# ── 1. Realistic demo tracking numbers ─────────────────────────────────


def test_generate_tracking_number_formats():
    """1. Tracking numbers match the realistic per-provider formats."""
    for provider, pattern in _TRACKING_FORMATS.items():
        for _ in range(20):
            value = store_db.generate_tracking_number(provider)
            assert pattern.match(value), (
                f"{provider} generated {value!r} — expected {pattern.pattern}"
            )
            assert store_db.is_valid_tracking_number(value)

    # A random provider always yields one of the supported formats too.
    for _ in range(50):
        value = store_db.generate_tracking_number()
        assert store_db.is_valid_tracking_number(value), value

    # Unknown provider falls back to a supported format.
    value = store_db.generate_tracking_number("DHL")
    assert store_db.is_valid_tracking_number(value), value

    assert store_db.is_valid_tracking_number("TH482913742TH")
    assert store_db.is_valid_tracking_number("FD0123456789")
    assert store_db.is_valid_tracking_number("JT012345678901")
    assert not store_db.is_valid_tracking_number("")
    assert not store_db.is_valid_tracking_number("ABC123")


# ── 2. Processing order ships and persists all fields ──────────────────


@pytest.mark.asyncio
async def test_ship_processing_order_persists_all_fields(client, tmp_db):
    """2. POST /ship-demo on a processing order persists shipment_status,
    tracking_number, shipping_provider, notification_sent and
    notification_sent_at (requirements 2 + 4)."""
    created = await _post_order(
        client, [{"product_id": "PROD-001", "quantity": 1}],
        payment_method="bank_transfer",
    )
    oid = created["order_id"]
    assert created["order_status"] == "processing"
    assert created["shipment_status"] == "not_shipped"
    assert created["tracking_number"] in (None, "")

    resp = await _ship(client, oid)
    assert resp.status_code == 200
    body = resp.json()

    assert body["order_id"] == oid
    assert body["order_status"] == "shipped"
    assert body["shipment_status"] == "shipped"
    assert body["shipping_provider"] in store_db.SHIPPING_PROVIDERS
    assert store_db.is_valid_tracking_number(body["tracking_number"])
    assert body["notification_sent"] is True
    assert body["notification_sent_at"]
    assert body["action_executed"] is True
    # Requirement 6 — notification simulation is explicitly labelled.
    assert "Simulation" in body["message"]
    assert "No real SMS or email" in body["simulation_notice"]

    # Persisted in SQLite (requirement 4).
    row = _order_row(tmp_db, oid)
    assert row["order_status"] == "shipped"
    assert row["shipment_status"] == "shipped"
    assert row["tracking_number"] == body["tracking_number"]
    assert row["shipping_provider"] == body["shipping_provider"]
    assert row["notification_sent"] == 1
    assert row["notification_sent_at"] == body["notification_sent_at"]

    # GET /api/orders/{id} exposes the shipment facts to the Order Details UI.
    detail = (await client.get("/api/orders/" + oid)).json()
    assert detail["shipment_status"] == "shipped"
    assert detail["tracking_number"] == body["tracking_number"]
    assert detail["shipping_provider"] == body["shipping_provider"]
    assert detail["notification_sent"] == 1
    assert detail["notification_sent_at"]


# ── 3. confirm_demo_shipment=false changes nothing ─────────────────────


@pytest.mark.asyncio
async def test_confirm_false_changes_nothing(client, tmp_db):
    """3. POST /ship-demo without confirm_demo_shipment=true returns 400
    and never touches the database."""
    created = await _post_order(
        client, [{"product_id": "PROD-002", "quantity": 1}],
        payment_method="bank_transfer",
    )
    oid = created["order_id"]
    row_before = _order_row(tmp_db, oid)

    resp = await _ship(client, oid, confirm=False)
    assert resp.status_code == 400
    assert "confirm_demo_shipment must be true" in resp.json()["detail"]

    row_after = _order_row(tmp_db, oid)
    assert row_after == row_before, "confirm=false must not modify the order row"


# ── 4. Second ship is idempotent ───────────────────────────────────────


@pytest.mark.asyncio
async def test_second_ship_is_idempotent(client, tmp_db):
    """4. Repeating the shipment returns the same state — the tracking
    number, provider and notification_sent_at are never regenerated."""
    created = await _post_order(
        client, [{"product_id": "PROD-003", "quantity": 2}],
        payment_method="bank_transfer",
    )
    oid = created["order_id"]

    first = (await _ship(client, oid)).json()
    second = (await _ship(client, oid)).json()

    assert second["order_status"] == "shipped"
    assert second["shipment_status"] == "shipped"
    assert second["tracking_number"] == first["tracking_number"]
    assert second["shipping_provider"] == first["shipping_provider"]
    assert second["notification_sent_at"] == first["notification_sent_at"]
    assert second["action_executed"] is False


# ── 5. Delivered / cancelled orders rejected ───────────────────────────


@pytest.mark.asyncio
async def test_delivered_order_rejected(client, tmp_db):
    """5a. A delivered order is rejected with 409 (NOT_SHIPPABLE)."""
    resp = await _ship(client, "ORD-1003")  # delivered / delivered / paid
    assert resp.status_code == 409
    assert "cannot be shipped" in resp.json()["detail"]
    row = _order_row(tmp_db, "ORD-1003")
    assert row["order_status"] == "delivered"
    assert row["tracking_number"] == "JNT-TRK-1003"


@pytest.mark.asyncio
async def test_cancelled_order_rejected(client, tmp_db):
    """5b. A cancelled order is rejected with 409 (NOT_SHIPPABLE)."""
    resp = await _ship(client, "ORD-8103")
    assert resp.status_code == 409
    assert "cannot be shipped" in resp.json()["detail"]
    row = _order_row(tmp_db, "ORD-8103")
    assert row["order_status"] == "cancelled"
    assert row["notification_sent"] == 0


# ── 6-7. Unknown / malformed orders rejected ───────────────────────────


@pytest.mark.asyncio
async def test_unknown_order_rejected(client):
    """6. Unknown orders are rejected with 404 (honest error)."""
    resp = await _ship(client, "ORD-9999")
    assert resp.status_code == 404
    assert "not found" in resp.json()["detail"].lower()


@pytest.mark.asyncio
async def test_invalid_order_id_rejected(client):
    """7. Malformed order ids are rejected with 400."""
    resp = await _ship(client, "abc")
    assert resp.status_code == 400
    resp2 = await _ship(client, "ORD-ABCD")
    assert resp2.status_code == 400


# ── 8. AI answers tracking-number questions ────────────────────────────


@pytest.mark.asyncio
async def test_ai_answers_tracking_number_question(client, tmp_db):
    """8. 'What is my tracking number for ORD-8101?' is answered with the
    parcel number and provider after the order is shipped."""
    # Ship ORD-8101 through the same deterministic backend the UI uses.
    shipped = (await _ship(client, "ORD-8101")).json()
    tracking = shipped["tracking_number"]
    provider = shipped["shipping_provider"]

    r = await _chat(client, "What is my tracking number for ORD-8101?", "t8c-1")
    assert r.status_code == 200
    data = r.json()
    assert data["intent"] == "TRACKING_NUMBER"
    assert data["order_id"] == "ORD-8101"
    assert data["evidence"]["tracking_number"] == tracking
    assert data["evidence"]["shipping_provider"] == provider
    assert data["evidence"]["notification_sent"] == 1
    assert tracking in data["response"]
    assert provider in data["response"]
    assert "ORD-8101" in data["response"]
    _assert_thai_dominant(data["response"], label="tracking answer")


# ── 9. AI answers has-order-been-shipped questions ─────────────────────


@pytest.mark.asyncio
async def test_ai_answers_shipped_question_before_shipping(client, tmp_db):
    """9a. 'Has order ORD-8102 been shipped?' before shipping honestly says
    it has not been shipped (no invented tracking number)."""
    r = await _chat(client, "Has order ORD-8102 been shipped?", "t8c-2")
    assert r.status_code == 200
    data = r.json()
    assert data["order_id"] == "ORD-8102"
    assert "ORD-8102" in data["response"]
    assert "ยังไม่ได้จัดส่ง" in data["response"]
    _assert_thai_dominant(data["response"], label="pre-ship answer")


@pytest.mark.asyncio
async def test_ai_answers_shipped_question_after_shipping(client, tmp_db):
    """9b. 'Has order ORD-8102 been shipped?' after shipping is answered
    with the shipped status, the parcel number and the provider."""
    shipped = (await _ship(client, "ORD-8102")).json()
    tracking = shipped["tracking_number"]

    r = await _chat(client, "Has order ORD-8102 been shipped?", "t8c-3")
    assert r.status_code == 200
    data = r.json()
    assert data["order_id"] == "ORD-8102"
    assert data["evidence"]["shipment_status"] == "shipped"
    assert data["evidence"]["tracking_number"] == tracking
    assert data["evidence"]["notification_sent"] == 1
    assert "ORD-8102" in data["response"]
    assert "จัดส่งแล้ว" in data["response"]
    assert tracking in data["response"]
    _assert_thai_dominant(data["response"], label="post-ship answer")


# ── 10. AI honestly reports no tracking number before shipping ─────────


@pytest.mark.asyncio
async def test_ai_no_tracking_number_before_shipping(client, tmp_db):
    """10. A tracking question for an unshipped order says there is no
    parcel number yet instead of inventing one."""
    r = await _chat(client, "What is my tracking number for ORD-8104?", "t8c-4")
    assert r.status_code == 200
    data = r.json()
    assert data["intent"] == "TRACKING_NUMBER"
    assert "ยังไม่มีหมายเลขพัสดุ" in data["response"]
    assert "ORD-8104" in data["response"]
    _assert_thai_dominant(data["response"], label="no-tracking answer")


# ── 11. Thai-dominant AI replies ───────────────────────────────────────


@pytest.mark.asyncio
async def test_ai_replies_are_thai(client, tmp_db):
    """11. Every customer-facing shipment answer is Thai-dominant."""
    await _ship(client, "ORD-8101")
    for msg in (
        "What is my tracking number for ORD-8101?",
        "Has order ORD-8101 been shipped?",
        "ส่งของหรือยัง ORD-8101",
        "เลขพัสดุของ ORD-8101 คืออะไร",
    ):
        r = await _chat(client, msg, "t8c-thai-" + msg[:4])
        assert r.status_code == 200
        _assert_thai_dominant(r.json()["response"], label=msg)


# ── 12. Order Details UI contract ──────────────────────────────────────


def test_ui_mark_as_shipped_button_rules():
    """12a. The Order Details UI shows 'Simulate Shipment' only for
    paid + processing + not_shipped orders, 'Mark Delivered' only while in
    transit, and the shipment section shows the status, courier, tracking
    number, lifecycle timestamps and the demo note (Task 8D requirements)."""
    js = _read("app/static/js/orders.js")

    # The legacy 8C rule function still exists (processing only).
    i = js.index("function canShip(order)")
    body = js[i:js.index("function canSimulateShipment(order)")]
    assert 'os === "processing"' in body
    assert "return os === \"processing\";" in body

    # Task 8D — Simulate Shipment gate: paid + processing + not_shipped.
    i = js.index("function canSimulateShipment(order)")
    ss = js[i:js.index("function canDeliver(order)")]
    assert 'ps === "paid"' in ss
    assert 'os === "processing"' in ss
    assert 'ss === "not_shipped"' in ss
    assert "return ps === \"paid\" && os === \"processing\" && ss === \"not_shipped\";" in ss

    # Task 8D — Mark Delivered gate: only while in transit.
    i = js.index("function canDeliver(order)")
    dd = js[i:js.index("var state = {", i)]
    assert 'ss === "in_transit"' in dd
    assert "return ss === \"in_transit\";" in dd

    rd = js[js.index("function renderDetails(order)"):js.index("function confirmCancellation(order)")]
    assert "var shipBtn = canSimulateShipment(order)" in rd
    assert 'id="odShipBtn"' in rd
    assert "Simulate Shipment" in rd
    assert "var deliverBtn = canDeliver(order)" in rd
    assert 'id="odDeliverBtn"' in rd
    assert "Mark Delivered" in rd

    # Shipment facts section (Task 8D requirements).
    assert 'od-row"><span>Shipment status</span>' in rd
    assert 'od-row"><span>Courier</span>' in rd
    assert 'od-row"><span>Tracking Number</span>' in rd
    assert "Shipped at" in rd
    assert "Delivered at" in rd
    assert "Demo shipment only. No real courier service is connected." in rd
    # Task 8C notification facts are still rendered.
    assert "Notification Sent (Simulation)" in rd
    assert "Simulated shipping notification — no real SMS or email was sent." in rd
    # Copy button only when a tracking number exists.
    assert 'id="odCopyTrackingBtn"' in rd
    assert "order.tracking_number" in rd

    # Both handlers are wired inside renderDetails.
    assert 'document.getElementById("odShipBtn")' in rd
    assert 'document.getElementById("odDeliverBtn")' in rd
    assert 'document.getElementById("odCopyTrackingBtn")' in rd
    assert "simulateShipment(order)" in rd
    assert "markDelivered(order)" in rd
    assert "copyTrackingNumber(order)" in rd


def test_ui_confirm_shipment_wiring():
    """12b. simulateShipment posts confirm_demo_shipment=true to
    /demo-shipment and markDelivered posts confirm_demo_delivery=true to
    /demo-delivery; both disable the button while pending and re-render
    from fresh SQLite facts on success."""
    js = _read("app/static/js/orders.js")
    ss = js[js.index("function simulateShipment(order)"):js.index("function markDelivered(order)")]
    assert "/demo-shipment" in ss
    assert "confirm_demo_shipment: true" in ss
    assert "btn.disabled = true" in ss
    assert "is-loading" in ss
    assert "renderDetails(full)" in ss
    assert "refreshListAfterPayment(full.order_id)" in ss
    assert "In Transit" in ss

    md = js[js.index("function markDelivered(order)"):js.index("function copyTrackingNumber(order)")]
    assert "/demo-delivery" in md
    assert "confirm_demo_delivery: true" in md
    assert "btn.disabled = true" in md
    assert "renderDetails(full)" in md
    assert "Delivered" in md


def test_ui_copy_tracking_number_wiring():
    """12c. copyTrackingNumber copies the parcel number via the Clipboard
    API with an execCommand fallback, and toasts the result."""
    js = _read("app/static/js/orders.js")
    ct = js[js.index("function copyTrackingNumber(order)"):js.index("function confirmDemoPayment(order)")]
    assert "navigator.clipboard.writeText" in ct
    assert "document.execCommand(\"copy\")" in ct
    assert "Tracking number copied to clipboard" in ct
    assert 'odCopyTrackingBtn' in js
