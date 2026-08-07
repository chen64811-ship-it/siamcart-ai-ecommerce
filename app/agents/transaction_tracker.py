"""Transaction Tracker Agent — Phase 4A (Refactored).

Pure data-fetching layer. No response templates, no Thai output.

Responsibilities:
1. Extract order ID from message.
2. Detect intent from keywords.
3. Query SQLite read-only.
4. Return structured evidence + flags.

All response generation moves to llm_generator.py.
"""

import re
import time
from typing import Dict, Optional

from app.db.orders import query_order, init_database
from app.config import DATA_DIR


ORDER_ID_PATTERN = re.compile(r"ORD[-]?\d{4}", re.IGNORECASE)

# Intent keywords — same as before, used for data-intent only
# Dict order = priority (first matching intent wins). Task 8D adds the
# ETA and COURIER intents BEFORE the generic shipment keywords so
# "When will it arrive?" / "Which courier?" get focused answers.
_INTENT_KEYWORDS: Dict[str, list] = {
    "ETA": [
        "when will it arrive", "when will it get", "when will",
        "estimated delivery", "estimated arrival", "arrival date",
        "delivery date", "delivery estimate", "eta",
        "จะถึงเมื่อไหร่", "ถึงเมื่อไหร่", "เมื่อไหร่จะถึง", "ถึงเมื่อไร",
        "เมื่อไหร่จะได้", "ส่งถึงเมื่อไหร่", "จัดส่งถึงเมื่อไหร่",
        "ถึงตอนไหน", "กี่วันจะถึง", "วันไหนจะถึง",
    ],
    "COURIER": [
        "courier", "which courier", "who is shipping", "shipping company",
        "carrier", "บริษัทขนส่ง", "ผู้ให้บริการขนส่ง", "จัดส่งผ่าน",
        "ขนส่งอะไร", "ส่งผ่านบริษัทอะไร", "บริษัทอะไรส่ง",
    ],
    "TRACKING_NUMBER": [
        "หมายเลขพัสดุ", "tracking", "เลขพัสดุ", "track", "TRK",
        "เลขติดตาม", "tracking number",
    ],
    "SHIPMENT_STATUS": [
        "สถานะการจัดส่ง", "สถานะจัดส่ง", "จัดส่ง", "ส่งของ", "ขนส่ง",
        "shipping", "shipment", "พัสดุ", "จัดส่งยัง", "ส่งหรือยัง",
        "การจัดส่ง",
        # Demo-repair: "shipped" / "delivered" questions are shipment
        # questions (the evaluated router taxonomy is untouched).
        "shipped", "delivered",
    ],
    "PAYMENT_STATUS": [
        "ชำระเงิน", "จ่ายเงิน", "payment", "paid", "จ่าย", "ยังไม่จ่าย",
        "ชำระ", "การชำระ", "ชำระยัง", "จ่ายหรือยัง",
        "จ่ายเงินแล้ว", "ชำระแล้ว",
    ],
    "ORDER_STATUS": [
        "ถึงไหน", "สถานะ", "อัพเดท", "update", "status", "ขั้นตอน",
        "ไปถึงไหน", "ถึงไหนแล้ว", "ปัจจุบัน",
    ],
}


def _extract_order_id(message: str) -> Optional[str]:
    """Extract order ID like ORD-1001 or ORD1001."""
    match = ORDER_ID_PATTERN.search(message)
    if match:
        raw = match.group(0).upper()
        if "-" not in raw:
            raw = raw[:3] + "-" + raw[3:]
        return raw
    return None


def _detect_intent(message: str) -> str:
    """Determine the data intent based on keyword matching.

    Returns one of: ORDER_STATUS, PAYMENT_STATUS, SHIPMENT_STATUS,
    TRACKING_NUMBER, CLARIFICATION.
    """
    msg_lower = message.lower()
    for intent, keywords in _INTENT_KEYWORDS.items():
        for kw in keywords:
            if kw.lower() in msg_lower:
                return intent
    if _extract_order_id(message):
        return "ORDER_STATUS"
    return "CLARIFICATION"


def fetch_transaction_evidence(
    message: str,
    db_path: Optional[str] = None,
) -> Dict:
    """Extract order ID + intent, query SQLite, return structured evidence.

    Pure data layer — no response text, no templates.

    Returns dict:
      - intent: detected data intent
      - order_id: extracted order ID or None
      - evidence: full order fields or {}
      - order_found: bool
      - requires_clarification: True if no order_id found
      - latency_ms: query time
    """
    start_time = time.time()
    db_path = db_path or str(DATA_DIR / "orders.db")
    init_database(db_path)

    order_id = _extract_order_id(message)

    if not order_id:
        return {
            "intent": "CLARIFICATION",
            "order_id": None,
            "evidence": {},
            "order_found": False,
            "requires_clarification": True,
            "latency_ms": round((time.time() - start_time) * 1000, 2),
        }

    intent = _detect_intent(message)
    order = query_order(db_path, order_id)

    if order is None:
        return {
            "intent": "ORDER_NOT_FOUND",
            "order_id": order_id,
            "evidence": {},
            "order_found": False,
            "requires_clarification": False,
            "latency_ms": round((time.time() - start_time) * 1000, 2),
        }

    evidence = {
        "order_id": order["order_id"],
        "order_status": order["order_status"],
        "payment_status": order["payment_status"],
        "shipment_status": order["shipment_status"],
        "tracking_number": order.get("tracking_number", ""),
        "shipping_provider": order.get("shipping_provider", ""),
        "product_name": order.get("product_name", ""),
        "estimated_delivery_date": order.get("estimated_delivery_date", ""),
        # Task 5D-4 — payment-method and paid_at facts so the deterministic
        # response formatter can explain Cash on Delivery / demo payment.
        "payment_method": order.get("payment_method", ""),
        "paid_at": order.get("paid_at", ""),
        # Demo-repair — cancellation facts for cancelled orders.
        "cancellation_reason": order.get("cancellation_reason", ""),
        "cancelled_at": order.get("cancelled_at", ""),
        "refund_type": order.get("refund_type", ""),
        # Task 8C — demo shipment notification facts (0/NULL until shipped).
        "notification_sent": order.get("notification_sent", 0),
        "notification_sent_at": order.get("notification_sent_at", ""),
        # Task 8D — demo shipment lifecycle timestamps (NULL until they happen).
        "shipped_at": order.get("shipped_at", ""),
        "delivered_at": order.get("delivered_at", ""),
    }

    return {
        "intent": intent,
        "order_id": order_id,
        "evidence": evidence,
        "order_found": True,
        "requires_clarification": False,
        "latency_ms": round((time.time() - start_time) * 1000, 2),
    }
