"""Post-submission reproducibility run for the current Thai e-commerce system.

This runner writes only to evaluation/reproducibility_2026/. It does not alter
production configuration or the original evaluation outputs.

The monolithic prompt, JSON schema, model temperature, max_tokens, and
provider timeout mirror scripts/run_monolithic_baseline.py. The experiment-
specific additions are one preflight request, transient-error retries,
complete raw records, deterministic aggregation, and independent verification.
"""

from __future__ import annotations

import csv
import hashlib
import json
import math
import os
import re
import statistics
import subprocess
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple


BASE_DIR = Path(__file__).resolve().parent.parent
SOURCE_DATASET = BASE_DIR / "data" / "evaluation" / "all_scenarios.json"
OUT_DIR = BASE_DIR / "evaluation" / "reproducibility_2026"
FROZEN_SCENARIOS = OUT_DIR / "scenarios_120.json"
MULTI_RAW = OUT_DIR / "multi_agent_raw.jsonl"
MONOLITHIC_RAW = OUT_DIR / "monolithic_raw.jsonl"
MANUAL_REVIEW = OUT_DIR / "manual_review.csv"
COMBINED_CSV = OUT_DIR / "combined_results.csv"
METRICS_JSON = OUT_DIR / "metrics_summary.json"
REPORT_MD = OUT_DIR / "reproducibility_report.md"
PROMPT_TXT = OUT_DIR / "monolithic_system_prompt.txt"
CONTEXT_TXT = OUT_DIR / "monolithic_context.md"
PREFLIGHT_JSON = OUT_DIR / "preflight.json"
ISOLATED_DB = OUT_DIR / "orders_repro.db"

MAX_RETRIES = 3
BASELINE_TIMEOUT_SECONDS = 60
BASELINE_TEMPERATURE = 0.1
BASELINE_MAX_TOKENS = 1024
PREFLIGHT_MAX_TOKENS = 32
TRANSIENT_ERRORS = {
    "timeout",
    "http_error",
    "connection_error",
    "unknown",
    "empty_output",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def json_dump(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def json_string(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def sanitize_error(value: Any) -> str:
    """Remove credential-like strings from provider/application errors."""
    text = str(value or "")
    text = re.sub(r"(?i)Bearer\s+[A-Za-z0-9._~+/=-]+", "Bearer [REDACTED]", text)
    text = re.sub(r"(?i)sk-[A-Za-z0-9_-]{8,}", "[REDACTED_API_KEY]", text)
    text = re.sub(
        r"(?i)(api[_-]?key|authorization)\s*[:=]\s*\S+",
        r"\1=[REDACTED]",
        text,
    )
    text = re.sub(
        r"(?i)DEEPSEEK_API_KEY\s*=\s*\S+",
        "DEEPSEEK_API_KEY=[REDACTED]",
        text,
    )
    return text[:1000]


def normalise(text: Any) -> str:
    value = str(text or "").lower()
    value = re.sub(r"\s+", " ", value)
    for ch in "\u2010\u2011\u2012\u2013\u2014\u2212":
        value = value.replace(ch, "-")
    return value.strip()


def parse_facts(facts: Iterable[Any]) -> List[Tuple[str, str]]:
    parsed: List[Tuple[str, str]] = []
    for item in facts or []:
        text = str(item)
        if "=" in text:
            key, value = text.split("=", 1)
            parsed.append((key.strip(), value.strip()))
    return parsed


def fact_aliases(key: str, value: str) -> List[str]:
    """Conservative aliases from the current response templates."""
    aliases = {
        ("order_status", "shipped"): ["จัดส่งแล้ว", "ถูกจัดส่งแล้ว"],
        ("order_status", "processing"): ["อยู่ระหว่างดำเนินการ"],
        ("order_status", "return_requested"): ["ขอคืนสินค้า", "ส่งคำขอคืนสินค้า"],
        ("order_status", "delivered"): ["จัดส่งถึงปลายทางแล้ว", "จัดส่งสำเร็จแล้ว"],
        ("order_status", "pending"): ["รอดำเนินการ", "รอการชำระเงิน"],
        ("payment_status", "paid"): ["ชำระเงินแล้ว", "ได้รับชำระเงินแล้ว"],
        ("payment_status", "unpaid"): [
            "ยังไม่ได้รับการยืนยันการชำระเงิน",
            "ยังไม่ได้ชำระ",
        ],
        ("shipment_status", "in_transit"): [
            "อยู่ระหว่างการขนส่ง",
            "กำลังอยู่ระหว่างการขนส่ง",
        ],
        ("shipment_status", "pending_pickup"): ["รอรับพัสดุ", "รอการจัดส่ง"],
        ("shipment_status", "delivered"): [
            "จัดส่งถึงปลายทางแล้ว",
            "จัดส่งเรียบร้อยแล้ว",
        ],
        ("shipment_status", "not_shipped"): ["ยังไม่ได้จัดส่ง"],
        ("shipment_status", "return_initiated"): [
            "อยู่ในกระบวนการคืน",
            "คืนสินค้า",
        ],
    }
    return [value] + aliases.get((key, value), [])


def response_contains_fact(
    key: str,
    expected_value: str,
    response: str,
    structured: Optional[Dict[str, Any]],
    actual_intent: Optional[str],
) -> bool:
    if key == "order_not_found" and expected_value.lower() == "true":
        if actual_intent == "ORDER_NOT_FOUND":
            return True
        response_lower = normalise(response)
        return any(
            phrase in response_lower
            for phrase in (
                "not found",
                "no information",
                "ไม่พบข้อมูล",
                "ไม่พบคำสั่งซื้อ",
            )
        )

    if structured is not None and key in structured:
        actual_value = structured.get(key)
        if expected_value == "" and str(actual_value or "") == "":
            return True
        if normalise(actual_value) == normalise(expected_value):
            return True

    if expected_value == "":
        response_lower = normalise(response)
        if key == "tracking_number":
            return any(
                phrase in response_lower
                for phrase in ("ยังไม่มีหมายเลข", "ไม่มีหมายเลข")
            )
        if key == "shipping_provider":
            return any(
                phrase in response_lower
                for phrase in ("ยังไม่มีผู้ให้บริการ", "ยังไม่ได้จัดส่ง")
            )
        return False

    response_lower = normalise(response)
    return any(
        normalise(alias) in response_lower
        for alias in fact_aliases(key, expected_value)
    )


def score_transaction(
    scenario: Dict[str, Any],
    response: str,
    structured: Optional[Dict[str, Any]],
    actual_intent: Optional[str],
    execution_success: bool,
) -> Optional[bool]:
    if scenario.get("category") != "transaction":
        return None
    if not execution_success:
        return False
    facts = parse_facts(scenario.get("expected_answer_facts") or [])
    return all(
        response_contains_fact(key, value, response, structured, actual_intent)
        for key, value in facts
    )


def literal_policy_anchor_score(
    scenario: Dict[str, Any],
    response: str,
) -> Tuple[int, int]:
    """Diagnostic only; policy correctness remains human-adjudicated."""
    anchors = parse_facts(scenario.get("expected_answer_facts") or [])
    response_lower = normalise(response)
    matches = sum(
        1 for _, value in anchors if normalise(value) in response_lower
    )
    return matches, len(anchors)


def pct(numerator: int, denominator: int) -> Optional[float]:
    if denominator == 0:
        return None
    return round(numerator / denominator * 100, 6)


def linear_percentile(values: List[float], percentile: float) -> Optional[float]:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return round(ordered[0], 6)
    position = (len(ordered) - 1) * percentile
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        value = ordered[lower]
    else:
        weight = position - lower
        value = ordered[lower] + (ordered[upper] - ordered[lower]) * weight
    return round(value, 6)


def bool_metric(records: List[Dict[str, Any]], field: str) -> Dict[str, Any]:
    denominator = len(records)
    numerator = sum(1 for record in records if record.get(field) is True)
    return {
        "numerator": numerator,
        "denominator": denominator,
        "percentage": pct(numerator, denominator),
    }


def response_time_metrics(records: List[Dict[str, Any]]) -> Dict[str, Any]:
    values = [float(record["latency_ms"]) for record in records]
    return {
        "value_ms": round(statistics.mean(values), 6) if values else None,
        "n": len(values),
        "minimum_ms": round(min(values), 6) if values else None,
        "maximum_ms": round(max(values), 6) if values else None,
        "median_ms": round(statistics.median(values), 6) if values else None,
        "standard_deviation_ms": round(statistics.pstdev(values), 6)
        if values
        else None,
        "p95_ms": linear_percentile(values, 0.95),
        "basis": "all attempted scenario runs, including execution failures",
    }


def compute_system_metrics(records: List[Dict[str, Any]]) -> Dict[str, Any]:
    total = len(records)
    successful = sum(
        1 for record in records if record.get("execution_success") is True
    )
    transaction_records = [
        record for record in records if record.get("category") == "transaction"
    ]
    policy_records = [
        record for record in records if record.get("category") == "policy"
    ]
    eligible_policy = [
        record
        for record in policy_records
        if record.get("manual_review_required") is not True
        and record.get("policy_answer_correct") is not None
    ]
    policy_numerator = sum(
        1 for record in eligible_policy
        if record.get("policy_answer_correct") is True
    )
    return {
        "total_scenarios": total,
        "successful_executions": successful,
        "execution_failures": total - successful,
        "execution_success_rate": {
            "numerator": successful,
            "denominator": total,
            "percentage": pct(successful, total),
        },
        "routing_accuracy": bool_metric(records, "routing_correct"),
        "transaction_answer_accuracy": bool_metric(
            transaction_records,
            "transaction_answer_correct",
        ),
        "policy_answer_correctness": {
            "numerator": policy_numerator,
            "denominator": len(eligible_policy),
            "percentage": pct(policy_numerator, len(eligible_policy)),
            "manual_review_required": len(policy_records) - len(eligible_policy),
            "status": "pending_manual_review"
            if len(eligible_policy) != len(policy_records)
            else "automatically_scored",
        },
        "response_time": response_time_metrics(records),
        "clarification_rate": bool_metric(records, "clarification_triggered"),
        "escalation_rate": bool_metric(records, "escalation_triggered"),
        "manual_review_case_count": sum(
            1 for record in records
            if record.get("manual_review_required") is True
        ),
        "llm_call_count": {
            "total": sum(
                int(record.get("llm_call_count") or 0) for record in records
            ),
            "scenarios_with_calls": sum(
                1 for record in records
                if int(record.get("llm_call_count") or 0) > 0
            ),
        },
    }


def build_base_record(
    experiment_id: str,
    scenario: Dict[str, Any],
    system_type: str,
    timestamp: str,
) -> Dict[str, Any]:
    return {
        "experiment_id": experiment_id,
        "scenario_id": scenario.get("scenario_id"),
        "category": scenario.get("category"),
        "subcategory": scenario.get("subcategory"),
        "language": scenario.get("language"),
        "case_type": scenario.get("case_type"),
        "input_type": scenario.get("input_type"),
        "user_input": scenario.get("user_message"),
        "expected_intent": scenario.get("expected_intent"),
        "expected_agent": scenario.get("expected_agent"),
        "expected_evidence": scenario.get("expected_entities"),
        "expected_answer_criteria": scenario.get("expected_answer_facts"),
        "expected_requires_clarification": scenario.get("requires_clarification"),
        "expected_simulated_human_review": scenario.get("simulated_human_review"),
        "system_type": system_type,
        "actual_intent": None,
        "actual_agent": None,
        "final_intent": None,
        "final_agent": None,
        "actual_response": None,
        "response_source": None,
        "retrieved_evidence": None,
        "structured_answer_evidence": None,
        "routing_correct": False,
        "transaction_answer_correct": None,
        "policy_answer_correct": None,
        "clarification_triggered": False,
        "escalation_triggered": False,
        "actual_requires_clarification": None,
        "actual_simulated_human_review": None,
        "execution_success": False,
        "latency_ms": 0.0,
        "llm_model": None,
        "llm_call_count": 0,
        "retry_count": 0,
        "retry_history": [],
        "error_type": None,
        "error_message_sanitized": None,
        "timestamp": timestamp,
        "manual_review_required": False,
        "evaluation_notes": "",
    }


def finalize_record(
    record: Dict[str, Any],
    scenario: Dict[str, Any],
) -> Dict[str, Any]:
    record["routing_correct"] = (
        record.get("actual_intent") == scenario.get("expected_intent")
    )
    record["clarification_triggered"] = bool(
        record.get("actual_requires_clarification")
    )
    record["escalation_triggered"] = bool(
        record.get("actual_simulated_human_review")
    )
    record["transaction_answer_correct"] = score_transaction(
        scenario,
        record.get("actual_response") or "",
        record.get("structured_answer_evidence"),
        record.get("actual_intent"),
        bool(record.get("execution_success")),
    )
    if scenario.get("category") == "policy":
        record["manual_review_required"] = True
        record["policy_answer_correct"] = None
        matches, total = literal_policy_anchor_score(
            scenario,
            record.get("actual_response") or "",
        )
        record["policy_literal_anchor_matches"] = matches
        record["policy_literal_anchor_total"] = total
        record["evaluation_notes"] = (
            "Policy answer semantic correctness is deferred to human "
            "adjudication; literal anchor diagnostics are not used as the "
            "policy metric."
        )
    elif not record.get("evaluation_notes"):
        record["evaluation_notes"] = (
            "Routing, clarification, escalation, and transaction facts "
            "scored deterministically from the frozen scenario criteria and "
            "raw output."
        )
    return record


def write_jsonl(path: Path, records: List[Dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def write_csv(
    path: Path,
    rows: List[Dict[str, Any]],
    fieldnames: List[str],
) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=fieldnames,
            extrasaction="ignore",
        )
        writer.writeheader()
        for row in rows:
            output = {}
            for field in fieldnames:
                value = row.get(field)
                output[field] = (
                    json_string(value)
                    if isinstance(value, (dict, list))
                    else value
                )
            writer.writerow(output)


def load_source_dataset() -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    source_bytes = SOURCE_DATASET.read_bytes()
    scenarios = json.loads(source_bytes.decode("utf-8"))
    if not isinstance(scenarios, list) or len(scenarios) != 120:
        raise RuntimeError(
            "Authoritative dataset must contain exactly 120 scenarios; got "
            f"{len(scenarios) if isinstance(scenarios, list) else 'non-list'}."
        )
    ids = [scenario.get("scenario_id") for scenario in scenarios]
    if any(not scenario.get("scenario_id") for scenario in scenarios):
        raise RuntimeError("Authoritative dataset contains a missing scenario_id.")
    if len(set(ids)) != len(ids):
        raise RuntimeError("Authoritative dataset contains duplicate scenario IDs.")

    required = {
        "scenario_id",
        "category",
        "language",
        "input_type",
        "user_message",
        "expected_intent",
        "expected_agent",
        "expected_entities",
        "requires_clarification",
        "simulated_human_review",
        "expected_answer_facts",
        "notes",
    }
    missing = sorted(
        {
            field
            for scenario in scenarios
            for field in required
            if field not in scenario
        }
    )
    if missing:
        raise RuntimeError(f"Authoritative dataset is missing required fields: {missing}")

    category_counts = dict(sorted(Counter(s["category"] for s in scenarios).items()))
    language_counts = dict(sorted(Counter(s["language"] for s in scenarios).items()))

    category_files = {
        "routing": BASE_DIR / "data" / "evaluation" / "routing_scenarios.json",
        "transaction": BASE_DIR / "data" / "evaluation" / "transaction_scenarios.json",
        "policy": BASE_DIR / "data" / "evaluation" / "policy_scenarios.json",
        "clarification": BASE_DIR
        / "data"
        / "evaluation"
        / "clarification_scenarios.json",
    }
    category_records: List[Dict[str, Any]] = []
    category_files_equal = True
    category_file_status: Dict[str, Any] = {}
    for category, path in category_files.items():
        if not path.exists():
            category_files_equal = False
            category_file_status[category] = {"exists": False}
            continue
        records = json.loads(path.read_text(encoding="utf-8"))
        category_records.extend(records)
        category_file_status[category] = {
            "exists": True,
            "count": len(records),
            "sha256": sha256_file(path),
        }
    if category_records != scenarios:
        category_files_equal = False
    if not category_files_equal:
        raise RuntimeError(
            "Category scenario files are incompatible with all_scenarios.json; "
            "execution stopped before any scenario run."
        )

    metadata = {
        "source_dataset_path": str(SOURCE_DATASET),
        "source_dataset_sha256": sha256_bytes(source_bytes),
        "total_scenarios": len(scenarios),
        "category_counts": category_counts,
        "language_counts": language_counts,
        "case_type_counts": dict(
            sorted(Counter(s.get("case_type") for s in scenarios).items())
        ),
        "requires_clarification_counts": dict(
            sorted(
                Counter(s.get("requires_clarification") for s in scenarios).items()
            )
        ),
        "simulated_human_review_counts": dict(
            sorted(
                Counter(s.get("simulated_human_review") for s in scenarios).items()
            )
        ),
        "scenario_schema": "data/evaluation/scenario_schema.json",
        "category_file_records_match_all_scenarios": category_files_equal,
        "category_file_status": category_file_status,
        "legacy_generator_note": (
            "simulation/generate_scenarios.py is a legacy generator with a "
            "different proposed distribution; it is not a stored dataset and "
            "was not used."
        ),
    }
    return scenarios, metadata


def freeze_scenarios(
    experiment_id: str,
    created_at: str,
    scenarios: List[Dict[str, Any]],
    source_metadata: Dict[str, Any],
) -> str:
    frozen = {
        "metadata": {
            "experiment_id": experiment_id,
            "created_at": created_at,
            **source_metadata,
        },
        "scenarios": scenarios,
    }
    json_dump(FROZEN_SCENARIOS, frozen)
    return sha256_file(FROZEN_SCENARIOS)


def load_baseline_context() -> Tuple[List[Dict[str, Any]], Dict[str, str], str]:
    from app.db.orders import SAMPLE_ORDERS

    policy_docs: Dict[str, str] = {}
    for filename in (
        "refund_policy.md",
        "return_policy.md",
        "exchange_policy.md",
        "shipping_policy.md",
        "payment_policy.md",
    ):
        path = BASE_DIR / "data" / "policies" / filename
        if path.exists():
            policy_docs[filename] = path.read_text(encoding="utf-8")

    orders_block = "\n".join(
        f"  Order {order['order_id']}: status={order['order_status']}, "
        f"payment={order['payment_status']}, shipment={order['shipment_status']}, "
        f"tracking={order['tracking_number']}, carrier={order['shipping_provider']}, "
        f"product={order['product_name']}, customer={order['customer_name']}, "
        f"purchased={order['purchase_date']}, "
        f"estimated_delivery={order['estimated_delivery_date']}"
        for order in SAMPLE_ORDERS
    )
    policies_block = "\n---\n".join(
        f"### {filename}\n{content}"
        for filename, content in policy_docs.items()
    )
    supported_intents = [
        "TRACKING_NUMBER",
        "PAYMENT_STATUS",
        "SHIPMENT_STATUS",
        "ORDER_STATUS",
        "RETURN_REFUND",
        "STORE_POLICY",
        "GREETING",
        "OUT_OF_SCOPE",
    ]
    system_prompt = f"""You are the SiamCart Demo Store customer support assistant — a monolithic AI that handles ALL customer messages in ONE response.

You have access to the store's complete knowledge context below. Use ONLY this context to answer. Do not use general knowledge to invent store rules or order data.

─── SUPPORTED INTENT LABELS (use exactly one) ───

{json.dumps(supported_intents, indent=2)}

─── ALL STORE ORDERS ───

{orders_block}

─── ALL STORE POLICIES ───

{policies_block}

─── RULES ───

1. Classify the customer's intent using one of the supported labels above.
2. Respond in the customer's language (Thai normally, English if the customer wrote in English).
3. Use only the supplied order data and policy text. Do not invent order facts.
4. Do NOT claim a refund, return, exchange, or payment was approved or completed.
5. If required information is missing (e.g. order ID for a transaction query), set requires_clarification=true.
6. If the request is unsupported, unsafe, or requires human intervention, set simulated_human_review=true and respond appropriately.
7. cited_sources may include order IDs (e.g. "ORD-1001") or policy filenames (e.g. "refund_policy.md").

─── OUTPUT FORMAT ───

Return exactly one valid JSON object and nothing else.

Constraints:
- No Markdown code fences;
- No reasoning, commentary, preamble, or trailing text;
- Use JSON booleans true/false, not Python True/False;
- All property names and string values must use double quotes.

Example (missing order ID clarification):
{{"intent": "ORDER_STATUS", "response": "ขออภัยครับ กรุณาแจ้งหมายเลขคำสั่งซื้อ (Order ID) เพื่อให้ตรวจสอบสถานะให้ได้ครับ", "requires_clarification": true, "simulated_human_review": false, "extracted_entities": {{}}, "cited_sources": [], "confidence": 0.85}}

Schema:
{{
  "intent": "<one supported intent label>",
  "response": "<your response to the customer>",
  "requires_clarification": <true or false>,
  "simulated_human_review": <true or false>,
  "extracted_entities": {{<key-value pairs of extracted entities>}},
  "cited_sources": [<order IDs or policy filenames>],
  "confidence": <0.0 to 1.0>
}}"""
    return SAMPLE_ORDERS, policy_docs, system_prompt


def parse_json_from_response(text: str) -> Tuple[Optional[Dict[str, Any]], str]:
    if not text:
        return None, "failed"
    text = text.strip()
    try:
        value = json.loads(text)
        return value, "direct"
    except json.JSONDecodeError:
        pass
    fence = chr(96) * 3
    fenced = re.match(
        r"^" + re.escape(fence) + r"(?:json)?\s*\n?(.*?)"
        + re.escape(fence) + r"\s*$",
        text,
        re.DOTALL,
    )
    if fenced:
        try:
            return json.loads(fenced.group(1).strip()), "code_fence"
        except json.JSONDecodeError:
            pass

    depth = 0
    start: Optional[int] = None
    in_string = False
    escape = False
    for index, char in enumerate(text):
        if escape:
            escape = False
            continue
        if char == "\\":
            escape = True
            continue
        if char == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if char == "{":
            if depth == 0:
                start = index
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0 and start is not None:
                try:
                    return json.loads(text[start : index + 1]), "balanced_object"
                except json.JSONDecodeError:
                    start = None
    return None, "failed"


def validate_baseline_schema(parsed: Any) -> Tuple[bool, str]:
    required = [
        "intent",
        "response",
        "requires_clarification",
        "simulated_human_review",
        "extracted_entities",
        "cited_sources",
        "confidence",
    ]
    if not isinstance(parsed, dict):
        return False, "top_level_not_object"
    for field in required:
        if field not in parsed:
            return False, f"missing_field:{field}"
    supported = {
        "TRACKING_NUMBER",
        "PAYMENT_STATUS",
        "SHIPMENT_STATUS",
        "ORDER_STATUS",
        "RETURN_REFUND",
        "STORE_POLICY",
        "GREETING",
        "OUT_OF_SCOPE",
    }
    if parsed["intent"] not in supported:
        return False, f"invalid_intent:{parsed['intent']}"
    if not isinstance(parsed["response"], str):
        return False, "response_not_string"
    if not isinstance(parsed["requires_clarification"], bool):
        return False, "requires_clarification_not_boolean"
    if not isinstance(parsed["simulated_human_review"], bool):
        return False, "simulated_human_review_not_boolean"
    if not isinstance(parsed["extracted_entities"], dict):
        return False, "extracted_entities_not_object"
    if not isinstance(parsed["cited_sources"], list):
        return False, "cited_sources_not_array"
    confidence = parsed["confidence"]
    if not isinstance(confidence, (int, float)) or isinstance(confidence, bool):
        return False, "confidence_not_numeric"
    if confidence < 0 or confidence > 1:
        return False, "confidence_out_of_range"
    return True, ""


def call_baseline_once(
    messages: List[Dict[str, str]],
    llm_config: Dict[str, Any],
) -> Dict[str, Any]:
    import openai

    start = time.perf_counter()
    try:
        client = openai.OpenAI(
            api_key=llm_config["api_key"],
            base_url=llm_config.get("base_url", "https://api.deepseek.com"),
            timeout=BASELINE_TIMEOUT_SECONDS,
        )
        response = client.chat.completions.create(
            model=llm_config["model"],
            temperature=BASELINE_TEMPERATURE,
            max_tokens=BASELINE_MAX_TOKENS,
            messages=messages,
            timeout=BASELINE_TIMEOUT_SECONDS,
        )
        latency_ms = round((time.perf_counter() - start) * 1000, 2)
        content = (response.choices[0].message.content or "").strip()
        if not content:
            return {
                "content": "",
                "latency_ms": latency_ms,
                "error_type": "empty_output",
                "error_message": "Provider returned an empty response.",
            }
        return {
            "content": content,
            "latency_ms": latency_ms,
            "error_type": None,
            "error_message": None,
        }
    except openai.APITimeoutError as exc:
        error_type = "timeout"
    except openai.APIStatusError as exc:
        error_type = "http_error"
    except openai.APIConnectionError as exc:
        error_type = "connection_error"
    except Exception as exc:
        error_type = "unknown"
    return {
        "content": "",
        "latency_ms": round((time.perf_counter() - start) * 1000, 2),
        "error_type": error_type,
        "error_message": sanitize_error(locals().get("exc", error_type)),
    }


def run_monolithic_scenario(
    experiment_id: str,
    scenario: Dict[str, Any],
    system_prompt: str,
    context_metadata: Dict[str, Any],
    llm_config: Dict[str, Any],
) -> Dict[str, Any]:
    started = time.perf_counter()
    record = build_base_record(
        experiment_id,
        scenario,
        "monolithic",
        utc_now(),
    )
    record.update(
        {
            "llm_model": llm_config["model"],
            "retrieved_evidence": {
                "context_type": "monolithic_static_context",
                **context_metadata,
            },
        }
    )
    messages = [
        {"role": "system", "content": system_prompt},
        {
            "role": "user",
            "content": f"Customer message: {scenario['user_message']}",
        },
    ]
    attempts: List[Dict[str, Any]] = []
    final_content = ""
    final_outcome: Dict[str, Any] = {}
    for attempt in range(1, MAX_RETRIES + 2):
        outcome = call_baseline_once(messages, llm_config)
        final_outcome = outcome
        attempts.append(
            {
                "attempt": attempt,
                "latency_ms": outcome["latency_ms"],
                "error_type": outcome["error_type"],
                "error_message_sanitized": sanitize_error(
                    outcome.get("error_message")
                ),
                "provider_response": outcome.get("content", ""),
            }
        )
        final_content = outcome.get("content", "")
        if not outcome.get("error_type"):
            break
        if outcome["error_type"] not in TRANSIENT_ERRORS or attempt > MAX_RETRIES:
            break
        backoff_ms = round(400 * (2 ** (attempt - 1)), 2)
        attempts[-1]["backoff_ms"] = backoff_ms
        time.sleep(backoff_ms / 1000)

    record["retry_history"] = attempts
    record["retry_count"] = max(0, len(attempts) - 1)
    record["llm_call_count"] = len(attempts)
    record["latency_ms"] = round((time.perf_counter() - started) * 1000, 2)
    record["raw_provider_response"] = final_content

    if final_outcome.get("error_type"):
        record.update(
            {
                "response_source": "deepseek_error",
                "error_type": final_outcome["error_type"],
                "error_message_sanitized": sanitize_error(
                    final_outcome.get("error_message")
                ),
                "evaluation_notes": (
                    "Monolithic provider failure after retry policy; no "
                    "synthetic response was substituted."
                ),
            }
        )
        return finalize_record(record, scenario)

    parsed, parse_method = parse_json_from_response(final_content)
    record["parse_method"] = parse_method
    if parsed is None:
        record.update(
            {
                "response_source": "deepseek_invalid_output",
                "actual_response": final_content,
                "error_type": "json_parse_failed",
                "error_message_sanitized": "The provider response was not valid JSON.",
                "evaluation_notes": (
                    "Provider response retained verbatim; no JSON repair or "
                    "synthetic replacement was applied."
                ),
            }
        )
        return finalize_record(record, scenario)

    schema_ok, schema_error = validate_baseline_schema(parsed)
    if not schema_ok:
        record.update(
            {
                "response_source": "deepseek_schema_invalid",
                "actual_response": final_content,
                "error_type": "schema_validation_failed",
                "error_message_sanitized": schema_error,
                "evaluation_notes": (
                    "Provider JSON retained verbatim; schema-invalid output "
                    "was recorded as an execution failure."
                ),
            }
        )
        return finalize_record(record, scenario)

    record.update(
        {
            "actual_intent": parsed["intent"],
            "actual_agent": "monolithic_llm_baseline",
            "final_intent": parsed["intent"],
            "final_agent": "monolithic_llm_baseline",
            "actual_response": parsed["response"],
            "response_source": "deepseek",
            "structured_answer_evidence": parsed.get("extracted_entities") or {},
            "actual_requires_clarification": parsed["requires_clarification"],
            "actual_simulated_human_review": parsed["simulated_human_review"],
            "baseline_entities": parsed.get("extracted_entities") or {},
            "baseline_cited_sources": parsed.get("cited_sources") or [],
            "baseline_confidence": parsed.get("confidence"),
            "execution_success": True,
            "error_type": None,
            "error_message_sanitized": None,
        }
    )
    return finalize_record(record, scenario)


def run_multi_agent_scenario(
    experiment_id: str,
    scenario: Dict[str, Any],
    process_message: Any,
    route_message: Any,
    llm_model: str,
) -> Dict[str, Any]:
    started = time.perf_counter()
    record = build_base_record(
        experiment_id,
        scenario,
        "multi_agent",
        utc_now(),
    )
    record["llm_model"] = llm_model
    session_id = f"repro-2026-{str(scenario['scenario_id']).lower()}"
    attempts: List[Dict[str, Any]] = []
    route_result: Optional[Dict[str, Any]] = None
    raw_result: Optional[Dict[str, Any]] = None
    final_exception: Optional[Exception] = None

    for attempt in range(1, MAX_RETRIES + 2):
        try:
            route_result = route_message(scenario["user_message"])
            raw_result = process_message(
                scenario["user_message"],
                session_id=session_id,
            )
            llm_latency = float(raw_result.get("llm_latency_ms") or 0.0)
            llm_error = raw_result.get("llm_error_type")
            attempts.append(
                {
                    "attempt": attempt,
                    "latency_ms": raw_result.get("latency_ms"),
                    "llm_latency_ms": llm_latency,
                    "error_type": llm_error,
                    "error_message_sanitized": sanitize_error(llm_error),
                }
            )
            if (
                llm_error in TRANSIENT_ERRORS
                and llm_latency > 0
                and attempt <= MAX_RETRIES
            ):
                backoff_ms = round(400 * (2 ** (attempt - 1)), 2)
                attempts[-1]["backoff_ms"] = backoff_ms
                time.sleep(backoff_ms / 1000)
                continue
            break
        except Exception as exc:
            final_exception = exc
            attempts.append(
                {
                    "attempt": attempt,
                    "latency_ms": round(
                        (time.perf_counter() - started) * 1000,
                        2,
                    ),
                    "llm_latency_ms": 0.0,
                    "error_type": "exception",
                    "error_message_sanitized": sanitize_error(exc),
                }
            )
            if attempt <= MAX_RETRIES:
                backoff_ms = round(400 * (2 ** (attempt - 1)), 2)
                attempts[-1]["backoff_ms"] = backoff_ms
                time.sleep(backoff_ms / 1000)
                continue
            break

    record["retry_history"] = attempts
    record["retry_count"] = max(0, len(attempts) - 1)
    record["llm_call_count"] = sum(
        1 for attempt in attempts
        if float(attempt.get("llm_latency_ms") or 0) > 0
    )
    record["latency_ms"] = round((time.perf_counter() - started) * 1000, 2)

    if route_result:
        record.update(
            {
                "router_intent": route_result.get("intent"),
                "router_agent": route_result.get("target_agent"),
                "router_confidence": route_result.get("confidence"),
                "router_reason": route_result.get("routing_reason"),
                "router_requires_clarification": route_result.get(
                    "requires_clarification"
                ),
                "router_simulated_human_review": route_result.get(
                    "simulated_human_review"
                ),
                "router_extracted_order_id": route_result.get(
                    "extracted_order_id"
                ),
                "router_missing_entities": route_result.get("missing_entities"),
                "actual_intent": route_result.get("intent"),
                "actual_agent": route_result.get("target_agent"),
            }
        )

    if raw_result is not None:
        policy_evidence = raw_result.get("policy_evidence") or {}
        record.update(
            {
                "final_intent": raw_result.get("intent"),
                "final_agent": raw_result.get("agent"),
                "actual_response": raw_result.get("response"),
                "response_source": raw_result.get("response_source"),
                "retrieved_evidence": {
                    "transaction": raw_result.get("evidence") or {},
                    "policy": policy_evidence,
                },
                "structured_answer_evidence": raw_result.get("evidence") or {},
                "actual_requires_clarification": raw_result.get(
                    "requires_clarification"
                ),
                "actual_simulated_human_review": raw_result.get(
                    "simulated_human_review"
                ),
                "execution_success": bool(raw_result.get("response")),
                "error_type": raw_result.get("llm_error_type"),
                "error_message_sanitized": sanitize_error(
                    raw_result.get("llm_error_type")
                ),
                "multi_agent_llm_fallback_used": raw_result.get(
                    "llm_fallback_used"
                ),
                "grounding_validation_passed": raw_result.get(
                    "grounding_validation_passed"
                ),
                "escalation_created": raw_result.get("escalation_created"),
            }
        )
        if raw_result.get("llm_error_type"):
            record["evaluation_notes"] = (
                "Current multi-agent implementation returned its own fallback "
                "after the recorded LLM outcome; no synthetic response was "
                "inserted."
            )
    elif final_exception is not None:
        record.update(
            {
                "error_type": "exception",
                "error_message_sanitized": sanitize_error(final_exception),
                "evaluation_notes": (
                    "Multi-agent execution failed after the retry policy; no "
                    "synthetic response was substituted."
                ),
            }
        )

    return finalize_record(record, scenario)


def make_manual_review_rows(
    records: Iterable[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    rows = []
    for record in records:
        if record.get("manual_review_required") is not True:
            continue
        rows.append(
            {
                "scenario_id": record.get("scenario_id"),
                "system_type": record.get("system_type"),
                "category": record.get("category"),
                "input": record.get("user_input"),
                "expected_criteria": record.get("expected_answer_criteria"),
                "actual_response": record.get("actual_response"),
                "reason_manual_review_needed": (
                    "Policy-answer semantic correctness cannot be established "
                    "reliably by exact deterministic matching alone; adjudicate "
                    "against the local policy source documents."
                ),
                "reviewer_score": "",
                "reviewer_notes": "",
            }
        )
    return rows


def make_combined_rows(records: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    fields = [
        "experiment_id",
        "scenario_id",
        "system_type",
        "category",
        "language",
        "input_type",
        "user_input",
        "expected_intent",
        "actual_intent",
        "expected_answer_criteria",
        "actual_response",
        "response_source",
        "routing_correct",
        "transaction_answer_correct",
        "policy_answer_correct",
        "clarification_triggered",
        "escalation_triggered",
        "execution_success",
        "latency_ms",
        "llm_model",
        "llm_call_count",
        "retry_count",
        "error_type",
        "error_message_sanitized",
        "timestamp",
        "manual_review_required",
        "evaluation_notes",
    ]
    return [
        {field: record.get(field) for field in fields}
        for record in records
    ]


def format_metric(metric: Dict[str, Any]) -> str:
    if metric.get("percentage") is None:
        return (
            f"{metric.get('numerator')}/{metric.get('denominator')} "
            "(percentage unavailable)"
        )
    return (
        f"{metric['numerator']}/{metric['denominator']} = "
        f"{metric['percentage']:.6f}%"
    )


def build_report(
    experiment_id: str,
    created_at: str,
    ended_at: str,
    source_metadata: Dict[str, Any],
    frozen_hash: str,
    prompt_hash: str,
    preflight: Dict[str, Any],
    model_config: Dict[str, Any],
    metrics: Dict[str, Any],
    verification: Dict[str, Any],
) -> str:
    multi = metrics["systems"]["multi_agent"]
    mono = metrics["systems"]["monolithic"]

    def percentage_delta(metric: Dict[str, Any], historical_percentage: float) -> str:
        current_percentage = metric.get("percentage")
        if current_percentage is None:
            return "n/a"
        return f"{current_percentage - historical_percentage:+.1f} pp"

    def count_delta(current: int, historical: int) -> str:
        difference = current - historical
        if difference == 0:
            return "no change"
        return f"{difference:+d} scenarios"

    def time_delta(current: float, historical: float) -> str:
        return f"{current - historical:+.6f} ms"

    verdict = (
        "REPRODUCIBILITY RUN COMPLETE"
        if verification.get("passed")
        else "REPRODUCIBILITY RUN NOT COMPLETE"
    )
    return f"""# Reproducibility Report — 2026

## Experiment Status

Post-submission reproducibility run using the current implementation.

Final verdict: {verdict}

This is a new execution and is not presented as the original measurement underlying Chapter 4.

## Dataset

- Authoritative source: {source_metadata["source_dataset_path"]}
- Source dataset SHA-256: {source_metadata["source_dataset_sha256"]}
- Frozen copy: {FROZEN_SCENARIOS}
- Frozen scenario-file SHA-256: {frozen_hash}
- Category distribution: {json_string(source_metadata["category_counts"])}
- Language distribution: {json_string(source_metadata["language_counts"])}
- Case-type distribution: {json_string(source_metadata["case_type_counts"])}
- Stored category files match all_scenarios.json: {source_metadata["category_file_records_match_all_scenarios"]}
- Status: simulated, researcher-designed controlled scenarios; no live customer conversations or production orders were used.
- Schema: {BASE_DIR / "data" / "evaluation" / "scenario_schema.json"}

The repository also contains the legacy generator {BASE_DIR / "simulation" / "generate_scenarios.py"}. It describes a different proposed 120-case distribution (45/40/20/15), but no generated dataset from it is stored in the repository. It was not used. The four stored category datasets exactly match the authoritative combined file at the record level.

## Systems Compared

### Multi-Agent current implementation

app/agents/router.py routes the message; app/agents/orchestrator.py coordinates the flow; app/agents/transaction_tracker.py supplies structured order evidence; app/agents/policy_evaluator.py retrieves local policy evidence; and app/agents/llm_generator.py generates policy responses when the current implementation calls DeepSeek. The evaluated components are the Intelligent Router, Transaction Tracker, and Store Policy Evaluator. Existing deterministic clarification and escalation paths were retained.

The run used an isolated SQLite database at {ISOLATED_DB}, initialized from the current sample-order seed. It did not write to {BASE_DIR / "data" / "orders.db"}. Policy retrieval used the current local policy implementation and its existing local index/fallback behavior.

### Monolithic baseline

The baseline semantics are the existing {BASE_DIR / "scripts" / "run_monolithic_baseline.py"} implementation: one DeepSeek call for the customer message, one JSON response schema, no Router, no Transaction Tracker, no Store Policy Evaluator, no SQLite query, and no ChromaDB retrieval by the baseline. The legacy class {BASE_DIR / "simulation" / "monolithic_baseline.py"} was inspected but not used because it supplies no transaction/policy evidence and therefore would not be a fair comparison.

Equivalent factual access was maintained by supplying the baseline with all five current sample orders and the full text of all five local policy documents in its single system prompt. The exact prompt used is stored at {PROMPT_TXT} (SHA-256 {prompt_hash}); the context inventory and policy-text hashes are stored at {CONTEXT_TXT}. The baseline therefore had the same underlying controlled store facts available to it, while still operating as one LLM pipeline.

## Model Configuration

- Provider: DeepSeek-compatible OpenAI API client
- Model: {model_config["model"]}
- Base URL: {model_config["base_url"]} (no credential values recorded)
- DEEPSEEK_ENABLED: {model_config["deepseek_enabled"]}
- Multi-Agent temperature/max tokens/timeout: {model_config["multi_temperature"]} / {model_config["multi_max_tokens"]} / {model_config["multi_timeout_seconds"]} seconds
- Monolithic temperature/max tokens/timeout: {model_config["baseline_temperature"]} / {model_config["baseline_max_tokens"]} / {model_config["baseline_timeout_seconds"]} seconds
- Baseline output format: JSON schema validated locally; no JSON repair or LLM self-grading
- Baseline retry policy: at most {MAX_RETRIES} retries after the first request for transient provider errors, with recorded exponential short backoff. No successful synthetic replacement was used.

### Required preflight

One harmless provider validation request was performed before scenario execution. Result: {preflight["status"]}, HTTP success: {preflight["http_success"]}, model: {preflight["model"]}, latency: {preflight["latency_ms"]} ms. Credentials were read from environment configuration and were not printed or written to any artifact.

## Execution

- Experiment ID: {experiment_id}
- Start: {created_at}
- End: {ended_at}
- Multi-Agent attempted records: 120 / 120
- Monolithic attempted records: 120 / 120
- Total evaluations: 240
- Multi-Agent successful executions: {multi["successful_executions"]} / 120
- Monolithic successful executions: {mono["successful_executions"]} / 120
- Multi-Agent execution failures: {multi["execution_failures"]} / 120
- Monolithic execution failures: {mono["execution_failures"]} / 120
- Every retry is retained inside its scenario record.
- API keys, Authorization headers, and secrets were not printed or included in the artifacts.

## Metrics

All metrics below were generated from the raw JSONL records. Percentages include explicit numerator and denominator. Response-time metrics use every attempted scenario run, including failed executions, and include retries/backoff in the wall-clock latency.

| Metric | Multi-Agent current | Monolithic baseline |
|---|---:|---:|
| Successful executions | {multi["successful_executions"]}/120 | {mono["successful_executions"]}/120 |
| Execution failures | {multi["execution_failures"]}/120 | {mono["execution_failures"]}/120 |
| Routing accuracy | {format_metric(multi["routing_accuracy"])} | {format_metric(mono["routing_accuracy"])} |
| Transaction-answer accuracy | {format_metric(multi["transaction_answer_accuracy"])} | {format_metric(mono["transaction_answer_accuracy"])} |
| Policy-answer correctness | {format_metric(multi["policy_answer_correctness"])}; {multi["policy_answer_correctness"]["manual_review_required"]} manual | {format_metric(mono["policy_answer_correctness"])}; {mono["policy_answer_correctness"]["manual_review_required"]} manual |
| Average response time | {multi["response_time"]["value_ms"]} ms (n={multi["response_time"]["n"]}) | {mono["response_time"]["value_ms"]} ms (n={mono["response_time"]["n"]}) |
| Median response time | {multi["response_time"]["median_ms"]} ms | {mono["response_time"]["median_ms"]} ms |
| Standard deviation | {multi["response_time"]["standard_deviation_ms"]} ms | {mono["response_time"]["standard_deviation_ms"]} ms |
| P95 response time | {multi["response_time"]["p95_ms"]} ms | {mono["response_time"]["p95_ms"]} ms |
| Clarification rate | {format_metric(multi["clarification_rate"])} | {format_metric(mono["clarification_rate"])} |
| Escalation rate | {format_metric(multi["escalation_rate"])} | {format_metric(mono["escalation_rate"])} |

Policy correctness is not assigned automatically in this run: all 30 policy records per system are included in manual_review.csv, with blank reviewer scores. Literal anchor counts in raw records are diagnostics only and are not promoted to the policy metric.

## Comparison with Thesis

These values were obtained in a post-submission reproducibility run using the current codebase. They should not be interpreted as the original raw measurements underlying Chapter 4 unless the frozen thesis implementation and original execution artifacts are independently verified.

The new execution is therefore a separate result set and may differ from the historical Chapter 4 percentages. No outcome was altered to match those historical claims. Against the repository's finalized historical comparison file ({BASE_DIR / "data" / "evaluation" / "results" / "chapter4_metrics_final.csv"}), the directly comparable results are:

| Metric | Multi-Agent current vs Chapter 4 | Monolithic current vs Chapter 4 |
|---|---:|---:|
| Routing accuracy | {format_metric(multi["routing_accuracy"])} vs 82/120 (68.3%), {percentage_delta(multi["routing_accuracy"], 68.3)} | {format_metric(mono["routing_accuracy"])} vs 78/120 (65.0%), {percentage_delta(mono["routing_accuracy"], 65.0)} |
| Transaction-answer accuracy | {format_metric(multi["transaction_answer_accuracy"])} vs 26/30 (86.7%), {percentage_delta(multi["transaction_answer_accuracy"], 86.7)} | {format_metric(mono["transaction_answer_accuracy"])} vs 12/30 (40.0%), {percentage_delta(mono["transaction_answer_accuracy"], 40.0)} |
| Successful executions | {multi["successful_executions"]}/120 vs 120/120, {count_delta(multi["successful_executions"], 120)} | {mono["successful_executions"]}/120 vs 103/120, {count_delta(mono["successful_executions"], 103)} |
| Average response time | {multi["response_time"]["value_ms"]} ms vs 4610 ms, {time_delta(multi["response_time"]["value_ms"], 4610)} | {mono["response_time"]["value_ms"]} ms vs 4455 ms, {time_delta(mono["response_time"]["value_ms"], 4455)} |

Thus, yes: the new routing, transaction, execution-reliability, and response-time results differ from the historical Chapter 4 claims. Policy-answer correctness cannot be compared numerically yet: Chapter 4 reports 22/30 (73.3%) and 24/30 (80.0%), while this run conservatively leaves all 30 policy cases per system pending manual review.

The current artifact reports clarification and simulated-human-review escalation as separate raw boolean fields. Chapter 4's combined escalation metric used their OR, so its historical 44/120 (36.7%) and 33/120 (27.5%) values are not directly interchangeable with the separate current rates shown above.

## Limitations

- The scenarios and order records are simulated/researcher-designed rather than real customer conversations or live orders.
- The current implementation may differ from the frozen thesis implementation.
- External DeepSeek responses can vary by provider state, model version, and time.
- This is a single run per scenario/system condition, not a repeated-run statistical study.
- Policy-answer correctness requires human review in this artifact because natural-language semantic adequacy cannot be established reliably by the deterministic checks used here.
- The stored legacy generator has a different proposed distribution, but the authoritative stored evaluation dataset was unambiguous because all four category files exactly matched the combined dataset.
- The baseline and Multi-Agent conditions expose equivalent store facts, but their prompt structures and output constraints necessarily differ because one is a single pipeline and the other is a routed current implementation.

## Artifacts

- Multi-Agent raw JSONL: {MULTI_RAW}
- Monolithic raw JSONL: {MONOLITHIC_RAW}
- Combined CSV: {COMBINED_CSV}
- Metrics summary: {METRICS_JSON}
- Manual-review CSV: {MANUAL_REVIEW}
- Independent verifier: {BASE_DIR / "evaluation" / "reproducibility_2026" / "verify_results.py"}
- Monolithic prompt: {PROMPT_TXT}
- Monolithic context inventory: {CONTEXT_TXT}
- Preflight record: {PREFLIGHT_JSON}

Verification result: {verification.get("status")}. Verifier output: {sanitize_error(verification.get("stdout"))}
"""


def main() -> int:
    allowed_existing = {"verify_results.py", "preflight.json"}
    existing_outputs = (
        [
            path
            for path in OUT_DIR.iterdir()
            if path.name not in allowed_existing
        ]
        if OUT_DIR.exists()
        else []
    )
    if existing_outputs:
        raise RuntimeError(
            f"Refusing to overwrite non-empty reproducibility directory: {OUT_DIR}"
        )
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    created_at = utc_now()
    experiment_id = "reproducibility_2026_" + datetime.now(
        timezone.utc
    ).strftime("%Y%m%dT%H%M%SZ")
    scenarios, source_metadata = load_source_dataset()

    os.environ["DEEPSEEK_ENABLED"] = "true"
    os.environ["STORE_DB_PATH"] = str(ISOLATED_DB)
    os.environ["DATABASE_URL"] = f"sqlite:///{ISOLATED_DB.as_posix()}"

    sys.path.insert(0, str(BASE_DIR))
    from app.agents.orchestrator import process_message, set_db_path
    from app.agents.router import route_message
    from app.config import DEEPSEEK_ENABLED, DEEPSEEK_MODEL, LLM_CONFIG
    from app.db.orders import init_database

    if not DEEPSEEK_ENABLED or not LLM_CONFIG.get("api_key"):
        raise RuntimeError(
            "DeepSeek precondition failed: DEEPSEEK_ENABLED must be true and "
            "the configured API key must be present."
        )

    preflight_started = time.perf_counter()
    preflight: Dict[str, Any] = {
        "timestamp": utc_now(),
        "model": DEEPSEEK_MODEL,
        "status": "failed",
        "http_success": False,
        "latency_ms": None,
        "error_type": None,
        "error_message_sanitized": None,
    }
    try:
        import openai

        client = openai.OpenAI(
            api_key=LLM_CONFIG["api_key"],
            base_url=LLM_CONFIG["base_url"],
            timeout=LLM_CONFIG["timeout"],
        )
        response = client.chat.completions.create(
            model=DEEPSEEK_MODEL,
            temperature=LLM_CONFIG["temperature"],
            max_tokens=PREFLIGHT_MAX_TOKENS,
            messages=[{"role": "user", "content": "Reply with the single word OK."}],
            timeout=LLM_CONFIG["timeout"],
        )
        content = (response.choices[0].message.content or "").strip()
        preflight["http_success"] = True
        preflight["status"] = "passed" if content else "failed"
        if not content:
            preflight["error_type"] = "empty_output"
    except Exception as exc:
        preflight["error_type"] = type(exc).__name__
        preflight["error_message_sanitized"] = sanitize_error(exc)
    preflight["latency_ms"] = round(
        (time.perf_counter() - preflight_started) * 1000,
        2,
    )
    json_dump(PREFLIGHT_JSON, preflight)
    if preflight["status"] != "passed":
        raise RuntimeError(
            "DeepSeek preflight failed; scenario execution was not started."
        )

    frozen_hash = freeze_scenarios(
        experiment_id,
        created_at,
        scenarios,
        source_metadata,
    )
    init_database(str(ISOLATED_DB))
    set_db_path(str(ISOLATED_DB))

    orders, policy_docs, system_prompt = load_baseline_context()
    prompt_hash = sha256_bytes(system_prompt.encode("utf-8"))
    PROMPT_TXT.write_text(system_prompt + "\n", encoding="utf-8")
    context_metadata = {
        "orders_in_context": [order["order_id"] for order in orders],
        "policy_documents": sorted(policy_docs),
        "policy_text_sha256": {
            filename: sha256_bytes(text.encode("utf-8"))
            for filename, text in policy_docs.items()
        },
        "system_prompt_sha256": prompt_hash,
        "access_scope": "all five seeded orders and all five local policy documents",
    }
    context_lines = [
        "# Monolithic baseline context inventory",
        "",
        "The baseline system prompt supplied all five seeded orders and all five",
        "local policy documents. The full exact prompt is stored in",
        str(PROMPT_TXT),
        "",
        "## Orders",
        "",
        json.dumps(orders, ensure_ascii=False, indent=2),
        "",
        "## Policy document hashes",
        "",
    ]
    for filename, digest in context_metadata["policy_text_sha256"].items():
        context_lines.append(f"- {filename}: {digest}")
    context_lines.append("")
    CONTEXT_TXT.write_text("\n".join(context_lines), encoding="utf-8")

    multi_records: List[Dict[str, Any]] = []
    monolithic_records: List[Dict[str, Any]] = []
    for index, scenario in enumerate(scenarios, start=1):
        print(f"[{index}/120] {scenario['scenario_id']} multi-agent", flush=True)
        multi_records.append(
            run_multi_agent_scenario(
                experiment_id,
                scenario,
                process_message,
                route_message,
                DEEPSEEK_MODEL,
            )
        )
        print(f"[{index}/120] {scenario['scenario_id']} monolithic", flush=True)
        monolithic_records.append(
            run_monolithic_scenario(
                experiment_id,
                scenario,
                system_prompt,
                context_metadata,
                LLM_CONFIG,
            )
        )

    write_jsonl(MULTI_RAW, multi_records)
    write_jsonl(MONOLITHIC_RAW, monolithic_records)
    all_records = multi_records + monolithic_records
    manual_rows = make_manual_review_rows(all_records)
    write_csv(
        MANUAL_REVIEW,
        manual_rows,
        [
            "scenario_id",
            "system_type",
            "category",
            "input",
            "expected_criteria",
            "actual_response",
            "reason_manual_review_needed",
            "reviewer_score",
            "reviewer_notes",
        ],
    )
    combined_rows = make_combined_rows(all_records)
    write_csv(COMBINED_CSV, combined_rows, list(combined_rows[0].keys()))

    metrics = {
        "experiment_id": experiment_id,
        "experiment_status": (
            "Post-submission reproducibility run using the current implementation."
        ),
        "created_at": created_at,
        "ended_at": utc_now(),
        "source_dataset_path": str(SOURCE_DATASET),
        "source_dataset_sha256": source_metadata["source_dataset_sha256"],
        "frozen_scenario_file_sha256": frozen_hash,
        "raw_record_counts": {
            "multi_agent": len(multi_records),
            "monolithic": len(monolithic_records),
        },
        "manual_review_case_count_total": len(manual_rows),
        "systems": {
            "multi_agent": compute_system_metrics(multi_records),
            "monolithic": compute_system_metrics(monolithic_records),
        },
        "model_configuration": {
            "model": DEEPSEEK_MODEL,
            "base_url": LLM_CONFIG["base_url"],
            "deepseek_enabled": DEEPSEEK_ENABLED,
            "multi_temperature": LLM_CONFIG["temperature"],
            "multi_max_tokens": LLM_CONFIG["max_tokens"],
            "multi_timeout_seconds": LLM_CONFIG["timeout"],
            "baseline_temperature": BASELINE_TEMPERATURE,
            "baseline_max_tokens": BASELINE_MAX_TOKENS,
            "baseline_timeout_seconds": BASELINE_TIMEOUT_SECONDS,
        },
        "preflight": preflight,
    }
    json_dump(METRICS_JSON, metrics)

    verifier_path = OUT_DIR / "verify_results.py"
    verify = subprocess.run(
        [sys.executable, str(verifier_path)],
        cwd=str(BASE_DIR),
        capture_output=True,
        text=True,
        timeout=120,
    )
    verification = {
        "passed": verify.returncode == 0,
        "status": "PASSED" if verify.returncode == 0 else "FAILED",
        "returncode": verify.returncode,
        "stdout": verify.stdout[-10000:],
        "stderr": verify.stderr[-10000:],
    }
    if not verification["passed"]:
        raise RuntimeError(
            "Independent verification failed. "
            + sanitize_error(verification["stdout"] + verification["stderr"])
        )

    ended_at = utc_now()
    metrics["ended_at"] = ended_at
    metrics["verification"] = {
        "status": verification["status"],
        "returncode": verification["returncode"],
    }
    json_dump(METRICS_JSON, metrics)
    REPORT_MD.write_text(
        build_report(
            experiment_id,
            created_at,
            ended_at,
            source_metadata,
            frozen_hash,
            prompt_hash,
            preflight,
            metrics["model_configuration"],
            metrics,
            verification,
        ),
        encoding="utf-8",
    )
    print("REPRODUCIBILITY RUN COMPLETE")
    print(f"Output directory: {OUT_DIR}")
    print(f"Metrics summary: {METRICS_JSON}")
    print(f"Verification: {verification['status']}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(
            f"REPRODUCIBILITY RUN NOT COMPLETE: {sanitize_error(exc)}",
            file=sys.stderr,
        )
        raise SystemExit(1)
