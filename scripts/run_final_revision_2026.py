"""Final post-defense three-run reproducibility evaluation.

This runner is intentionally separate from the application business logic.
It freezes the current source/data state, executes three clean worker
processes, preserves sanitized raw records, computes all metrics from the
raw JSONL files, and invokes an independent verifier before writing the final
report.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import inspect
import json
import os
import platform
import random
import re
import shutil
import statistics
import subprocess
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple


BASE_DIR = Path(__file__).resolve().parent.parent
OUT_DIR = BASE_DIR / "evaluation" / "final_revision_2026"
ENV_DIR = OUT_DIR / "environment"
RUNTIME_DIR = OUT_DIR / "runtime"
FROZEN_SCENARIOS = OUT_DIR / "scenarios_120_frozen.json"
PROMPT_TXT = OUT_DIR / "monolithic_system_prompt.txt"
CONTEXT_TXT = OUT_DIR / "monolithic_context_inventory.md"
POLICY_REVIEW_CSV = OUT_DIR / "policy_manual_review.csv"
FINAL_SUMMARY = OUT_DIR / "final_metrics_summary.json"
FINAL_REPORT = OUT_DIR / "final_repeated_evaluation_report.md"
VERIFIER = OUT_DIR / "verify_final_revision.py"

SOURCE_DATASET = BASE_DIR / "data" / "evaluation" / "all_scenarios.json"
PRODUCTION_DB = BASE_DIR / "data" / "orders.db"
EXPECTED_DATASET_SHA256 = (
    "ea45e38b068285dfd0ae174047773b05654313faac718a19d981b991900bb137"
)
EXPECTED_CATEGORIES = {
    "routing": 30,
    "transaction": 30,
    "policy": 30,
    "clarification": 30,
}
EXPECTED_LANGUAGES = {"th": 72, "th-en": 32, "en": 16}
EXPECTED_INPUT_TYPES = {"normal": 70, "boundary": 32, "abnormal": 18}
RUN_IDS = ("run_1", "run_2", "run_3")
MAX_RETRIES = 3
BASELINE_TIMEOUT_SECONDS = 60
BASELINE_MAX_TOKENS = 1024
BOOTSTRAP_RESAMPLES = 10_000
BOOTSTRAP_SEED = 20260901
TRANSIENT_ERRORS = {
    "timeout",
    "http_error",
    "connection_error",
    "unknown",
    "empty_output",
}
SUPPORTED_BASELINE_INTENTS = [
    "TRACKING_NUMBER",
    "PAYMENT_STATUS",
    "SHIPMENT_STATUS",
    "ORDER_STATUS",
    "RETURN_REFUND",
    "STORE_POLICY",
    "GREETING",
    "OUT_OF_SCOPE",
]


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def json_write(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def json_read(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def redact_text(value: Any) -> str:
    """Sanitize credential-like values without exposing the original text."""
    text = str(value or "")
    text = re.sub(
        r"(?i)Bearer\s+[A-Za-z0-9._~+/=-]+",
        "Bearer [REDACTED]",
        text,
    )
    text = re.sub(r"(?i)sk-[A-Za-z0-9_-]{8,}", "[REDACTED_API_KEY]", text)
    text = re.sub(
        r"(?i)(DEEPSEEK_API_KEY|DATABASE_URL|JWT_SECRET_KEY|RAILWAY_TOKEN)\s*[:=]\s*\S+",
        r"\1=[REDACTED]",
        text,
    )
    text = re.sub(
        r"(?i)(api[_-]?key|authorization)\s*[:=]\s*\S+",
        r"\1=[REDACTED]",
        text,
    )
    return text[:4000]


def sanitize_obj(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): sanitize_obj(v) for k, v in value.items()}
    if isinstance(value, list):
        return [sanitize_obj(v) for v in value]
    if isinstance(value, tuple):
        return [sanitize_obj(v) for v in value]
    if isinstance(value, str):
        return redact_text(value)
    return value


def command_output(args: Sequence[str], cwd: Path = BASE_DIR) -> str:
    result = subprocess.run(
        list(args),
        cwd=str(cwd),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    return redact_text((result.stdout or "") + (result.stderr or ""))


def git_state_snapshot() -> str:
    head = command_output(["git", "rev-parse", "HEAD"]).strip()
    status = command_output(["git", "status", "--porcelain"]).rstrip()
    branch = command_output(["git", "branch", "--show-current"]).strip()
    return (
        f"git_rev_parse_HEAD: {head}\n"
        f"git_branch: {branch}\n"
        "git_status_porcelain:\n"
        f"{status}\n"
    )


def normalise(text: Any) -> str:
    value = str(text or "").lower()
    value = re.sub(r"\s+", " ", value)
    for ch in "\u2010\u2011\u2012\u2013\u2014\u2212":
        value = value.replace(ch, "-")
    return value.strip()


def parse_facts(facts: Iterable[Any]) -> List[Tuple[str, str]]:
    parsed = []
    for item in facts or []:
        text = str(item)
        if "=" in text:
            key, value = text.split("=", 1)
            parsed.append((key.strip(), value.strip()))
    return parsed


def fact_aliases(key: str, value: str) -> List[str]:
    aliases = {
        ("order_status", "shipped"): ["จัดส่งแล้ว", "ถูกจัดส่งแล้ว"],
        ("order_status", "processing"): ["อยู่ระหว่างดำเนินการ"],
        ("order_status", "return_requested"): ["ขอคืนสินค้า", "ส่งคำขอคืนสินค้า"],
        ("order_status", "delivered"): ["จัดส่งถึงปลายทางแล้ว", "จัดส่งสำเร็จแล้ว"],
        ("order_status", "pending"): ["รอดำเนินการ", "รอการชำระเงิน"],
        ("payment_status", "paid"): ["ชำระเงินแล้ว", "ได้รับชำระเงินแล้ว"],
        ("payment_status", "unpaid"): ["ยังไม่ได้รับการยืนยันการชำระเงิน", "ยังไม่ได้ชำระ"],
        ("shipment_status", "in_transit"): ["อยู่ระหว่างการขนส่ง", "กำลังอยู่ระหว่างการขนส่ง"],
        ("shipment_status", "pending_pickup"): ["รอรับพัสดุ", "รอการจัดส่ง"],
        ("shipment_status", "delivered"): ["จัดส่งถึงปลายทางแล้ว", "จัดส่งเรียบร้อยแล้ว"],
        ("shipment_status", "not_shipped"): ["ยังไม่ได้จัดส่ง"],
        ("shipment_status", "return_initiated"): ["อยู่ในกระบวนการคืน", "คืนสินค้า"],
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
            for phrase in ("not found", "no information", "ไม่พบข้อมูล", "ไม่พบคำสั่งซื้อ")
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
            return any(phrase in response_lower for phrase in ("ยังไม่มีหมายเลข", "ไม่มีหมายเลข"))
        if key == "shipping_provider":
            return any(phrase in response_lower for phrase in ("ยังไม่มีผู้ให้บริการ", "ยังไม่ได้จัดส่ง"))
        return False

    response_lower = normalise(response)
    return any(normalise(alias) in response_lower for alias in fact_aliases(key, expected_value))


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
    """Diagnostics only; policy correctness remains human-adjudicated."""
    anchors = parse_facts(scenario.get("expected_answer_facts") or [])
    response_lower = normalise(response)
    matches = sum(1 for _, value in anchors if normalise(value) in response_lower)
    return matches, len(anchors)


def pct(numerator: int, denominator: int) -> Optional[float]:
    return round((numerator / denominator) * 100, 6) if denominator else None


def linear_percentile(values: List[float], fraction: float) -> Optional[float]:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * fraction
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    weight = position - lower
    return round(ordered[lower] + (ordered[upper] - ordered[lower]) * weight, 6)


def bool_metric(records: List[Dict[str, Any]], field: str) -> Dict[str, Any]:
    numerator = sum(1 for record in records if record.get(field) is True)
    return {
        "numerator": numerator,
        "denominator": len(records),
        "percentage": pct(numerator, len(records)),
    }


def confusion_metric(
    records: List[Dict[str, Any]],
    expected_field: str,
    actual_field: str,
) -> Dict[str, Any]:
    matrix = {"true_positive": 0, "false_negative": 0, "false_positive": 0, "true_negative": 0}
    for record in records:
        expected = record.get(expected_field) is True
        actual = record.get(actual_field) is True
        if expected and actual:
            matrix["true_positive"] += 1
        elif expected and not actual:
            matrix["false_negative"] += 1
        elif not expected and actual:
            matrix["false_positive"] += 1
        else:
            matrix["true_negative"] += 1
    return {
        "raw_frequency": sum(1 for record in records if record.get(actual_field) is True),
        "denominator": len(records),
        "confusion_matrix": matrix,
        "expected_positive": sum(1 for record in records if record.get(expected_field) is True),
    }


def latency_metrics(records: List[Dict[str, Any]]) -> Dict[str, Any]:
    values = [float(record.get("latency_ms") or 0.0) for record in records]
    return {
        "n": len(values),
        "mean_ms": round(statistics.mean(values), 6) if values else None,
        "median_ms": round(statistics.median(values), 6) if values else None,
        "sample_standard_deviation_ms": round(statistics.stdev(values), 6)
        if len(values) > 1
        else 0.0,
        "p95_ms": linear_percentile(values, 0.95),
        "minimum_ms": round(min(values), 6) if values else None,
        "maximum_ms": round(max(values), 6) if values else None,
        "basis": "all attempted scored scenario runs; warm-up and preflight excluded",
    }


def compute_system_metrics(records: List[Dict[str, Any]]) -> Dict[str, Any]:
    transaction_records = [r for r in records if r.get("category") == "transaction"]
    policy_records = [r for r in records if r.get("category") == "policy"]
    successful = sum(1 for r in records if r.get("execution_success") is True)
    policy_scored = [
        r for r in policy_records
        if r.get("manual_review_required") is not True and r.get("policy_answer_correct") is not None
    ]
    policy_correct = sum(1 for r in policy_scored if r.get("policy_answer_correct") is True)
    return {
        "total_scenarios": len(records),
        "successful_executions": successful,
        "execution_failures": len(records) - successful,
        "execution_success": {
            "numerator": successful,
            "denominator": len(records),
            "percentage": pct(successful, len(records)),
        },
        "routing_accuracy": bool_metric(records, "routing_correct"),
        "transaction_answer_accuracy": bool_metric(transaction_records, "transaction_answer_correct"),
        "policy_answer_correctness": {
            "numerator": policy_correct,
            "denominator": len(policy_scored),
            "percentage": pct(policy_correct, len(policy_scored)),
            "manual_review_required": len(policy_records) - len(policy_scored),
            "status": "pending_manual_review" if len(policy_scored) != len(policy_records) else "automatically_scored",
        },
        "clarification": confusion_metric(
            records, "expected_requires_clarification", "clarification_triggered"
        ),
        "escalation": confusion_metric(
            records, "expected_simulated_human_review", "escalation_triggered"
        ),
        "latency": latency_metrics(records),
        "manual_review_case_count": sum(1 for r in records if r.get("manual_review_required") is True),
        "llm_call_count": {
            "total": sum(int(r.get("llm_call_count") or 0) for r in records),
            "scenarios_with_calls": sum(1 for r in records if int(r.get("llm_call_count") or 0) > 0),
        },
        "error_counts": dict(sorted(Counter(str(r.get("error_type")) for r in records).items())),
    }


def bootstrap_mean_ci(values: List[float]) -> Dict[str, Any]:
    rng = random.Random(BOOTSTRAP_SEED)
    means: List[float] = []
    for _ in range(BOOTSTRAP_RESAMPLES):
        total = sum(rng.choice(values) for _ in values)
        means.append(total / len(values))
    return {
        "method": "nonparametric bootstrap confidence interval for the mean",
        "resamples": BOOTSTRAP_RESAMPLES,
        "seed": BOOTSTRAP_SEED,
        "confidence_level": 0.95,
        "lower_ms": linear_percentile(means, 0.025),
        "upper_ms": linear_percentile(means, 0.975),
    }


def pooled_latency_metrics(records: List[Dict[str, Any]]) -> Dict[str, Any]:
    result = latency_metrics(records)
    values = [float(record.get("latency_ms") or 0.0) for record in records]
    result["bootstrap_mean_ci_95"] = bootstrap_mean_ci(values)
    return result


def validate_source_dataset() -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    raw = SOURCE_DATASET.read_bytes()
    source_hash = sha256_bytes(raw)
    if source_hash != EXPECTED_DATASET_SHA256:
        raise RuntimeError(
            f"ABORT: authoritative dataset hash differs: {source_hash}"
        )
    data = json.loads(raw.decode("utf-8"))
    if not isinstance(data, list) or len(data) != 120:
        raise RuntimeError("ABORT: authoritative dataset is not exactly 120 records")
    required = {
        "scenario_id", "category", "subcategory", "language", "input_type",
        "user_message", "expected_intent", "expected_agent", "expected_entities",
        "requires_clarification", "simulated_human_review", "expected_answer_facts",
    }
    missing = sorted(required - set(data[0]))
    if missing:
        raise RuntimeError(f"ABORT: dataset schema missing keys: {missing}")
    category_counts = dict(sorted(Counter(x.get("category") for x in data).items()))
    language_counts = dict(sorted(Counter(x.get("language") for x in data).items()))
    input_type_counts = dict(sorted(Counter(x.get("input_type") for x in data).items()))
    if category_counts != EXPECTED_CATEGORIES:
        raise RuntimeError(f"ABORT: category distribution differs: {category_counts}")
    if language_counts != EXPECTED_LANGUAGES:
        raise RuntimeError(f"ABORT: language distribution differs: {language_counts}")
    if input_type_counts != EXPECTED_INPUT_TYPES:
        raise RuntimeError(f"ABORT: input-type distribution differs: {input_type_counts}")
    ids = [x.get("scenario_id") for x in data]
    if len(ids) != len(set(ids)):
        raise RuntimeError("ABORT: duplicate scenario IDs")
    category_files_match = True
    for category in EXPECTED_CATEGORIES:
        category_path = BASE_DIR / "data" / "evaluation" / f"{category}_scenarios.json"
        if not category_path.exists():
            category_files_match = False
            continue
        subset = [x for x in data if x.get("category") == category]
        if json.loads(category_path.read_text(encoding="utf-8")) != subset:
            category_files_match = False
    metadata = {
        "source_dataset_path": str(SOURCE_DATASET),
        "source_dataset_sha256": source_hash,
        "total_scenarios": len(data),
        "category_counts": category_counts,
        "language_counts": language_counts,
        "input_type_counts": input_type_counts,
        "case_type_counts": dict(sorted(Counter(x.get("case_type") for x in data).items(), key=lambda item: str(item[0]))),
        "category_files_match_authoritative": category_files_match,
        "simulated_researcher_designed": True,
        "scenario_schema": str(BASE_DIR / "data" / "evaluation" / "scenario_schema.json"),
    }
    return data, metadata


def prepare_isolated_environment() -> None:
    os.environ.update({
        "DEEPSEEK_ENABLED": "true",
        "AUTH_REQUIRED": "false",
        "STORE_DB_PATH": str(RUNTIME_DIR / "prepare_probe.db"),
        "DATABASE_URL": f"sqlite:///{(RUNTIME_DIR / 'prepare_probe.db').as_posix()}",
        "CHROMA_DIR": str(RUNTIME_DIR / "prepare_probe_chroma"),
    })


def load_runtime_snapshot() -> Dict[str, Any]:
    sys.path.insert(0, str(BASE_DIR))
    import app.config as config
    import app.agents.policy_index as policy_index
    import app.agents.policy_evaluator as policy_evaluator
    import app.agents.router as router

    from app.agents.policy_evaluator import evaluate_policy_query

    router_source = (BASE_DIR / "app" / "agents" / "router.py").read_text(encoding="utf-8")
    numeric_threshold_present = bool(
        re.search(r"routing[_ ]?threshold\s*=\s*[-+]?\d", router_source, re.IGNORECASE)
    )
    confidence_examples = {
        "explicit_intent": router.route_message("ORD-1001 status").get("confidence"),
        "out_of_scope_or_human_review": router.route_message("cancel order ORD-1001").get("confidence"),
        "unknown_clarification": router.route_message("qwerty unrelated").get("confidence"),
        "order_id_fallback": router.route_message("ORD-1001").get("confidence"),
    }
    return {
        "git_head": command_output(["git", "rev-parse", "HEAD"]).strip(),
        "deepseek_enabled": bool(config.DEEPSEEK_ENABLED),
        "deepseek_model": config.DEEPSEEK_MODEL,
        "deepseek_base_url": config.LLM_CONFIG.get("base_url"),
        "multi_agent_temperature": config.LLM_CONFIG.get("temperature"),
        "multi_agent_max_tokens": config.LLM_CONFIG.get("max_tokens"),
        "multi_agent_timeout_seconds": config.LLM_CONFIG.get("timeout"),
        "api_key_present": bool(config.LLM_CONFIG.get("api_key")),
        "embedding_model": config.EMBEDDING_MODEL,
        "chroma_collection_name": policy_index.CHROMA_COLLECTION_NAME,
        "distance_metric": "cosine",
        "policy_chunking_strategy": "Markdown H2 section-based chunks; bilingual Thai and English blocks split into separate chunks; complete section metadata preserved.",
        "max_chunk_characters": policy_index.MAX_CHUNK_CHARS,
        "policy_retrieval_top_k": inspect.signature(evaluate_policy_query).parameters["top_k"].default,
        "policy_retrieval_distance_criterion": policy_evaluator.CONFIDENCE_THRESHOLD,
        "router_implementation": "deterministic rule-based",
        "routing_threshold": None if not numeric_threshold_present else "present_in_source",
        "routing_threshold_reason": (
            "Deterministic rule-based routing; confidence values are heuristic labels, not calibrated probabilities or an acceptance threshold."
            if not numeric_threshold_present
            else "A numeric routing threshold was detected in source and must be reviewed."
        ),
        "router_confidence_values_observed": confidence_examples,
        "router_confidence_meanings": {
            "1.0": "explicit recognized intent and the current implementation's order-ID fallback",
            "0.6": "out-of-scope or clarification/human-review class",
            "0.3": "unknown message requiring clarification",
        },
        "effective_isolated_store_db_path": str(RUNTIME_DIR),
        "effective_isolated_chroma_path": str(RUNTIME_DIR),
        "auth_required": bool(config.AUTH_REQUIRED),
        "baseline_temperature": 0.1,
        "baseline_max_tokens": BASELINE_MAX_TOKENS,
        "baseline_timeout_seconds": BASELINE_TIMEOUT_SECONDS,
        "max_retries_after_first_attempt": MAX_RETRIES,
        "warmup_excluded_from_scored_latency": True,
    }


def build_baseline_context() -> Tuple[str, Dict[str, Any]]:
    sys.path.insert(0, str(BASE_DIR))
    from app.db.orders import SAMPLE_ORDERS

    policy_order = [
        "refund_policy.md",
        "return_policy.md",
        "exchange_policy.md",
        "shipping_policy.md",
        "payment_policy.md",
    ]
    policy_docs = {
        filename: (BASE_DIR / "data" / "policies" / filename).read_text(encoding="utf-8")
        for filename in policy_order
    }
    orders_block = "\n".join(
        f"  Order {order['order_id']}: status={order['order_status']}, "
        f"payment={order['payment_status']}, shipment={order['shipment_status']}, "
        f"tracking={order['tracking_number']}, carrier={order['shipping_provider']}, "
        f"product={order['product_name']}, customer={order['customer_name']}, "
        f"purchased={order['purchase_date']}, estimated_delivery={order['estimated_delivery_date']}"
        for order in SAMPLE_ORDERS
    )
    policies_block = "\n---\n".join(
        f"### {filename}\n{content}" for filename, content in policy_docs.items()
    )
    system_prompt = f"""You are the SiamCart Demo Store customer support assistant — a monolithic AI that handles ALL customer messages in ONE response.

You have access to the store's complete knowledge context below. Use ONLY this context to answer. Do not use general knowledge to invent store rules or order data.

─── SUPPORTED INTENT LABELS (use exactly one) ───

{json.dumps(SUPPORTED_BASELINE_INTENTS, indent=2)}

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
    inventory = {
        "order_source": str(BASE_DIR / "app" / "db" / "orders.py"),
        "order_source_sha256": sha256_file(BASE_DIR / "app" / "db" / "orders.py"),
        "order_count": len(SAMPLE_ORDERS),
        "order_context_sha256": sha256_bytes(
            json.dumps(SAMPLE_ORDERS, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ),
        "policy_sources": [
            {
                "path": str(BASE_DIR / "data" / "policies" / filename),
                "sha256": sha256_file(BASE_DIR / "data" / "policies" / filename),
                "characters": len(content),
            }
            for filename, content in policy_docs.items()
        ],
        "system_prompt_sha256": sha256_bytes(system_prompt.encode("utf-8")),
        "baseline_design": "one unified DeepSeek JSON pipeline; no Router, Transaction Tracker, Store Policy Evaluator, direct SQLite lookup, or ChromaDB retrieval",
    }
    return system_prompt, inventory


def freeze_and_record_environment(scenarios: List[Dict[str, Any]], metadata: Dict[str, Any]) -> Dict[str, Any]:
    shutil.copyfile(SOURCE_DATASET, FROZEN_SCENARIOS)
    frozen_hash = sha256_file(FROZEN_SCENARIOS)
    if frozen_hash != EXPECTED_DATASET_SHA256:
        raise RuntimeError("Frozen scenario copy is not byte-identical to the authoritative dataset")

    git_state = git_state_snapshot()
    (ENV_DIR / "git_state.txt").write_text(git_state, encoding="utf-8")
    (ENV_DIR / "python_version.txt").write_text(
        redact_text(sys.version) + "\n", encoding="utf-8"
    )
    platform_text = json.dumps(
        {
            "platform": platform.platform(),
            "system": platform.system(),
            "release": platform.release(),
            "version": platform.version(),
            "machine": platform.machine(),
            "processor": platform.processor(),
            "python_implementation": platform.python_implementation(),
        },
        ensure_ascii=False,
        indent=2,
    )
    (ENV_DIR / "platform.txt").write_text(platform_text + "\n", encoding="utf-8")
    pip_freeze = command_output([sys.executable, "-m", "pip", "freeze"])
    (ENV_DIR / "pip_freeze.txt").write_text(pip_freeze + "\n", encoding="utf-8")

    source_paths = [
        BASE_DIR / "app" / "agents" / "router.py",
        BASE_DIR / "app" / "agents" / "orchestrator.py",
        BASE_DIR / "app" / "agents" / "transaction_tracker.py",
        BASE_DIR / "app" / "agents" / "policy_evaluator.py",
        BASE_DIR / "app" / "agents" / "policy_index.py",
        BASE_DIR / "app" / "agents" / "llm_generator.py",
        BASE_DIR / "app" / "config.py",
        BASE_DIR / "scripts" / "run_reproducibility_2026.py",
        BASE_DIR / "scripts" / "run_monolithic_baseline.py",
        BASE_DIR / "scripts" / "run_final_revision_2026.py",
        SOURCE_DATASET,
    ]
    source_paths.extend(sorted((BASE_DIR / "data" / "policies").glob("*.md")))
    source_entries = []
    for path in source_paths:
        if not path.exists():
            raise RuntimeError(f"Required source file missing: {path}")
        source_entries.append(
            {
                "path": str(path),
                "relative_path": str(path.relative_to(BASE_DIR)),
                "sha256": sha256_file(path),
                "bytes": path.stat().st_size,
            }
        )
    json_write(
        ENV_DIR / "source_hashes.json",
        {
            "captured_at": utc_now(),
            "git_head": command_output(["git", "rev-parse", "HEAD"]).strip(),
            "files": source_entries,
        },
    )

    prompt, inventory = build_baseline_context()
    PROMPT_TXT.write_text(prompt, encoding="utf-8")
    CONTEXT_TXT.write_text(
        "# Monolithic Context Inventory\n\n"
        "This is the exact controlled context supplied to the monolithic baseline.\n\n"
        f"- Order source: `{inventory['order_source']}`\n"
        f"- Order source SHA-256: `{inventory['order_source_sha256']}`\n"
        f"- Synthetic order count: {inventory['order_count']}\n"
        f"- Canonical order-context SHA-256: `{inventory['order_context_sha256']}`\n"
        "- Policy source files:\n"
        + "\n".join(
            f"  - `{item['path']}` — SHA-256 `{item['sha256']}`, {item['characters']} characters"
            for item in inventory["policy_sources"]
        )
        + "\n\n"
        f"- Exact system-prompt SHA-256: `{inventory['system_prompt_sha256']}`\n"
        f"- Baseline design: {inventory['baseline_design']}\n",
        encoding="utf-8",
    )

    runtime_snapshot = load_runtime_snapshot()
    package_versions = extract_package_versions(pip_freeze)
    config_snapshot = {
        "captured_at": utc_now(),
        "experiment_label": "Post-defense repeated evaluation — current implementation",
        "runtime": runtime_snapshot,
        "package_versions": package_versions,
        "monolithic_prompt_sha256": sha256_file(PROMPT_TXT),
        "monolithic_context_inventory_sha256": sha256_file(CONTEXT_TXT),
        "source_dataset": metadata,
        "baseline_context": inventory,
        "production_db_target": str(PRODUCTION_DB),
        "production_db_used": False,
    }
    json_write(ENV_DIR / "configuration_snapshot.json", config_snapshot)

    if PRODUCTION_DB.exists():
        before = sha256_file(PRODUCTION_DB)
        db_info = {
            "path": str(PRODUCTION_DB),
            "exists_before": True,
            "sha256_before": before,
            "size_before": PRODUCTION_DB.stat().st_size,
            "last_write_time_before": PRODUCTION_DB.stat().st_mtime_ns,
        }
    else:
        db_info = {
            "path": str(PRODUCTION_DB),
            "exists_before": False,
            "sha256_before": None,
            "size_before": None,
            "last_write_time_before": None,
        }
    json_write(ENV_DIR / "production_db_integrity.json", db_info)
    return {
        "frozen_hash": frozen_hash,
        "prompt_hash": sha256_file(PROMPT_TXT),
        "config_snapshot": config_snapshot,
        "inventory": inventory,
        "git_state": git_state,
    }


def extract_package_versions(pip_freeze: str) -> Dict[str, str]:
    wanted = {
        "openai",
        "chromadb",
        "sentence-transformers",
        "numpy",
        "sqlalchemy",
        "pydantic",
        "python-dotenv",
        "httpx",
        "fastapi",
    }
    versions: Dict[str, str] = {}
    for line in pip_freeze.splitlines():
        match = re.match(r"^([A-Za-z0-9_.-]+)==(.+)$", line.strip())
        if match and match.group(1).lower() in wanted:
            versions[match.group(1).lower()] = redact_text(match.group(2))
    return dict(sorted(versions.items()))


def parse_json_from_response(text: str) -> Tuple[Optional[Dict[str, Any]], str]:
    if not text:
        return None, "failed"
    text = text.strip()
    try:
        value = json.loads(text)
        return value if isinstance(value, dict) else None, "direct"
    except json.JSONDecodeError:
        pass
    fenced = re.match(r"^```(?:json)?\s*\n?(.*?)```\s*$", text, re.DOTALL)
    if fenced:
        try:
            value = json.loads(fenced.group(1).strip())
            return value if isinstance(value, dict) else None, "code_fence"
        except json.JSONDecodeError:
            pass
    depth = 0
    start: Optional[int] = None
    in_string = False
    escaped = False
    for index, char in enumerate(text):
        if escaped:
            escaped = False
            continue
        if char == "\\":
            escaped = True
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
                    value = json.loads(text[start:index + 1])
                    return value if isinstance(value, dict) else None, "balanced_object"
                except json.JSONDecodeError:
                    start = None
    return None, "failed"


def validate_baseline_schema(parsed: Any) -> Tuple[bool, str]:
    if not isinstance(parsed, dict):
        return False, "not_an_object"
    required = [
        "intent", "response", "requires_clarification", "simulated_human_review",
        "extracted_entities", "cited_sources", "confidence",
    ]
    for field in required:
        if field not in parsed:
            return False, f"missing_field:{field}"
    if parsed["intent"] not in set(SUPPORTED_BASELINE_INTENTS):
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
    if isinstance(parsed["confidence"], bool) or not isinstance(parsed["confidence"], (int, float)):
        return False, "confidence_not_numeric"
    if not 0 <= parsed["confidence"] <= 1:
        return False, "confidence_out_of_range"
    return True, ""


def call_deepseek(
    messages: List[Dict[str, str]],
    model: str,
    base_url: str,
    api_key: str,
    temperature: float,
    max_tokens: int,
    timeout: int,
) -> Tuple[Optional[str], float, Optional[str]]:
    import openai

    started = time.perf_counter()
    try:
        client = openai.OpenAI(api_key=api_key, base_url=base_url, timeout=timeout)
        response = client.chat.completions.create(
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            messages=messages,
            timeout=timeout,
        )
        latency = round((time.perf_counter() - started) * 1000, 2)
        content = (response.choices[0].message.content or "").strip()
        if not content:
            return None, latency, "empty_output"
        return content, latency, None
    except openai.APITimeoutError:
        return None, round((time.perf_counter() - started) * 1000, 2), "timeout"
    except openai.APIStatusError:
        return None, round((time.perf_counter() - started) * 1000, 2), "http_error"
    except openai.APIConnectionError:
        return None, round((time.perf_counter() - started) * 1000, 2), "connection_error"
    except Exception:
        return None, round((time.perf_counter() - started) * 1000, 2), "unknown"


def baseline_system_record(
    run_id: str,
    scenario: Dict[str, Any],
    system_type: str,
    model: str,
) -> Dict[str, Any]:
    return {
        "run_id": run_id,
        "scenario_id": scenario.get("scenario_id"),
        "system_type": system_type,
        "category": scenario.get("category"),
        "language": scenario.get("language"),
        "input_type": scenario.get("input_type"),
        "case_type": scenario.get("case_type"),
        "user_input": scenario.get("user_message"),
        "expected_intent": scenario.get("expected_intent"),
        "expected_evidence": scenario.get("expected_entities"),
        "expected_answer_criteria": scenario.get("expected_answer_facts"),
        "expected_requires_clarification": scenario.get("requires_clarification"),
        "expected_simulated_human_review": scenario.get("simulated_human_review"),
        "actual_intent": None,
        "final_intent": None,
        "actual_response": "",
        "response_source": None,
        "retrieved_evidence": {},
        "structured_answer_evidence": {},
        "routing_correct": False,
        "transaction_answer_correct": None,
        "policy_answer_correct": None,
        "clarification_triggered": False,
        "escalation_triggered": False,
        "actual_requires_clarification": False,
        "actual_simulated_human_review": False,
        "execution_success": False,
        "latency_ms": 0.0,
        "llm_model": model,
        "llm_call_count": 0,
        "retry_count": 0,
        "retry_history": [],
        "error_type": None,
        "error_message_sanitized": None,
        "timestamp": utc_now(),
        "manual_review_required": scenario.get("category") == "policy",
        "evaluation_notes": "",
    }


def finalize_record(
    record: Dict[str, Any],
    scenario: Dict[str, Any],
    structured: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    record["routing_correct"] = record.get("actual_intent") == scenario.get("expected_intent")
    record["clarification_triggered"] = bool(record.get("actual_requires_clarification"))
    record["escalation_triggered"] = bool(record.get("actual_simulated_human_review"))
    record["transaction_answer_correct"] = score_transaction(
        scenario,
        record.get("actual_response") or "",
        structured,
        record.get("actual_intent"),
        bool(record.get("execution_success")),
    )
    if scenario.get("category") == "policy":
        record["policy_answer_correct"] = None
        record["manual_review_required"] = True
        matches, total = literal_policy_anchor_score(scenario, record.get("actual_response") or "")
        record["policy_anchor_matches"] = matches
        record["policy_anchor_total"] = total
        record["evaluation_notes"] = (
            (record.get("evaluation_notes") + " ").strip()
            + "Policy correctness is pending human adjudication against local policy documents; anchor counts are diagnostic only."
        )
    return record


def run_multi_agent_scenario(
    run_id: str,
    scenario: Dict[str, Any],
    route_message: Any,
    process_message: Any,
    model: str,
) -> Dict[str, Any]:
    started = time.perf_counter()
    record = baseline_system_record(run_id, scenario, "multi_agent", model)
    attempts: List[Dict[str, Any]] = []
    route_result: Optional[Dict[str, Any]] = None
    app_result: Optional[Dict[str, Any]] = None
    final_exception: Optional[Exception] = None
    for attempt in range(1, MAX_RETRIES + 2):
        try:
            route_result = route_message(scenario["user_message"])
            app_result = process_message(
                scenario["user_message"],
                session_id=f"final-revision-{run_id}-{scenario['scenario_id']}-{attempt}",
            )
            app_error = app_result.get("llm_error_type")
            app_latency = float(app_result.get("llm_latency_ms") or 0.0)
            attempt_entry = {
                "attempt": attempt,
                "latency_ms": app_result.get("latency_ms"),
                "llm_latency_ms": app_latency,
                "error_type": app_error,
                "error_message_sanitized": redact_text(app_error),
                "app_result_sanitized": sanitize_obj(app_result),
            }
            if app_error in TRANSIENT_ERRORS and app_latency > 0 and attempt <= MAX_RETRIES:
                backoff_ms = round(400 * (2 ** (attempt - 1)), 2)
                attempt_entry["backoff_ms"] = backoff_ms
                attempts.append(attempt_entry)
                time.sleep(backoff_ms / 1000)
                continue
            attempts.append(attempt_entry)
            break
        except Exception as exc:
            final_exception = exc
            attempt_entry = {
                "attempt": attempt,
                "latency_ms": round((time.perf_counter() - started) * 1000, 2),
                "llm_latency_ms": 0.0,
                "error_type": "exception",
                "error_message_sanitized": redact_text(type(exc).__name__),
            }
            if attempt <= MAX_RETRIES:
                backoff_ms = round(400 * (2 ** (attempt - 1)), 2)
                attempt_entry["backoff_ms"] = backoff_ms
                attempts.append(attempt_entry)
                time.sleep(backoff_ms / 1000)
                continue
            attempts.append(attempt_entry)
            break

    record["retry_history"] = attempts
    record["retry_count"] = max(0, len(attempts) - 1)
    record["llm_call_count"] = sum(
        1 for attempt in attempts if float(attempt.get("llm_latency_ms") or 0) > 0
    )
    record["latency_ms"] = round((time.perf_counter() - started) * 1000, 2)
    if route_result is not None:
        record.update(
            {
                "actual_intent": route_result.get("intent"),
                "final_intent": app_result.get("intent") if app_result else None,
                "router_agent": route_result.get("target_agent"),
                "router_confidence": route_result.get("confidence"),
                "router_reason": route_result.get("routing_reason"),
                "router_missing_entities": route_result.get("missing_entities"),
            }
        )
    if app_result is not None:
        record.update(
            {
                "actual_response": app_result.get("response") or "",
                "response_source": app_result.get("response_source"),
                "retrieved_evidence": {
                    "transaction": app_result.get("evidence") or {},
                    "policy": app_result.get("policy_evidence") or {},
                },
                "structured_answer_evidence": app_result.get("evidence") or {},
                "actual_requires_clarification": bool(app_result.get("requires_clarification")),
                "actual_simulated_human_review": bool(app_result.get("simulated_human_review")),
                "execution_success": bool(app_result.get("response")),
                "error_type": app_result.get("llm_error_type"),
                "error_message_sanitized": redact_text(app_result.get("llm_error_type")),
                "multi_agent_result_sanitized": sanitize_obj(app_result),
            }
        )
        if app_result.get("llm_error_type"):
            record["evaluation_notes"] = (
                "The current Multi-Agent implementation returned its application fallback after the recorded LLM outcome; no synthetic success was inserted."
            )
    elif final_exception is not None:
        record["error_type"] = "exception"
        record["error_message_sanitized"] = redact_text(type(final_exception).__name__)
        record["evaluation_notes"] = "Multi-Agent execution failed after the fixed retry policy; no synthetic response was substituted."
    return finalize_record(record, scenario, record.get("structured_answer_evidence"))


def run_monolithic_scenario(
    run_id: str,
    scenario: Dict[str, Any],
    system_prompt: str,
    model: str,
    base_url: str,
    api_key: str,
) -> Dict[str, Any]:
    started = time.perf_counter()
    record = baseline_system_record(run_id, scenario, "monolithic", model)
    attempts: List[Dict[str, Any]] = []
    parsed: Optional[Dict[str, Any]] = None
    final_error: Optional[str] = None
    final_error_message: Optional[str] = None
    for attempt in range(1, MAX_RETRIES + 2):
        content, provider_latency, error_type = call_deepseek(
            [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": f"Customer message: {scenario['user_message']}"},
            ],
            model=model,
            base_url=base_url,
            api_key=api_key,
            temperature=0.1,
            max_tokens=BASELINE_MAX_TOKENS,
            timeout=BASELINE_TIMEOUT_SECONDS,
        )
        attempt_entry: Dict[str, Any] = {
            "attempt": attempt,
            "provider_latency_ms": provider_latency,
            "error_type": error_type,
            "error_message_sanitized": redact_text(error_type),
            "provider_response_sanitized": redact_text(content or ""),
        }
        if error_type:
            final_error = error_type
            final_error_message = error_type
            if error_type in TRANSIENT_ERRORS and attempt <= MAX_RETRIES:
                backoff_ms = round(400 * (2 ** (attempt - 1)), 2)
                attempt_entry["backoff_ms"] = backoff_ms
                attempts.append(attempt_entry)
                time.sleep(backoff_ms / 1000)
                continue
            attempts.append(attempt_entry)
            break
        parsed, parse_method = parse_json_from_response(content or "")
        attempt_entry["parse_method"] = parse_method
        if parsed is None:
            final_error = "json_parse_failed"
            final_error_message = final_error
            attempts.append(attempt_entry)
            break
        valid, validation_error = validate_baseline_schema(parsed)
        attempt_entry["schema_valid"] = valid
        if not valid:
            final_error = "schema_validation_failed"
            final_error_message = validation_error
            attempts.append(attempt_entry)
            break
        attempts.append(attempt_entry)
        final_error = None
        final_error_message = None
        break

    record["retry_history"] = attempts
    record["retry_count"] = max(0, len(attempts) - 1)
    record["llm_call_count"] = len(attempts)
    record["latency_ms"] = round((time.perf_counter() - started) * 1000, 2)
    record["error_type"] = final_error
    record["error_message_sanitized"] = redact_text(final_error_message)
    if parsed is not None and final_error is None:
        record.update(
            {
                "actual_intent": parsed.get("intent"),
                "final_intent": parsed.get("intent"),
                "actual_response": parsed.get("response") or "",
                "response_source": "deepseek",
                "retrieved_evidence": {
                    "baseline_context": "all five synthetic orders and all five local policy documents supplied in the unified system prompt"
                },
                "structured_answer_evidence": parsed.get("extracted_entities") or {},
                "actual_requires_clarification": bool(parsed.get("requires_clarification")),
                "actual_simulated_human_review": bool(parsed.get("simulated_human_review")),
                "execution_success": True,
                "baseline_entities": parsed.get("extracted_entities") or {},
                "baseline_cited_sources": parsed.get("cited_sources") or [],
                "baseline_confidence": parsed.get("confidence"),
                "baseline_raw_response_sanitized": redact_text(
                    attempts[-1].get("provider_response_sanitized") if attempts else ""
                ),
            }
        )
    else:
        record["evaluation_notes"] = "Monolithic baseline execution failed after the fixed retry policy; no synthetic response was substituted."
    return finalize_record(record, scenario, record.get("structured_answer_evidence"))


def append_jsonl(path: Path, record: Dict[str, Any]) -> None:
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(sanitize_obj(record), ensure_ascii=False) + "\n")


CSV_FIELDS = [
    "run_id", "scenario_id", "system_type", "category", "language", "input_type", "case_type",
    "user_input", "expected_intent", "expected_evidence", "expected_answer_criteria",
    "actual_intent", "final_intent", "actual_response", "response_source", "retrieved_evidence",
    "structured_answer_evidence", "routing_correct", "transaction_answer_correct",
    "policy_answer_correct", "clarification_triggered", "escalation_triggered",
    "execution_success", "latency_ms", "llm_model", "llm_call_count", "retry_count",
    "error_type", "error_message_sanitized", "timestamp", "manual_review_required", "evaluation_notes",
]


def csv_value(value: Any) -> Any:
    if isinstance(value, (dict, list, tuple)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return value


def write_combined_csv(path: Path, records: List[Dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS, extrasaction="ignore")
        writer.writeheader()
        for record in records:
            writer.writerow({field: csv_value(record.get(field)) for field in CSV_FIELDS})


def read_jsonl(path: Path) -> List[Dict[str, Any]]:
    records = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                records.append(json.loads(line))
    return records


def record_run_metrics(run_id: str, run_dir: Path) -> Dict[str, Any]:
    multi = read_jsonl(run_dir / "multi_agent_raw.jsonl")
    mono = read_jsonl(run_dir / "monolithic_raw.jsonl")
    metrics = {
        "run_id": run_id,
        "systems": {
            "multi_agent": compute_system_metrics(multi),
            "monolithic": compute_system_metrics(mono),
        },
    }
    json_write(run_dir / "run_metrics.json", metrics)
    combined = multi + mono
    write_combined_csv(run_dir / "combined_results.csv", combined)
    return metrics


def warmup_and_preflight(run_id: str) -> Dict[str, Any]:
    run_dir = OUT_DIR / run_id
    runtime_dir = RUNTIME_DIR / run_id
    db_path = runtime_dir / "orders.db"
    chroma_dir = runtime_dir / "chroma"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    os.environ.update({
        "DEEPSEEK_ENABLED": "true",
        "AUTH_REQUIRED": "false",
        "STORE_DB_PATH": str(db_path),
        "DATABASE_URL": f"sqlite:///{db_path.as_posix()}",
        "CHROMA_DIR": str(chroma_dir),
    })
    sys.path.insert(0, str(BASE_DIR))
    from app.config import DEEPSEEK_MODEL, LLM_CONFIG
    from app.db.orders import init_database
    from app.agents.policy_index import (
        _get_embedding_model,
        build_policy_index,
        retrieve_policy_clauses,
    )
    from app.agents.router import route_message
    from app.agents.orchestrator import process_message

    manifest: Dict[str, Any] = {
        "run_id": run_id,
        "started_at": utc_now(),
        "experiment_type": "post-defense repeated evaluation — current implementation",
        "scenario_file": str(FROZEN_SCENARIOS),
        "scenario_file_sha256": sha256_file(FROZEN_SCENARIOS),
        "database_path": str(db_path),
        "chroma_path": str(chroma_dir),
        "warmup_excluded_from_scored_latency": True,
        "retry_policy": {
            "max_retries_after_first_attempt": MAX_RETRIES,
            "backoff_ms": [400, 800, 1600],
            "no_synthetic_success": True,
        },
    }
    started = time.perf_counter()
    init_database(str(db_path))
    manifest["db_initialization_ms"] = round((time.perf_counter() - started) * 1000, 2)

    started = time.perf_counter()
    _get_embedding_model()
    manifest["embedding_model_load_ms"] = round((time.perf_counter() - started) * 1000, 2)

    started = time.perf_counter()
    added_chunks = build_policy_index(
        policies_dir=str(BASE_DIR / "data" / "policies"),
        chroma_dir=str(chroma_dir),
    )
    manifest["policy_index_build_ms"] = round((time.perf_counter() - started) * 1000, 2)
    manifest["policy_index_new_chunks"] = added_chunks

    started = time.perf_counter()
    warmup_result = retrieve_policy_clauses(
        "return policy", top_k=3, chroma_dir=str(chroma_dir)
    )
    manifest["policy_retrieval_warmup_ms"] = round((time.perf_counter() - started) * 1000, 2)
    manifest["policy_retrieval_warmup"] = {
        "success": bool(warmup_result.get("retrieval_success")),
        "chunk_count": len(warmup_result.get("retrieved_clauses") or []),
    }

    import openai

    started = time.perf_counter()
    preflight: Dict[str, Any] = {
        "model": DEEPSEEK_MODEL,
        "http_success": False,
        "nonempty": False,
        "status": "failed",
    }
    try:
        client = openai.OpenAI(
            api_key=LLM_CONFIG["api_key"],
            base_url=LLM_CONFIG["base_url"],
            timeout=LLM_CONFIG["timeout"],
        )
        response = client.chat.completions.create(
            model=DEEPSEEK_MODEL,
            temperature=0.0,
            max_tokens=32,
            messages=[
                {"role": "system", "content": "You are a provider connectivity check."},
                {"role": "user", "content": "Reply with the single word READY."},
            ],
            timeout=LLM_CONFIG["timeout"],
        )
        content = (response.choices[0].message.content or "").strip()
        preflight.update({
            "http_success": True,
            "nonempty": bool(content),
            "status": "passed" if content else "failed_empty_output",
        })
    except openai.APITimeoutError:
        preflight["status"] = "timeout"
    except openai.APIStatusError:
        preflight["status"] = "http_error"
    except openai.APIConnectionError:
        preflight["status"] = "connection_error"
    except Exception:
        preflight["status"] = "unknown"
    preflight["latency_ms"] = round((time.perf_counter() - started) * 1000, 2)
    manifest["deepseek_preflight"] = preflight
    manifest["ended_warmup_at"] = utc_now()
    json_write(run_dir / "run_manifest.json", sanitize_obj(manifest))
    if preflight["status"] != "passed":
        raise RuntimeError(f"DeepSeek preflight failed for {run_id}: {preflight['status']}")
    return {
        "manifest": manifest,
        "route_message": route_message,
        "process_message": process_message,
        "model": DEEPSEEK_MODEL,
        "base_url": LLM_CONFIG["base_url"],
        "api_key": LLM_CONFIG["api_key"],
        "system_prompt": PROMPT_TXT.read_text(encoding="utf-8"),
    }


def run_worker(run_id: str) -> int:
    scenarios = json_read(FROZEN_SCENARIOS)
    context = warmup_and_preflight(run_id)
    run_dir = OUT_DIR / run_id
    multi_path = run_dir / "multi_agent_raw.jsonl"
    mono_path = run_dir / "monolithic_raw.jsonl"
    multi_path.write_text("", encoding="utf-8")
    mono_path.write_text("", encoding="utf-8")
    for index, scenario in enumerate(scenarios, start=1):
        multi_record = run_multi_agent_scenario(
            run_id,
            scenario,
            context["route_message"],
            context["process_message"],
            context["model"],
        )
        mono_record = run_monolithic_scenario(
            run_id,
            scenario,
            context["system_prompt"],
            context["model"],
            context["base_url"],
            context["api_key"],
        )
        append_jsonl(multi_path, multi_record)
        append_jsonl(mono_path, mono_record)
        print(
            f"[{run_id} {index}/120] {scenario['scenario_id']} "
            f"MA={'ok' if multi_record['execution_success'] else 'FAIL'} "
            f"MO={'ok' if mono_record['execution_success'] else 'FAIL'}",
            flush=True,
        )
    metrics = record_run_metrics(run_id, run_dir)
    manifest = json_read(run_dir / "run_manifest.json")
    manifest.update({
        "ended_at": utc_now(),
        "multi_agent_records": len(read_jsonl(multi_path)),
        "monolithic_records": len(read_jsonl(mono_path)),
        "run_metrics_sha256": sha256_file(run_dir / "run_metrics.json"),
    })
    json_write(run_dir / "run_manifest.json", manifest)
    print(
        f"{run_id} COMPLETE: multi={len(read_jsonl(multi_path))}, "
        f"monolithic={len(read_jsonl(mono_path))}, "
        f"multi_failures={metrics['systems']['multi_agent']['execution_failures']}, "
        f"monolithic_failures={metrics['systems']['monolithic']['execution_failures']}",
        flush=True,
    )
    return 0


def make_policy_review_rows(records: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    rows = []
    for record in records:
        if record.get("category") != "policy":
            continue
        criteria = record.get("expected_answer_criteria") or []
        criterion_slots = [
            {"criterion_item": criterion, "score": None} for criterion in criteria
        ]
        rows.append(
            {
                "run_id": record.get("run_id"),
                "scenario_id": record.get("scenario_id"),
                "system_type": record.get("system_type"),
                "input": record.get("user_input"),
                "expected_criteria": json.dumps(criteria, ensure_ascii=False),
                "actual_response": record.get("actual_response") or "",
                "criterion_item_scores": json.dumps(criterion_slots, ensure_ascii=False),
                "correct_criterion_items": "",
                "total_criterion_items": len(criteria),
                "scenario_policy_correct": "",
                "reviewer_score": "",
                "reviewer_notes": "",
            }
        )
    return rows


def write_policy_review_csv(records: Iterable[Dict[str, Any]]) -> None:
    rows = make_policy_review_rows(records)
    fields = [
        "run_id", "scenario_id", "system_type", "input", "expected_criteria",
        "actual_response", "criterion_item_scores", "correct_criterion_items",
        "total_criterion_items", "scenario_policy_correct", "reviewer_score", "reviewer_notes",
    ]
    with POLICY_REVIEW_CSV.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def read_all_run_records() -> Dict[str, Dict[str, List[Dict[str, Any]]]]:
    result: Dict[str, Dict[str, List[Dict[str, Any]]]] = {}
    for run_id in RUN_IDS:
        run_dir = OUT_DIR / run_id
        result[run_id] = {
            "multi_agent": read_jsonl(run_dir / "multi_agent_raw.jsonl"),
            "monolithic": read_jsonl(run_dir / "monolithic_raw.jsonl"),
        }
    return result


def build_final_summary(
    metadata: Dict[str, Any],
    preparation: Dict[str, Any],
    all_records: Dict[str, Dict[str, List[Dict[str, Any]]]],
) -> Dict[str, Any]:
    per_run: Dict[str, Dict[str, Any]] = {}
    pooled_records: Dict[str, List[Dict[str, Any]]] = {"multi_agent": [], "monolithic": []}
    for run_id in RUN_IDS:
        per_run[run_id] = {}
        for system_type in ("multi_agent", "monolithic"):
            records = all_records[run_id][system_type]
            per_run[run_id][system_type] = compute_system_metrics(records)
            pooled_records[system_type].extend(records)
    systems = {
        system_type: {
            "per_run": {run_id: per_run[run_id][system_type] for run_id in RUN_IDS},
            "pooled": {
                **compute_system_metrics(pooled_records[system_type]),
                "latency": pooled_latency_metrics(pooled_records[system_type]),
            },
        }
        for system_type in ("multi_agent", "monolithic")
    }
    summary = {
        "experiment_id": f"final_revision_2026_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}",
        "status": "post-defense repeated evaluation — current implementation",
        "created_at": utc_now(),
        "source_dataset_path": metadata["source_dataset_path"],
        "source_dataset_sha256": metadata["source_dataset_sha256"],
        "frozen_scenario_path": str(FROZEN_SCENARIOS),
        "frozen_scenario_sha256": preparation["frozen_hash"],
        "dataset_metadata": metadata,
        "runs": list(RUN_IDS),
        "total_evaluations": 720,
        "systems": systems,
        "policy_manual_review_rows": len(make_policy_review_rows(
            pooled_records["multi_agent"] + pooled_records["monolithic"]
        )),
        "latency_bootstrap": {
            "method": "nonparametric bootstrap confidence interval for the pooled mean",
            "resamples": BOOTSTRAP_RESAMPLES,
            "seed": BOOTSTRAP_SEED,
        },
        "configuration_snapshot_path": str(ENV_DIR / "configuration_snapshot.json"),
        "source_hashes_path": str(ENV_DIR / "source_hashes.json"),
        "monolithic_prompt_sha256": preparation["prompt_hash"],
        "monolithic_context_inventory_path": str(CONTEXT_TXT),
        "production_db_integrity_path": str(ENV_DIR / "production_db_integrity.json"),
    }
    json_write(FINAL_SUMMARY, summary)
    return summary


def verify_production_db_after() -> Dict[str, Any]:
    integrity_path = ENV_DIR / "production_db_integrity.json"
    before = json_read(integrity_path)
    if PRODUCTION_DB.exists():
        after = {
            "exists_after": True,
            "sha256_after": sha256_file(PRODUCTION_DB),
            "size_after": PRODUCTION_DB.stat().st_size,
            "last_write_time_after": PRODUCTION_DB.stat().st_mtime_ns,
        }
    else:
        after = {
            "exists_after": False,
            "sha256_after": None,
            "size_after": None,
            "last_write_time_after": None,
        }
    before.update(after)
    before["unchanged"] = (
        before.get("exists_before") == before.get("exists_after")
        and before.get("sha256_before") == before.get("sha256_after")
        and before.get("size_before") == before.get("size_after")
        and before.get("last_write_time_before") == before.get("last_write_time_after")
    )
    json_write(integrity_path, before)
    return before


def source_hash_status() -> Dict[str, Any]:
    snapshot = json_read(ENV_DIR / "source_hashes.json")
    checks = []
    for entry in snapshot["files"]:
        path = Path(entry["path"])
        current = sha256_file(path) if path.exists() else None
        checks.append({
            "relative_path": entry["relative_path"],
            "expected_sha256": entry["sha256"],
            "current_sha256": current,
            "matches": current == entry["sha256"],
        })
    return {
        "all_match": all(item["matches"] for item in checks),
        "files": checks,
    }


def metric_string(metric: Dict[str, Any]) -> str:
    if metric.get("percentage") is None:
        return f"{metric.get('numerator')}/{metric.get('denominator')} (percentage unavailable)"
    return f"{metric['numerator']}/{metric['denominator']} = {metric['percentage']:.6f}%"


def confusion_string(metric: Dict[str, Any]) -> str:
    matrix = metric["confusion_matrix"]
    return (
        f"raw={metric['raw_frequency']}/{metric['denominator']}; "
        f"TP={matrix['true_positive']}, FN={matrix['false_negative']}, "
        f"FP={matrix['false_positive']}, TN={matrix['true_negative']}"
    )


def build_final_report(
    summary: Dict[str, Any],
    preparation: Dict[str, Any],
    verification: Dict[str, Any],
    integrity: Dict[str, Any],
    hash_status: Dict[str, Any],
) -> str:
    config = preparation["config_snapshot"]
    systems = summary["systems"]
    manifest_lines = []
    for run_id in RUN_IDS:
        manifest = json_read(OUT_DIR / run_id / "run_manifest.json")
        warm = manifest.get("deepseek_preflight", {})
        manifest_lines.append(
            f"- {run_id}: DB init {manifest.get('db_initialization_ms')} ms; "
            f"embedding load {manifest.get('embedding_model_load_ms')} ms; "
            f"index build {manifest.get('policy_index_build_ms')} ms; "
            f"retrieval warm-up {manifest.get('policy_retrieval_warmup_ms')} ms; "
            f"DeepSeek preflight {warm.get('status')} in {warm.get('latency_ms')} ms."
        )
    per_run_metrics = []
    for run_id in RUN_IDS:
        ma = systems["multi_agent"]["per_run"][run_id]
        mo = systems["monolithic"]["per_run"][run_id]
        per_run_metrics.append(
            f"| {run_id} | {metric_string(ma['routing_accuracy'])} | {metric_string(mo['routing_accuracy'])} | "
            f"{metric_string(ma['transaction_answer_accuracy'])} | {metric_string(mo['transaction_answer_accuracy'])} | "
            f"{ma['successful_executions']}/120 | {mo['successful_executions']}/120 |"
        )
    latency_lines = []
    for system_type, label in (("multi_agent", "Multi-Agent"), ("monolithic", "Monolithic")):
        for run_id in RUN_IDS:
            latency = systems[system_type]["per_run"][run_id]["latency"]
            latency_lines.append(
                f"- {label} {run_id}: n={latency['n']}, mean={latency['mean_ms']} ms, median={latency['median_ms']} ms, "
                f"sample SD={latency['sample_standard_deviation_ms']} ms, P95={latency['p95_ms']} ms, "
                f"min={latency['minimum_ms']} ms, max={latency['maximum_ms']} ms."
            )
        pooled = systems[system_type]["pooled"]["latency"]
        latency_lines.append(
            f"- {label} pooled: n={pooled['n']}, mean={pooled['mean_ms']} ms, median={pooled['median_ms']} ms, "
            f"sample SD={pooled['sample_standard_deviation_ms']} ms, P95={pooled['p95_ms']} ms, "
            f"min={pooled['minimum_ms']} ms, max={pooled['maximum_ms']} ms, "
            f"95% bootstrap CI for mean=[{pooled['bootstrap_mean_ci_95']['lower_ms']}, {pooled['bootstrap_mean_ci_95']['upper_ms']}] ms."
        )
    verdict = "FINAL REPEATED EVALUATION COMPLETE" if verification.get("passed") else "FINAL REPEATED EVALUATION NOT COMPLETE"
    return f"""# Final Repeated Evaluation Report — 2026

## Experiment Status

Post-defense repeated evaluation using a frozen current implementation and complete raw logging.

Final verdict: {verdict}

This post-defense repeated evaluation was executed using a frozen current implementation and complete raw logging. It is reported transparently as a revised reproducibility evaluation and is not represented as the missing original raw execution record underlying the defense-draft aggregate percentages.

## Frozen State and Dataset

- Git HEAD: `{config.get('runtime', {}).get('git_head', 'see environment/git_state.txt')}`
- Environment record: `{ENV_DIR}`
- Source-hash status: `{'ALL MATCH' if hash_status['all_match'] else 'MISMATCH DETECTED'}`
- Authoritative dataset: `{summary['source_dataset_path']}`
- Dataset SHA-256: `{summary['source_dataset_sha256']}`
- Frozen copy: `{summary['frozen_scenario_path']}`
- Frozen-copy SHA-256: `{summary['frozen_scenario_sha256']}`
- Composition: {json.dumps(summary['dataset_metadata']['category_counts'], ensure_ascii=False, sort_keys=True)}
- Language mix: {json.dumps(summary['dataset_metadata']['language_counts'], ensure_ascii=False, sort_keys=True)}
- Input types: {json.dumps(summary['dataset_metadata']['input_type_counts'], ensure_ascii=False, sort_keys=True)}
- Status: simulated/researcher-designed controlled scenarios; no live customer or production orders.

## Systems and Configuration

### Multi-Agent current implementation

The evaluated implementation uses `app/agents/router.py` for deterministic intent routing, `app/agents/orchestrator.py` for coordination, `app/agents/transaction_tracker.py` for structured order evidence, `app/agents/policy_evaluator.py` for policy retrieval and evidence preparation, and `app/agents/llm_generator.py` for DeepSeek response generation where the current path calls it. Each scored scenario used a clean session and an isolated per-run SQLite database.

### Monolithic baseline

The established `scripts/run_monolithic_baseline.py` semantics were retained: one unified DeepSeek JSON pipeline per scenario, with no Router, Transaction Tracker, Store Policy Evaluator, direct SQLite lookup, or ChromaDB retrieval. The baseline received all five synthetic sample orders and all five local policy documents in one system prompt. The exact prompt is stored at `{PROMPT_TXT}` with SHA-256 `{summary['monolithic_prompt_sha256']}`; the source inventory is `{CONTEXT_TXT}`.

### Runtime values

- DeepSeek model: `{config['runtime']['deepseek_model']}`
- DeepSeek base URL: `{config['runtime']['deepseek_base_url']}`; credentials were not recorded.
- Multi-Agent temperature/max tokens/timeout: `{config['runtime']['multi_agent_temperature']} / {config['runtime']['multi_agent_max_tokens']} / {config['runtime']['multi_agent_timeout_seconds']} seconds`
- Monolithic temperature/max tokens/timeout: `0.1 / {config['runtime']['baseline_max_tokens']} / {config['runtime']['baseline_timeout_seconds']} seconds`
- Embedding model: `{config['runtime']['embedding_model']}`
- Chroma collection: `{config['runtime']['chroma_collection_name']}`; distance metric: `{config['runtime']['distance_metric']}`
- Policy chunking: `{config['runtime']['policy_chunking_strategy']}`
- Maximum chunk characters: `{config['runtime']['max_chunk_characters']}`
- Retrieval top-k: `{config['runtime']['policy_retrieval_top_k']}`; distance criterion: `{config['runtime']['policy_retrieval_distance_criterion']}`
- Router: `{config['runtime']['router_implementation']}`
- Routing threshold: `{config['runtime']['routing_threshold']}`
- Routing threshold reason: {config['runtime']['routing_threshold_reason']}
- Observed heuristic confidence values: `{json.dumps(config['runtime']['router_confidence_values_observed'], ensure_ascii=False, sort_keys=True)}`
- Retry policy: maximum {MAX_RETRIES} retries after the first attempt, fixed short exponential backoff, no synthetic replacement.

## Warm-up Boundary

Before each run, the isolated DB was initialized, the embedding model loaded, the policy index built, one unscored policy retrieval was performed, and one harmless unscored DeepSeek preflight was performed. Warm-up and preflight latency were excluded from scored scenario latency.

{chr(10).join(manifest_lines)}

## Completion and Results

Three runs × 120 scenarios × 2 systems = 720 attempted scenario-system evaluations.

| Run | MA routing | Monolithic routing | MA transaction | Monolithic transaction | MA success | Monolithic success |
|---|---:|---:|---:|---:|---:|---:|
{chr(10).join(per_run_metrics)}

### Pooled metrics

| Metric | Multi-Agent pooled | Monolithic pooled |
|---|---:|---:|
| Routing accuracy | {metric_string(systems['multi_agent']['pooled']['routing_accuracy'])} | {metric_string(systems['monolithic']['pooled']['routing_accuracy'])} |
| Transaction-answer accuracy | {metric_string(systems['multi_agent']['pooled']['transaction_answer_accuracy'])} | {metric_string(systems['monolithic']['pooled']['transaction_answer_accuracy'])} |
| Execution success | {metric_string(systems['multi_agent']['pooled']['execution_success'])} | {metric_string(systems['monolithic']['pooled']['execution_success'])} |
| Execution failures | {systems['multi_agent']['pooled']['execution_failures']}/360 | {systems['monolithic']['pooled']['execution_failures']}/360 |
| Policy correctness | Pending manual review, {systems['multi_agent']['pooled']['policy_answer_correctness']['manual_review_required']} rows | Pending manual review, {systems['monolithic']['pooled']['policy_answer_correctness']['manual_review_required']} rows |

### Clarification and escalation confusion results

Operational definition: the system's structured `requires_clarification` and `simulated_human_review` outputs are the booleans recorded as triggered; false negatives include failed executions with no structured output.

| Scope | Clarification | Simulated human review escalation |
|---|---|---|
| Multi-Agent pooled | {confusion_string(systems['multi_agent']['pooled']['clarification'])} | {confusion_string(systems['multi_agent']['pooled']['escalation'])} |
| Monolithic pooled | {confusion_string(systems['monolithic']['pooled']['clarification'])} | {confusion_string(systems['monolithic']['pooled']['escalation'])} |

Per-run confusion matrices are preserved in `final_metrics_summary.json` and each `run_metrics.json`.

### Latency

Sample standard deviation is used. P95 uses deterministic linear interpolation. The pooled 95% confidence interval is a deterministic nonparametric bootstrap for the mean with {BOOTSTRAP_RESAMPLES:,} resamples and fixed seed `{BOOTSTRAP_SEED}`.

{chr(10).join(latency_lines)}

## Policy Review

Every policy response is included in `{POLICY_REVIEW_CSV}`: 30 policy scenarios × 2 systems × 3 runs = 180 rows. Criterion slots are left unscored; `reviewer_score` and `reviewer_notes` are blank. DeepSeek was not used to grade its own answers.

## Limitations

- This is a single three-repetition evaluation of simulated/researcher-designed scenarios, not a broad statistical study.
- External DeepSeek responses may vary with provider state, model version, and time.
- Current implementation behavior may differ from the frozen thesis implementation and is reported as a revised reproducibility result.
- Policy correctness remains pending human adjudication against the local policy documents.
- Warm-up is intentionally excluded, so latency represents steady-state scenario processing rather than cold-start application latency.

## Verification and Integrity

- Independent verifier: `{VERIFIER}`
- Verification result: `{'PASSED' if verification.get('passed') else 'FAILED'}`
- Scenario hash check: `PASSED`
- Raw records: 120 Multi-Agent + 120 Monolithic per run
- Pooled combined rows: 720
- Production DB path: `{PRODUCTION_DB}`
- Production DB unchanged: `{integrity.get('unchanged')}`
- Secret scan: performed by independent verifier; no credential values were recorded.

## Artifacts

- `{ENV_DIR / 'git_state.txt'}`
- `{ENV_DIR / 'python_version.txt'}`
- `{ENV_DIR / 'platform.txt'}`
- `{ENV_DIR / 'pip_freeze.txt'}`
- `{ENV_DIR / 'configuration_snapshot.json'}`
- `{ENV_DIR / 'source_hashes.json'}`
- `{FROZEN_SCENARIOS}`
- `{PROMPT_TXT}`
- `{CONTEXT_TXT}`
- `{OUT_DIR / 'run_1'}`
- `{OUT_DIR / 'run_2'}`
- `{OUT_DIR / 'run_3'}`
- `{POLICY_REVIEW_CSV}`
- `{FINAL_SUMMARY}`
- `{FINAL_REPORT}`
- `{VERIFIER}`
"""


def run_master() -> int:
    if not OUT_DIR.exists():
        OUT_DIR.mkdir(parents=True)
    existing_files = [path for path in OUT_DIR.rglob("*") if path.is_file()]
    unexpected_files = [path for path in existing_files if path.resolve() != VERIFIER.resolve()]
    completed_markers = {
        "final_metrics_summary.json",
        "final_repeated_evaluation_report.md",
        "policy_manual_review.csv",
        "multi_agent_raw.jsonl",
        "monolithic_raw.jsonl",
        "run_metrics.json",
    }
    incomplete_preflight_only = not any(
        path.name in completed_markers for path in unexpected_files
    )
    if unexpected_files and not incomplete_preflight_only:
        raise RuntimeError(
            "Refusing to overwrite an existing final_revision_2026 artifact set"
        )
    scenarios, metadata = validate_source_dataset()
    prepare_isolated_environment()
    preparation = freeze_and_record_environment(scenarios, metadata)
    print("DATASET AND ENVIRONMENT FROZEN", flush=True)
    for run_id in RUN_IDS:
        print(f"STARTING {run_id}", flush=True)
        result = subprocess.run(
            [sys.executable, str(Path(__file__).resolve()), "--worker-run", run_id],
            cwd=str(BASE_DIR),
            env=os.environ.copy(),
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        if result.stdout:
            print(result.stdout, end="" if result.stdout.endswith("\n") else "\n")
        if result.stderr:
            print(redact_text(result.stderr), file=sys.stderr)
        if result.returncode != 0:
            print(f"ABORTED: {run_id} returned exit code {result.returncode}", file=sys.stderr)
            return result.returncode
    all_records = read_all_run_records()
    flattened = []
    for run_id in RUN_IDS:
        flattened.extend(all_records[run_id]["multi_agent"])
        flattened.extend(all_records[run_id]["monolithic"])
    write_policy_review_csv(flattened)
    summary = build_final_summary(metadata, preparation, all_records)
    integrity = verify_production_db_after()
    verification_process = subprocess.run(
        [sys.executable, str(VERIFIER)],
        cwd=str(BASE_DIR),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    verification_output = redact_text((verification_process.stdout or "") + (verification_process.stderr or ""))
    verification = {
        "passed": verification_process.returncode == 0,
        "returncode": verification_process.returncode,
        "output": verification_output,
    }
    summary["verification"] = verification
    summary["production_db_integrity"] = integrity
    summary["source_hash_status"] = source_hash_status()
    json_write(FINAL_SUMMARY, summary)
    report = build_final_report(
        summary,
        preparation,
        verification,
        integrity,
        summary["source_hash_status"],
    )
    FINAL_REPORT.write_text(report, encoding="utf-8")
    print(verification_output, flush=True)
    print(f"FINAL REPORT: {FINAL_REPORT}", flush=True)
    return 0 if verification["passed"] else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Final three-run reproducibility evaluation")
    parser.add_argument("--worker-run", choices=RUN_IDS, default=None)
    args = parser.parse_args()
    if args.worker_run:
        return run_worker(args.worker_run)
    return run_master()


if __name__ == "__main__":
    raise SystemExit(main())
