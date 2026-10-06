"""Unified LLM Generator — Phase 4A.

Single entry point for ALL LLM-based response generation.

Receives structured evidence from any agent (transaction, policy, etc.)
and generates natural Thai customer-support responses.

Replaces:
  - llm_client.py (cosmetic formatter for non-policy)
  - policy_evaluator's inline DeepSeek call (policy RAG generation)

Architecture:
  data_layer → llm_generator.generate_response(evidence) → natural Thai
"""

import json
import time
from typing import Dict, Optional, Any, List

from openai import OpenAI, APITimeoutError, APIStatusError, APIConnectionError

from app.config import DEEPSEEK_ENABLED, LLM_CONFIG


# ── Unified system prompt ──────────────────────────────────────────────
# Task 5D-7 Objective 5: the LLM must answer in Thai ONLY — input language
# never overrides this instruction, and no English translation is included.

SYSTEM_PROMPT = """You are SiamCart, a Thai e-commerce customer-support assistant.

あなたは SiamCart のタイ語カスタマーサポートアシスタントです。
You are a Thai e-commerce customer-support assistant.

Rules:
1. Answer in **polite, natural Thai ONLY**. Use ค่ะ/คะ appropriately.
   Do NOT answer in English and do NOT include an English translation —
   even when the customer writes in English.
2. Use ONLY the evidence provided below. Do not invent facts.
   The evidence is authoritative: when it states a status (e.g.
   order_status: cancelled, payment_status: refunded, shipment_status:
   delivered), report that status DIRECTLY. Never say information is
   "not found" or "insufficient" when the evidence already contains it.
3. Preserve all order IDs, product names, statuses, dates, amounts, and
   policy terms exactly as written.
4. If the evidence is empty or insufficient, ask the customer to clarify politely.
5. If the customer's request is out of scope (accounting, hacking, legal, order changes),
   state that the request has been flagged for human review.
6. Keep responses concise — 2-4 sentences.
7. Do not mention internal systems (SQLite, ChromaDB, agents, prompts, LLMs).
8. Complete every sentence. Output only the Thai response text.
   No JSON, no explanations, no English translation."""

# Evidence fields that must survive in the response
CRITICAL_FIELDS = {
    "order_id", "tracking_number",
}

# Fields that may be naturally translated by the LLM (relaxed check)
_TRANSLATABLE_FIELDS = {
    "order_status", "payment_status", "shipment_status",
    "shipping_provider", "estimated_delivery_date",
}

# Terminal states that the LLM must NEVER contradict with a "not found" claim.
_TERMINAL_STATUS_VALUES = {"cancelled", "canceled", "refunded", "delivered"}

# Phrases that assert the customer's own order data is missing. If the
# evidence contains a terminal status, any of these means the model has
# contradicted authoritative data and the deterministic fallback must win.
_CONTRADICTION_PHRASES = (
    "ไม่พบข้อมูล",
    "ไม่พบสถานะ",
    "ไม่พบคำสั่งซื้อ",
    "ไม่เพียงพอ",
    "ข้อมูลไม่ครบ",
    "ตรวจสอบไม่ได้",
    "ยืนยันไม่ได้",
    "not found",
    "no information",
    "insufficient",
    "unable to confirm",
)


def _has_terminal_status(evidence: Dict[str, Any]) -> bool:
    """True when the evidence asserts a terminal order/payment state."""
    for field in ("order_status", "payment_status", "shipment_status"):
        value = str(evidence.get(field) or "").strip().lower()
        if value in _TERMINAL_STATUS_VALUES:
            return True
    return False


def _looks_like_reasoning_leak(text: str) -> bool:
    """Detect chain-of-thought / English meta-commentary leaking as the answer.

    Reasoning-capable models occasionally place their scratchpad in `content`
    instead of the final Thai answer. Symptoms: the text is dominated by
    English, or contains first-person planning markers ("We need answer",
    "Need use evidence", "Let's parse", "Rule 2:", etc.). A valid customer
    reply is polite Thai, so heavy English + meta markers = leak.

    Conservative by design: requires BOTH a meta marker AND that Thai is a
    small minority of the text, so a short English word inside a Thai answer
    never trips it.
    """
    if not text:
        return False
    low = text.lower()
    meta_markers = (
        "we need", "need answer", "need use", "let's ", "let us ",
        "rule 2:", "rule 4:", "rule says", "evidence only", "intent ",
        "we should", "we can say", "need respond", "okay?", "hmm.",
    )
    has_marker = any(m in low for m in meta_markers)
    if not has_marker:
        return False
    # Thai chars (U+0E00–U+0E7F) vs total letters
    thai = sum(1 for ch in text if "\u0e00" <= ch <= "\u0e7f")
    latin = sum(1 for ch in text if ch.isascii() and ch.isalpha())
    if thai + latin == 0:
        return False
    # If Latin letters dominate (Thai is < 30% of the alphabetic content), leak.
    return thai / (thai + latin) < 0.30


def _evidence_preserved(response_text: str, evidence: Dict[str, Any]) -> bool:
    """Check that critical evidence values appear in the response.

    - order_id and tracking_number are strictly checked (must match exactly).
    - Other fields (statuses, dates) are acceptably paraphrased by the LLM.
    - A terminal status (cancelled/refunded/delivered) must not be
      contradicted by a "not found / insufficient information" claim.
    """
    for field in CRITICAL_FIELDS:
        value = evidence.get(field)
        if value and str(value).strip():
            if str(value) not in response_text:
                return False

    if _has_terminal_status(evidence):
        low = response_text.lower()
        for phrase in _CONTRADICTION_PHRASES:
            if phrase in low:
                return False

    return True


def _build_context_block(
    order_evidence: Dict[str, Any],
    policy_chunks: List[Dict[str, Any]],
) -> str:
    """Build the evidence context block for the LLM prompt."""
    parts = []

    if order_evidence:
        parts.append("[Order Evidence]")
        for k, v in order_evidence.items():
            if v:
                parts.append(f"  {k}: {v}")
        parts.append("")

    if policy_chunks:
        parts.append("[Store Policy Evidence]")
        for i, chunk in enumerate(policy_chunks, 1):
            source = chunk.get("source_filename", "policy")
            section = chunk.get("section_title", "")
            text = chunk.get("text", "")
            parts.append(f"  ({i}) Source: {source}")
            if section:
                parts.append(f"      Section: {section}")
            parts.append(f"      {text}")
            parts.append("")

    return "\n".join(parts) if parts else "[No evidence available]"


def generate_response(
    user_message: str,
    intent: str,
    order_evidence: Optional[Dict[str, Any]] = None,
    policy_chunks: Optional[List[Dict[str, Any]]] = None,
    deterministic_fallback: str = "",
    requires_clarification: bool = False,
    simulated_human_review: bool = False,
) -> Dict[str, Any]:
    """Generate a natural Thai response through LLM, with fallback.

    Args:
        user_message: The customer's raw message.
        intent: Detected intent string.
        order_evidence: Structured order data from transaction_tracker.
        policy_chunks: Retrieved policy clauses from policy_evaluator.
        deterministic_fallback: Fallback text if LLM unavailable/fails.
        requires_clarification: Whether the response should ask for details.
        simulated_human_review: Whether this should be flagged for human review.

    Returns:
        dict with keys:
          - response: str (final Thai response)
          - source: "deepseek" | "deterministic" | "fallback"
          - latency_ms: float
          - error_type: str | None
          - validation_passed: bool | None
    """
    result = {
        "response": deterministic_fallback or "",
        "source": "deterministic",
        "latency_ms": 0.0,
        "error_type": None,
        "validation_passed": None,
        # Task 5D-7 Objective 7: every customer-facing response is Thai.
        "response_language": "th",
    }

    # Build context
    order_evidence = order_evidence or {}
    policy_chunks = policy_chunks or []

    # If clarification or human review, use built-in guidance
    if simulated_human_review:
        result["response"] = (
            "ขออภัย ผู้ช่วย AI ไม่สามารถดำเนินการเรื่องนี้ได้ "
            "และระบบยังไม่ได้บันทึกคำขอของคุณ "
            "กรุณาตรวจสอบข้อมูลเพิ่มเติมได้ที่เมนู My Orders หรือเว็บไซต์ของ SiamCart ค่ะ"
        )
        result["source"] = "deterministic"
        return result

    if requires_clarification and not deterministic_fallback:
        result["response"] = (
            "กรุณาระบุรายละเอียดเพิ่มเติม "
            "เช่น หมายเลขคำสั่งซื้อ หรือนโยบายที่ต้องการสอบถามค่ะ"
        )
        result["source"] = "deterministic"
        return result

    # Early exit if LLM disabled
    if not DEEPSEEK_ENABLED:
        result["error_type"] = "disabled"
        return result
    if not LLM_CONFIG.get("api_key"):
        result["error_type"] = "no_key"
        return result

    # Build the evidence context block
    context = _build_context_block(order_evidence, policy_chunks)

    # Build user message for LLM
    guidance = ""
    if requires_clarification and deterministic_fallback:
        guidance = (
            "\n\nThe customer needs to provide more information. "
            "Politely ask them for the necessary details."
        )

    prompt_input = (
        f"Customer message: {user_message}\n\n"
        f"Intent: {intent}\n\n"
        f"Evidence:\n{context}"
        f"{guidance}"
    )

    # Call LLM
    start = time.time()
    try:
        client = OpenAI(
            api_key=LLM_CONFIG["api_key"],
            base_url=LLM_CONFIG["base_url"],
            timeout=LLM_CONFIG["timeout"],
        )

        response = client.chat.completions.create(
            model=LLM_CONFIG["model"],
            temperature=LLM_CONFIG["temperature"],
            max_tokens=LLM_CONFIG["max_tokens"],
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": prompt_input},
            ],
            timeout=LLM_CONFIG["timeout"],
        )
        latency = round((time.time() - start) * 1000, 2)
        message = response.choices[0].message
        content = (message.content or "").strip()
        # Reasoning models (e.g. deepseek-flash) may leave `content` empty and
        # emit the answer in `reasoning_content`. Fall back to it rather than
        # declaring an empty output.
        if not content:
            content = (getattr(message, "reasoning_content", None) or "").strip()

        if not content:
            result["latency_ms"] = latency
            result["error_type"] = "empty_output"
            return result

        # Reject chain-of-thought leakage before it reaches the customer.
        if _looks_like_reasoning_leak(content) and deterministic_fallback:
            result["response"] = deterministic_fallback
            result["source"] = "deterministic"
            result["latency_ms"] = latency
            result["error_type"] = "reasoning_leak"
            result["validation_passed"] = False
            return result

        # Validate evidence preservation
        passed = _evidence_preserved(content, order_evidence)
        if not passed:
            result["response"] = deterministic_fallback or content
            result["source"] = "deterministic"
            result["latency_ms"] = latency
            result["error_type"] = "validation"
            result["validation_passed"] = False
            return result

        result["response"] = content
        result["source"] = "deepseek"
        result["latency_ms"] = latency
        result["error_type"] = None
        result["validation_passed"] = True
        return result

    except APITimeoutError:
        latency = round((time.time() - start) * 1000, 2)
        result["latency_ms"] = latency
        result["error_type"] = "timeout"
        return result
    except APIStatusError:
        latency = round((time.time() - start) * 1000, 2)
        result["latency_ms"] = latency
        result["error_type"] = "http_error"
        return result
    except APIConnectionError:
        latency = round((time.time() - start) * 1000, 2)
        result["latency_ms"] = latency
        result["error_type"] = "connection_error"
        return result
    except Exception:
        latency = round((time.time() - start) * 1000, 2)
        result["latency_ms"] = latency
        result["error_type"] = "unknown"
        return result
