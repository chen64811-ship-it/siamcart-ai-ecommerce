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
from app.agents.policy_index import (
    retrieve_policy_clauses,
    keyword_policy_retrieval,
    is_title_only_chunk,
    POLICY_TYPE_KEYWORDS,
    GENERAL_POLICY_KEYWORDS,
)
from app.config import DEEPSEEK_ENABLED, LLM_CONFIG


# ── Constants ─────────────────────────────────────────────────────────

CONFIDENCE_THRESHOLD = 0.6  # Cosine distance: lower = more similar

# Cancellation-related words. There is no dedicated cancellation policy
# file — the grounded cancellation content lives in the payment policy
# (automatic order cancellation on failed payment). A cancellation
# question must therefore NOT be collapsed into the general-policy
# overview; the ChromaDB retrieval (which returns the payment clauses)
# stays the primary source for it.
_CANCEL_POLICY_KEYWORDS = ["cancel", "cancellation", "ยกเลิก"]


def _detect_explicit_policy_type(query: str) -> Optional[str]:
    """Return the policy type explicitly named in the query, else None.

    Deterministic keyword scoring over the shared policy-type lexicon
    (English + Thai). A query naming a policy type must be answered with
    that type's clauses.
    """
    norm = (query or "").lower().strip()
    hits = {
        ptype: sum(1 for kw in kws if kw.lower() in norm)
        for ptype, kws in POLICY_TYPE_KEYWORDS.items()
    }
    if not any(hits.values()):
        return None
    return max(hits, key=hits.get)

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

    Deterministic data-source priority (DeepSeek stays disabled):
      1. ChromaDB retrieval — used only when its top clause is confident,
         substantive (non-title-only), AND consistent with the question:
         same explicit policy type when one is named, Thai text when
         available;
      2. keyword retrieval from the local policy markdown files — when
         ChromaDB is unavailable, the index is empty, the best match is
         low-confidence, every top chunk is title-only, or Chroma's top
         clause answers a DIFFERENT policy type / language than asked;
      3. honest Thai fallback (clarification / index-not-setup) — when
         neither source produced grounded clauses.
    Never invents policy terms: every clause comes verbatim from the local
    policy files or the ChromaDB index built from them.
    """
    # Step 1: Retrieve from ChromaDB (never raises — the fallback chain
    # absorbs index/model unavailability).
    retrieval_source = "chromadb"
    try:
        retrieval_result = retrieve_policy_clauses(query, top_k=top_k)
    except Exception:
        retrieval_result = {
            "retrieved_clauses": [],
            "retrieval_success": False,
            "total_chunks_in_index": 0,
        }

    # Drop document-header chunks ("# Return Policy — ...") — they carry no
    # policy body and must never be used as grounded evidence.
    raw_clauses = retrieval_result.get("retrieved_clauses", [])
    clauses = [
        c for c in raw_clauses
        if not is_title_only_chunk(c.get("text", ""))
    ]
    total_in_index = retrieval_result.get("total_chunks_in_index", 0)
    best_distance = clauses[0]["distance"] if clauses else 1.0

    # What is the question actually about? (deterministic keyword lexicon)
    explicit_type = _detect_explicit_policy_type(query)
    cancel_q = any(kw in query.lower() for kw in _CANCEL_POLICY_KEYWORDS)
    general_q = any(kw in query.lower() for kw in GENERAL_POLICY_KEYWORDS)

    use_chroma = (
        retrieval_result.get("retrieval_success", False)
        and len(clauses) > 0
        and best_distance <= CONFIDENCE_THRESHOLD
    )
    if use_chroma:
        top_type = clauses[0].get("policy_type", "")
        top_lang = str(clauses[0].get("language", "")).lower()
        if explicit_type and top_type != explicit_type:
            # Chroma's top clause answers a different policy than asked.
            use_chroma = False
        elif explicit_type and top_lang != "th":
            # Chroma's top clause is English-only; the keyword path
            # guarantees a Thai customer-facing answer.
            use_chroma = False
        elif not explicit_type and general_q and not cancel_q:
            # General policy question → grounded overview, not a single
            # random policy area.
            use_chroma = False

    keyword_hint = None
    is_general = False

    if not use_chroma:
        # Step 2: deterministic keyword retrieval from the local policy
        # markdown files (priority 2 of the data-source chain).
        keyword_result = keyword_policy_retrieval(query)
        if keyword_result.get("retrieval_success"):
            clauses = keyword_result.get("retrieved_clauses", [])
            best_distance = 0.0
            retrieval_success = True
            retrieval_source = "keyword"
            keyword_hint = keyword_result.get("policy_type_hint")
            is_general = bool(keyword_result.get("is_general_policy"))
        else:
            clauses = []
            retrieval_success = False
            retrieval_source = "keyword"
    else:
        retrieval_success = True

    requires_clarification = False
    simulated_human_review = False

    # Step 3: nothing grounded → honest fallback (priority 3).
    if not retrieval_success or not clauses:
        if total_in_index == 0:
            return _build_result(
                query=query,
                clauses=[],
                retrieval_success=False,
                deterministic_response=_INDEX_NOT_SETUP,
                requires_clarification=False,
                simulated_human_review=True,
                evidence={},
                retrieval_source=retrieval_source,
            )
        return _build_result(
            query=query,
            clauses=[],
            retrieval_success=False,
            deterministic_response=_CLARIFICATION_REQUEST,
            requires_clarification=True,
            simulated_human_review=False,
            evidence={},
            retrieval_source=retrieval_source,
        )

    # Step 4: Extract policy type. For a keyword overview, every policy
    # area is present → the overview builder handles the response.
    if is_general:
        policy_type = "overview"
    elif keyword_hint:
        policy_type = keyword_hint
    else:
        policy_type = _detect_dominant_policy_type(clauses)

    # Step 5: Build evidence object
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
        "retrieval_source": retrieval_source,
    }

    # Step 6: Build deterministic fallback response
    deterministic_response = _build_deterministic_response(policy_type, clauses)

    # Step 7: Assemble result (NO DeepSeek call — generation is now in llm_generator.py)
    result = _build_result(
        query=query,
        clauses=clauses,
        retrieval_success=True,
        deterministic_response=deterministic_response,
        requires_clarification=False,
        simulated_human_review=False,
        evidence=evidence,
        policy_sources=policy_sources,
        retrieval_source=retrieval_source,
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


# Thai customer-facing labels for the general-policy overview.
_POLICY_TYPE_LABELS_THAI = {
    "return": "นโยบายการคืนสินค้า",
    "refund": "นโยบายการคืนเงิน",
    "exchange": "นโยบายการเปลี่ยนสินค้า",
    "shipping": "นโยบายการจัดส่ง",
    "payment": "นโยบายการชำระเงิน",
}


def _build_overview_response(clauses: List[Dict]) -> str:
    """Grounded Thai overview of every SiamCart policy area.

    Built exclusively from the local policy markdown files (one Thai
    section per policy type). Never invents policy terms.
    """
    parts = []
    for c in clauses:
        ptype = c.get("policy_type", "")
        label = _POLICY_TYPE_LABELS_THAI.get(ptype, ptype)
        text = " ".join((c.get("text") or "").split())[:110]
        if text:
            parts.append(f"- {label}: {text}")
    if not parts:
        return ""
    return "ตามนโยบายของ SiamCart ค่ะ มีนโยบายหลักดังนี้:\n" + "\n".join(parts)


def _build_deterministic_response(policy_type: str, clauses: List[Dict]) -> str:
    """Build a readable Thai deterministic fallback from retrieved clauses.

    Task 5D-7 Objective 5: the customer-facing fallback must be Thai even when
    retrieval returned an English chunk, so a Thai-language clause is preferred
    when one is available (chunks carry a "language" field).

    Intent-boundary hotfix: document-header chunks ("# Return Policy — ...")
    are never used as the summary, and a general-policy question ("What
    policies does SiamCart follow?") produces a grounded overview instead of a
    single mislabelled clause.
    """
    if policy_type == "overview":
        return _build_overview_response(clauses)

    substantive = [
        c for c in clauses
        if c.get("text") and not is_title_only_chunk(c.get("text", ""))
    ] or clauses
    thai_clause = next(
        (
            c for c in substantive
            if str(c.get("language", "")).lower() == "th" and c.get("text")
        ),
        None,
    )
    summary_clause = thai_clause or (substantive[0] if substantive else None)
    if not summary_clause:
        return ""
    template = _RESPONSE_TEMPLATES.get(policy_type)
    if template:
        summary = summary_clause["text"][:200]
        return template.format(summary=summary)

    sources = ", ".join(set(c.get("source_filename", "") for c in substantive[:2]))
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
    retrieval_source: str = "chromadb",
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
        "retrieval_source": retrieval_source,
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
