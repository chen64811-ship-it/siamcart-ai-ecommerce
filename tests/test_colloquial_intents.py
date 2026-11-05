"""P1-2 — AI high-frequency colloquial regression cases.

Locks in the deterministic intent routing for the casual, spoken-style Thai
and English phrases a real shopper actually types, so a later router change
cannot silently regress them. Pure string routing — no LLM, no ChromaDB, no
SQLite, no network.
"""

import pytest

from app.agents.router import route_message


# ── Thai high-frequency colloquial phrases ──────────────────────────────
# (message, expected intent). These are the short, spoken-style forms that
# appear most often in Thai e-commerce chat.
_THAI_COLLOQUIAL = [
    # Shipping / arrival
    ("ส่งของหรือยัง", "SHIPMENT_STATUS"),
    ("จัดส่งหรือยัง", "SHIPMENT_STATUS"),
    ("ส่งถึงไหน", "SHIPMENT_STATUS"),
    ("ของจะถึงเมื่อไหร่", "SHIPMENT_STATUS"),
    ("จะถึงเมื่อไหร่", "SHIPMENT_STATUS"),
    ("เมื่อไหร่จะถึง", "SHIPMENT_STATUS"),
    ("กี่วันจะถึง", "SHIPMENT_STATUS"),
    # Payment
    ("จ่ายหรือยัง", "PAYMENT_STATUS"),
    ("ชำระเงินแล้วหรือยัง", "PAYMENT_STATUS"),
    ("ชำระยัง", "PAYMENT_STATUS"),
    ("จ่ายเงินแล้ว", "PAYMENT_STATUS"),
    # Order progress
    ("ถึงไหนแล้ว", "ORDER_STATUS"),
    ("ออเดอร์ถึงไหน", "ORDER_STATUS"),
    ("คำสั่งซื้อถึงไหน", "ORDER_STATUS"),
    # Tracking
    ("ขอเลขพัสดุ", "TRACKING_NUMBER"),
    ("ตามพัสดุ", "TRACKING_NUMBER"),
    # Refund / return
    ("ขอคืนเงิน", "RETURN_REFUND"),
    ("คืนเงิน", "RETURN_REFUND"),
    ("คืนสินค้า", "RETURN_REFUND"),
    ("ส่งสินค้าคืน", "RETURN_REFUND"),
    # Policy (asked as a yes/no question — stays policy, never a refund action)
    ("คืนเงินได้ไหม", "STORE_POLICY"),
    ("นโยบายร้าน", "STORE_POLICY"),
]


# ── English high-frequency colloquial phrases ───────────────────────────

_EN_COLLOQUIAL = [
    ("where's my order", "ORDER_STATUS"),
    ("where is my order", "ORDER_STATUS"),
    ("has it shipped", "SHIPMENT_STATUS"),
    ("paid yet", "PAYMENT_STATUS"),
    ("payment status", "PAYMENT_STATUS"),
    ("track my package", "TRACKING_NUMBER"),
    ("tracking number", "TRACKING_NUMBER"),
    ("i want a refund", "RETURN_REFUND"),
    ("refund pls", "RETURN_REFUND"),
    ("when will it arrive", "SHIPMENT_STATUS"),
]


@pytest.mark.parametrize("message,expected", _THAI_COLLOQUIAL)
def test_thai_colloquial_routes_correctly(message, expected):
    r = route_message(message)
    assert r["intent"] == expected, (
        f"{message!r} routed to {r['intent']!r}, expected {expected!r}"
    )


@pytest.mark.parametrize("message,expected", _EN_COLLOQUIAL)
def test_english_colloquial_routes_correctly(message, expected):
    r = route_message(message)
    assert r["intent"] == expected, (
        f"{message!r} routed to {r['intent']!r}, expected {expected!r}"
    )


def test_colloquial_with_order_id_extracts_entity():
    """A colloquial phrase plus an order ID still extracts the ID as an entity."""
    r = route_message("ส่งของหรือยัง ORD-1001")
    assert r["intent"] == "SHIPMENT_STATUS"
    assert r["extracted_order_id"] == "ORD-1001"
    assert r["requires_clarification"] is False
