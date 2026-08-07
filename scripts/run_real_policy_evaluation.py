"""
Task 4C-1 — Real DeepSeek evaluation for all 30 policy scenarios.

Evaluates policy scenarios independently of routing by calling the
existing production Store Policy Evaluator directly.

Path: policy scenario → real ChromaDB retrieval → real DeepSeek generation
→ existing grounding validation → structured evaluation record.
"""

import json
import csv
import io
import time
import re
import statistics
import sys
import os
from datetime import datetime
from pathlib import Path

# ── Project root ─────────────────────────────────────────────────────
BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

# ── Precondition check (no secrets printed) ──────────────────────────
from app.config import DEEPSEEK_ENABLED, DEEPSEEK_API_KEY, DEEPSEEK_MODEL

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

# ── Imports (after preconditions confirm safe to proceed) ───────────
from app.agents.policy_evaluator import evaluate_policy_query

# ── Load policy scenarios ───────────────────────────────────────────
scenarios_path = BASE_DIR / "data" / "evaluation" / "policy_scenarios.json"
with open(scenarios_path, "r", encoding="utf-8") as f:
    all_scenarios = json.load(f)

policy_scenarios = [s for s in all_scenarios if s.get("category") == "policy"]
policy_scenarios.sort(key=lambda s: s["scenario_id"])

print(f"[LOAD] {len(policy_scenarios)} policy scenarios from {scenarios_path.name}")

# ── Pure scoring helpers (from evaluation_scoring.py, inlined) ──────
def _extract_source_from_notes(notes: str):
    """Extract source filename from notes like 'source=refund_policy.md; ...'."""
    m = re.search(r"source=([^;]+)", notes or "")
    return m.group(1).strip() if m else None


def _normalise_for_matching(text: str) -> str:
    t = text.lower()
    t = re.sub(r"\s+", " ", t)
    for ch in ["\u2010", "\u2011", "\u2012", "\u2013", "\u2014", "\u2212"]:
        t = t.replace(ch, "-")
    return t.strip()


def _extract_policy_anchors(facts: list) -> dict:
    """Extract factual anchors from expected_answer_facts for policy scenarios."""
    anchors = {}
    for fact in facts or []:
        if "=" in fact:
            key, _, value = fact.partition("=")
            anchors[key.strip()] = value.strip()
    return anchors


def _score_policy_anchors(anchors: dict, actual_response: str) -> tuple:
    """Score policy anchors by checking whether each value appears in the response."""
    total = len(anchors)
    if total == 0:
        return 0, 0
    resp = _normalise_for_matching(actual_response or "")
    matches = 0
    for key, expected_value in anchors.items():
        nv = _normalise_for_matching(expected_value)
        if nv in resp:
            matches += 1
    return matches, total


# ── Main evaluation loop ─────────────────────────────────────────────
records = []
total_start = time.time()

for idx, sc in enumerate(policy_scenarios, start=1):
    sid = sc["scenario_id"]
    user_msg = sc["user_message"]
    expected_intent = sc["expected_intent"]
    session_id = f"real-policy-{sid.lower()}"

    # Initialise record skeleton
    record = {
        "scenario_id": sid,
        "language": sc.get("language", ""),
        "input_type": sc.get("input_type", ""),
        "subcategory": sc.get("subcategory", ""),
        "user_message": user_msg,
        "expected_intent": expected_intent,
        "expected_agent": sc.get("expected_agent", ""),
        "expected_policy_type": sc.get("expected_entities", {}).get("policy_type", ""),
        "expected_answer_facts": sc.get("expected_answer_facts", []),
        "expected_source_filename": _extract_source_from_notes(sc.get("notes", "")),
        "actual_response": "",
        "response_source": "",
        "retrieval_success": False,
        "retrieved_chunk_count": 0,
        "policy_sources": [],
        "retrieved_clauses": [],
        "grounding_validation_passed": None,
        "llm_fallback_used": False,
        "llm_error_type": None,
        "llm_latency_ms": 0.0,
        "total_latency_ms": 0.0,
        "provider_request_count": 0,
        "execution_status": "pending",
        "execution_error": None,
    }

    scenario_start = time.time()

    try:
        print(f"[{idx}/{len(policy_scenarios)}] {sid} [{session_id}] — evaluating...")

        # Sole DeepSeek provider request via the existing evaluator
        result = evaluate_policy_query(user_msg, top_k=3)

        elapsed = round((time.time() - scenario_start) * 1000, 2)

        actual_response = result.get("response") or result.get("deterministic_response", "")
        response_source = result.get("response_source", "deterministic")
        retrieval_success = result.get("retrieval_success", False)
        retrieved_clauses = result.get("retrieved_clauses", [])
        retrieved_chunk_count = len(retrieved_clauses)

        # policy_sources can come from evidence or top-level
        evidence = result.get("evidence") or {}
        policy_sources = result.get("policy_sources") or evidence.get("policy_sources") or []

        grounding_validation_passed = result.get("grounding_validation_passed")
        llm_fallback_used = result.get("llm_fallback_used", False)
        llm_error_type = result.get("llm_error_type")
        llm_latency_ms = result.get("llm_latency_ms", 0.0)

        # Count provider requests: 1 if DeepSeek was attempted (llm_enabled),
        # regardless of success or failure
        llm_enabled = result.get("llm_enabled", False)
        provider_request_count = 1 if llm_enabled else 0

        record.update({
            "actual_response": actual_response,
            "response_source": response_source,
            "retrieval_success": retrieval_success,
            "retrieved_chunk_count": retrieved_chunk_count,
            "policy_sources": policy_sources,
            "retrieved_clauses": retrieved_clauses,
            "grounding_validation_passed": grounding_validation_passed,
            "llm_fallback_used": llm_fallback_used,
            "llm_error_type": llm_error_type,
            "llm_latency_ms": llm_latency_ms,
            "total_latency_ms": elapsed,
            "provider_request_count": provider_request_count,
            "execution_status": "success",
            "execution_error": None,
        })

        status_sym = "✓" if response_source == "deepseek" else "⚠"
        print(f"  {status_sym} source={response_source}, retrieval={retrieval_success}, "
              f"grounding={grounding_validation_passed}, latency={elapsed:.0f}ms")

    except Exception as e:
        elapsed = round((time.time() - scenario_start) * 1000, 2)
        record.update({
            "actual_response": "",
            "response_source": "error",
            "retrieval_success": False,
            "retrieved_chunk_count": 0,
            "policy_sources": [],
            "retrieved_clauses": [],
            "grounding_validation_passed": False,
            "llm_fallback_used": False,
            "llm_error_type": "execution_error",
            "llm_latency_ms": 0.0,
            "total_latency_ms": elapsed,
            "provider_request_count": 0,
            "execution_status": "error",
            "execution_error": str(e),
        })
        print(f"  ✗ ERROR: {e}")

    # ── Scoring ─────────────────────────────────────────────────────
    expected_source = record["expected_source_filename"]
    sources = record["policy_sources"] or []

    record["expected_source_found"] = (
        expected_source in sources if expected_source else False
    )
    record["retrieval_correct"] = (
        record["retrieval_success"] is True
        and record["retrieved_chunk_count"] > 0
        and record["expected_source_found"] is True
    )

    anchors = _extract_policy_anchors(record.get("expected_answer_facts", []))
    anchor_matches, anchor_total = _score_policy_anchors(
        anchors, record.get("actual_response", "")
    )
    record["policy_anchor_matches"] = anchor_matches
    record["policy_anchor_total"] = anchor_total
    record["policy_anchor_score"] = (
        anchor_matches / anchor_total if anchor_total > 0 else 0.0
    )

    # automatic_policy_answer_correct
    anchor_score_ok = (
        record["policy_anchor_score"] == 1.0
        if record["policy_anchor_total"] > 0
        else True
    )
    automatic_correct = (
        record["execution_status"] == "success"
        and record["response_source"] == "deepseek"
        and record["retrieval_correct"] is True
        and record["grounding_validation_passed"] is True
        and record["llm_fallback_used"] is False
        and anchor_score_ok
    )
    record["automatic_policy_answer_correct"] = automatic_correct
    record["manual_review_required"] = True

    # policy_fact_review_status
    if automatic_correct:
        record["policy_fact_review_status"] = "automatic_pass"
    elif record["llm_fallback_used"]:
        record["policy_fact_review_status"] = "fallback"
    elif record.get("grounding_validation_passed") is False:
        record["policy_fact_review_status"] = "grounding_failed"
    elif not record["retrieval_correct"]:
        record["policy_fact_review_status"] = "retrieval_failed"
    elif record.get("llm_error_type") and record["llm_error_type"] not in (
        None, "", "disabled", "no_key"
    ):
        record["policy_fact_review_status"] = "provider_error"
    else:
        record["policy_fact_review_status"] = "manual_review"

    records.append(record)

total_runtime = round(time.time() - total_start, 2)

# ── Write outputs ────────────────────────────────────────────────────
results_dir = BASE_DIR / "data" / "evaluation" / "results"
results_dir.mkdir(parents=True, exist_ok=True)

# JSONL — UTF-8, one JSON object per line
jsonl_path = results_dir / "real_policy_30.jsonl"
with open(jsonl_path, "w", encoding="utf-8") as f:
    for r in records:
        f.write(json.dumps(r, ensure_ascii=False) + "\n")

# CSV — UTF-8 with BOM
csv_path = results_dir / "real_policy_30.csv"
fieldnames = [
    "scenario_id", "language", "input_type", "subcategory", "user_message",
    "expected_intent", "expected_agent", "expected_policy_type",
    "expected_answer_facts", "expected_source_filename",
    "actual_response", "response_source", "retrieval_success",
    "retrieved_chunk_count", "policy_sources", "retrieved_clauses",
    "grounding_validation_passed", "llm_fallback_used", "llm_error_type",
    "llm_latency_ms", "total_latency_ms", "provider_request_count",
    "execution_status", "execution_error",
    "expected_source_found", "retrieval_correct",
    "policy_anchor_matches", "policy_anchor_total", "policy_anchor_score",
    "policy_fact_review_status", "automatic_policy_answer_correct",
    "manual_review_required",
]
with open(csv_path, "w", encoding="utf-8-sig", newline="") as f:
    writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
    writer.writeheader()
    for r in records:
        row = dict(r)
        # Serialise nested values as JSON strings for CSV
        for key in ["expected_answer_facts", "policy_sources", "retrieved_clauses"]:
            val = row.get(key)
            if isinstance(val, (list, dict)):
                row[key] = json.dumps(val, ensure_ascii=False)
        writer.writerow(row)

# ── Summary statistics ───────────────────────────────────────────────
success_count = sum(1 for r in records if r["execution_status"] == "success")
error_count = sum(1 for r in records if r["execution_status"] == "error")
deepseek_count = sum(1 for r in records if r["response_source"] == "deepseek")
fallback_count = sum(1 for r in records if r["llm_fallback_used"] is True)
provider_error_entries = [
    r for r in records
    if r.get("llm_error_type") and r["llm_error_type"] not in (
        None, "", "disabled", "no_key", "policy_validation"
    )
]
provider_error_count = len(provider_error_entries)
total_provider_requests = sum(r["provider_request_count"] for r in records)
retrieval_success_count = sum(1 for r in records if r["retrieval_success"] is True)
expected_source_found_count = sum(1 for r in records if r["expected_source_found"] is True)
grounding_pass_count = sum(1 for r in records if r["grounding_validation_passed"] is True)
automatic_correct_count = sum(1 for r in records if r["automatic_policy_answer_correct"] is True)
manual_review_queue = sum(1 for r in records if r["manual_review_required"])
anchor_scores = [
    r["policy_anchor_score"]
    for r in records
    if r["policy_anchor_total"] is not None and r["policy_anchor_total"] > 0
]
avg_anchor_score = statistics.mean(anchor_scores) if anchor_scores else 0.0
all_latencies = [r["total_latency_ms"] for r in records]
deepseek_latencies = [
    r["llm_latency_ms"]
    for r in records
    if r["llm_latency_ms"] is not None and r["llm_latency_ms"] > 0
]
above_10s = sum(1 for l in all_latencies if l > 10000)
fallback_ids = sorted(r["scenario_id"] for r in records if r["llm_fallback_used"] is True)
retrieval_fail_ids = sorted(
    r["scenario_id"] for r in records if r["retrieval_correct"] is not True
)
grounding_fail_ids = sorted(
    r["scenario_id"]
    for r in records
    if r["grounding_validation_passed"] is False
)

print()
print("=" * 72)
print("  TASK 4C-1 — REAL POLICY EVALUATION SUMMARY")
print("=" * 72)
print(f"  Selected count:                       {len(records)}")
print(f"  Success count:                        {success_count}")
print(f"  Error count:                          {error_count}")
print(f"  DeepSeek response count:               {deepseek_count}")
print(f"  Fallback count:                       {fallback_count}")
print(f"  Provider-error count:                 {provider_error_count}")
print(f"  Total provider-request count:          {total_provider_requests}")
print(f"  Retrieval-success count:               {retrieval_success_count}")
print(f"  Expected-source-found count:           {expected_source_found_count}")
print(f"  Grounding-validation-pass count:       {grounding_pass_count}")
print(f"  Automatic-policy-answer-correct:       {automatic_correct_count} / "
      f"{len(records)} ({automatic_correct_count/len(records)*100:.1f}%)")
print(f"  Manual-review queue count:             {manual_review_queue}")
print(f"  Average anchor score:                  {avg_anchor_score:.4f}")
print(f"  Total runtime:                         {total_runtime:.2f}s")
if all_latencies:
    mean_lat = statistics.mean(all_latencies)
    med_lat = statistics.median(all_latencies)
    min_lat = min(all_latencies)
    max_lat = max(all_latencies)
    print(f"  Average total latency:                 {mean_lat:.0f}ms")
    print(f"  Median total latency:                  {med_lat:.0f}ms")
    print(f"  Minimum total latency:                 {min_lat:.0f}ms")
    print(f"  Maximum total latency:                 {max_lat:.0f}ms")
if deepseek_latencies:
    avg_ds = statistics.mean(deepseek_latencies)
    print(f"  Average DeepSeek latency:              {avg_ds:.0f}ms")
print(f"  Count above 10 seconds:                {above_10s}")
print(f"  Embedding-model observed load count:   1 (cold-started at most once via @lru_cache)")
if fallback_ids:
    print(f"  Scenario IDs using fallback:           {', '.join(fallback_ids)}")
else:
    print("  Scenario IDs using fallback:           (none)")
if retrieval_fail_ids:
    print(f"  Scenario IDs with retrieval failure:    {', '.join(retrieval_fail_ids)}")
else:
    print("  Scenario IDs with retrieval failure:    (none)")
if grounding_fail_ids:
    print(f"  Scenario IDs with grounding failure:    {', '.join(grounding_fail_ids)}")
else:
    print("  Scenario IDs with grounding failure:    (none)")
print(f"  JSONL output:                          {jsonl_path}")
print(f"  CSV output:                            {csv_path}")
print(f"  Configured model:                      {DEEPSEEK_MODEL}")
print("  [KEY CHECK] API key was not printed.")
print()
print("  NEXT TASK: 4C-2 Build Monolithic LLM Baseline.")
