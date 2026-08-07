"""
Task 4C-2A — Monolithic LLM Baseline (Smoke: 4 scenarios).

One unified DeepSeek call per customer message.
No Router, no specialized agents, no ChromaDB, no SQLite, no Policy Evaluator.
"""

import argparse
import json
import csv
import io
import re
import time
import statistics
import sys
import os
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Any, Tuple

# ── Project root ─────────────────────────────────────────────────────
BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

# ── Precondition check ──────────────────────────────────────────────
from app.config import DEEPSEEK_ENABLED, DEEPSEEK_API_KEY, DEEPSEEK_MODEL, LLM_CONFIG

preconditions_ok = True
missing = []
if not DEEPSEEK_ENABLED:
    missing.append("DEEPSEEK_ENABLED is not true")
    preconditions_ok = False
if not DEEPSEEK_API_KEY:
    missing.append("DEEPSEEK_API_KEY is missing/empty")
    preconditions_ok = False
expected_model = "deepseek-v4-flash"
if DEEPSEEK_MODEL != expected_model:
    missing.append(f"model is '{DEEPSEEK_MODEL}', expected '{expected_model}'")
    preconditions_ok = False

if not preconditions_ok:
    for m in missing:
        print(f"[PRECHECK FAIL] {m}")
    print("\nPrecondition(s) failed — exiting. Zero provider requests made.")
    sys.exit(1)

print(f"[PRECHECK OK] DEEPSEEK_ENABLED=true, model={DEEPSEEK_MODEL}, key present (not printed)")

# ── Imports ──────────────────────────────────────────────────────────
import openai

# ── Argparse ──────────────────────────────────────────────────────────
parser = argparse.ArgumentParser(description="Monolithic LLM Baseline")
group = parser.add_mutually_exclusive_group()
group.add_argument("--scenario-ids", nargs="*", default=None,
                    help="Space-separated scenario IDs to run")
group.add_argument("--limit", type=int, default=None,
                    help="Run first N scenarios")
parser.add_argument("--run-name", default="monolithic_baseline",
                    help="Name for output files")
args = parser.parse_args()

if not args.scenario_ids and args.limit is None:
    print("[ERROR] Neither --scenario-ids nor --limit provided. Specify one.")
    sys.exit(1)

# ── Load all scenarios ──────────────────────────────────────────────
ALL_SCENARIOS = []

def _load_json(path):
    with open(BASE_DIR / path, "r", encoding="utf-8") as f:
        return json.load(f)

# Load from each category file (all_scenarios.json is the combined one)
all_scenarios_path = BASE_DIR / "data" / "evaluation" / "all_scenarios.json"
with open(all_scenarios_path, "r", encoding="utf-8") as f:
    ALL_SCENARIOS = json.load(f)

# Build lookup
scenario_map = {s["scenario_id"]: s for s in ALL_SCENARIOS}

# Select scenarios
if args.scenario_ids:
    selected = []
    for sid in args.scenario_ids:
        if sid in scenario_map:
            selected.append(scenario_map[sid])
        else:
            print(f"[WARN] Scenario {sid} not found in all_scenarios.json — skipping")
    if not selected:
        print("[ERROR] No valid --scenario-ids found")
        sys.exit(1)
elif args.limit:
    selected = ALL_SCENARIOS[:args.limit]

print(f"[LOAD] {len(selected)} scenario(s) selected from {len(ALL_SCENARIOS)} total")

# ── Build unified knowledge context ──────────────────────────────────
# All 5 sample orders (from app/db/orders.py)
SAMPLE_ORDERS = [
    {"order_id": "ORD-1001", "customer_name": "สมหญิง ใจดี", "product_name": "เสื้อเชิ้ตผู้หญิง",
     "order_status": "shipped", "payment_status": "paid", "shipment_status": "in_transit",
     "tracking_number": "FLASH-TRK-1001", "shipping_provider": "Flash Express",
     "purchase_date": "2026-07-08", "estimated_delivery_date": "2026-07-14"},
    {"order_id": "ORD-1002", "customer_name": "มนัส ทรัพย์มั่นคง", "product_name": "หูฟัง Bluetooth",
     "order_status": "processing", "payment_status": "paid", "shipment_status": "pending_pickup",
     "tracking_number": "KERRY-TRK-1002", "shipping_provider": "Kerry Express",
     "purchase_date": "2026-07-10", "estimated_delivery_date": "2026-07-16"},
    {"order_id": "ORD-1003", "customer_name": "ณัฐวุฒิ รักดี", "product_name": "สมาร์ทโฟนรุ่น A พร้อมเคส",
     "order_status": "delivered", "payment_status": "paid", "shipment_status": "delivered",
     "tracking_number": "JNT-TRK-1003", "shipping_provider": "J&T Express",
     "purchase_date": "2026-07-01", "estimated_delivery_date": "2026-07-05"},
    {"order_id": "ORD-1004", "customer_name": "ปริญญา วงศ์ไชยา", "product_name": "โน้ตบุ๊ก",
     "order_status": "pending", "payment_status": "unpaid", "shipment_status": "not_shipped",
     "tracking_number": "", "shipping_provider": "",
     "purchase_date": "2026-07-10", "estimated_delivery_date": "รอการชำระเงิน"},
    {"order_id": "ORD-1005", "customer_name": "วิไล รักษ์ไทย", "product_name": "รองเท้าวิ่ง",
     "order_status": "return_requested", "payment_status": "paid", "shipment_status": "return_initiated",
     "tracking_number": "KERRY-TRK-1005", "shipping_provider": "Kerry Express",
     "purchase_date": "2026-06-28", "estimated_delivery_date": "N/A"},
]

# All 5 policy documents
POLICY_DOCS = {}
for fname in ["refund_policy.md", "return_policy.md", "exchange_policy.md",
              "shipping_policy.md", "payment_policy.md"]:
    fpath = BASE_DIR / "data" / "policies" / fname
    if fpath.exists():
        POLICY_DOCS[fname] = fpath.read_text(encoding="utf-8")

# Supported intent labels (from app/agents/router.py)
SUPPORTED_INTENTS = [
    "TRACKING_NUMBER", "PAYMENT_STATUS", "SHIPMENT_STATUS", "ORDER_STATUS",
    "RETURN_REFUND", "STORE_POLICY", "GREETING", "OUT_OF_SCOPE",
]
SUPPORTED_INTENTS_SET = set(SUPPORTED_INTENTS)

# ── Build the unified system prompt ─────────────────────────────────
ORDERS_BLOCK = "\n".join(
    f"  Order {o['order_id']}: status={o['order_status']}, "
    f"payment={o['payment_status']}, shipment={o['shipment_status']}, "
    f"tracking={o['tracking_number']}, carrier={o['shipping_provider']}, "
    f"product={o['product_name']}, customer={o['customer_name']}, "
    f"purchased={o['purchase_date']}, estimated_delivery={o['estimated_delivery_date']}"
    for o in SAMPLE_ORDERS
)

POLICIES_BLOCK = "\n---\n".join(
    f"### {fname}\n{content}" for fname, content in POLICY_DOCS.items()
)

SYSTEM_PROMPT = f"""You are the SiamCart Demo Store customer support assistant — a monolithic AI that handles ALL customer messages in ONE response.

You have access to the store's complete knowledge context below. Use ONLY this context to answer. Do not use general knowledge to invent store rules or order data.

─── SUPPORTED INTENT LABELS (use exactly one) ───

{json.dumps(SUPPORTED_INTENTS, indent=2)}

─── ALL STORE ORDERS ───

{ORDERS_BLOCK}

─── ALL STORE POLICIES ───

{POLICIES_BLOCK}

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

# ── Track parse method usage ────────────────────────────────────────
parse_method_counts = {"direct": 0, "code_fence": 0, "balanced_object": 0}
schema_validation_failures = 0

# ── Helpers ──────────────────────────────────────────────────────────
def extract_source_from_notes(notes):
    m = re.search(r"source=([^;]+)", notes or "")
    return m.group(1).strip() if m else None

def normalise_for_matching(text):
    t = text.lower()
    t = re.sub(r"\s+", " ", t)
    for ch in ["\u2010", "\u2011", "\u2012", "\u2013", "\u2014", "\u2212"]:
        t = t.replace(ch, "-")
    return t.strip()

def parse_facts(facts):
    result = {}
    for fact in facts or []:
        if "=" in fact:
            key, _, value = fact.partition("=")
            result[key.strip()] = value.strip()
    return result

def value_in_response(value, response):
    return normalise_for_matching(value) in normalise_for_matching(response)

def extract_policy_anchors(facts):
    anchors = {}
    for fact in facts or []:
        if "=" in fact:
            key, _, value = fact.partition("=")
            anchors[key.strip()] = value.strip()
    return anchors

def score_policy_anchors(anchors, actual_response):
    total = len(anchors)
    if total == 0:
        return 0, 0
    resp = normalise_for_matching(actual_response or "")
    matches = 0
    for key, expected_value in anchors.items():
        nv = normalise_for_matching(expected_value)
        if nv in resp:
            matches += 1
    return matches, total


def parse_json_from_response(text: str) -> Tuple[Optional[dict], str]:
    """Deterministic local JSON extraction. Three attempts in order.

    Returns (parsed_dict, method_name) where method_name is one of:
    'direct', 'code_fence', 'balanced_object', or 'failed'.
    """
    if not text:
        return None, "failed"
    text = text.strip()

    # 1. Direct json.loads
    try:
        obj = json.loads(text)
        parse_method_counts["direct"] += 1
        return obj, "direct"
    except json.JSONDecodeError:
        pass

    # 2. Remove a single ```json ... ``` or ``` ... ``` wrapper and parse
    m = re.match(r"^```(?:json)?\s*\n?(.*?)```\s*$", text, re.DOTALL)
    if m:
        try:
            obj = json.loads(m.group(1).strip())
            parse_method_counts["code_fence"] += 1
            return obj, "code_fence"
        except json.JSONDecodeError:
            pass

    # 3. Locate and parse the first balanced top-level JSON object
    #    while correctly handling quoted strings and escaped quotation marks.
    brace_depth = 0
    start = None
    in_string = False
    escape = False
    for i, ch in enumerate(text):
        if escape:
            escape = False
            continue
        if ch == '\\':
            escape = True
            continue
        if ch == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch == '{':
            if brace_depth == 0:
                start = i
            brace_depth += 1
        elif ch == '}':
            brace_depth -= 1
            if brace_depth == 0 and start is not None:
                try:
                    obj = json.loads(text[start:i+1])
                    parse_method_counts["balanced_object"] += 1
                    return obj, "balanced_object"
                except json.JSONDecodeError:
                    # Reset and continue scanning for another possible object
                    start = None

    return None, "failed"


def validate_schema(parsed: dict) -> Tuple[bool, str]:
    """Validate parsed JSON against expected schema.

    Required fields:
      intent (str, one of SUPPORTED_INTENTS_SET)
      response (str)
      requires_clarification (bool)
      simulated_human_review (bool)
      extracted_entities (dict)
      cited_sources (list)
      confidence (numeric, 0-1)

    Returns (is_valid, error_msg).
    """
    global schema_validation_failures
    required = ["intent", "response", "requires_clarification",
                 "simulated_human_review", "extracted_entities",
                 "cited_sources", "confidence"]

    for field in required:
        if field not in parsed:
            schema_validation_failures += 1
            return False, f"missing_field:{field}"

    if parsed["intent"] not in SUPPORTED_INTENTS_SET:
        schema_validation_failures += 1
        return False, f"invalid_intent:{parsed['intent']}"

    if not isinstance(parsed["response"], str):
        schema_validation_failures += 1
        return False, "response_not_string"

    if not isinstance(parsed["requires_clarification"], bool):
        schema_validation_failures += 1
        return False, "requires_clarification_not_boolean"

    if not isinstance(parsed["simulated_human_review"], bool):
        schema_validation_failures += 1
        return False, "simulated_human_review_not_boolean"

    if not isinstance(parsed["extracted_entities"], dict):
        schema_validation_failures += 1
        return False, "extracted_entities_not_object"

    if not isinstance(parsed["cited_sources"], list):
        schema_validation_failures += 1
        return False, "cited_sources_not_array"

    conf = parsed["confidence"]
    if not isinstance(conf, (int, float)):
        schema_validation_failures += 1
        return False, "confidence_not_numeric"
    if conf < 0 or conf > 1:
        schema_validation_failures += 1
        return False, f"confidence_out_of_range:{conf}"

    return True, ""


def call_deepseek(messages, timeout=60):
    """Single DeepSeek provider call. Returns (content, latency_ms, error_type)."""
    start = time.time()
    try:
        client = openai.OpenAI(
            api_key=LLM_CONFIG["api_key"],
            base_url=LLM_CONFIG.get("base_url", "https://api.deepseek.com"),
            timeout=timeout,
        )
        response = client.chat.completions.create(
            model=LLM_CONFIG["model"],
            temperature=0.1,
            max_tokens=1024,
            messages=messages,
            timeout=timeout,
        )
        latency = round((time.time() - start) * 1000, 2)
        content = (response.choices[0].message.content or "").strip()
        if not content:
            return None, latency, "empty_output"
        return content, latency, None
    except openai.APITimeoutError:
        lat = round((time.time() - start) * 1000, 2)
        return None, lat, "timeout"
    except openai.APIStatusError:
        lat = round((time.time() - start) * 1000, 2)
        return None, lat, "http_error"
    except openai.APIConnectionError:
        lat = round((time.time() - start) * 1000, 2)
        return None, lat, "connection_error"
    except Exception:
        lat = round((time.time() - start) * 1000, 2)
        return None, lat, "unknown"


def score_transaction_facts(expected_facts, record):
    """Score transaction facts against response text (no structured evidence in baseline)."""
    facts = parse_facts(expected_facts)
    total = len(facts)
    if total == 0:
        return 0, 0
    actual_response = record.get("baseline_response") or ""
    matches = 0
    for key, expected_value in facts.items():
        if key == "order_not_found" and expected_value == "true":
            resp_lower = normalise_for_matching(actual_response)
            if any(p in resp_lower for p in [
                "not found", "no information", "ไม่พบข้อมูล", "ไม่พบคำสั่งซื้อ",
            ]):
                matches += 1
                continue
        # Check entity in extracted_entities
        entities = record.get("baseline_entities") or {}
        if key in entities:
            if str(entities[key]) == expected_value:
                matches += 1
                continue
        # Fallback: response text matching
        if value_in_response(expected_value, actual_response):
            matches += 1
            continue
    return matches, total


# ── Main evaluation loop ─────────────────────────────────────────────
records = []
total_start = time.time()

for idx, sc in enumerate(selected, start=1):
    sid = sc["scenario_id"]
    category = sc.get("category", "unknown")
    user_msg = sc["user_message"]

    print(f"[{idx}/{len(selected)}] {sid} ({category}) — sending monolithic request...")

    # Build the user message for this scenario
    user_prompt = f"Customer message: {user_msg}"

    # Record skeleton
    record = {
        "scenario_id": sid,
        "category": category,
        "language": sc.get("language", ""),
        "input_type": sc.get("input_type", ""),
        "user_message": user_msg,
        "expected_intent": sc.get("expected_intent", ""),
        "expected_agent": sc.get("expected_agent", ""),
        "expected_entities": sc.get("expected_entities", {}),
        "expected_requires_clarification": sc.get("requires_clarification", False),
        "expected_simulated_human_review": sc.get("simulated_human_review", False),
        "expected_answer_facts": sc.get("expected_answer_facts", []),
        "baseline_intent": "",
        "baseline_agent": "monolithic_llm_baseline",
        "baseline_response": "",
        "baseline_entities": {},
        "baseline_requires_clarification": False,
        "baseline_simulated_human_review": False,
        "baseline_cited_sources": [],
        "baseline_confidence": 0.0,
        "provider_request_count": 0,
        "provider_latency_ms": 0.0,
        "total_latency_ms": 0.0,
        "raw_provider_response": "",
        "json_parse_success": False,
        "parse_method": "",
        "schema_valid": False,
        "execution_status": "pending",
        "execution_error": None,
        "intent_correct": False,
        "clarification_flag_correct": False,
        "human_review_flag_correct": False,
        "transaction_fact_matches": 0,
        "transaction_fact_total": 0,
        "transaction_fact_score": 0.0,
        "transaction_answer_correct": False,
        "policy_anchor_matches": 0,
        "policy_anchor_total": 0,
        "policy_anchor_score": 0.0,
        "manual_review_required": False,
    }

    scenario_start = time.time()

    try:
        # === One provider request per scenario ===
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ]
        content, provider_latency, error_type = call_deepseek(messages)
        total_latency = round((time.time() - scenario_start) * 1000, 2)

        record["provider_latency_ms"] = provider_latency or 0.0
        record["total_latency_ms"] = total_latency
        record["raw_provider_response"] = content or ""
        record["provider_request_count"] = 1 if content is not None or error_type else 0

        if error_type:
            record["execution_status"] = "error"
            record["execution_error"] = error_type
            print(f"  ✗ Provider error: {error_type}")
            records.append(record)
            continue

        # Parse JSON from response (deterministic local, no LLM repair)
        parsed, parse_method = parse_json_from_response(content)
        record["parse_method"] = parse_method

        if parsed is None:
            record["execution_status"] = "error"
            record["execution_error"] = "json_parse_failed"
            print(f"  ✗ JSON parse failed from response: {content[:200]}...")
            records.append(record)
            continue

        # Schema validation (no semantic repair, no expected_answer injection)
        schema_ok, schema_error = validate_schema(parsed)
        record["schema_valid"] = schema_ok

        if not schema_ok:
            record["execution_status"] = "error"
            record["execution_error"] = schema_error
            print(f"  ✗ Schema validation failed: {schema_error}")
            records.append(record)
            continue

        # Schema passed — populate record
        record["json_parse_success"] = True
        record["baseline_intent"] = parsed.get("intent", "")
        record["baseline_response"] = parsed.get("response", "")
        record["baseline_entities"] = parsed.get("extracted_entities", {})
        record["baseline_requires_clarification"] = parsed.get("requires_clarification", False)
        record["baseline_simulated_human_review"] = parsed.get("simulated_human_review", False)
        record["baseline_cited_sources"] = parsed.get("cited_sources", [])
        record["baseline_confidence"] = parsed.get("confidence", 0.0)
        record["execution_status"] = "success"

        # ── Scoring ────────────────────────────────────────────────
        expected_intent = sc.get("expected_intent", "")
        predicted_intent = record["baseline_intent"]

        # Intent correctness
        record["intent_correct"] = (predicted_intent == expected_intent)

        # Clarification flag
        expected_clarify = sc.get("requires_clarification", False)
        actual_clarify = record["baseline_requires_clarification"]
        record["clarification_flag_correct"] = (actual_clarify == expected_clarify)

        # Human review flag
        expected_human = sc.get("simulated_human_review", False)
        actual_human = record["baseline_simulated_human_review"]
        record["human_review_flag_correct"] = (actual_human == expected_human)

        # Transaction fact scoring
        if category == "transaction":
            exp_facts = sc.get("expected_answer_facts", [])
            matches, total = score_transaction_facts(exp_facts, record)
            record["transaction_fact_matches"] = matches
            record["transaction_fact_total"] = total
            record["transaction_fact_score"] = matches / total if total > 0 else 0.0
            record["transaction_answer_correct"] = (record["transaction_fact_score"] == 1.0)

        # Policy anchor scoring
        if category == "policy":
            exp_facts = sc.get("expected_answer_facts", [])
            anchors = extract_policy_anchors(exp_facts)
            anchor_matches, anchor_total = score_policy_anchors(
                anchors, record["baseline_response"]
            )
            record["policy_anchor_matches"] = anchor_matches
            record["policy_anchor_total"] = anchor_total
            record["policy_anchor_score"] = (
                anchor_matches / anchor_total if anchor_total > 0 else 0.0
            )
            record["manual_review_required"] = True

        print(f"  ✓ intent={predicted_intent}, clarify={actual_clarify}, "
              f"human_review={actual_human}, json_ok=True, parse={parse_method}, "
              f"provider_lat={record['provider_latency_ms']:.0f}ms")

    except Exception as e:
        total_latency = round((time.time() - scenario_start) * 1000, 2)
        record["total_latency_ms"] = total_latency
        record["execution_status"] = "error"
        record["execution_error"] = str(e)
        print(f"  ✗ Exception: {e}")

    records.append(record)

total_runtime = round(time.time() - total_start, 2)

# ── Write outputs ────────────────────────────────────────────────────
run_name = args.run_name.replace(" ", "_")
results_dir = BASE_DIR / "data" / "evaluation" / "results"
results_dir.mkdir(parents=True, exist_ok=True)

# JSONL
jsonl_path = results_dir / f"{run_name}.jsonl"
with open(jsonl_path, "w", encoding="utf-8") as f:
    for r in records:
        f.write(json.dumps(r, ensure_ascii=False) + "\n")

# CSV with BOM
csv_path = results_dir / f"{run_name}.csv"
fieldnames = [
    "scenario_id", "category", "language", "input_type", "user_message",
    "expected_intent", "expected_agent", "expected_entities",
    "expected_requires_clarification", "expected_simulated_human_review",
    "expected_answer_facts",
    "baseline_intent", "baseline_agent", "baseline_response",
    "baseline_entities", "baseline_requires_clarification",
    "baseline_simulated_human_review", "baseline_cited_sources",
    "baseline_confidence",
    "provider_request_count", "provider_latency_ms", "total_latency_ms",
    "raw_provider_response", "json_parse_success",
    "parse_method", "schema_valid",
    "execution_status", "execution_error",
    "intent_correct", "clarification_flag_correct", "human_review_flag_correct",
    "transaction_fact_matches", "transaction_fact_total", "transaction_fact_score",
    "transaction_answer_correct",
    "policy_anchor_matches", "policy_anchor_total", "policy_anchor_score",
    "manual_review_required",
]
with open(csv_path, "w", encoding="utf-8-sig", newline="") as f:
    writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
    writer.writeheader()
    for r in records:
        row = dict(r)
        for key in ["expected_entities", "expected_answer_facts",
                     "baseline_entities", "baseline_cited_sources"]:
            val = row.get(key)
            if isinstance(val, (list, dict)):
                row[key] = json.dumps(val, ensure_ascii=False)
        writer.writerow(row)

# ── Summary ──────────────────────────────────────────────────────────
success_count = sum(1 for r in records if r["execution_status"] == "success")
error_count = sum(1 for r in records if r["execution_status"] == "error")
total_reqs = sum(r["provider_request_count"] for r in records)
json_ok_count = sum(1 for r in records if r["json_parse_success"])
intent_correct_count = sum(1 for r in records if r["intent_correct"])
clarify_correct_count = sum(1 for r in records if r["clarification_flag_correct"])
human_correct_count = sum(1 for r in records if r["human_review_flag_correct"])
all_latencies = [r["provider_latency_ms"] for r in records if r["provider_latency_ms"] > 0]
all_total_latencies = [r["total_latency_ms"] for r in records]

predicted_intents = [f"{r['scenario_id']}={r['baseline_intent']}" for r in records]

# Clarification outputs
clarify_records = [r for r in records if r["scenario_id"].startswith("CLARIFY")]
clarify_responses = {r["scenario_id"]: r["baseline_response"] for r in clarify_records}

print()
print("=" * 72)
print("  TASK 4C-2A — MONOLITHIC LLM BASELINE (SMOKE 4) SUMMARY")
print("=" * 72)
print(f"  Selected count:                       {len(selected)}")
print(f"  Success count:                        {success_count}")
print(f"  Error count:                          {error_count}")
print(f"  Provider-request count:               {total_reqs}")
print(f"  JSON parse-success count:             {json_ok_count}")
print(f"  Direct JSON parse count:              {parse_method_counts['direct']}")
print(f"  Code-fence extraction count:          {parse_method_counts['code_fence']}")
print(f"  Balanced-object extraction count:     {parse_method_counts['balanced_object']}")
print(f"  Schema-validation failure count:      {schema_validation_failures}")
print(f"  Intent-correct count:                 {intent_correct_count} / {len(selected)}")
print(f"  Clarification flag-correct count:     {clarify_correct_count} / {len(selected)}")
print(f"  Human-review flag-correct count:      {human_correct_count} / {len(selected)}")
print(f"  Total runtime:                        {total_runtime:.2f}s")
if all_latencies:
    print(f"  Average provider latency:             {statistics.mean(all_latencies):.0f}ms")
if all_total_latencies:
    avg_total = statistics.mean(all_total_latencies)
    print(f"  Average total latency:                {avg_total:.0f}ms")
print(f"  Predicted intents:                    {', '.join(predicted_intents)}")
if clarify_responses:
    for cid, cresp in clarify_responses.items():
        print(f"  Clarification output {cid}:          \"{cresp[:100]}\"")
print(f"  JSONL output:                          {jsonl_path}")
print(f"  CSV output:                            {csv_path}")
print(f"  Configured model:                      {DEEPSEEK_MODEL}")
print(f"  Provider requests per scenario:        max 1 — enforced (no retry path)")
print(f"  API key printed or in command:         NO — key read from env only, never printed")
print()
print("  PROHIBITED COMPONENTS CHECK:")
print("  • Router (deterministic):              NOT invoked")
print("  • Specialized agents:                  NOT invoked")
print("  • SQLite runtime query:                NOT invoked")
print("  • ChromaDB retrieval:                  NOT invoked")
print("  • SentenceTransformer embeddings:      NOT invoked")
print("  • Policy Evaluator:                    NOT invoked")
print("  • Production Orchestrator:             NOT invoked")
print("  • Network services other than DeepSeek: NOT invoked")
print("  • JSON-repair API calls:               NOT attempted")
print("  • LLM-based parsing repair:            NOT attempted")
print("  • expected_intent injection:           NOT attempted")
print("  [KEY CHECK] API key was not printed.")
print()
print("  NEXT TASK: 4C-2B Run Complete Monolithic Baseline.")
print()
