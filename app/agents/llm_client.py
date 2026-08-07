"""
LLM Formatter Client — Phase 1B.

Single-purpose client for optional DeepSeek-Chat response formatting.
Never used for data retrieval. Always has a deterministic fallback.

Error categories:
- disabled:      LLM not enabled in configuration
- no_key:        DEEPSEEK_API_KEY not set
- timeout:       provider did not respond within timeout
- http_error:    provider returned non-200 HTTP status
- empty_output:  provider returned empty or whitespace-only response
- validation:    provider response changed a critical factual value
"""

import json
import time
from typing import Dict, Optional, Any

from openai import OpenAI, APITimeoutError, APIStatusError, APIConnectionError

from app.config import DEEPSEEK_ENABLED, LLM_CONFIG

# Formatter system prompt — pure cosmetic polish, never data retrieval
FORMATTER_SYSTEM_PROMPT = """You are a Thai e-commerce customer-support response formatter.

Rewrite the supplied deterministic response in concise, polite, natural Thai.

Rules:
1. Preserve every factual value exactly.
2. Do not add or modify order IDs, order statuses, payment statuses,
   shipment statuses, tracking numbers, dates, or delivery estimates.
3. If the original response requests clarification, preserve the
   clarification request.
4. Use polite Thai customer-service language (use ค่ะ).
5. Keep responses concise — 2-4 sentences.
6. Output only the rewritten Thai text. No explanations, no JSON."""

# Fields that must be validated after formatting
CRITICAL_EVIDENCE_FIELDS = {
    "order_id",
    "order_status",
    "payment_status",
    "shipment_status",
    "tracking_number",
    "estimated_delivery_date",
}


def _evidence_matches(original: Dict[str, Any], text: str) -> bool:
    """Check that all critical evidence values are preserved in the text.

    Returns True if every critical field from original evidence is present
    somewhere in the LLM response text. For empty strings, we accept either
    the empty value or its absence (the LLM may rephrase around it).
    """
    for field in CRITICAL_EVIDENCE_FIELDS:
        value = original.get(field)
        if value and value.strip():
            if value not in text:
                return False
    return True


def format_response(
    deterministic_response: str,
    evidence: Dict[str, Any],
) -> Dict[str, Any]:
    """Optionally format a deterministic response through DeepSeek-Chat.

    Returns a dict with keys:
      - response: str (final response, LLM-formatted or deterministic)
      - response_source: "deterministic" | "deepseek"
      - llm_enabled: bool
      - llm_fallback_used: bool
      - llm_latency_ms: float
      - llm_error_type: str | None
    """
    result = {
        "response": deterministic_response,
        "response_source": "deterministic",
        "llm_enabled": DEEPSEEK_ENABLED,
        "llm_fallback_used": False,
        "llm_latency_ms": 0.0,
        "llm_error_type": None,
    }

    # Early exit: disabled or no key
    if not DEEPSEEK_ENABLED:
        result["llm_error_type"] = "disabled"
        return result

    if not LLM_CONFIG.get("api_key"):
        result["llm_error_type"] = "no_key"
        return result

    # Build a compact input for the LLM — no SQL, no secrets, no logs
    formatter_input = json.dumps(
        {
            "deterministic_response": deterministic_response,
            "verified_evidence": evidence,
        },
        ensure_ascii=False,
    )

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
                {"role": "system", "content": FORMATTER_SYSTEM_PROMPT},
                {"role": "user", "content": formatter_input},
            ],
            timeout=LLM_CONFIG["timeout"],
        )
        llm_latency = round((time.time() - start) * 1000, 2)
        content = (response.choices[0].message.content or "").strip()

        # Empty output check
        if not content:
            result["llm_latency_ms"] = llm_latency
            result["llm_error_type"] = "empty_output"
            result["llm_fallback_used"] = True
            return result

        # Factual validation — reject if any critical value was changed
        if not _evidence_matches(evidence, content):
            result["llm_latency_ms"] = llm_latency
            result["llm_error_type"] = "validation"
            result["llm_fallback_used"] = True
            return result

        # Success — use LLM output
        result["response"] = content
        result["response_source"] = "deepseek"
        result["llm_latency_ms"] = llm_latency
        return result

    except APITimeoutError:
        llm_latency = round((time.time() - start) * 1000, 2)
        result["llm_latency_ms"] = llm_latency
        result["llm_error_type"] = "timeout"
        result["llm_fallback_used"] = True
        return result

    except APIStatusError as e:
        llm_latency = round((time.time() - start) * 1000, 2)
        result["llm_latency_ms"] = llm_latency
        result["llm_error_type"] = "http_error"
        result["llm_fallback_used"] = True
        return result

    except APIConnectionError:
        llm_latency = round((time.time() - start) * 1000, 2)
        result["llm_latency_ms"] = llm_latency
        result["llm_error_type"] = "connection_error"
        result["llm_fallback_used"] = True
        return result

    except Exception:
        llm_latency = round((time.time() - start) * 1000, 2)
        result["llm_latency_ms"] = llm_latency
        result["llm_error_type"] = "unknown"
        result["llm_fallback_used"] = True
        return result
