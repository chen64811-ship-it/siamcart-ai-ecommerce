"""System Orchestrator — Phase 4A (Refactored).

Pipeline:
  message → Router → data_fetch → llm_generator → response

Data and generation are now fully separated:
  - Router decides WHAT data to fetch (shallow intent)
  - Data layer fetches evidence (SQLite, ChromaDB)
  - llm_generator produces natural Thai from evidence

Task 5D-5 — session-aware pending intent and routing priority:
  - structured per-session conversation state is stored through the
    SessionManager (pending_intent, missing_slots, collected_slots,
    last_agent, last_assistant_action);
  - routing priority: explicit user intent words > active pending
    conversation intent > order ID extraction > generic fallback;
  - an ORD-XXXX identifier is an entity, never an intent override.
"""

import re
import time
import uuid
from typing import Dict, List, Optional

from app.agents.router import route_message
from app.agents.transaction_tracker import fetch_transaction_evidence
from app.agents.llm_generator import generate_response
from app.agents.policy_evaluator import evaluate_policy_query
from app.agents.session_manager import SessionManager
from app.agents.language_policy import (
    CUSTOMER_RESPONSE_LANGUAGE,
    ENGLISH_REQUEST_RESPONSE,
    THAI_REQUEST_RESPONSE,
    detect_input_language,
    explicit_language_request,
)
from app.db import store as store_db
from app.config import DEEPSEEK_ENABLED


# ── Controlled response templates (keep only for non-data paths) ───────
# Task 5D-7: every customer-facing response is written in natural Thai.
# Input language is never allowed to switch the output to English.

_CLARIFICATION_RESPONSE = "กรุณาระบุหมายเลขคำสั่งซื้อของคุณด้วยค่ะ"

_GREETING_RESPONSE = (
    "สวัสดีค่ะ ยินดีต้อนรับสู่ SiamCart "
    "กรุณาสอบถามเกี่ยวกับสินค้า คำสั่งซื้อ การชำระเงิน "
    "การจัดส่ง การคืนสินค้า หรือการคืนเงินได้เลยค่ะ"
)

# ── Intent-boundary hotfix: deterministic research/about response ──────
# Static application metadata — no SQLite, no DeepSeek, no order ID.
# Customer-facing response is always Thai.
_RESEARCH_INFO_RESPONSE = (
    "SiamCart เป็นระบบต้นแบบงานวิจัย (research prototype) ค่ะ "
    "อยู่ภายใต้หัวข้องานวิจัย 'LLM-Powered Multi-Agent Customer Support "
    "Framework for Thai E-commerce' โดยระบบนี้ประเมินกรอบงานหลายตัวแทน "
    "(multi-agent framework) ทั้งหมด 3 ตัวแทน ได้แก่ Intelligent Router "
    "(ตัวกำหนดเส้นทางอัจฉริยะ) Transaction Tracker (ผู้ติดตามธุรกรรมคำสั่งซื้อ) "
    "และ Store Policy Evaluator (ผู้ประเมินนโยบายของร้าน) ค่ะ "
    "ส่วนการค้นหาสินค้า (Product Catalog Lookup) เป็นส่วนเสริมสำหรับการสาธิต "
    "เท่านั้น ไม่ใช่ตัวแทนที่ถูกประเมิน "
    "ระบบนี้เป็นเพียงต้นแบบการวิจัย ไม่ใช่บริการเชิงพาณิชย์ "
    "และข้อมูลบางส่วนเป็นข้อมูลจำลองสำหรับการสาธิตค่ะ"
)

_OUT_OF_SCOPE_RESPONSE = (
    "ขออภัย ผู้ช่วย AI ไม่สามารถดำเนินการเรื่องนี้ได้ "
    "และระบบยังไม่ได้บันทึกคำขอของคุณ "
    "กรุณาตรวจสอบข้อมูลเพิ่มเติมได้ที่เมนู My Orders หรือเว็บไซต์ของ SiamCart ค่ะ"
)

_UNKNOWN_RESPONSE = (
    "ขออภัย ฉันไม่เข้าใจคำถามของคุณ "
    "กรุณาถามเกี่ยวกับสถานะคำสั่งซื้อ การชำระเงิน "
    "หรือการจัดส่งนะคะ"
)

_DB_PATH: Optional[str] = None


def _norm_method(method) -> str:
    return re.sub(r"[\s\-]+", "_", str(method or "").strip().lower())


def _shipment_phrase_thai(shipment_status) -> str:
    s = str(shipment_status or "")
    if s == "not_shipped":
        return "ยังไม่ได้จัดส่ง"
    if s == "shipped":
        return "จัดส่งแล้ว"
    if s in ("in_transit", "pending_pickup"):
        return "อยู่ระหว่างการจัดส่ง"
    if s == "delivered":
        return "จัดส่งถึงผู้รับแล้ว"
    return f"สถานะการจัดส่ง: {s}"


def _order_phrase_thai(order_status) -> str:
    """Thai customer-facing meaning of a raw order status (Objective 2)."""
    s = str(order_status or "")
    if s == "processing":
        return "อยู่ระหว่างดำเนินการ"
    if s == "confirmed":
        return "ได้รับการยืนยันแล้ว"
    if s == "shipped":
        return "จัดส่งแล้ว"
    if s == "delivered":
        return "จัดส่งสำเร็จแล้ว"
    if s == "cancelled":
        return "ถูกยกเลิกแล้ว"
    return f"มีสถานะ {s}"


def _shipment_notice_thai(notification_sent, notification_sent_at) -> str:
    """Thai demo notice when a simulated shipping notification was sent.

    Empty string when the order has no notification record (seeded orders,
    or not-yet-shipped orders). When present, the notice explicitly states
    the notification is simulated and no real SMS/email was sent
    (Task 8C requirement 6).
    """
    if not notification_sent:
        return ""
    sent_at = str(notification_sent_at or "").strip()
    when = f" เมื่อ {sent_at}" if sent_at else ""
    return (
        " ระบบได้ส่งการแจ้งเตือนการจัดส่ง (การจำลอง) แล้ว"
        f"{when} — ไม่มีการส่ง SMS หรืออีเมลจริง"
    )


# ── Deterministic Transaction Tracker response formatter (Task 5D-4) ──
# Order-status answers are always SQLite-backed and deterministic; DeepSeek
# is never called for order facts.

def _not_found_response(order_id: Optional[str], message: str) -> str:
    """Thai unknown-order response (Task 5D-7)."""
    if not order_id:
        return _CLARIFICATION_RESPONSE
    return (
        f"ขออภัย ไม่พบข้อมูลคำสั่งซื้อ {order_id} "
        f"กรุณาตรวจสอบหมายเลขคำสั่งซื้ออีกครั้งค่ะ"
    )


def _txn_status_response(message: str, evidence: Dict, intent: str) -> str:
    """Deterministic Transaction Tracker response — always Thai.

    Order-status answers stay SQLite-backed and deterministic; DeepSeek is
    never called for order facts (Task 5D-4 + Task 5D-7 Objective 3).

    Intent-specific (Task 8D): ORDER_STATUS focuses on the order stage,
    PAYMENT_STATUS on payment facts, SHIPMENT_STATUS on the parcel facts,
    TRACKING_NUMBER on the parcel number, COURIER on the provider, ETA on
    the (never invented) delivery estimate.
    """
    order_id = str(evidence.get("order_id", ""))
    payment_status = str(evidence.get("payment_status") or "").lower()
    method = _norm_method(evidence.get("payment_method"))
    is_cod = method in ("cod", "cash_on_delivery")
    paid_at = str(evidence.get("paid_at") or "")
    order_status = str(evidence.get("order_status") or "")
    shipment_status = str(evidence.get("shipment_status") or "")
    tracking = str(evidence.get("tracking_number") or "")
    provider = str(evidence.get("shipping_provider") or "")
    notice = _shipment_notice_thai(
        evidence.get("notification_sent"), evidence.get("notification_sent_at")
    )

    tracking_part = ""
    if tracking:
        tracking_part = (
            f" หมายเลขติดตามพัสดุ: {tracking}"
            + (f" (จัดส่งโดย {provider})" if provider else "")
        )

    # ── ETA — never invent a delivery date ─────────────────────────────
    if intent == "ETA":
        if shipment_status in ("in_transit", "shipped", "pending_pickup"):
            return (
                f"คำสั่งซื้อ {order_id} กำลังอยู่ระหว่างการขนส่งค่ะ "
                f"ขณะนี้ระบบสาธิตยังไม่มีข้อมูลวันจัดส่งโดยประมาณ "
                f"กรุณาตรวจสอบสถานะติดตามพัสดุค่ะ"
                + notice
            )
        if shipment_status == "delivered":
            return f"คำสั่งซื้อ {order_id} จัดส่งถึงปลายทางแล้วค่ะ" + notice
        return (
            f"คำสั่งซื้อ {order_id} ยังไม่ได้จัดส่งค่ะ "
            f"จึงยังไม่สามารถระบุวันจัดส่งโดยประมาณได้ "
            f"เมื่อเริ่มจัดส่งแล้ว ระบบจะแสดงหมายเลขติดตามพัสดุให้ค่ะ"
            + notice
        )

    # ── COURIER — the demo provider only ───────────────────────────────
    if intent == "COURIER":
        if provider:
            return f"คำสั่งซื้อ {order_id} จัดส่งผ่าน {provider} ค่ะ" + notice
        return (
            f"คำสั่งซื้อ {order_id} ยังไม่ได้จัดส่ง จึงยังไม่มีผู้ให้บริการจัดส่งค่ะ"
            + notice
        )

    # ── TRACKING_NUMBER — the parcel number only ───────────────────────
    if intent == "TRACKING_NUMBER":
        if tracking:
            return (
                f"หมายเลขติดตามพัสดุของคำสั่งซื้อ {order_id} คือ {tracking}"
                + (f" (จัดส่งโดย {provider})" if provider else "")
                + " ค่ะ"
                + notice
            )
        return (
            f"คำสั่งซื้อ {order_id} ยังไม่มีหมายเลขพัสดุ — "
            f"{_shipment_phrase_thai(shipment_status)} ค่ะ"
        )

    pending = payment_status in ("pending", "unpaid")

    # ── ORDER_STATUS — focus on the order stage ────────────────────────
    if intent == "ORDER_STATUS":
        if order_status == "cancelled":
            reason = str(evidence.get("cancellation_reason") or "").strip()
            reason_part = f" (เหตุผล: {reason})" if reason else ""
            return (
                f"คำสั่งซื้อ {order_id} ถูกยกเลิกแล้ว{reason_part} ค่ะ "
                f"{_shipment_phrase_thai(shipment_status)}"
            )
        if shipment_status == "delivered" or order_status == "delivered":
            return f"คำสั่งซื้อ {order_id} จัดส่งถึงปลายทางแล้วค่ะ" + notice
        if shipment_status in ("in_transit", "shipped", "pending_pickup"):
            return (
                f"คำสั่งซื้อ {order_id} ถูกจัดส่งแล้วและกำลังอยู่ระหว่างการขนส่งค่ะ"
                + tracking_part
                + notice
            )
        return f"คำสั่งซื้อ {order_id} {_order_phrase_thai(order_status)}ค่ะ"

    # ── PAYMENT_STATUS — focus on payment facts ────────────────────────
    if intent == "PAYMENT_STATUS":
        if payment_status == "refunded":
            return (
                f"คำสั่งซื้อ {order_id} ได้รับการคืนเงินแล้ว (จำลอง) ค่ะ "
                f"— ไม่มีการโอนเงินจริงเกิดขึ้น"
            )
        if is_cod and pending:
            return (
                f"คำสั่งซื้อ {order_id} เป็นแบบเก็บเงินปลายทาง (Cash on Delivery) "
                f"ยังไม่มีการเรียกเก็บเงิน เพราะจะชำระเงินเมื่อได้รับสินค้า ค่ะ"
            )
        if pending:
            return (
                f"คำสั่งซื้อ {order_id} ยังไม่ได้รับการยืนยันการชำระเงิน "
                f"กรุณากดปุ่ม Confirm Demo Payment ในหน้ารายละเอียดคำสั่งซื้อ "
                f"เพื่อจำลองการชำระเงิน (ไม่มีการเรียกเก็บเงินจริง) ค่ะ"
            )
        paid_part = f" (ชำระเมื่อ {paid_at})" if paid_at else ""
        return f"คำสั่งซื้อ {order_id} ได้รับชำระเงินแล้ว{paid_part} ค่ะ"

    # ── SHIPMENT_STATUS — focus on the parcel facts ────────────────────
    if intent == "SHIPMENT_STATUS":
        if shipment_status == "not_shipped":
            return f"คำสั่งซื้อ {order_id} ยังไม่ได้จัดส่งค่ะ" + notice
        if shipment_status == "delivered":
            return f"คำสั่งซื้อ {order_id} จัดส่งเรียบร้อยแล้วค่ะ" + tracking_part + notice
        return (
            f"คำสั่งซื้อ {order_id} ถูกจัดส่งแล้วและกำลังอยู่ระหว่างการขนส่งค่ะ"
            + tracking_part
            + notice
        )

    # Defensive fallback — full status summary (unchanged behaviour).
    if is_cod and pending:
        return (
            f"คำสั่งซื้อ {order_id} เป็นแบบเก็บเงินปลายทาง (Cash on Delivery) "
            f"ยังไม่มีการเรียกเก็บเงิน เพราะจะชำระเงินเมื่อได้รับสินค้า "
            f"ขณะนี้คำสั่งซื้อ{_order_phrase_thai(order_status)} "
            f"และ{_shipment_phrase_thai(shipment_status)} ค่ะ"
        )
    if pending:
        return (
            f"คำสั่งซื้อ {order_id} ยังไม่ได้รับการยืนยันการชำระเงิน "
            f"กรุณากดปุ่ม Confirm Demo Payment ในหน้ารายละเอียดคำสั่งซื้อ "
            f"เพื่อจำลองการชำระเงิน (ไม่มีการเรียกเก็บเงินจริง) "
            f"ขณะนี้คำสั่งซื้อ{_order_phrase_thai(order_status)} "
            f"และ{_shipment_phrase_thai(shipment_status)} ค่ะ"
        )
    paid_part = f" (ชำระเมื่อ {paid_at})" if paid_at else ""
    return (
        f"คำสั่งซื้อ {order_id} ได้รับชำระเงินแล้ว{paid_part} "
        f"ขณะนี้คำสั่งซื้อ{_order_phrase_thai(order_status)} "
        f"และ{_shipment_phrase_thai(shipment_status)} ค่ะ"
    )


# ── Session-aware pending intent (Task 5D-5) ───────────────────────────

_SESSION_STORE = SessionManager()

_SLOT_SPECS = {
    "RETURN_REFUND": ["order_id", "reason"],
}


def _pending_state(session_id: str) -> Dict:
    state = _SESSION_STORE.get_session(session_id)["state"]
    return {
        "pending_intent": state.get("pending_intent"),
        "pending_subtype": state.get("pending_subtype"),
        "missing_slots": list(state.get("missing_slots") or []),
        "collected_slots": dict(state.get("collected_slots") or {}),
        "last_agent": state.get("last_agent"),
        "last_completed_intent": state.get("last_completed_intent"),
        "last_assistant_action": state.get("last_assistant_action"),
        "active_order_id": state.get("active_order_id"),
    }


def _set_pending(
    session_id: str,
    pending_intent: str,
    missing_slots: List[str],
    collected_slots: Dict,
    last_agent: str,
    last_assistant_action: str,
):
    _SESSION_STORE.update_state(session_id, {
        "pending_intent": pending_intent,
        "missing_slots": missing_slots,
        "collected_slots": collected_slots,
        "last_agent": last_agent,
        "last_assistant_action": last_assistant_action,
    })


def _clear_pending(session_id: str):
    _SESSION_STORE.update_state(session_id, {
        "pending_intent": None,
        "missing_slots": [],
        "collected_slots": {},
        "last_agent": None,
        "last_assistant_action": None,
    })


# ── Task 8A — persistent active order context ─────────────────────────
# active_order_id is session state that survives informational turns and
# is only cleared by an explicit remove-order-context, a chat reset, or a
# new explicit order replacing it.

def _get_active_order(session_id: str) -> Optional[str]:
    return _SESSION_STORE.get_session(session_id)["state"].get("active_order_id")


def _set_active_order(session_id: str, order_id: Optional[str]):
    _SESSION_STORE.update_state(session_id, {"active_order_id": order_id})


def _set_pending_subtype(session_id: str, subtype: Optional[str]):
    _SESSION_STORE.update_state(session_id, {"pending_subtype": subtype})


def _set_last_completed(session_id: str, intent: str):
    _SESSION_STORE.update_state(session_id, {"last_completed_intent": intent})


def remove_order_context(session_id: str) -> None:
    """Explicit user action: clear the session active order.

    Preserves pending workflow state, transcript and session_id.
    """
    _SESSION_STORE.update_state(session_id, {"active_order_id": None})


def get_active_order_for_session(session_id: str) -> Optional[str]:
    """Public accessor for the session active order (used by server.py)."""
    return _get_active_order(session_id)


def reset_session(session_id: str) -> None:
    """Full chat reset: clear pending workflow AND active order context."""
    _SESSION_STORE.update_state(session_id, {
        "active_order_id": None,
        "pending_intent": None,
        "pending_subtype": None,
        "missing_slots": [],
        "collected_slots": {},
        "last_agent": None,
        "last_completed_intent": None,
        "last_assistant_action": None,
    })


# ── Task 8A — order-intent refinement + follow-up resolution ──────────
# These run INSIDE the orchestrator (demo layer). The evaluated router
# taxonomy is untouched; only the response behaviour improves.

# Transaction intents (SQLite-backed). ORDER_TOTAL and PURCHASED_ITEMS are
# Task 8A additions answered from SQLite order_items; ETA and COURIER are
# Task 8D additions answered from the persisted shipment facts.
_TXN_INTENTS_EXT = {
    "ORDER_STATUS", "PAYMENT_STATUS", "SHIPMENT_STATUS",
    "TRACKING_NUMBER", "ORDER_TOTAL", "PURCHASED_ITEMS",
    "ETA", "COURIER",
}

# Strong order-referential phrases — a message containing one of these is
# about the active order even when the router classified it UNKNOWN.
_STRONG_ORDER_FOLLOWUP_RE = re.compile(
    r"("
    r"this\s+order|that\s+order|this\s+one|that\s+one|my\s+order|the\s+order"
    r"|คำสั่งซื้อนี้|ออเดอร์นี้|รายการนี้|รายการล่าสุด|อันนี้|อันนั้น"
    r")",
    re.IGNORECASE,
)

# English bare-pronoun follow-ups ("Where is it?", "Is it shipped?",
# "What about it?") refer to the active order. Scoped so product-price /
# buying questions ("How much is it?", "สินค้านี้ราคาเท่าไร", "ฉันต้องการซื้อ")
# are never hijacked into order-status work.
_EN_PRONOUN_FOLLOWUP_RE = re.compile(
    r"\b(it|it's)\b",
    re.IGNORECASE,
)
_EN_PRICE_PHRASE_RE = re.compile(
    r"(\bhow\s+much\b|\bprice\b|\bcost\b|\bbuy\b|ราคา|เท่าไร|เท่าไหร่|กี่บาท|ซื้อ)",
    re.IGNORECASE,
)

# Order-total intents ("What is the total?", "How much did I pay?").
# Kept narrow so product questions ("how much is this product?",
# "what is the total price of ...?") are never hijacked.
_ORDER_TOTAL_RE = re.compile(
    r"("
    r"what('s|\s+is)\s+the\s+total|what\s+is\s+my\s+total|the\s+total\s+amount|"
    r"how\s+much\s+(did|have)\s+i\s+(paid|pay)|"
    r"how\s+much\s+was\s+my\s+order|how\s+much\s+is\s+my\s+order"
    r"|ยอดรวม|ยอดเงิน|รวมเป็นเงิน|จ่ายไปเท่าไร|จ่ายไปเท่าไหร่|จ่ายเงินไปเท่าไร"
    r")",
    re.IGNORECASE,
)

# Purchased-items intents ("What did I buy?"). "ซื้ออะไร" alone is a
# product-recommendation phrase and must NOT match. "ซื้ออะไรบ้าง" (what all
# did I buy) IS order work and matches (demo stabilization pass).
_PURCHASED_ITEMS_RE = re.compile(
    r"("
    r"what\s+did\s+i\s+buy|what\s+products\s+did\s+i\s+buy|"
    r"what\s+items\s+did\s+i\s+buy|what\s+did\s+i\s+order"
    r"|ฉันซื้ออะไร|ฉันซื้อสินค้า|ซื้อสินค้าอะไร|สั่งซื้ออะไร|ซื้ออะไรไป|ซื้ออะไรบ้าง"
    r")",
    re.IGNORECASE,
)

# Cancellation request markers (honest-guidance path).
_CANCEL_RE = re.compile(r"cancel|ยกเลิก", re.IGNORECASE)


def detect_order_intent(message: str) -> Optional[str]:
    """Return ORDER_TOTAL / PURCHASED_ITEMS when the message clearly asks
    for one of the Task 8A order-intents. None otherwise.

    Router taxonomy is untouched — this is a demo-layer refinement used
    only when order context exists or the message is explicitly about the
    active order.
    """
    if _ORDER_TOTAL_RE.search(message or ""):
        return "ORDER_TOTAL"
    if _PURCHASED_ITEMS_RE.search(message or ""):
        return "PURCHASED_ITEMS"
    return None


def _normalize_order_id(order_id: Optional[str]) -> Optional[str]:
    """Normalize ORD-XXXX (case/whitespace tolerant) or return None."""
    if not order_id:
        return None
    raw = str(order_id).strip().upper()
    m = re.match(r"^ORD[-]?(\d{4})$", raw)
    if not m:
        return None
    return f"ORD-{m.group(1)}"


def _resolve_order_context(
    message: str,
    request_order_id: Optional[str],
    session_id: str,
    intent: str,
    collected: Dict,
) -> Optional[str]:
    """Resolve the effective order ID for this turn by priority:

    1. explicit order ID in the current message;
    2. explicit order_id in the request (frontend order context);
    3. pending workflow order ID (collected_slots['order_id']);
    4. session active_order_id — only for order-focused messages;
    5. None (clarification).
    """
    route = route_message(message)
    msg_order_id = _normalize_order_id(route.get("extracted_order_id"))
    if msg_order_id:
        return msg_order_id

    req_order_id = _normalize_order_id(request_order_id)
    if req_order_id:
        return req_order_id

    workflow_order_id = _normalize_order_id(collected.get("order_id"))
    if workflow_order_id:
        return workflow_order_id

    active_order_id = _get_active_order(session_id)
    if not active_order_id:
        return None

    # Only fall back to the active order for order-focused messages.
    if intent in _TXN_INTENTS_EXT:
        return active_order_id
    if intent == "OUT_OF_SCOPE" and _CANCEL_RE.search(message or ""):
        return active_order_id
    if _STRONG_ORDER_FOLLOWUP_RE.search(message or ""):
        return active_order_id
    return None


def _is_order_focused_followup(message: str, intent: str) -> bool:
    """True when the message refers to the active order without naming it."""
    if intent in _TXN_INTENTS_EXT:
        return True
    if intent == "OUT_OF_SCOPE" and _CANCEL_RE.search(message or ""):
        return True
    return bool(_STRONG_ORDER_FOLLOWUP_RE.search(message or ""))


# ── Demo cancellation flow (repair) ──────────────────────────────────
# The AI can now guide a REAL simulated cancellation for processing +
# not_shipped demo orders. It uses the SAME deterministic service as the
# Order Details UI (store.cancel_demo_order) and never claims a
# cancellation happened before the SQLite transaction succeeds.

_CANCELLATION_CONFIRM_INTENT = "CANCELLATION_CONFIRM"

# Confirmation markers — only honored while a CANCELLATION_CONFIRM pending
# intent is active, so a bare "yes" never executes anything by itself.
_CONFIRM_RE = re.compile(
    r"(\b(confirm|confirmed|yes|yep|yeah|ok|okay|sure|go ahead|cancel it)\b"
    r"|ยืนยัน|ใช่|ตกลง|ได้เลย|ยกเลิกเลย)",
    re.IGNORECASE,
)

# Decline markers — cancels a pending cancellation ask.
_DECLINE_RE = re.compile(
    r"(\b(no|nope|don't|dont|never mind)\b|ไม่|ไม่เอา|ไม่ต้อง|ไม่เป็นไร)",
    re.IGNORECASE,
)


def _is_confirmation(message: str) -> bool:
    return bool(_CONFIRM_RE.search(message or ""))


def _is_decline(message: str) -> bool:
    return bool(_DECLINE_RE.search(message or ""))


def _extract_cancel_reason(message: str, order_id: Optional[str]) -> str:
    """Pull a short 'because …' reason, else the demo default."""
    text = message or ""
    if order_id:
        text = re.sub(re.escape(order_id), " ", text, flags=re.IGNORECASE)
    m = re.search(
        r"(?:because|due to|since|because of|เหตุผล|เพราะ)[:\s]+(.+)$",
        text,
        re.IGNORECASE,
    )
    if m:
        reason = m.group(1).strip(" .,;:")
        if reason:
            return reason
    return store_db.DEFAULT_CANCEL_REASON


def _cancellable_state(order_evidence: Dict) -> bool:
    return (
        str(order_evidence.get("order_status") or "") == "processing"
        and str(order_evidence.get("shipment_status") or "") == "not_shipped"
    )


def _cancellation_ask_response(order_evidence: Dict) -> str:
    """Thai confirmation ask — cancellation IS possible for this order."""
    oid = str(order_evidence.get("order_id") or "")
    payment_status = str(order_evidence.get("payment_status") or "").lower()
    method = _norm_method(order_evidence.get("payment_method"))
    is_cod = method in ("cod", "cash_on_delivery")
    if payment_status == "paid":
        payment_part = "ชำระเงินแล้ว"
        refund_part = "และดำเนินการคืนเงินแบบจำลองได้ (ไม่มีการโอนเงินจริง)"
    elif is_cod:
        payment_part = "เป็นแบบเก็บเงินปลายทาง (Cash on Delivery)"
        refund_part = "ได้โดยไม่มีการเรียกเก็บเงิน"
    else:
        payment_part = "ยังไม่ได้ยืนยันการชำระเงิน"
        refund_part = "ได้"
    return (
        f"คำสั่งซื้อ {oid} {payment_part}และยังไม่ได้จัดส่งค่ะ "
        f"สามารถยกเลิกคำสั่งซื้อ{refund_part} "
        f"ระบบยังไม่ได้ยกเลิกคำสั่งซื้อ — ต้องการยืนยันการยกเลิกหรือไม่คะ"
    )


def _cancellation_success_response(result: Dict) -> str:
    """Thai post-execution status — only called AFTER the transaction."""
    oid = str(result.get("order_id") or "")
    if str(result.get("payment_status") or "") == "refunded":
        return (
            f"ยกเลิกคำสั่งซื้อ {oid} เรียบร้อยแล้วค่ะ "
            f"คำสั่งซื้อถูกยกเลิกและสถานะการชำระเงินเปลี่ยนเป็นคืนเงินแล้ว (จำลอง) "
            f"— ไม่มีการโอนเงินจริงเกิดขึ้น และสินค้าได้กลับเข้าคลังแล้วค่ะ"
        )
    return (
        f"ยกเลิกคำสั่งซื้อ {oid} เรียบร้อยแล้วค่ะ "
        f"คำสั่งซื้อถูกยกเลิก ไม่มีการเรียกเก็บเงิน และสินค้าได้กลับเข้าคลังแล้วค่ะ"
    )


def _cancellation_already_response(order_evidence: Dict) -> str:
    """Thai already-cancelled response (idempotent state)."""
    oid = str(order_evidence.get("order_id") or "")
    parts = [f"คำสั่งซื้อ {oid} ถูกยกเลิกไปแล้ว"]
    if str(order_evidence.get("refund_type") or "") == "simulated":
        parts.append("และได้รับการคืนเงินแล้ว (จำลอง) — ไม่มีการโอนเงินจริง")
    reason = str(order_evidence.get("cancellation_reason") or "").strip()
    if reason:
        parts.append(f"(เหตุผล: {reason})")
    parts.append("ค่ะ")
    return " ".join(parts)


def _cancellation_reject_response(order_evidence: Dict) -> str:
    """Honest Thai rejection — the order is no longer cancellable.

    Task 8D: once the parcel is in transit or delivered, direct
    cancellation is blocked with status-specific guidance (no return is
    ever created automatically).
    """
    oid = str(order_evidence.get("order_id") or "")
    shipment_status = str(order_evidence.get("shipment_status") or "")
    if shipment_status == "delivered":
        return (
            f"คำสั่งซื้อ {oid} จัดส่งถึงปลายทางแล้ว จึงไม่สามารถยกเลิกโดยตรงได้ "
            f"กรุณาตรวจสอบนโยบายการคืนสินค้าหรือคืนเงินค่ะ "
            f"ระบบยังไม่ได้ยกเลิกคำสั่งซื้อหรือเริ่มคืนเงิน"
        )
    if shipment_status in ("in_transit", "shipped", "pending_pickup"):
        return (
            f"คำสั่งซื้อ {oid} ถูกจัดส่งแล้ว จึงไม่สามารถยกเลิกโดยตรงได้ค่ะ "
            f"ระบบยังไม่ได้ยกเลิกคำสั่งซื้อหรือเริ่มคืนเงิน "
            f"กรุณาตรวจสอบนโยบายการคืนสินค้าหรือคืนเงินค่ะ"
        )
    return (
        f"ขออภัย ไม่สามารถยกเลิกคำสั่งซื้อ {oid} ได้ค่ะ "
        f"เพราะสินค้า{_shipment_phrase_thai(shipment_status)}แล้ว "
        f"ระบบยังไม่ได้ยกเลิกคำสั่งซื้อหรือเริ่มคืนเงิน "
        f"กรุณาตรวจสอบสถานะในเมนู My Orders ค่ะ"
    )


def _cancellation_result(
    text: str,
    evidence: Dict,
    requires_clarification: bool = False,
    order_evidence_source: Optional[str] = None,
) -> Dict:
    return {
        "response": text,
        "handler": "transaction_tracker",
        "evidence": evidence,
        "policy_evidence": {},
        "llm_result": _deterministic_llm_result(text),
        "requires_clarification": requires_clarification,
        "order_evidence_source": order_evidence_source,
        "action_executed": False,
        "escalation_created": False,
        "cancellation_created": False,
    }


def _handle_cancellation_flow(
    session_id: str,
    message: str,
    order_id: Optional[str],
) -> Dict:
    """Cancellation intent — deterministic simulated cancellation.

    Cancellable orders (processing + not_shipped) get a Thai confirmation
    ask and set a pending CANCELLATION_CONFIRM state (preserving
    active_order_id). A follow-up confirmation executes
    store.cancel_demo_order — the SAME service the Order Details UI uses.
    Shipped/delivered orders are rejected; unknown orders get an honest
    not-found. action_executed is only True after the SQLite transaction
    succeeds.
    """
    pending = _pending_state(session_id)
    collected = dict(pending.get("collected_slots") or {})
    pending_cancel = pending.get("pending_intent") == _CANCELLATION_CONFIRM_INTENT
    confirm = _is_confirmation(message) and pending_cancel

    if not order_id:
        order_id = _normalize_order_id(collected.get("order_id"))
    if not order_id:
        _set_pending(session_id, _CANCELLATION_CONFIRM_INTENT, ["order_id"],
                     {}, "transaction_tracker", "ask_missing_slots")
        text = (
            "ยินดีช่วยยกเลิกคำสั่งซื้อค่ะ กรุณาระบุหมายเลขคำสั่งซื้อ "
            "(เช่น ORD-1036) เพื่อให้ฉันตรวจสอบสถานะก่อนนะคะ"
        )
        return _cancellation_result(text, {}, requires_clarification=True)

    txn = fetch_transaction_evidence(f"status {order_id}", db_path=_DB_PATH)
    evidence = txn.get("evidence", {})
    if not evidence:
        _clear_pending(session_id)
        text = _not_found_response(order_id, message)
        return _cancellation_result(text, {}, requires_clarification=False)

    # Enrich with line items (read-only, deterministic).
    try:
        detail = store_db.get_order_with_items(store_db.STORE_DB_PATH, order_id)
        if detail and detail.get("items"):
            evidence["items"] = detail["items"]
    except Exception:
        pass

    oid = str(evidence.get("order_id") or order_id)
    order_status = str(evidence.get("order_status") or "")

    if order_status == "cancelled":
        _clear_pending(session_id)
        text = _cancellation_already_response(evidence)
        return _cancellation_result(text, evidence, order_evidence_source="SQLite")

    if not _cancellable_state(evidence):
        _clear_pending(session_id)
        text = _cancellation_reject_response(evidence)
        return _cancellation_result(text, evidence, order_evidence_source="SQLite")

    if pending_cancel and _is_decline(message):
        _clear_pending(session_id)
        text = (
            f"โอเค ไม่ได้ยกเลิกคำสั่งซื้อ {oid} ค่ะ "
            f"คำสั่งซื้อของคุณยังคงอยู่เหมือนเดิม"
        )
        return _cancellation_result(text, evidence, order_evidence_source="SQLite")

    reason = collected.get("reason") or _extract_cancel_reason(message, order_id)

    if confirm:
        try:
            result = store_db.cancel_demo_order(
                store_db.STORE_DB_PATH, order_id, reason
            )
        except ValueError as exc:
            err = str(exc)
            _clear_pending(session_id)
            if err == "ORDER_NOT_FOUND":
                text = _not_found_response(order_id, message)
                return _cancellation_result(text, {}, requires_clarification=False)
            # e.g. NOT_CANCELLABLE — the order moved on since the ask.
            text = _cancellation_reject_response(
                {
                    "order_id": oid,
                    "shipment_status": evidence.get("shipment_status") or "shipped",
                }
            )
            return _cancellation_result(
                text, evidence, order_evidence_source="SQLite"
            )
        _clear_pending(session_id)
        evidence["order_status"] = result["order_status"]
        evidence["payment_status"] = result["payment_status"]
        evidence["cancelled_at"] = result.get("cancelled_at")
        evidence["refund_type"] = result.get("refund_type")
        evidence["cancellation_reason"] = result.get("cancellation_reason")
        return {
            "response": _cancellation_success_response(result),
            "handler": "transaction_tracker",
            "evidence": evidence,
            "policy_evidence": {},
            "llm_result": _deterministic_llm_result(
                _cancellation_success_response(result)
            ),
            "requires_clarification": False,
            "order_evidence_source": "SQLite",
            "action_executed": True,
            "escalation_created": False,
            "cancellation_created": True,
        }

    # Not confirmed yet — ask for confirmation and keep the pending state.
    _set_pending(
        session_id,
        _CANCELLATION_CONFIRM_INTENT,
        [],
        {"order_id": oid, "reason": reason},
        "transaction_tracker",
        "ask_cancellation_confirmation",
    )
    text = _cancellation_ask_response(evidence)
    return _cancellation_result(text, evidence, order_evidence_source="SQLite")


# ── Task 8A — ORDER_TOTAL / PURCHASED_ITEMS responses ─────────────────

def _order_total_response(order_id: str, order: Dict, items: List[Dict]) -> str:
    """Thai total response from SQLite total_amount (deterministic)."""
    total = order.get("total_amount")
    if total is None:
        total = sum(float(it.get("line_total") or 0) for it in (items or []))
    if not total:
        return f"ขออภัย ไม่พบข้อมูลยอดรวมของคำสั่งซื้อ {order_id} ค่ะ"
    return f"ยอดรวมของคำสั่งซื้อ {order_id} คือ ฿{float(total):,.0f} ค่ะ"


def _purchased_items_response(order_id: str, items: List[Dict]) -> str:
    """Thai purchased-items response from SQLite order_items (deterministic)."""
    if not items:
        return f"ขออภัย ไม่พบข้อมูลรายการสินค้าของคำสั่งซื้อ {order_id} ค่ะ"
    names = [f"{it.get('product_name') or it.get('product_id') or 'สินค้า'} จำนวน {int(it.get('quantity') or 0)} ชิ้น"
             for it in items]
    if len(names) == 1:
        body = names[0]
    elif len(names) == 2:
        body = f"{names[0]} และ {names[1]}"
    else:
        body = ", ".join(names[:-1]) + f" และ {names[-1]}"
    return f"ในคำสั่งซื้อ {order_id} มีสินค้าทั้งหมด {len(items)} รายการ ได้แก่ {body} ค่ะ"


# ── Refund / cancellation request helpers (Task 5D-5) ──────────────────

# A refund REQUEST (personal intent) vs. a general refund POLICY question
# ("Can I return this product?") — only requests enter the slot-filling flow.
_REFUND_REQUEST_RE = re.compile(
    r"("
    r"i\s+wan\w*"                            # i want / wanna / wana / wanan / wanted
    r"|i'?d\s+like"
    r"|i\s+would\s+like"
    r"|i\s+need"
    r"|please\s+(refund|return|cancel)"
    r"|refund\s+(my|this|the)\s+order"
    r"|return\s+(my|this|the)\s+order"
    r"|cancel\s+(my|this|the)\s+order"
    r"|i\s+dont\s+like"
    r"|i\s+don'?t\s+like"
    r"|not\s+satisfied"
    r"|change\s+my\s+mind"
    r"|wrong\s+item"
    r"|defective"
    r"|damaged"
    r")",
    re.IGNORECASE,
)

_REFUND_PREFIX_RE = re.compile(
    r"^("
    r"i\s+wan\w*\s*"
    r"|i'?d\s+like\s*"
    r"|i\s+would\s+like\s*"
    r"|i\s+need\s*"
    r"|please\s*"
    r")",
    re.IGNORECASE,
)


def _is_refund_request(message: str) -> bool:
    return bool(_REFUND_REQUEST_RE.search(message or ""))


def _extract_reason(message: str, order_id: Optional[str]) -> Optional[str]:
    """Extract the refund/cancellation reason from a follow-up message.

    "ORD-1029, i dont like" → "i dont like"
    "I want a refund for ORD-1029 because I don't like it" → "I don't like it"
    """
    text = message or ""
    if order_id:
        text = re.sub(re.escape(order_id), " ", text, flags=re.IGNORECASE)
    text = _REFUND_PREFIX_RE.sub(" ", text)
    # strip topic phrases: "a refund for", "the return", "refund my order"
    # (leading whitespace survives the prefix replacement, so allow \s* first)
    text = re.sub(
        r"^\s*(a|the|my)?\s*(refund|return|cancellation|cancel)\b[^,;.]*?for\b",
        " ", text, flags=re.IGNORECASE,
    )
    text = re.sub(
        r"^\s*(a|the|my)?\s*(refund|return|cancellation|cancel)\b",
        " ", text, flags=re.IGNORECASE,
    )
    text = re.sub(r"^(refund|return|cancel)\b", " ", text, flags=re.IGNORECASE)
    text = re.sub(r"^(my\s+)?order\b", " ", text, flags=re.IGNORECASE)
    text = re.sub(r"^(because|reason|due to|since)\b", " ", text, flags=re.IGNORECASE)
    text = re.sub(r"^[,;\s:\-]+", "", text)
    text = re.sub(r"[,;\s:\-]+$", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text or None


def _ask_refund_details(message: str, need_reason: bool = True) -> str:
    base = "ยินดีช่วยตรวจสอบคำขอคืนสินค้าหรือยกเลิกคำสั่งซื้อค่ะ กรุณาระบุหมายเลขคำสั่งซื้อ"
    if need_reason:
        base += " และเหตุผลสั้น ๆ ที่ต้องการคืนสินค้า"
    return base + " ค่ะ"


def _ask_refund_reason(message: str) -> str:
    return "ขอบคุณค่ะ กรุณาระบุเหตุผลที่ต้องการคืนสินค้าหรือยกเลิกด้วยค่ะ"


def _ask_order_id(message: str) -> str:
    return _CLARIFICATION_RESPONSE


def _deterministic_llm_result(text: str) -> Dict:
    return {
        "response": text,
        "source": "deterministic",
        "latency_ms": 0.0,
        "error_type": None,
        "validation_passed": None,
    }


def _reason_display_thai(reason: Optional[str]) -> str:
    """Thai customer-facing display of a raw refund reason (Objective 4).

    The raw reason is preserved verbatim in the refund_reason metadata; only
    the customer-facing sentence is localized for the common change-of-mind
    phrase so the response stays natural Thai.
    """
    r = (reason or "").strip().lower()
    # Normalize trailing object pronouns ("it", "them", "this product", ...).
    r_norm = re.sub(
        r"\s+(it|them|this|that|the\s+product|this\s+product|the\s+item|this\s+item)$",
        "",
        r,
    )
    if r_norm in (
        "i dont like", "i don't like", "i do not like",
        "dont like", "don't like", "do not like", "not like",
    ):
        return "ไม่ชอบสินค้า"
    return (reason or "").strip()


def _prefer_thai_clause(clauses: List[Dict]) -> str:
    """Prefer a Thai-language policy clause for the customer-facing summary.

    Policy chunks carry a "language" field ("th"/"en"); the Thai chunk is used
    when available so the deterministic fallback stays Thai even when retrieval
    returned an English chunk. Falls back to the first clause.
    """
    for c in clauses or []:
        if str(c.get("language", "")).lower() == "th" and c.get("text"):
            return c["text"]
    return clauses[0]["text"] if clauses else ""


def _refund_flow_response(
    message: str,
    order_evidence: Dict,
    reason: Optional[str],
    clauses: List[Dict],
) -> str:
    """Grounded deterministic Thai refund/cancellation response (Task 5D-7).

    Distinguishes not-shipped, shipped/delivered, Cash on Delivery pending,
    and paid orders. Never approves or executes a refund. Always Thai.
    """
    oid = str(order_evidence.get("order_id") or "")
    payment_status = str(order_evidence.get("payment_status") or "").lower()
    order_status = str(order_evidence.get("order_status") or "")
    shipment_status = str(order_evidence.get("shipment_status") or "")
    method = _norm_method(order_evidence.get("payment_method"))
    is_cod = method in ("cod", "cash_on_delivery")
    paid = payment_status in ("paid",)
    shipped = shipment_status not in ("", "not_shipped", "None")
    policy_text = _prefer_thai_clause(clauses)[:220]

    reason_display = _reason_display_thai(reason)
    reason_part = f" (เหตุผล: {reason_display})" if reason_display else ""

    if is_cod and not paid:
        status_part = (
            "คำสั่งซื้อนี้เป็นแบบเก็บเงินปลายทาง (Cash on Delivery) "
            "และยังไม่มีการเรียกเก็บเงิน การยกเลิกก่อนจัดส่งอาจเหมาะสมกว่าการคืนเงิน"
        )
    elif paid:
        status_part = "คำสั่งซื้อนี้ชำระเงินแล้ว"
    else:
        status_part = ""
    if shipped:
        guidance = "สินค้าจัดส่งแล้วหรือส่งถึงแล้ว การคืนสินค้า/ขอคืนเงินอาจเหมาะสมกว่า"
    else:
        guidance = "สินค้ายังไม่ได้จัดส่ง การยกเลิกคำสั่งซื้ออาจเหมาะสมกว่า"
    policy_part = (
        f" ตามนโยบายของ SiamCart: {policy_text}" if policy_text else
        " กรุณาตรวจสอบนโยบายการคืนเงิน/การคืนสินค้าบนเว็บไซต์ของ SiamCart"
    )
    return (
        f"พบคำสั่งซื้อ {oid} แล้ว เข้าใจว่าคุณต้องการคืนสินค้าหรือยกเลิกคำสั่งซื้อ{reason_part} "
        f"{status_part} {guidance} สถานะปัจจุบันของคำสั่งซื้อคือ{_order_phrase_thai(order_status)} "
        f"{policy_part} "
        f"ยังไม่มีการอนุมัติคืนเงินหรือยกเลิก — ระบบยังไม่ได้ยกเลิกคำสั่งซื้อหรือเริ่มคืนเงิน "
        f"และขณะนี้ฉันยังไม่สามารถยกเลิกคำสั่งซื้อโดยอัตโนมัติได้ "
        f"โปรดใช้เมนู My Orders เพื่อตรวจสอบสถานะคำสั่งซื้อของคุณค่ะ"
    )


def _run_refund_flow(
    session_id: str,
    message: str,
    extracted_order_id: Optional[str],
    collected: Dict,
    missing: List[str],
) -> Dict:
    """Slot-fill and execute the RETURN_REFUND conversation.

    Returns the pieces the orchestrator merges into the final result
    (response, handler, evidence, policy_evidence, llm_result, flags and
    Task 5D-5 research metadata).
    """
    slots = _SLOT_SPECS.get("RETURN_REFUND", [])
    effective_missing = list(missing) if missing else list(slots)

    new_collected = dict(collected)
    if "order_id" in effective_missing and extracted_order_id:
        new_collected["order_id"] = extracted_order_id
    if "reason" in effective_missing:
        reason = _extract_reason(message, extracted_order_id)
        if reason:
            new_collected["reason"] = reason

    order_id = new_collected.get("order_id")
    reason = new_collected.get("reason")

    if not order_id:
        _set_pending(session_id, "RETURN_REFUND", ["order_id", "reason"],
                     new_collected, "store_policy_evaluator", "ask_missing_slots")
        text = _ask_refund_details(message, need_reason=not reason)
        return {
            "response": text,
            "handler": "store_policy_evaluator",
            "evidence": {},
            "policy_evidence": {},
            "llm_result": _deterministic_llm_result(text),
            "requires_clarification": True,
            "refund_reason": reason,
        }

    if not reason:
        _set_pending(session_id, "RETURN_REFUND", ["reason"],
                     new_collected, "store_policy_evaluator", "ask_missing_slots")
        text = _ask_refund_reason(message)
        return {
            "response": text,
            "handler": "store_policy_evaluator",
            "evidence": {},
            "policy_evidence": {},
            "llm_result": _deterministic_llm_result(text),
            "requires_clarification": True,
            "refund_reason": reason,
        }

    # ── All slots filled → execute the refund/cancellation flow ──
    # 1. Transaction Tracker supplies deterministic SQLite order facts.
    txn_result = fetch_transaction_evidence(f"refund {order_id}", db_path=_DB_PATH)
    order_evidence = txn_result.get("evidence", {})
    if not order_evidence:
        _clear_pending(session_id)
        text = _not_found_response(order_id, message)
        return {
            "response": text,
            "handler": "store_policy_evaluator",
            "evidence": {},
            "policy_evidence": {},
            "llm_result": _deterministic_llm_result(text),
            "requires_clarification": False,
            "refund_reason": reason,
        }

    # Enrich with line items from SQLite (read-only, deterministic).
    try:
        detail = store_db.get_order_with_items(store_db.STORE_DB_PATH, order_id)
        if detail and detail.get("items"):
            order_evidence["items"] = detail["items"]
    except Exception:
        pass

    # 2. Retrieve the relevant refund/return/cancellation policy.
    policy_query = f"refund return cancellation policy for order {order_id}"
    if reason:
        policy_query += f" because: {reason}"
    policy_result = _safe_policy_query(policy_query)
    clauses = policy_result.get("retrieved_clauses", [])
    policy_evidence = {
        "retrieved_clauses": clauses,
        "retrieval_success": policy_result.get("retrieval_success", False),
        "policy_sources": policy_result.get("policy_sources", []),
        "retrieved_chunk_count": policy_result.get("retrieved_chunk_count", 0),
        "top_similarity_score": policy_result.get("top_similarity_score", 0.0),
    }

    # 3. Grounded response: DeepSeek when enabled, deterministic otherwise.
    deterministic = _refund_flow_response(message, order_evidence, reason, clauses)
    llm_result = generate_response(
        user_message=message,
        intent="RETURN_REFUND",
        order_evidence=order_evidence,
        policy_chunks=clauses,
        deterministic_fallback=deterministic,
        requires_clarification=False,
        simulated_human_review=False,
    )
    response_text = llm_result["response"] or deterministic

    # 4. Request resolved → clear the pending conversation state.
    _clear_pending(session_id)

    policy_sources = policy_evidence.get("policy_sources") or []
    return {
        "response": response_text,
        "handler": "store_policy_evaluator",
        "evidence": order_evidence,
        "policy_evidence": policy_evidence,
        "llm_result": llm_result,
        "requires_clarification": False,
        "refund_reason": reason,
        "order_evidence_source": "SQLite",
        "policy_source": policy_sources[0] if policy_sources else None,
        "retrieved_chunks": len(clauses),
    }


def set_db_path(path: str):
    global _DB_PATH
    _DB_PATH = path


# ── Policy retrieval safety net (demo stabilization pass) ─────────────
# If ChromaDB / the embedding model is unavailable, the policy paths must
# degrade to a Thai deterministic response instead of raising an HTTP 500.
_POLICY_UNAVAILABLE_RESPONSE = (
    "ขออภัย ระบบนโยบายของร้านไม่พร้อมใช้งานในขณะนี้ "
    "กรุณาถามเกี่ยวกับคำสั่งซื้อ การชำระเงิน หรือการจัดส่ง "
    "หรือลองอีกครั้งในภายหลังค่ะ"
)


def _safe_policy_query(query: str, top_k: int = 3) -> Dict:
    """Evaluate a policy query without ever raising.

    Returns the same shape as evaluate_policy_query; on any failure it
    returns an honest Thai deterministic response with retrieval_success
    False so the caller always has something to say (no HTTP 500).
    """
    try:
        return evaluate_policy_query(query, top_k=top_k)
    except Exception:
        return {
            "agent": "store_policy_evaluator",
            "retrieved_clauses": [],
            "retrieval_success": False,
            "retrieval_source": "unavailable",
            "policy_sources": [],
            "retrieved_chunk_count": 0,
            "top_similarity_score": 0.0,
            "deterministic_response": _POLICY_UNAVAILABLE_RESPONSE,
            "requires_clarification": False,
            "simulated_human_review": False,
            "evidence": {},
            "response": _POLICY_UNAVAILABLE_RESPONSE,
            "response_source": "deterministic",
        }


def process_message(
    message: str,
    session_id: Optional[str] = None,
    order_id: Optional[str] = None,
) -> Dict:
    """Process a customer message through the full pipeline.

    Task 8A: an optional explicit order_id (from the frontend order-context
    card) is normalized and attached to session state as active_order_id.
    The browser's order details are display context only — SQLite is the
    source of truth.

    Returns a dict matching the POST /api/chat response schema.
    """
    start_time = time.time()
    session_id = session_id or f"demo-{uuid.uuid4().hex[:8]}"

    # ── Load conversation state ──────────────────────────────────
    pending = _pending_state(session_id)
    pending_intent = pending.get("pending_intent")
    missing = pending.get("missing_slots")
    collected = pending.get("collected_slots")

    # ── Step 1: Route (stateless classification) ─────────────────
    route = route_message(message)
    router_intent = route["intent"]
    intent_source = route.get("intent_source", "explicit")
    extracted_order_id = _normalize_order_id(route.get("extracted_order_id"))

    # Task 8A — normalize the explicit request order_id (frontend context).
    request_order_id = _normalize_order_id(order_id)

    # ── Task 5D-7 Objective 8: fixed Thai output policy ──────────
    # Explicit output-language requests ("reply in English") never switch the
    # output language. They are answered in Thai with a polite explanation.
    # Routing decisions are unchanged; only the response text is affected.
    lang_request = explicit_language_request(message)
    if lang_request == "en":
        response_text = ENGLISH_REQUEST_RESPONSE
        handler_used = route["target_agent"]
        return {
            "session_id": session_id,
            "intent": router_intent,
            "agent": handler_used,
            "handler": handler_used,
            "order_id": extracted_order_id,
            "response": response_text,
            "evidence": {},
            "policy_evidence": {},
            "requires_clarification": False,
            "simulated_human_review": False,
            "latency_ms": round((time.time() - start_time) * 1000, 2),
            "response_source": "deterministic",
            "llm_enabled": DEEPSEEK_ENABLED,
            "llm_fallback_used": True,
            "llm_latency_ms": 0.0,
            "llm_error_type": None,
            "routing_confidence": route.get("confidence", 1.0),
            "routing_reason": route.get("routing_reason", ""),
            "grounding_validation_passed": None,
            "refund_reason": None,
            "order_evidence_source": None,
            "policy_source": None,
            "retrieved_chunks": None,
            "llm_used": False,
            "fallback_reason": "deterministic",
            "response_language": CUSTOMER_RESPONSE_LANGUAGE,
            "input_language_detected": detect_input_language(message),
        }
    if lang_request == "th":
        response_text = THAI_REQUEST_RESPONSE
        handler_used = route["target_agent"]
        return {
            "session_id": session_id,
            "intent": router_intent,
            "agent": handler_used,
            "handler": handler_used,
            "order_id": extracted_order_id,
            "response": response_text,
            "evidence": {},
            "policy_evidence": {},
            "requires_clarification": False,
            "simulated_human_review": False,
            "latency_ms": round((time.time() - start_time) * 1000, 2),
            "response_source": "deterministic",
            "llm_enabled": DEEPSEEK_ENABLED,
            "llm_fallback_used": True,
            "llm_latency_ms": 0.0,
            "llm_error_type": None,
            "routing_confidence": route.get("confidence", 1.0),
            "routing_reason": route.get("routing_reason", ""),
            "grounding_validation_passed": None,
            "refund_reason": None,
            "order_evidence_source": None,
            "policy_source": None,
            "retrieved_chunks": None,
            "llm_used": False,
            "fallback_reason": "deterministic",
            "response_language": CUSTOMER_RESPONSE_LANGUAGE,
            "input_language_detected": detect_input_language(message),
        }

    # ── Session-aware routing priority (Task 5D-5) ───────────────
    # 1. Explicit user intent words
    # 2. Active pending conversation intent
    # 3. Order ID extraction (entity, not intent)
    # 4. Generic fallback
    intent = router_intent
    continued_pending = False
    if intent_source == "explicit":
        if router_intent == "GREETING" and pending_intent:
            intent = pending_intent          # a greeting never hijacks pending work
            continued_pending = True
        elif router_intent == "GREETING":
            intent = "GREETING"
        else:
            intent = router_intent
            if pending_intent and router_intent != pending_intent:
                _clear_pending(session_id)   # a new explicit topic cancels pending
                pending_intent = None
                missing = []
                collected = {}
    elif pending_intent:
        intent = pending_intent              # active pending conversation intent
        continued_pending = True
    elif intent_source == "order_id_fallback":
        intent = "ORDER_STATUS"              # order ID is an entity, not an intent
    else:
        intent = "UNKNOWN"

    # ── Task 8A — order-intent refinement + active order context ──
    # Demo-layer refinement (the evaluated router taxonomy is untouched):
    # ORDER_TOTAL and PURCHASED_ITEMS are recognized when the message is
    # order-focused. Without any order context the transaction branch asks
    # for the order ID once (never repeating a supplied ID — CONV-006).
    demo_intent = detect_order_intent(message)
    active_order_id = _get_active_order(session_id)
    if demo_intent:
        intent = demo_intent

    # A strong order-referential phrase ("this order", "คำสั่งซื้อนี้") or a
    # bare English pronoun ("Where is it?") in an otherwise UNKNOWN message
    # refers to the active order. Product-price / buying questions never match.
    if (
        intent == "UNKNOWN"
        and active_order_id
        and (
            _STRONG_ORDER_FOLLOWUP_RE.search(message or "")
            or (
                _EN_PRONOUN_FOLLOWUP_RE.search(message or "")
                and not _EN_PRICE_PHRASE_RE.search(message or "")
            )
        )
    ):
        intent = "ORDER_STATUS"

    # ── Task 8A — resolve the effective order for this turn ──────
    effective_order_id = _resolve_order_context(
        message, request_order_id, session_id, intent, collected
    )

    # Establish / replace active order context from explicit sources.
    # An explicit new order always overrides an old active order.
    explicit_order = extracted_order_id or request_order_id
    if explicit_order:
        if active_order_id != explicit_order:
            _set_active_order(session_id, explicit_order)
            active_order_id = explicit_order
    elif (
        intent in _TXN_INTENTS_EXT
        and effective_order_id
        and effective_order_id != active_order_id
    ):
        # A follow-up / workflow-resolved order is promoted to the session
        # active order so later pronoun turns keep working.
        _set_active_order(session_id, effective_order_id)
        active_order_id = effective_order_id

    # Task 8A honest-action metadata (defaults: no action was executed).
    action_executed = False
    escalation_created = False
    cancellation_created = False

    # Default result fields
    llm_result = _deterministic_llm_result("")
    response_text = ""
    handler_used = route["target_agent"]
    evidence = {}
    policy_evidence = {}
    requires_clarification = route.get("requires_clarification", False)
    simulated_human_review = route.get("simulated_human_review", False)

    # Task 5D-5 research metadata (refund path)
    refund_reason = None
    order_evidence_source = None
    policy_source = None
    retrieved_chunks = None

    # ── Step 2: Handle based on intent ───────────────────────────

    if intent == "GREETING":
        response_text = _GREETING_RESPONSE

    elif intent == "RESEARCH_INFO":
        # ── Deterministic research/about answer ──────────────────
        # Static application metadata only — no SQLite, no DeepSeek,
        # no order-ID request. An explicit new topic already cleared any
        # pending workflow above; active_order_id stays as context but
        # never hijacks the answer.
        response_text = _RESEARCH_INFO_RESPONSE

    elif intent == "OUT_OF_SCOPE":
        _clear_pending(session_id)
        if _CANCEL_RE.search(message or ""):
            # ── Cancellation request — deterministic simulated flow ──
            # processing + not_shipped orders get a Thai confirmation ask
            # and, once confirmed, a real simulated cancellation through
            # the same service the Order Details UI uses.
            cancel = _handle_cancellation_flow(
                session_id, message, effective_order_id
            )
            response_text = cancel["response"]
            handler_used = cancel["handler"]
            evidence = cancel["evidence"]
            llm_result = cancel["llm_result"]
            requires_clarification = cancel["requires_clarification"]
            order_evidence_source = cancel["order_evidence_source"]
            action_executed = cancel["action_executed"]
            escalation_created = cancel["escalation_created"]
            cancellation_created = cancel["cancellation_created"]
            _set_last_completed(
                session_id,
                "CANCELLATION_EXECUTED" if action_executed else "CANCELLATION_GUIDANCE",
            )
        else:
            response_text = _OUT_OF_SCOPE_RESPONSE

    elif intent == _CANCELLATION_CONFIRM_INTENT:
        # ── Pending cancellation continuation ("Confirm", "ยืนยัน", …) ──
        # The pending CANCELLATION_CONFIRM intent keeps active_order_id and
        # the collected order id; a confirmation executes the deterministic
        # cancellation service, anything else re-asks or declines honestly.
        cancel = _handle_cancellation_flow(
            session_id, message, effective_order_id
        )
        response_text = cancel["response"]
        handler_used = cancel["handler"]
        evidence = cancel["evidence"]
        llm_result = cancel["llm_result"]
        requires_clarification = cancel["requires_clarification"]
        order_evidence_source = cancel["order_evidence_source"]
        action_executed = cancel["action_executed"]
        escalation_created = cancel["escalation_created"]
        cancellation_created = cancel["cancellation_created"]
        _set_last_completed(
            session_id,
            "CANCELLATION_EXECUTED" if action_executed else "CANCELLATION_GUIDANCE",
        )

    elif intent in ("CLARIFICATION", "UNKNOWN") and requires_clarification:
        response_text = _CLARIFICATION_RESPONSE

    elif intent == "UNKNOWN":
        response_text = _UNKNOWN_RESPONSE

    elif intent == "RETURN_REFUND":
        if _is_refund_request(message) or pending_intent == "RETURN_REFUND":
            # ── Refund/cancellation request flow (Task 5D-5) ──
            # Task 8A: effective_order_id may come from the request context
            # or the session active order, not only from the message text.
            flow = _run_refund_flow(session_id, message, effective_order_id,
                                    collected, missing)
            response_text = flow["response"]
            handler_used = flow["handler"]
            evidence = flow["evidence"]
            policy_evidence = flow["policy_evidence"]
            llm_result = flow["llm_result"]
            requires_clarification = flow["requires_clarification"]
            refund_reason = flow.get("refund_reason")
            order_evidence_source = flow.get("order_evidence_source")
            policy_source = flow.get("policy_source")
            retrieved_chunks = flow.get("retrieved_chunks")
        else:
            # ── General refund/return policy question ──────────
            policy_result = _safe_policy_query(message)
            handler_used = policy_result["agent"]

            retrieved_clauses = policy_result.get("retrieved_clauses", [])
            policy_evidence = {
                "retrieved_clauses": retrieved_clauses,
                "retrieval_success": policy_result.get("retrieval_success", False),
                "retrieval_source": policy_result.get("retrieval_source", "chromadb"),
                "policy_sources": policy_result.get("policy_sources", []),
                "retrieved_chunk_count": policy_result.get("retrieved_chunk_count", 0),
                "top_similarity_score": policy_result.get("top_similarity_score", 0.0),
            }
            deterministic_fallback = policy_result.get("deterministic_response", "")

            route["requires_clarification"] = policy_result.get(
                "requires_clarification", False
            )
            route["simulated_human_review"] = policy_result.get(
                "simulated_human_review", False
            )

            llm_result = generate_response(
                user_message=message,
                intent=intent,
                order_evidence=None,
                policy_chunks=retrieved_clauses,
                deterministic_fallback=deterministic_fallback,
                requires_clarification=route["requires_clarification"],
                simulated_human_review=route["simulated_human_review"],
            )
            response_text = llm_result["response"]
            evidence = policy_result.get("evidence", {})
            policy_sources = policy_evidence.get("policy_sources") or []
            policy_source = policy_sources[0] if policy_sources else None
            retrieved_chunks = policy_evidence.get("retrieved_chunk_count")

    elif intent == "STORE_POLICY":
        # ── Policy path: RAG retrieval + LLM generation ─────────
        policy_result = _safe_policy_query(message)
        handler_used = policy_result["agent"]

        retrieved_clauses = policy_result.get("retrieved_clauses", [])
        policy_evidence = {
            "retrieved_clauses": retrieved_clauses,
            "retrieval_success": policy_result.get("retrieval_success", False),
            "retrieval_source": policy_result.get("retrieval_source", "chromadb"),
            "policy_sources": policy_result.get("policy_sources", []),
            "retrieved_chunk_count": policy_result.get("retrieved_chunk_count", 0),
            "top_similarity_score": policy_result.get("top_similarity_score", 0.0),
        }
        deterministic_fallback = policy_result.get("deterministic_response", "")

        route["requires_clarification"] = policy_result.get(
            "requires_clarification", False
        )
        route["simulated_human_review"] = policy_result.get(
            "simulated_human_review", False
        )

        llm_result = generate_response(
            user_message=message,
            intent=intent,
            order_evidence=None,
            policy_chunks=retrieved_clauses,
            deterministic_fallback=deterministic_fallback,
            requires_clarification=route["requires_clarification"],
            simulated_human_review=route["simulated_human_review"],
        )
        response_text = llm_result["response"]
        evidence = policy_result.get("evidence", {})
        policy_sources = policy_evidence.get("policy_sources") or []
        policy_source = policy_sources[0] if policy_sources else None
        retrieved_chunks = policy_evidence.get("retrieved_chunk_count")

    elif intent in _TXN_INTENTS_EXT:
        # ── Transaction path: data fetch + DETERMINISTIC response ──
        # Order-status answers are always SQLite-backed and deterministic
        # (Task 5D-4): DeepSeek is never called for order facts.
        # Task 8A: effective_order_id covers explicit IDs, request context
        # and the session active order (pronoun / follow-up reuse).
        if not effective_order_id:
            # Missing order ID — remember the intent and ask for the ID.
            _set_pending(session_id, intent, ["order_id"], collected,
                         route["target_agent"], "ask_missing_slots")
            _set_pending_subtype(
                session_id,
                intent if intent in ("ORDER_TOTAL", "PURCHASED_ITEMS") else None,
            )
            response_text = _ask_order_id(message)
            requires_clarification = True
        else:
            # Always inject the resolved order ID so the pure data layer
            # finds the order even when the message is a bare follow-up
            # ("ส่งของหรือยัง", "Has it been paid?").
            txn_result = fetch_transaction_evidence(
                f"{message} {effective_order_id}", db_path=_DB_PATH
            )
            txn_evidence = txn_result.get("evidence", {})
            txn_intent = txn_result.get("intent", intent)
            txn_order_id = txn_result.get("order_id")

            # Use tracker's intent when it is more specific — but never
            # override a continued pending intent or a Task 8A refined
            # ORDER_TOTAL / PURCHASED_ITEMS intent.
            if (
                txn_intent
                and not continued_pending
                and intent not in ("ORDER_TOTAL", "PURCHASED_ITEMS")
            ):
                intent = txn_intent
            if txn_order_id:
                effective_order_id = txn_order_id

            evidence = txn_evidence
            order_evidence_source = "SQLite" if txn_evidence else None

            if not txn_evidence:
                deterministic_fallback = _not_found_response(effective_order_id, message)
            elif intent == "ORDER_TOTAL":
                # SQLite total_amount (order_items for the fallback sum).
                detail = None
                try:
                    detail = store_db.get_order_with_items(
                        store_db.STORE_DB_PATH, effective_order_id
                    )
                except Exception:
                    detail = None
                items = (detail or {}).get("items") or []
                if items:
                    evidence["items"] = items
                if detail is not None and detail.get("total_amount") is not None:
                    evidence["total_amount"] = detail["total_amount"]
                deterministic_fallback = _order_total_response(
                    effective_order_id, detail or {}, items
                )
            elif intent == "PURCHASED_ITEMS":
                # SQLite order_items — the purchased product list.
                detail = None
                try:
                    detail = store_db.get_order_with_items(
                        store_db.STORE_DB_PATH, effective_order_id
                    )
                except Exception:
                    detail = None
                items = (detail or {}).get("items") or []
                evidence["items"] = items
                deterministic_fallback = _purchased_items_response(
                    effective_order_id, items
                )
            else:
                deterministic_fallback = _txn_status_response(message, txn_evidence, intent)

            llm_result = _deterministic_llm_result(deterministic_fallback)
            response_text = deterministic_fallback
            _set_last_completed(session_id, intent)

    # ── Step 3: Assemble final result ─────────────────────────────
    final_latency = round((time.time() - start_time) * 1000, 2)

    llm_used = llm_result["source"] == "deepseek"
    if llm_used:
        fallback_reason = None
    elif not DEEPSEEK_ENABLED:
        fallback_reason = "deepseek_disabled"
    elif llm_result.get("error_type"):
        fallback_reason = llm_result["error_type"]
    else:
        fallback_reason = "deterministic"

    return {
        "session_id": session_id,
        "intent": intent,
        "agent": handler_used,
        "handler": handler_used,
        "order_id": extracted_order_id or effective_order_id,
        "response": response_text,
        "evidence": evidence,
        "policy_evidence": policy_evidence,
        "requires_clarification": requires_clarification,
        "simulated_human_review": simulated_human_review,
        "latency_ms": final_latency,
        "response_source": llm_result["source"],
        "llm_enabled": DEEPSEEK_ENABLED,
        "llm_fallback_used": llm_result["source"] != "deepseek",
        "llm_latency_ms": llm_result["latency_ms"],
        "llm_error_type": llm_result.get("error_type"),
        "routing_confidence": route.get("confidence", 1.0),
        "routing_reason": route.get("routing_reason", ""),
        "grounding_validation_passed": llm_result.get("validation_passed"),
        # Task 5D-5 multi-source research metadata
        "refund_reason": refund_reason,
        "order_evidence_source": order_evidence_source,
        "policy_source": policy_source,
        "retrieved_chunks": retrieved_chunks,
        "llm_used": llm_used,
        "fallback_reason": fallback_reason,
        # Task 8A — session active order + honest-action metadata
        "active_order_id": _get_active_order(session_id),
        "action_executed": action_executed,
        "escalation_created": escalation_created,
        "cancellation_created": cancellation_created,
        # Task 5D-7 Objective 7: fixed-Thai response metadata
        "response_language": CUSTOMER_RESPONSE_LANGUAGE,
        "input_language_detected": detect_input_language(message),
    }
