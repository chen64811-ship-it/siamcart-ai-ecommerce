"""
Intelligent Router — Phase 2A.

Deterministic, offline intent classification and routing.
No LLM calls. No embeddings. No external dependencies.

Returns a structured RouteDecision dict.
"""

import re
from typing import Dict, List, Optional, Any, Tuple

# ── Order ID pattern ──────────────────────────────────────────────────
ORDER_ID_PATTERN = re.compile(r"ORD[-]?\d{4}", re.IGNORECASE)


# ── Intent taxonomy ───────────────────────────────────────────────────

# Priority order — highest first. Matches are checked in this order.
_INTENT_PRIORITY: List[str] = [
    "RESEARCH_INFO",
    "TRACKING_NUMBER",
    "PAYMENT_STATUS",
    "SHIPMENT_STATUS",
    "ORDER_STATUS",
    "RETURN_REFUND",
    "STORE_POLICY",
    "GREETING",
    "OUT_OF_SCOPE",
]

# Intents that require an order_id
_REQUIRES_ORDER_ID = {
    "ORDER_STATUS",
    "PAYMENT_STATUS",
    "SHIPMENT_STATUS",
    "TRACKING_NUMBER",
}

# Intents that always require clarification
_ALWAYS_CLARIFY = {"UNKNOWN"}

# Intent → target agent mapping
_AGENT_MAP = {
    "ORDER_STATUS": "transaction_tracker",
    "PAYMENT_STATUS": "transaction_tracker",
    "SHIPMENT_STATUS": "transaction_tracker",
    "TRACKING_NUMBER": "transaction_tracker",
    "RETURN_REFUND": "store_policy_evaluator",
    "STORE_POLICY": "store_policy_evaluator",
    "RESEARCH_INFO": "general_response",
    "GREETING": "general_response",
    "CLARIFICATION": "clarification_handler",
    "UNKNOWN": "clarification_handler",
    "OUT_OF_SCOPE": "simulated_human_review",
}

# Intent → keyword patterns (checked in priority order per intent group)
_INTENT_PATTERNS: Dict[str, List[str]] = {
    "TRACKING_NUMBER": [
        "หมายเลขพัสดุ",
        "เลขพัสดุ",
        "เลขติดตาม",
        "tracking number",
        "tracking code",
        "track no",
        "track number",
        "tracking id",
        # Task 5D-5 — explicit intent words include bare "tracking"
        "tracking",
        "TRK",
        # Task 8C — package/parcel/track-my phrasing routes to tracking.
        # Specific phrases only: bare "my package" / "parcel" would hijack
        # return requests ("Can I return my package?").
        "track my",
        "track my package",
        "track my parcel",
        "track my order",
        "package tracking",
        "parcel tracking",
        "my package status",
        "my parcel status",
        "where is my package",
        "where is my parcel",
        "ตามพัสดุ",
        "ติดตามพัสดุ",
        "พัสดุของฉัน",
    ],
    "PAYMENT_STATUS": [
        "ชำระเงินแล้วหรือยัง",
        "ชำระเงินแล้ว",
        "ชำระยัง",
        "จ่ายเงินแล้ว",
        "จ่ายหรือยัง",
        "payment status",
        "payment confirmed",
        "paid yet",
        "จ่ายเงิน",
        "ชำระเงิน",
        "ชำระ",
        # Task 5D-5 — "paid" is an explicit intent word, not an entity
        "paid",
    ],
    "SHIPMENT_STATUS": [
        "สถานะการจัดส่ง",
        "สถานะจัดส่ง",
        "shipment status",
        "shipping status",
        "จัดส่งหรือยัง",
        "ส่งของหรือยัง",
        "ส่งถึงไหน",
        "สถานะขนส่ง",
        "ได้รับของหรือยัง",
        "delivery status",
        "จัดส่ง",
        "ขนส่ง",
        # Task 5D-5 — explicit intent words include bare "shipment"
        "shipment",
        # Task 8D — courier / ETA questions are shipment work; the
        # Transaction Tracker refines them into COURIER / ETA intents.
        "courier",
        "which courier",
        "when will it arrive",
        "when will it get",
        "when will",
        "estimated delivery",
        "delivery date",
        "จะถึงเมื่อไหร่",
        "ถึงเมื่อไหร่",
        "เมื่อไหร่จะถึง",
        "จัดส่งถึงเมื่อไหร่",
        "ส่งถึงเมื่อไหร่",
        "กี่วันจะถึง",
    ],
    "ORDER_STATUS": [
        "สถานะคำสั่งซื้อ",
        "สถานะออเดอร์",
        "สถานะ",
        "ออเดอร์ถึงไหน",
        "คำสั่งซื้อถึงไหน",
        "ถึงไหนแล้ว",
        "order status",
        "อัพเดท",
        "update",
        "status",
        "ขั้นตอน",
        "ไปถึงไหน",
        "ถึงไหน",
    ],
    "RETURN_REFUND": [
        # Priority: longer/more specific phrases first to avoid false positives
        "defective item",
        "damaged item",
        "สินค้ามีตำหนิ",
        "สินค้าชำรุด",
        "refund status",
        "return status",
        "ได้เงินคืน",
        "เงินคืน",
        "ส่งสินค้าคืน",
        "ส่งคืนสินค้า",
        "คืนสินค้าแล้ว",
        "ขอคืนสินค้า",
        "คืนสินค้า",
        "ขอคืนเงิน",
        "refund",
        "return",
        "เปลี่ยนสินค้า",
        "คืนเงิน",
        "ขอเปลี่ยน",
        "exchange",
    ],
    "STORE_POLICY": [
        # General policy questions — checked after specific refund/return intents
        "กฎของร้าน",
        "นโยบายการคืน",
        "เงื่อนไขการคืน",
        "exchange policy",
        "shipping policy",
        "payment policy",
        "store rules",
        "terms and conditions",
        "นโยบายร้าน",
        "นโยบาย",
        "เงื่อนไขการคืนสินค้า",
        "return policy",
        "refund policy",
        "policy",
        "conditions",
        "ข้อกำหนด",
        "เงื่อนไข",
    ],
    "OUT_OF_SCOPE": [
        # Unsupported / human-review requests — high-priority detection
        "hacking",
        "hack",
        "unauthorized access",
        "force refund",
        "force a refund",
        "manually approve refund",
        "modify bank",
        "payment system",
        "bank records",
        "legal",
        "lawsuit",
        "attorney",
        "ไม่เกี่ยวกับร้าน",
        "judgment",
        # Existing patterns
        "เปลี่ยนที่อยู่",
        "เปลี่ยนที่อยู่จัดส่ง",
        "cancel order",
        "cancel",
        "ยกเลิก",
        "ยกเลิกคำสั่งซื้อ",
        "change address",
        "change order",
        "modify order",
        "edit order",
        "make payment",
        "process refund",
    ],
    "RESEARCH_INFO": [
        # Deterministic research/about intent — checked via pre-scan regex
        # before the priority loop (see _POLICY_PHRASE_RE / _RESEARCH_INFO_RE).
        # Substring list here keeps route_message() taxonomy self-documenting.
        "what research",
        "which research",
        "this research",
        "research prototype",
        "what is this project",
        "what is this research",
        "about this research",
        "งานวิจัยนี้",
        "เกี่ยวกับงานวิจัย",
        "siamcart คืออะไร",
        "siamcart คือ",
    ],
}

# ── Greeting typo handling (Task 5D-5) ───────────────────────────────
# Token-aware / whole-message matching only — never substring matching
# ("hi" must NOT match inside "this").
_EN_GREETING_WORDS = {"hi", "hai", "hii", "helo", "hello", "hey", "heya", "yo", "howdy"}
_EN_GREETING_PHRASES = {
    "good morning", "good afternoon", "good evening", "good day",
    "hello there", "hi there", "hey there", "hello everyone",
}
_THAI_GREETING_WORDS = ("สวัสดี", "หวัดดี")


# ── Intent-boundary hotfix: deterministic pre-scans ────────────────────
# Checked BEFORE the generic priority loop so that:
#   - policy questions ("What is the return policy?", "What policies does
#     SiamCart follow?", "คืนสินค้าได้ไหม") never fall into RETURN_REFUND
#     or OUT_OF_SCOPE;
#   - research/about questions ("What research is SiamCart part of?",
#     "What is SiamCart?") are never hijacked by active-order context.
# These are pure regex matches on the normalized message — no LLM, no
# external data, no invented policy terms.

_POLICY_PHRASE_RE = re.compile(
    r"("
    r"return policy|refund policy|store policy|cancellation policy|"
    r"exchange policy|shipping policy|payment policy|privacy policy|"
    r"delivery policy|terms and conditions|"
    r"what policies|which policies|the policies|store rules|"
    r"นโยบายการคืนสินค้า|นโยบายการคืนเงิน|นโยบายการเปลี่ยนสินค้า|"
    r"นโยบายการจัดส่ง|นโยบายการชำระเงิน|นโยบายของร้าน|"
    r"คืนสินค้าได้ไหม|คืนสินค้าได้มั้ย|คืนได้ไหม|คืนเงินได้ไหม|"
    r"\bpolicy\b|\bpolicies\b|นโยบาย|ข้อกำหนด|กฎของร้าน|เงื่อนไขของร้าน"
    r")",
    re.IGNORECASE,
)

_RESEARCH_INFO_RE = re.compile(
    r"("
    r"\bwhat\s+research\b|\bwhich\s+research\b|\bthis\s+research\b|"
    r"\bresearch\s+prototype\b|\bpart\s+of\s+(a\s+)?research\b|"
    r"\babout\s+this\s+research\b|\babout\s+the\s+research\b|"
    r"\bthesis\b|งานวิจัย|"
    r"\bwhat\s+is\s+this\s+project\b|\bwhat\s+is\s+this\s+research\b|"
    r"\bwhat\s+is\s+this\s+system\b|\bthis\s+project\s+about\b|"
    r"\babout\s+this\s+project\b"
    r")",
    re.IGNORECASE,
)

# Anchored identity questions ("What is SiamCart?", "SiamCart คืออะไร") —
# anchored so "What is SiamCart's return policy?" stays a policy question.
_IDENTITY_QUESTION_RE = re.compile(
    r"^\s*what('s|\s+is)\s+siam\s*cart\s*\??\s*$"
    r"|^\s*siam\s*cart\s+คืออะไร\s*\??\s*$"
    r"|^\s*siam\s*cart\s+คือ\s*\??\s*$",
    re.IGNORECASE,
)


def _is_greeting_message(message: str) -> bool:
    """Whole-message / first-token greeting detection.

    Thai greetings match as substrings (Thai words do not create the
    "hi inside this" hazard). English greetings only match as a whole
    normalized message or as the first token — never as a bare substring.
    """
    msg = (message or "").strip().lower()
    if not msg:
        return False
    for kw in _THAI_GREETING_WORDS:
        if kw in msg:
            return True
    norm = re.sub(r"[^a-z\s']", " ", msg)
    tokens = [t for t in norm.split() if t]
    if not tokens:
        return False
    whole = " ".join(tokens)
    if whole in _EN_GREETING_PHRASES:
        return True
    if tokens[0] in _EN_GREETING_WORDS:
        return True
    # "good morning" / "good afternoon" split across tokens
    if tokens[0] == "good" and len(tokens) >= 2 and tokens[1] in {"morning", "afternoon", "evening", "day"}:
        return True
    return False


def _extract_order_id(message: str) -> Optional[str]:
    """Extract order ID like ORD-1001 or ORD1001."""
    match = ORDER_ID_PATTERN.search(message)
    if match:
        raw = match.group(0).upper()
        if "-" not in raw:
            raw = raw[:3] + "-" + raw[3:]
        return raw
    return None


def _looks_out_of_scope(msg_lower: str) -> bool:
    """Conservative off-topic detector.

    Returns True only when the message contains a clear non-store topic marker
    AND no store-domain vocabulary. Ambiguous messages return False so they
    fall through to UNKNOWN (which asks for an order number) rather than being
    wrongly escalated to human review.
    """
    _STORE_DOMAIN = (
        "order", "ord-", "ord1", "สินค้า", "คำสั่งซื้อ", "ออเดอร์", "พัสดุ",
        "จัดส่ง", "ขนส่ง", "ชำระ", "จ่าย", "คืน", "ยกเลิก", "refund", "return",
        "tracking", "shipment", "delivery", "payment", "product", "price",
        "ราคา", "นโยบาย", "policy", "ใบเสร็จ", "ที่อยู่", "address", "stock",
        "สต็อก", "โปรโมชั่น", "promotion", "discount", "ส่วนลด",
    )
    if any(kw in msg_lower for kw in _STORE_DOMAIN):
        return False

    _OFFTOPIC_MARKERS = (
        # General knowledge / unrelated topics
        "weather", "อากาศ", "พยากรณ์", "joke", "ตลก", "เรื่องตลก",
        "football", "ฟุตบอล", "politics", "การเมือง", "news", "ข่าว",
        "recipe", "สูตรอาหาร", "song", "เพลง", "movie", "หนัง",
        "capital of", "who is the president", "translate this to",
        # Prompt-injection / system-probing attempts
        "ignore previous", "ignore all previous", "ignore the above",
        "system prompt", "reveal your", "your instructions", "jailbreak",
        "ลืมคำสั่ง", "เปิดเผยคำสั่ง",
    )
    return any(m in msg_lower for m in _OFFTOPIC_MARKERS)


def _classify_with_source(message: str) -> Tuple[str, str]:
    """Return (intent, source) where source is one of:
      - "explicit": a keyword pattern matched
      - "order_id_fallback": no pattern matched; ORDER_STATUS inferred from ID
      - "unknown": nothing matched
    """
    msg_lower = message.lower().strip()

    # ── Intent-boundary pre-scans (deterministic) ──────────────────
    # Policy phrases first, then research/about. Both are "explicit"
    # sources so the orchestrator's explicit-topic priority (new intent
    # overrides active-order context and pending workflows) applies.
    if _POLICY_PHRASE_RE.search(msg_lower):
        return "STORE_POLICY", "explicit"
    if _IDENTITY_QUESTION_RE.match(msg_lower) or _RESEARCH_INFO_RE.search(msg_lower):
        return "RESEARCH_INFO", "explicit"

    # Check each intent group in priority order
    for intent in _INTENT_PRIORITY:
        if intent == "GREETING":
            if _is_greeting_message(msg_lower):
                return "GREETING", "explicit"
            continue
        patterns = _INTENT_PATTERNS.get(intent, [])
        for pattern in patterns:
            if pattern.lower() in msg_lower:
                return intent, "explicit"

    # Fallback: if message contains an order ID, it's likely ORDER_STATUS
    # (an order ID is an entity, not an intent — Task 5D-5)
    if _extract_order_id(message):
        return "ORDER_STATUS", "order_id_fallback"

    # Off-topic detection (conservative): only when there is NO store-domain
    # vocabulary anywhere in the message AND a clear non-store topic marker
    # is present. Anything ambiguous stays UNKNOWN (safer: asks for an order
    # number rather than wrongly flagging a real question for human review).
    if _looks_out_of_scope(msg_lower):
        return "OUT_OF_SCOPE", "explicit"

    return "UNKNOWN", "unknown"


def _classify_intent(message: str) -> str:
    """Backward-compatible wrapper: return the intent label only."""
    intent, _ = _classify_with_source(message)
    return intent


def route_message(message: str) -> Dict[str, Any]:
    """Classify a customer message and return a structured RouteDecision.

    Returns dict with keys:
      - intent: str
      - target_agent: str
      - confidence: float (1.0 for exact match, 0.5 for inferred)
      - required_entities: list
      - missing_entities: list
      - extracted_order_id: str | None
      - requires_clarification: bool
      - simulated_human_review: bool
      - routing_reason: str
    """
    message = message.strip()
    if not message:
        return {
            "intent": "UNKNOWN",
            "intent_source": "unknown",
            "target_agent": "clarification_handler",
            "confidence": 0.3,
            "required_entities": [],
            "missing_entities": [],
            "extracted_order_id": None,
            "requires_clarification": True,
            "simulated_human_review": False,
            "routing_reason": "Empty message",
        }

    intent, intent_source = _classify_with_source(message)
    order_id = _extract_order_id(message)

    required = list(_REQUIRES_ORDER_ID) if intent in _REQUIRES_ORDER_ID else []
    missing = []
    requires_clarification = False

    # Transaction intents need order_id; UNKNOWN always needs clarification
    if intent in _REQUIRES_ORDER_ID and not order_id:
        missing.append("order_id")
        requires_clarification = True
    elif intent in _ALWAYS_CLARIFY:
        requires_clarification = True

    # OUT_OF_SCOPE always sets simulated_human_review
    simulated_human_review = intent == "OUT_OF_SCOPE"

    # Confidence
    if intent == "UNKNOWN":
        confidence = 0.3
    elif intent in ("OUT_OF_SCOPE", "CLARIFICATION"):
        confidence = 0.6
    else:
        confidence = 1.0

    target_agent = _AGENT_MAP.get(intent, "clarification_handler")

    # Build routing_reason
    if intent == "UNKNOWN":
        routing_reason = "No matching intent pattern found"
    elif requires_clarification:
        routing_reason = (
            f"Matched {intent} pattern but missing required entity: order_id"
        )
    else:
        routing_reason = f"Matched {intent} intent pattern"

    return {
        "intent": intent,
        "intent_source": intent_source,
        "target_agent": target_agent,
        "confidence": confidence,
        "required_entities": required,
        "missing_entities": missing,
        "extracted_order_id": order_id,
        "requires_clarification": requires_clarification,
        "simulated_human_review": simulated_human_review,
        "routing_reason": routing_reason,
    }
