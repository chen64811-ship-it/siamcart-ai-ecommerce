"""Centralized customer-response language policy (Task 5D-7).

Thesis scope: the target user is a Thai-speaking online retail customer, so
EVERY customer-facing assistant response is written in natural Thai.

Policy:
  - customer_response_language is fixed to "th";
  - input language is detected only for research metadata
    (input_language_detected) — it never switches the output language;
  - explicit requests such as "reply in English" still receive a polite Thai
    explanation in the thesis demo mode;
  - deterministic facts never require DeepSeek for localization.

This module is pure string logic — no I/O, no LLM, no routing changes.
"""

import re

# ── The one centralized rule ───────────────────────────────────────────
CUSTOMER_RESPONSE_LANGUAGE = "th"

# Polite explanation used when a customer explicitly asks for English in the
# thesis demo mode (Objective 8).
ENGLISH_REQUEST_RESPONSE = (
    "ขออภัย ระบบต้นแบบนี้ออกแบบมาเพื่อให้บริการลูกค้าภาษาไทยค่ะ "
    "(This research prototype serves Thai-speaking customers.)"
)

# Polite acknowledgment when a customer explicitly asks for Thai.
THAI_REQUEST_RESPONSE = (
    "ได้เลยค่ะ ฉันจะตอบเป็นภาษาไทยเสมอ "
    "กรุณาสอบถามเกี่ยวกับสินค้า คำสั่งซื้อ การชำระเงิน "
    "หรือการจัดส่งได้เลยค่ะ"
)

_THAI_RE = re.compile(r"[\u0E00-\u0E7F]")
_EN_LETTER_RE = re.compile(r"[A-Za-z]")

# Explicit output-language requests (whole-message / phrase matching only).
_EN_REQUEST_RE = re.compile(
    r"("
    r"\b(reply|answer|respond|speak|talk|write|type|say)\s+in\s+english\b"
    r"|\bin\s+english\s+please\b"
    r"|\benglish\s+please\b"
    r"|\btranslate\s+(to|into)\s+english\b"
    r"|\bswitch\s+(to|into)\s+english\b"
    r"|\bภาษาอังกฤษ\b"
    r"|\bspeak\s+english\b"
    r")",
    re.IGNORECASE,
)

_TH_REQUEST_RE = re.compile(
    r"("
    r"\b(reply|answer|respond|speak|talk|write|type|say)\s+in\s+thai\b"
    r"|\bin\s+thai\s+please\b"
    r"|\bthai\s+please\b"
    r"|\btranslate\s+(to|into)\s+thai\b"
    r"|\bswitch\s+(to|into)\s+thai\b"
    r"|ตอบเป็นภาษาไทย"
    r"|ตอบภาษาไทย"
    r"|ภาษาไทย"
    r")",
    re.IGNORECASE,
)


def is_thai_text(message: str) -> bool:
    """True when the message contains Thai script (U+0E00–U+0E7F)."""
    return bool(_THAI_RE.search(message or ""))


def detect_input_language(message: str) -> str:
    """Detect the customer's input language for research metadata only.

    Returns one of: "th", "en", "mixed", "unknown".
    The output language is ALWAYS "th" regardless of this value.
    """
    msg = message or ""
    thai = bool(_THAI_RE.search(msg))
    english = bool(_EN_LETTER_RE.search(msg))
    if thai and english:
        return "mixed"
    if thai:
        return "th"
    if english:
        return "en"
    return "unknown"


def explicit_language_request(message: str):
    """Return "th", "en" or None for an explicit output-language request.

    Explicit requests are handled at the response layer only — routing
    decisions are never changed.
    """
    msg = (message or "").strip()
    if not msg:
        return None
    if _EN_REQUEST_RE.search(msg):
        return "en"
    if _TH_REQUEST_RE.search(msg):
        return "th"
    return None
