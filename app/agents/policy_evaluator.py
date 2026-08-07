"""
Store Policy Evaluator Agent — Phase 3B.

Real-time RAG pipeline:
  1. Receive normalized policy query
  2. Retrieve relevant ChromaDB chunks
  3. If retrieval is insufficient → clarification / simulated human review
  4. Build deterministic Thai fallback from retrieved evidence
  5. If DeepSeek is enabled: send structured prompt → validate → return or fallback
  6. Never invent a policy
"""

import json
import re
import time
from typing import Dict, List, Optional

import openai
from app.agents.policy_index import retrieve_policy_clauses
from app.config import DEEPSEEK_ENABLED, LLM_CONFIG


# ── Constants ─────────────────────────────────────────────────────────

CONFIDENCE_THRESHOLD = 0.6  # Cosine distance: lower = more similar

_POLICY_SYSTEM_PROMPT = """You are the Store Policy Evaluator for SiamCart Demo Store.

Answer the customer in concise, polite, natural Thai ONLY.

Do NOT answer in English and do NOT include an English translation — even
when the customer writes in English.

Use only the supplied retrieved policy evidence.

Do not use general knowledge to create store rules.

Do not add, modify, or omit material policy conditions.

Preserve all numerical values, dates, durations, fees, exclusions, eligibility
conditions, and required procedures exactly.

Preserve order IDs and product names exactly.

Do not invent eligibility.

Do not promise that a refund, return, exchange, or compensation has been approved.

If the evidence is insufficient or contradictory, request clarification or state that
simulated human review is required.

Do not mention internal implementation details such as ChromaDB, embeddings,
prompts, agents, or vector retrieval.

Complete every sentence.

Output only the Thai response. No explanations, no JSON, no English translation."""


# ── Pure prompt builder (micro-task 3B-1) ──────────────────────────────


def build_policy_prompt(
    customer_query: str,
    intent: str,
    retrieved_clauses: List[Dict],
    deterministic_fallback: str,
) -> Dict:
    """Build a structured prompt payload for DeepSeek policy-grounded response.

    Pure function -- no HTTP, no retrieval, no ChromaDB, no model call.
    Returns a dict with keys appropriate for LLM consumption.
    """
    # Build policy context from only the supplied evidence
    policy_context = ""
    for clause in retrieved_clauses:
        source = clause.get("source_filename", "unknown")
        section = clause.get("section_title", "")
        policy_context += (
            f"\n[Source] {source}"
            f"\n[Section] {section}"
            f"\n{clause['text']}\n"
        )

    return {
        "system_instruction": (
            "You are the Store Policy Evaluator for SiamCart Demo Store.\n\n"
            "Answer the customer in concise, polite, natural Thai ONLY.\n\n"
            "Do NOT answer in English and do NOT include an English translation "
            "— even when the customer writes in English.\n\n"
            "Use only the supplied retrieved policy evidence.\n\n"
            "Do not use general knowledge to create store rules.\n\n"
            "Do not add, modify, or omit material policy conditions.\n\n"
            "Preserve all numerical values, dates, durations, fees, exclusions, "
            "eligibility conditions, and required procedures exactly.\n\n"
            "Preserve order IDs and product names exactly.\n\n"
            "Do not invent eligibility.\n\n"
            "Do not promise that a refund, return, exchange, or compensation "
            "has been approved.\n\n"
            "If the evidence is insufficient or contradictory, request "
            "clarification or state that simulated human review is required.\n\n"
            "Do not mention internal implementation details such as ChromaDB, "
            "embeddings, prompts, agents, or vector retrieval.\n\n"
            "Complete every sentence.\n\n"
            "Output only the Thai response. No explanations, no JSON, "
            "no English translation."
        ),
        "customer_query": customer_query,
        "intent": intent,
        "retrieved_clauses": retrieved_clauses,
        "thai_instruction": (
            "ตอบเป็นภาษาไทยที่สุภาพ กระชับ และเป็นธรรมชาติ"
        ),
        "deterministic_fallback": deterministic_fallback,
    }


# Thai response templates for deterministic fallback
_RESPONSE_TEMPLATES = {
    "return": "ตามนโยบายการคืนสินค้าของ SiamCart: {summary}",
    "refund": "ตามนโยบายการคืนเงินของ SiamCart: {summary}",
    "exchange": "ตามนโยบายการเปลี่ยนสินค้าของ SiamCart: {summary}",
    "shipping": "ตามนโยบายการจัดส่งของ SiamCart: {summary}",
    "payment": "ตามนโยบายการชำระเงินของ SiamCart: {summary}",
}

_CLARIFICATION_REQUEST = (
    "กรุณาระบุรายละเอียดเพิ่มเติมเกี่ยวกับนโยบายที่คุณต้องการสอบถามค่ะ "
    "เช่น การคืนสินค้า การคืนเงิน การเปลี่ยนสินค้า การจัดส่ง หรือการชำระเงิน"
)

_INDEX_NOT_SETUP = "ระบบนโยบายยังไม่ได้ถูกตั้งค่า กรุณาติดต่อเจ้าหน้าที่ค่ะ"


# ── Public API ────────────────────────────────────────────────────────


def evaluate_policy_query(
    query: str,
    top_k: int = 3,
) -> Dict:
    """Evaluate a store policy query with optional DeepSeek RAG generation.

    Returns structured result with all fields needed by the orchestrator.
    """
    # Step 1: Retrieve from ChromaDB
    retrieval_result = retrieve_policy_clauses(query, top_k=top_k)
    clauses = retrieval_result.get("retrieved_clauses", [])
    retrieval_success = retrieval_result.get("retrieval_success", False)
    total_in_index = retrieval_result.get("total_chunks_in_index", 0)

    requires_clarification = False
    simulated_human_review = False

    # Step 2: Empty index
    if total_in_index == 0:
        return _build_result(
            query=query,
            clauses=[],
            retrieval_success=False,
            deterministic_response=_INDEX_NOT_SETUP,
            requires_clarification=False,
            simulated_human_review=True,
            evidence={},
        )

    # Step 3: No relevant results
    if not retrieval_success or not clauses:
        return _build_result(
            query=query,
            clauses=[],
            retrieval_success=False,
            deterministic_response=_CLARIFICATION_REQUEST,
            requires_clarification=True,
            simulated_human_review=False,
            evidence={},
        )

    # Step 4: Low confidence
    best_distance = clauses[0]["distance"]
    if best_distance > CONFIDENCE_THRESHOLD:
        return _build_result(
            query=query,
            clauses=clauses,
            retrieval_success=True,
            deterministic_response=_CLARIFICATION_REQUEST,
            requires_clarification=True,
            simulated_human_review=False,
            evidence={"top_clause_distance": best_distance},
        )

    # Step 5: Extract policy type
    policy_type = _detect_dominant_policy_type(clauses)

    # Step 6: Build evidence object
    policy_sources = sorted(set(c["source_filename"] for c in clauses))
    evidence = {
        "policy_type": policy_type,
        "primary_source": clauses[0].get("source_filename", ""),
        "retrieved_chunks": len(clauses),
        "best_distance": best_distance,
        "top_clause_section": clauses[0].get("section_title", ""),
        "policy_sources": policy_sources,
        "retrieved_chunk_count": len(clauses),
        "top_similarity_score": round(1.0 - best_distance, 4),
    }

    # Step 7: Build deterministic fallback response
    deterministic_response = _build_deterministic_response(policy_type, clauses)

    # Step 8: Assemble result (NO DeepSeek call — generation is now in llm_generator.py)
    result = _build_result(
        query=query,
        clauses=clauses,
        retrieval_success=True,
        deterministic_response=deterministic_response,
        requires_clarification=False,
        simulated_human_review=False,
        evidence=evidence,
        policy_sources=policy_sources,
    )

    # Set response to deterministic fallback; orchestrator will call llm_generator
    result["response"] = deterministic_response
    result["response_source"] = "deterministic"
    result["llm_enabled"] = DEEPSEEK_ENABLED
    result["llm_fallback_used"] = False
    result["llm_latency_ms"] = 0.0
    result["llm_error_type"] = None
    result["grounding_validation_passed"] = None
    result["retrieved_clauses"] = clauses  # pass chunks for orchestrator

    return result


# ── Internal: Deterministic response builder ──────────────────────────


def _build_deterministic_response(policy_type: str, clauses: List[Dict]) -> str:
    """Build a readable Thai deterministic fallback from retrieved clauses.

    Task 5D-7 Objective 5: the customer-facing fallback must be Thai even when
    retrieval returned an English chunk, so a Thai-language clause is preferred
    when one is available (chunks carry a "language" field).
    """
    thai_clause = next(
        (c for c in clauses if str(c.get("language", "")).lower() == "th" and c.get("text")),
        None,
    )
    summary_clause = thai_clause or (clauses[0] if clauses else None)
    if not summary_clause:
        return ""
    template = _RESPONSE_TEMPLATES.get(policy_type)
    if template:
        summary = summary_clause["text"][:200]
        return template.format(summary=summary)

    sources = ", ".join(set(c.get("source_filename", "") for c in clauses[:2]))
    return f"ตามนโยบายของ SiamCart ที่พบใน {sources}: {summary_clause['text'][:200]}"


def _detect_dominant_policy_type(clauses: List[Dict]) -> str:
    """Detect the most common policy type among retrieved clauses."""
    type_counts = {}
    for c in clauses:
        pt = c.get("policy_type", "unknown")
        type_counts[pt] = type_counts.get(pt, 0) + 1
    return max(type_counts, key=type_counts.get) if type_counts else "unknown"


def _build_result(
    query: str,
    clauses: List[Dict],
    retrieval_success: bool,
    deterministic_response: str,
    requires_clarification: bool,
    simulated_human_review: bool,
    evidence: Dict,
    policy_sources: Optional[List[str]] = None,
) -> Dict:
    """Build the common result dict structure."""
    return {
        "intent": "STORE_POLICY",
        "agent": "store_policy_evaluator",
        "query": query,
        "retrieved_clauses": [
            {
                "text": c["text"],
                "policy_type": c["policy_type"],
                "source_filename": c["source_filename"],
                "chunk_id": c["chunk_id"],
                "section_title": c["section_title"],
                "similarity_score": round(1.0 - c["distance"], 4),
            }
            for c in clauses
        ],
        "retrieval_success": retrieval_success,
        "requires_clarification": requires_clarification,
        "simulated_human_review": simulated_human_review,
        "deterministic_response": deterministic_response,
        "evidence": evidence,
        "policy_sources": policy_sources or [],
        "retrieved_chunk_count": len(clauses),
        "top_similarity_score": round(
            1.0 - (clauses[0]["distance"] if clauses else 1.0), 4
        ),
    }


# ── Internal: DeepSeek integration (Phase 3B) ─────────────────────────


def _try_deepseek_generation(
    query: str,
    clauses: List[Dict],
    deterministic_response: str,
) -> Dict:
    """Attempt to generate a Thai policy response via DeepSeek if enabled.

    Returns a dict:
      {
        "used_llm": bool,        # whether an attempt was made
        "llm_response": str|None,# the LLM output if successful
        "validation_passed": bool|None,
        "latency_ms": float,
        "error_type": str|None,
      }
    """
    if not DEEPSEEK_ENABLED:
        return {"used_llm": False, "llm_response": None,
                "validation_passed": None, "latency_ms": 0.0, "error_type": "disabled"}

    if not LLM_CONFIG.get("api_key"):
        return {"used_llm": False, "llm_response": None,
                "validation_passed": None, "latency_ms": 0.0, "error_type": "no_key"}

    # Build prompt via the pure prompt builder
    prompt_payload = build_policy_prompt(
        customer_query=query,
        intent="STORE_POLICY",
        retrieved_clauses=clauses,
        deterministic_fallback=deterministic_response,
    )

    policy_context = ""
    for c in clauses:
        source = c.get("source_filename", "unknown")
        policy_context += f"\n[Policy] Source: {source}\n{c['text']}\n"

    prompt_input = (
        f"Customer question: {query}\n\n"
        f"Retrieved policy evidence:{policy_context}\n\n"
        f"Deterministic fallback (use only as reference):\n{deterministic_response}\n\n"
        "Please respond in Thai."
    )

    start = time.time()
    try:
        client = openai.OpenAI(
            api_key=LLM_CONFIG["api_key"],
            base_url=LLM_CONFIG["base_url"],
            timeout=LLM_CONFIG["timeout"],
        )

        response = client.chat.completions.create(
            model=LLM_CONFIG["model"],
            temperature=LLM_CONFIG["temperature"],
            max_tokens=LLM_CONFIG["max_tokens"],
            messages=[
                {"role": "system", "content": _POLICY_SYSTEM_PROMPT},
                {"role": "user", "content": prompt_input},
            ],
            timeout=LLM_CONFIG["timeout"],
        )
        llm_latency = round((time.time() - start) * 1000, 2)
        content = (response.choices[0].message.content or "").strip()

        if not content:
            return {"used_llm": True, "llm_response": None,
                    "validation_passed": False, "latency_ms": llm_latency,
                    "error_type": "empty_output"}

        # Run policy validation
        validation_passed = _validate_policy_response(content, clauses)

        return {
            "used_llm": True,
            "llm_response": content if validation_passed else None,
            "validation_passed": validation_passed,
            "latency_ms": llm_latency,
            "error_type": None if validation_passed else "policy_validation",
        }

    except openai.APITimeoutError:
        lat = round((time.time() - start) * 1000, 2)
        return {"used_llm": True, "llm_response": None,
                "validation_passed": False, "latency_ms": lat, "error_type": "timeout"}
    except openai.APIStatusError:
        lat = round((time.time() - start) * 1000, 2)
        return {"used_llm": True, "llm_response": None,
                "validation_passed": False, "latency_ms": lat, "error_type": "http_error"}
    except openai.APIConnectionError:
        lat = round((time.time() - start) * 1000, 2)
        return {"used_llm": True, "llm_response": None,
                "validation_passed": False, "latency_ms": lat,
                "error_type": "connection_error"}
    except Exception:
        lat = round((time.time() - start) * 1000, 2)
        return {"used_llm": True, "llm_response": None,
                "validation_passed": False, "latency_ms": lat, "error_type": "unknown"}


# ── Internal: Policy fact validation ──────────────────────────────────


def validate_policy_response(
    generated_response: str,
    retrieved_clauses: List[Dict],
) -> tuple:
    """Validate a generated policy response against retrieved policy evidence.

    Pure, deterministic function — no HTTP, model, ChromaDB, or file access.

    Returns (valid: bool, reason: str | None).

    Rules:
    1. Reject empty or whitespace-only response.
    2. Extract numeric anchors from evidence; reject invented values.
    3. Reject changed policy periods, fees, amounts, or percentages.
    4. Reject unsupported completion/approval claims.
    5. Accept concise paraphrases when all factual anchors are consistent.
    6. Do not require every evidence sentence to appear in the response.
    7. Do not mutate input arguments.
    """
    _ERROR_PREFIX = "policy_validation"

    # Rule 1: Reject empty / whitespace-only response
    if not generated_response or not generated_response.strip():
        return (False, f"{_ERROR_PREFIX}: empty_response")

    evidence_text = " ".join(c["text"] for c in retrieved_clauses)

    # ── Helper: normalise Thai and Arabic digits ──────────────────
    _THAI_TO_ARABIC = str.maketrans(
        "๐๑๒๓๔๕๖๗๘๙", "0123456789"
    )

    def _normalise_nums(text: str) -> str:
        return text.translate(_THAI_TO_ARABIC)

    normalised_response = _normalise_nums(generated_response)
    normalised_evidence = _normalise_nums(evidence_text)

    # ── Internal helpers (avoid recompiling patterns per call) ─────
    _NUM_RE = re.compile(r"\b\d+(?:\.\d+)?\b")
    _DAY_PERIOD_RE = re.compile(r"\b(\d+)\s*d[aeiyu]",
                                re.IGNORECASE)
    _HOUR_PERIOD_RE = re.compile(r"\b(\d+)\s*(?:hour|ชั่วโมง|hr)s?\b",
                                 re.IGNORECASE)
    _CURRENCY_RE = re.compile(
        r"(?:฿|THB|USD|บาท)\s*(\d+(?:,\d{3})*(?:\.\d+)?)"
        r"|(\d+(?:,\d{3})*(?:\.\d+)?)\s*(?:฿|THB|USD|บาท)",
        re.IGNORECASE,
    )
    _PERCENT_RE = re.compile(r"(\d+(?:\.\d+)?)\s*%")
    _APPROVAL_PHRASES = [
        "refund approved", "return approved", "exchange approved",
        "refund completed", "return completed", "payment completed by the store",
        "refund has been processed", "your refund is approved",
        "your return is approved",
        "ได้รับการอนุมัติ", "ได้รับการคืนเงิน",
        "คืนเงินเรียบร้อย", "อนุมัติการคืน",
        "การคืนเงินของคุณได้รับการอนุมัติ",
        "การคืนสินค้าได้รับการอนุมัติ",
        "การเปลี่ยนสินค้าได้รับการอนุมัติ",
        "คืนเงินเสร็จสมบูรณ์", "ชำระเงินเสร็จสมบูรณ์",
    ]

    # ── Rule 2: Extract numeric anchors from evidence ──────────────
    ev_numbers = set(_NUM_RE.findall(normalised_evidence))
    resp_numbers = set(_NUM_RE.findall(normalised_response))

    # Common non-policy numbers that are always allowed (counters, date parts)
    _POLICY_NEUTRAL = {"1", "2", "3", "4", "5", "6", "7", "8", "9", "10"}

    invented_numbers = resp_numbers - ev_numbers - _POLICY_NEUTRAL
    if invented_numbers:
        return (False, f"{_ERROR_PREFIX}: invented_numbers_{{{', '.join(sorted(invented_numbers, key=int))}}}")

    # ── Rule 3: Reject changed policy periods ──────────────────────
    ev_days = set(_DAY_PERIOD_RE.findall(normalised_evidence))
    resp_days = set(_DAY_PERIOD_RE.findall(normalised_response))
    if resp_days and ev_days and resp_days != ev_days:
        return (False, f"{_ERROR_PREFIX}: changed_day_period_ev={{{', '.join(ev_days)}}}_resp={{{', '.join(resp_days)}}}")

    ev_hours = set(_HOUR_PERIOD_RE.findall(normalised_evidence))
    resp_hours = set(_HOUR_PERIOD_RE.findall(normalised_response))
    if resp_hours and ev_hours and resp_hours != ev_hours:
        return (False, f"{_ERROR_PREFIX}: changed_hour_period_ev={{{', '.join(ev_hours)}}}_resp={{{', '.join(resp_hours)}}}")

    # ── Rule 3 (continued): Reject changed currency amounts ─────────
    def _extract_currency_nums(text: str) -> set:
        matches = _CURRENCY_RE.findall(text)
        result = set()
        for m in matches:
            if m[0]:
                result.add(m[0].replace(",", ""))
            if m[1]:
                result.add(m[1].replace(",", ""))
        return result

    ev_currency = _extract_currency_nums(normalised_evidence)
    resp_currency = _extract_currency_nums(normalised_response)
    for rc in resp_currency:
        if rc not in ev_currency:
            return (False, f"{_ERROR_PREFIX}: invented_currency_{rc}")

    # ── Rule 3 (continued): Reject changed percentages ──────────────
    ev_pct = set(_PERCENT_RE.findall(normalised_evidence))
    resp_pct = set(_PERCENT_RE.findall(normalised_response))
    for rp in resp_pct:
        if rp not in ev_pct:
            return (False, f"{_ERROR_PREFIX}: invented_percentage_{rp}")

    # ── Rule 4: Reject unsupported completion/approval claims ──────
    lower_resp = generated_response.lower()
    for phrase in _APPROVAL_PHRASES:
        if phrase.lower() in lower_resp:
            return (False, f"{_ERROR_PREFIX}: unsupported_approval_claim")

    # ── All rules passed ───────────────────────────────────────────
    return (True, None)


# Keep the legacy private signature unchanged for backward compat.
def _validate_policy_response(llm_text: str, clauses: List[Dict]) -> bool:
    """Legacy wrapper — returns bool only. Delegates to validate_policy_response."""
    valid, _ = validate_policy_response(llm_text, clauses)
    return valid
