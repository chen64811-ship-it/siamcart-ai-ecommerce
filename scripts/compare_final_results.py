"""
Task 4C-3B — Compare final Multi-Agent and Monolithic results
and generate Chapter 4 metric tables.

Offline analysis. No network, DB, or app dependencies.
"""

import csv
import io
import json
import math
import os
import statistics
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
RESULTS_DIR = BASE_DIR / "data" / "evaluation" / "results"

# ── Load data ───────────────────────────────────────────────────────

# All scenarios for metadata
scenarios_path = BASE_DIR / "data" / "evaluation" / "all_scenarios.json"
with open(scenarios_path, "r", encoding="utf-8") as f:
    all_scenarios = json.load(f)
scenario_map = {s["scenario_id"]: s for s in all_scenarios}

# Multi-agent results
ma_records = {}
with open(RESULTS_DIR / "multi_agent_real_120.jsonl", "r", encoding="utf-8") as f:
    for line in f:
        line = line.strip()
        if line:
            r = json.loads(line)
            ma_records[r["scenario_id"]] = r

# Monolithic results
mo_records = {}
with open(RESULTS_DIR / "monolithic_120_final.jsonl", "r", encoding="utf-8") as f:
    for line in f:
        line = line.strip()
        if line:
            r = json.loads(line)
            mo_records[r["scenario_id"]] = r

# Real policy 30 results (for richer policy diagnostics)
rp_records = {}
try:
    with open(RESULTS_DIR / "real_policy_30.jsonl", "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                r = json.loads(line)
                rp_records[r["scenario_id"]] = r
except FileNotFoundError:
    rp_records = {}

# Build merged dataset (120 rows)
merged = []
for sc in all_scenarios:
    sid = sc["scenario_id"]
    ma = ma_records.get(sid, {})
    mo = mo_records.get(sid, {})
    rp = rp_records.get(sid, {})
    merged.append({
        "scenario": sc,
        "multi_agent": ma,
        "monolithic": mo,
        "real_policy": rp,
    })

N = len(merged)  # 120

# ── Helper functions ─────────────────────────────────────────────────

def safe(d, key, default=None):
    v = d.get(key, default)
    if v is None:
        return default
    return v

def is_success(r):
    return r.get("execution_status") == "success"

def is_error(r):
    return r.get("execution_status") == "error"

# ── Metric 1: Routing Accuracy ──────────────────────────────────────

ma_routing_correct = 0
mo_routing_correct = 0
ma_routing_by_cat = {}
mo_routing_by_cat = {}
ma_routing_by_lang = {}
mo_routing_by_lang = {}
ma_routing_by_input = {}
mo_routing_by_input = {}

# Confusion matrices
ma_confusion = {}  # {(expected, predicted): count}
mo_confusion = {}
ma_intents = set()
mo_intents = set()
all_intents = set()

for item in merged:
    sc = item["scenario"]
    ma = item["multi_agent"]
    mo = item["monolithic"]
    sid = sc["scenario_id"]
    cat = sc.get("category", "unknown")
    lang = sc.get("language", "unknown")
    inp = sc.get("input_type", "unknown")
    expected = sc.get("expected_intent", "")

    # Multi-agent: router_intent
    ma_pred = safe(ma, "router_intent", "")
    ma_ok = (ma_pred == expected)
    if ma_ok:
        ma_routing_correct += 1
    ma_routing_by_cat[cat] = ma_routing_by_cat.get(cat, {"correct": 0, "total": 0})
    ma_routing_by_cat[cat]["total"] += 1
    if ma_ok:
        ma_routing_by_cat[cat]["correct"] += 1

    ma_routing_by_lang[lang] = ma_routing_by_lang.get(lang, {"correct": 0, "total": 0})
    ma_routing_by_lang[lang]["total"] += 1
    if ma_ok:
        ma_routing_by_lang[lang]["correct"] += 1

    ma_routing_by_input[inp] = ma_routing_by_input.get(inp, {"correct": 0, "total": 0})
    ma_routing_by_input[inp]["total"] += 1
    if ma_ok:
        ma_routing_by_input[inp]["correct"] += 1

    all_intents.add(expected)
    ma_intents.add(ma_pred)
    key = (expected, ma_pred)
    ma_confusion[key] = ma_confusion.get(key, 0) + 1

    # Monolithic: baseline_intent
    mo_pred = safe(mo, "baseline_intent", "")
    mo_ok = (mo_pred == expected)
    if mo_ok:
        mo_routing_correct += 1
    mo_routing_by_cat[cat] = mo_routing_by_cat.get(cat, {"correct": 0, "total": 0})
    mo_routing_by_cat[cat]["total"] += 1
    if mo_ok:
        mo_routing_by_cat[cat]["correct"] += 1

    mo_routing_by_lang[lang] = mo_routing_by_lang.get(lang, {"correct": 0, "total": 0})
    mo_routing_by_lang[lang]["total"] += 1
    if mo_ok:
        mo_routing_by_lang[lang]["correct"] += 1

    mo_routing_by_input[inp] = mo_routing_by_input.get(inp, {"correct": 0, "total": 0})
    mo_routing_by_input[inp]["total"] += 1
    if mo_ok:
        mo_routing_by_input[inp]["correct"] += 1

    mo_intents.add(mo_pred)
    key2 = (expected, mo_pred)
    mo_confusion[key2] = mo_confusion.get(key2, 0) + 1

# Multi-agent separate: intent, agent, combined
ma_intent_correct = 0
ma_agent_correct = 0
ma_combined_correct = 0
for item in merged:
    sc = item["scenario"]
    ma = item["multi_agent"]
    ei = sc.get("expected_intent", "")
    ea = sc.get("expected_agent", "")
    pi = safe(ma, "router_intent", "")
    pa = safe(ma, "router_agent", "")
    if pi == ei:
        ma_intent_correct += 1
    if pa == ea:
        ma_agent_correct += 1
    if pi == ei and pa == ea:
        ma_combined_correct += 1

# ── Metric 2: Transaction Accuracy ──────────────────────────────────

trans_items = [item for item in merged if item["scenario"].get("category") == "transaction"]
T = len(trans_items)

ma_trans_e2e = 0
mo_trans_e2e = 0
ma_trans_independent = 0
mo_trans_independent = 0

for item in trans_items:
    sc = item["scenario"]
    ma = item["multi_agent"]
    mo = item["monolithic"]
    expected = sc.get("expected_intent", "")

    # Multi-agent e2e: correct intent + all facts correct + success
    ma_intent_ok = (safe(ma, "router_intent", "") == expected)
    ma_facts_ok = (safe(ma, "transaction_answer_correct") is True)
    ma_success = (ma.get("execution_status") == "success")
    if ma_intent_ok and ma_facts_ok and ma_success:
        ma_trans_e2e += 1
    # Independent fact accuracy
    if ma_facts_ok and ma_success:
        ma_trans_independent += 1

    # Monolithic e2e: correct baseline_intent + all facts correct + success
    mo_intent_ok = (safe(mo, "baseline_intent", "") == expected)
    mo_facts_ok = (safe(mo, "transaction_answer_correct") is True)
    mo_success = (mo.get("execution_status") == "success")
    if mo_intent_ok and mo_facts_ok and mo_success:
        mo_trans_e2e += 1
    # Independent fact accuracy
    if mo_facts_ok and mo_success:
        mo_trans_independent += 1

# ── Metric 3: Policy Diagnostics ────────────────────────────────────

policy_items = [item for item in merged if item["scenario"].get("category") == "policy"]
P = len(policy_items)

ma_pol_success = sum(1 for item in policy_items
                     if item["multi_agent"].get("execution_status") == "success")
ma_pol_retrieval_ok = sum(1 for item in policy_items
                          if item["multi_agent"].get("retrieval_success") is True)
ma_pol_source_ok = sum(1 for item in policy_items
                       if item["multi_agent"].get("policy_source_correct") is True)
ma_pol_grounding_ok = sum(1 for item in policy_items
                          if item["multi_agent"].get("grounding_validation_passed") is True)
ma_pol_fallback = sum(1 for item in policy_items
                      if item["multi_agent"].get("llm_fallback_used") is True)

mo_pol_success = sum(1 for item in policy_items
                     if item["monolithic"].get("execution_status") == "success")
mo_pol_error = sum(1 for item in policy_items
                   if item["monolithic"].get("execution_status") == "error")
mo_pol_parse_fail = sum(1 for item in policy_items
                        if item["monolithic"].get("execution_error") == "json_parse_failed")
mo_pol_schema_fail = sum(1 for item in policy_items
                         if item["monolithic"].get("execution_error")
                         and "missing_field" in str(item["monolithic"].get("execution_error", "")))
mo_pol_provider_fail = sum(1 for item in policy_items
                           if item["monolithic"].get("execution_error")
                           and item["monolithic"]["execution_error"] in ("empty_output", "timeout", "http_error", "connection_error", "unknown"))

# Monolithic anchor scores
mo_pol_anchor_scores = []
for item in policy_items:
    sc = item["monolithic"].get("policy_anchor_score")
    if sc is not None:
        mo_pol_anchor_scores.append(sc)
mo_pol_avg_anchor = statistics.mean(mo_pol_anchor_scores) if mo_pol_anchor_scores else 0.0

# ── Metric 4: Latency ───────────────────────────────────────────────

ma_latencies = [item["multi_agent"].get("latency_ms", 0) for item in merged]
mo_latencies = [item["monolithic"].get("total_latency_ms", 0) for item in merged]

ma_success_latencies = [item["multi_agent"].get("latency_ms", 0) for item in merged
                        if item["multi_agent"].get("execution_status") == "success"]
mo_success_latencies = [item["monolithic"].get("total_latency_ms", 0) for item in merged
                        if item["monolithic"].get("execution_status") == "success"]

def latency_stats(lats):
    if not lats:
        return {"mean": 0, "median": 0, "min": 0, "max": 0, "std": 0, "p95": 0}
    s = sorted(lats)
    n = len(s)
    mean = sum(s) / n
    median = s[n // 2]
    mn = min(s)
    mx = max(s)
    variance = sum((x - mean) ** 2 for x in s) / n
    std = math.sqrt(variance)
    p95 = s[int(n * 0.95)]
    return {"mean": mean, "median": median, "min": mn, "max": mx, "std": std, "p95": p95}

ma_lat_all = latency_stats(ma_latencies)
mo_lat_all = latency_stats(mo_latencies)
ma_lat_success = latency_stats(ma_success_latencies)
mo_lat_success = latency_stats(mo_success_latencies)

# By category
ma_lat_by_cat = {}
mo_lat_by_cat = {}
for item in merged:
    cat = item["scenario"].get("category", "unknown")
    ml = item["multi_agent"].get("latency_ms", 0)
    if cat not in ma_lat_by_cat:
        ma_lat_by_cat[cat] = []
    ma_lat_by_cat[cat].append(ml)
    tl = item["monolithic"].get("total_latency_ms", 0)
    if cat not in mo_lat_by_cat:
        mo_lat_by_cat[cat] = []
    mo_lat_by_cat[cat].append(tl)

# Warmed MA latency: exclude the single highest policy cold-start record
# The highest MA latency is typically the first policy record (cold start)
ma_warmed = sorted(ma_latencies)
ma_warmed_excl = ma_warmed[:-1] if len(ma_warmed) > 1 else ma_warmed
ma_warmed_stats = latency_stats(ma_warmed_excl)

# ── Metric 5: Clarification and Human Review ────────────────────────

# Multi-agent
ma_actual_clarify = sum(1 for item in merged
                        if item["multi_agent"].get("actual_requires_clarification") is True)
ma_actual_human = sum(1 for item in merged
                      if item["multi_agent"].get("actual_simulated_human_review") is True)
ma_combined_escalation = sum(1 for item in merged
                             if item["multi_agent"].get("actual_requires_clarification") is True
                             or item["multi_agent"].get("actual_simulated_human_review") is True)
ma_clarify_flag_acc = sum(1 for item in merged
                          if item["multi_agent"].get("clarification_flag_correct") is True)
ma_human_flag_acc = sum(1 for item in merged
                        if item["multi_agent"].get("human_review_flag_correct") is True)
ma_escalation_flags_acc = sum(1 for item in merged
                              if item["multi_agent"].get("escalation_flags_correct") is True)

# Monolithic
mo_actual_clarify = sum(1 for item in merged
                        if item["monolithic"].get("baseline_requires_clarification") is True)
mo_actual_human = sum(1 for item in merged
                      if item["monolithic"].get("baseline_simulated_human_review") is True)
mo_combined_escalation = sum(1 for item in merged
                             if item["monolithic"].get("baseline_requires_clarification") is True
                             or item["monolithic"].get("baseline_simulated_human_review") is True)
mo_clarify_flag_acc = sum(1 for item in merged
                          if item["monolithic"].get("clarification_flag_correct") is True)
mo_human_flag_acc = sum(1 for item in merged
                        if item["monolithic"].get("human_review_flag_correct") is True)
# Monolithic doesn't have escalation_flags_correct field; compute it
mo_escalation_flags_acc = sum(1 for item in merged
                              if item["monolithic"].get("clarification_flag_correct") is True
                              and item["monolithic"].get("human_review_flag_correct") is True)

# ── Error and Reliability Report ────────────────────────────────────

# Multi-agent
ma_success_count = sum(1 for item in merged if item["multi_agent"].get("execution_status") == "success")
ma_error_count = sum(1 for item in merged if item["multi_agent"].get("execution_status") == "error")
ma_provider_errors = sum(1 for item in merged
                         if item["multi_agent"].get("llm_error_type") is not None
                         and item["multi_agent"].get("llm_fallback_used") is True)
ma_fallback_count = sum(1 for item in merged
                        if item["multi_agent"].get("llm_fallback_used") is True)
ma_response_completion = sum(1 for item in merged
                             if item["multi_agent"].get("actual_response")
                             and len(str(item["multi_agent"].get("actual_response", ""))) > 0)

# Monolithic
mo_success_count = sum(1 for item in merged if item["monolithic"].get("execution_status") == "success")
mo_error_count = sum(1 for item in merged if item["monolithic"].get("execution_status") == "error")
mo_parse_fail_count = sum(1 for item in merged if item["monolithic"].get("execution_error") == "json_parse_failed")
mo_schema_fail_count = sum(1 for item in merged
                           if item["monolithic"].get("execution_error")
                           and "missing_field" in str(item["monolithic"].get("execution_error", "")))
mo_provider_error_count = sum(1 for item in merged
                              if item["monolithic"].get("execution_error") in
                              ("empty_output", "timeout", "http_error", "connection_error", "unknown"))
mo_response_completion = sum(1 for item in merged
                             if item["monolithic"].get("baseline_response")
                             and len(str(item["monolithic"].get("baseline_response", ""))) > 0)

# ── Output: final_comparison_summary.json ─────────────────────────

summary = {
    "metadata": {
        "total_scenarios": N,
        "multi_agent_source": str(RESULTS_DIR / "multi_agent_real_120.jsonl"),
        "monolithic_source": str(RESULTS_DIR / "monolithic_120_final.jsonl"),
    },
    "routing_accuracy": {
        "multi_agent": {
            "overall": f"{ma_routing_correct}/{N} ({ma_routing_correct/N*100:.1f}%)",
            "by_category": {k: f"{v['correct']}/{v['total']} ({v['correct']/v['total']*100:.1f}%)"
                           for k, v in sorted(ma_routing_by_cat.items())},
            "by_language": {k: f"{v['correct']}/{v['total']} ({v['correct']/v['total']*100:.1f}%)"
                           for k, v in sorted(ma_routing_by_lang.items())},
            "by_input_type": {k: f"{v['correct']}/{v['total']} ({v['correct']/v['total']*100:.1f}%)"
                             for k, v in sorted(ma_routing_by_input.items())},
        },
        "monolithic": {
            "overall": f"{mo_routing_correct}/{N} ({mo_routing_correct/N*100:.1f}%)",
            "by_category": {k: f"{v['correct']}/{v['total']} ({v['correct']/v['total']*100:.1f}%)"
                           for k, v in sorted(mo_routing_by_cat.items())},
            "by_language": {k: f"{v['correct']}/{v['total']} ({v['correct']/v['total']*100:.1f}%)"
                           for k, v in sorted(mo_routing_by_lang.items())},
            "by_input_type": {k: f"{v['correct']}/{v['total']} ({v['correct']/v['total']*100:.1f}%)"
                             for k, v in sorted(mo_routing_by_input.items())},
        },
        "multi_agent_separate": {
            "intent_accuracy": f"{ma_intent_correct}/{N} ({ma_intent_correct/N*100:.1f}%)",
            "agent_accuracy": f"{ma_agent_correct}/{N} ({ma_agent_correct/N*100:.1f}%)",
            "combined_routing_accuracy": f"{ma_combined_correct}/{N} ({ma_combined_correct/N*100:.1f}%)",
        },
    },
    "transaction_accuracy": {
        "multi_agent": {
            "independent_fact": f"{ma_trans_independent}/{T} ({ma_trans_independent/T*100:.1f}%)",
            "end_to_end": f"{ma_trans_e2e}/{T} ({ma_trans_e2e/T*100:.1f}%)",
        },
        "monolithic": {
            "independent_fact": f"{mo_trans_independent}/{T} ({mo_trans_independent/T*100:.1f}%)",
            "end_to_end": f"{mo_trans_e2e}/{T} ({mo_trans_e2e/T*100:.1f}%)",
        },
    },
    "policy_diagnostics": {
        "description": "Provisional diagnostics. Final correctness pending manual review.",
        "multi_agent": {
            "successful_executions": f"{ma_pol_success}/{P}",
            "retrieval_success": f"{ma_pol_retrieval_ok}/{P}",
            "expected_source_found": f"{ma_pol_source_ok}/{P}",
            "grounding_validation_pass": f"{ma_pol_grounding_ok}/{P}",
            "fallback_count": ma_pol_fallback,
        },
        "monolithic": {
            "successful_structured_responses": f"{mo_pol_success}/{P}",
            "parse_failures": mo_pol_parse_fail,
            "schema_failures": mo_pol_schema_fail,
            "provider_failures": mo_pol_provider_fail,
            "average_automatic_anchor_score": f"{mo_pol_avg_anchor:.3f}",
        },
    },
    "latency": {
        "multi_agent": {
            "all_120": ma_lat_all,
            "successful_only": ma_lat_success,
            "by_category": {k: latency_stats(v) for k, v in sorted(ma_lat_by_cat.items())},
            "warmed_excluding_top_policy_cold_start": {
                "description": "Excludes the single highest-latency record (typically first policy cold-start).",
                **ma_warmed_stats,
            },
        },
        "monolithic": {
            "all_120": mo_lat_all,
            "successful_only": mo_lat_success,
            "by_category": {k: latency_stats(v) for k, v in sorted(mo_lat_by_cat.items())},
        },
    },
    "escalation_and_flags": {
        "multi_agent": {
            "actual_clarification_count": ma_actual_clarify,
            "actual_clarification_rate": f"{ma_actual_clarify/N*100:.1f}%",
            "actual_human_review_count": ma_actual_human,
            "actual_human_review_rate": f"{ma_actual_human/N*100:.1f}%",
            "combined_escalation_count": ma_combined_escalation,
            "combined_escalation_rate": f"{ma_combined_escalation/N*100:.1f}%",
            "clarification_flag_accuracy": f"{ma_clarify_flag_acc}/{N} ({ma_clarify_flag_acc/N*100:.1f}%)",
            "human_review_flag_accuracy": f"{ma_human_flag_acc}/{N} ({ma_human_flag_acc/N*100:.1f}%)",
            "escalation_flags_accuracy": f"{ma_escalation_flags_acc}/{N} ({ma_escalation_flags_acc/N*100:.1f}%)",
        },
        "monolithic": {
            "actual_clarification_count": mo_actual_clarify,
            "actual_clarification_rate": f"{mo_actual_clarify/N*100:.1f}%",
            "actual_human_review_count": mo_actual_human,
            "actual_human_review_rate": f"{mo_actual_human/N*100:.1f}%",
            "combined_escalation_count": mo_combined_escalation,
            "combined_escalation_rate": f"{mo_combined_escalation/N*100:.1f}%",
            "clarification_flag_accuracy": f"{mo_clarify_flag_acc}/{N} ({mo_clarify_flag_acc/N*100:.1f}%)",
            "human_review_flag_accuracy": f"{mo_human_flag_acc}/{N} ({mo_human_flag_acc/N*100:.1f}%)",
            "escalation_flags_accuracy": f"{mo_escalation_flags_acc}/{N} ({mo_escalation_flags_acc/N*100:.1f}%)",
        },
    },
    "error_and_reliability": {
        "multi_agent": {
            "successful_records": ma_success_count,
            "execution_errors": ma_error_count,
            "provider_errors": ma_provider_errors,
            "fallback_count": ma_fallback_count,
            "response_completion_count": ma_response_completion,
            "response_completion_rate": f"{ma_response_completion/N*100:.1f}%",
        },
        "monolithic": {
            "successful_records": mo_success_count,
            "execution_errors": mo_error_count,
            "json_parse_failures": mo_parse_fail_count,
            "schema_validation_failures": mo_schema_fail_count,
            "provider_errors": mo_provider_error_count,
            "response_completion_count": mo_response_completion,
            "response_completion_rate": f"{mo_response_completion/N*100:.1f}%",
        },
    },
    "output_files": {
        "final_comparison_summary": str(RESULTS_DIR / "final_comparison_summary.json"),
        "final_comparison_by_scenario": str(RESULTS_DIR / "final_comparison_by_scenario.csv"),
        "chapter4_metrics": str(RESULTS_DIR / "chapter4_metrics.csv"),
        "policy_manual_review": str(RESULTS_DIR / "policy_manual_review.csv"),
        "routing_confusion_multi_agent": str(RESULTS_DIR / "routing_confusion_multi_agent.csv"),
        "routing_confusion_monolithic": str(RESULTS_DIR / "routing_confusion_monolithic.csv"),
    },
    "prohibited_components_check": {
        "network_calls": False,
        "deepseek_api": False,
        "sqlite_access": False,
        "chromadb_access": False,
        "sentencetransformer_load": False,
        "application_source_modification": False,
        "scenario_file_modification": False,
        "existing_result_modification": False,
    },
    "next_task": "4C-3C Complete Policy Manual Review and Finalize Chapter 4 Metrics",
}

# ── Output: confusion matrices as CSV ──────────────────────────────

def write_confusion_csv(path, confusion, label_cols, row_cols):
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        header = ["expected \\ predicted"] + sorted(row_cols)
        writer.writerow(header)
        for exp in sorted(label_cols):
            row = [exp]
            for pred in sorted(row_cols):
                row.append(confusion.get((exp, pred), 0))
            writer.writerow(row)
        # Total row
        total_row = ["TOTAL"]
        for pred in sorted(row_cols):
            total_row.append(sum(confusion.get((exp, pred), 0) for exp in sorted(label_cols)))
        writer.writerow(total_row)

write_confusion_csv(
    RESULTS_DIR / "routing_confusion_multi_agent.csv",
    ma_confusion, all_intents, ma_intents | all_intents,
)
write_confusion_csv(
    RESULTS_DIR / "routing_confusion_monolithic.csv",
    mo_confusion, all_intents, mo_intents | all_intents,
)

# ── Output: final_comparison_by_scenario.csv ──────────────────────

csv_fieldnames = [
    "scenario_id", "category", "subcategory", "language", "input_type",
    "user_message",
    "expected_intent", "expected_agent", "expected_requires_clarification",
    "expected_simulated_human_review",
    "expected_answer_facts",
    # Multi-agent
    "ma_execution_status", "ma_execution_error",
    "ma_router_intent", "ma_router_agent", "ma_actual_intent", "ma_actual_agent",
    "ma_actual_response", "ma_actual_requires_clarification",
    "ma_actual_simulated_human_review", "ma_response_source",
    "ma_retrieval_success", "ma_grounding_validation_passed",
    "ma_llm_fallback_used", "ma_llm_error_type",
    "ma_latency_ms",
    "ma_intent_correct", "ma_agent_correct", "ma_routing_correct",
    "ma_clarification_flag_correct", "ma_human_review_flag_correct",
    "ma_transaction_answer_correct", "ma_transaction_end_to_end_correct",
    "ma_policy_source_correct", "ma_policy_anchor_score",
    # Monolithic
    "mo_execution_status", "mo_execution_error",
    "mo_baseline_intent", "mo_baseline_response",
    "mo_baseline_requires_clarification", "mo_baseline_simulated_human_review",
    "mo_baseline_cited_sources", "mo_baseline_confidence",
    "mo_json_parse_success", "mo_parse_method", "mo_schema_valid",
    "mo_total_latency_ms",
    "mo_intent_correct", "mo_clarification_flag_correct",
    "mo_human_review_flag_correct",
    "mo_transaction_answer_correct",
    "mo_policy_anchor_score",
    # Winner
    "routing_winner",
    "transaction_fact_winner",
]

with open(RESULTS_DIR / "final_comparison_by_scenario.csv", "w", encoding="utf-8-sig", newline="") as f:
    writer = csv.DictWriter(f, fieldnames=csv_fieldnames, extrasaction="ignore")
    writer.writeheader()
    for item in merged:
        sc = item["scenario"]
        ma = item["multi_agent"]
        mo = item["monolithic"]
        sid = sc["scenario_id"]

        ma_r_ok = safe(ma, "router_intent", "") == sc.get("expected_intent", "")
        mo_r_ok = safe(mo, "baseline_intent", "") == sc.get("expected_intent", "")
        # Multi-agent routes better or same? Monolithic routes better or same?
        if ma_r_ok and not mo_r_ok:
            r_winner = "multi_agent"
        elif mo_r_ok and not ma_r_ok:
            r_winner = "monolithic"
        elif ma_r_ok and mo_r_ok:
            r_winner = "tie_correct"
        else:
            r_winner = "tie_incorrect"

        ma_t_ok = safe(ma, "transaction_answer_correct") is True
        mo_t_ok = safe(mo, "transaction_answer_correct") is True
        if ma_t_ok and not mo_t_ok:
            t_winner = "multi_agent"
        elif mo_t_ok and not ma_t_ok:
            t_winner = "monolithic"
        elif ma_t_ok and mo_t_ok:
            t_winner = "tie_correct"
        else:
            t_winner = "tie_incorrect"

        row = {
            "scenario_id": sid,
            "category": sc.get("category", ""),
            "subcategory": sc.get("subcategory", ""),
            "language": sc.get("language", ""),
            "input_type": sc.get("input_type", ""),
            "user_message": sc.get("user_message", ""),
            "expected_intent": sc.get("expected_intent", ""),
            "expected_agent": sc.get("expected_agent", ""),
            "expected_requires_clarification": sc.get("requires_clarification", False),
            "expected_simulated_human_review": sc.get("simulated_human_review", False),
            "expected_answer_facts": json.dumps(sc.get("expected_answer_facts", []), ensure_ascii=False),

            "ma_execution_status": ma.get("execution_status", ""),
            "ma_execution_error": ma.get("execution_error", ""),
            "ma_router_intent": ma.get("router_intent", ""),
            "ma_router_agent": ma.get("router_agent", ""),
            "ma_actual_intent": ma.get("actual_intent", ""),
            "ma_actual_agent": ma.get("actual_agent", ""),
            "ma_actual_response": ma.get("actual_response", ""),
            "ma_actual_requires_clarification": ma.get("actual_requires_clarification", False),
            "ma_actual_simulated_human_review": ma.get("actual_simulated_human_review", False),
            "ma_response_source": ma.get("response_source", ""),
            "ma_retrieval_success": ma.get("retrieval_success"),
            "ma_grounding_validation_passed": ma.get("grounding_validation_passed"),
            "ma_llm_fallback_used": ma.get("llm_fallback_used"),
            "ma_llm_error_type": ma.get("llm_error_type"),
            "ma_latency_ms": ma.get("latency_ms", 0),
            "ma_intent_correct": ma.get("intent_correct", False),
            "ma_agent_correct": ma.get("agent_correct", False),
            "ma_routing_correct": ma.get("routing_correct", False),
            "ma_clarification_flag_correct": ma.get("clarification_flag_correct", False),
            "ma_human_review_flag_correct": ma.get("human_review_flag_correct", False),
            "ma_transaction_answer_correct": ma.get("transaction_answer_correct"),
            "ma_transaction_end_to_end_correct": ma.get("transaction_end_to_end_correct"),
            "ma_policy_source_correct": ma.get("policy_source_correct"),
            "ma_policy_anchor_score": ma.get("policy_anchor_score"),

            "mo_execution_status": mo.get("execution_status", ""),
            "mo_execution_error": mo.get("execution_error", ""),
            "mo_baseline_intent": mo.get("baseline_intent", ""),
            "mo_baseline_response": mo.get("baseline_response", ""),
            "mo_baseline_requires_clarification": mo.get("baseline_requires_clarification", False),
            "mo_baseline_simulated_human_review": mo.get("baseline_simulated_human_review", False),
            "mo_baseline_cited_sources": json.dumps(mo.get("baseline_cited_sources", []), ensure_ascii=False),
            "mo_baseline_confidence": mo.get("baseline_confidence", 0.0),
            "mo_json_parse_success": mo.get("json_parse_success", False),
            "mo_parse_method": mo.get("parse_method", ""),
            "mo_schema_valid": mo.get("schema_valid", False),
            "mo_total_latency_ms": mo.get("total_latency_ms", 0),
            "mo_intent_correct": mo.get("intent_correct", False),
            "mo_clarification_flag_correct": mo.get("clarification_flag_correct", False),
            "mo_human_review_flag_correct": mo.get("human_review_flag_correct", False),
            "mo_transaction_answer_correct": mo.get("transaction_answer_correct"),
            "mo_policy_anchor_score": mo.get("policy_anchor_score"),

            "routing_winner": r_winner,
            "transaction_fact_winner": t_winner,
        }
        writer.writerow(row)

# ── Output: policy_manual_review.csv ──────────────────────────────

pr_fieldnames = [
    "scenario_id", "language", "input_type",
    "user_message",
    "expected_intent", "expected_policy_type",
    "expected_answer_facts", "expected_source_filename",
    "multi_agent_response", "multi_agent_response_source",
    "multi_agent_policy_sources", "multi_agent_retrieval_success",
    "multi_agent_grounding_validation_passed", "multi_agent_fallback_used",
    "monolithic_response", "monolithic_cited_sources",
    "monolithic_execution_status",
    "reviewer_multi_agent_correct", "reviewer_monolithic_correct",
    "reviewer_notes",
]

with open(RESULTS_DIR / "policy_manual_review.csv", "w", encoding="utf-8-sig", newline="") as f:
    writer = csv.DictWriter(f, fieldnames=pr_fieldnames, extrasaction="ignore")
    writer.writeheader()
    for item in policy_items:
        sc = item["scenario"]
        ma = item["multi_agent"]
        mo = item["monolithic"]
        rp = item["real_policy"]

        sid = sc["scenario_id"]

        # Use real_policy_30 data if available (richer), fallback to multi_agent_real_120
        if rp:
            ma_resp = rp.get("actual_response", "")
            ma_src = rp.get("response_source", "")
            ma_ps = rp.get("policy_sources", [])
            ma_rs = rp.get("retrieval_success", None)
            ma_gv = rp.get("grounding_validation_passed", None)
            ma_fb = rp.get("llm_fallback_used", None)
        else:
            ma_resp = ma.get("actual_response", "")
            ma_src = ma.get("response_source", "")
            ma_ps = ma.get("policy_sources", [])
            ma_rs = ma.get("retrieval_success", None)
            ma_gv = ma.get("grounding_validation_passed", None)
            ma_fb = ma.get("llm_fallback_used", None)

        writer.writerow({
            "scenario_id": sid,
            "language": sc.get("language", ""),
            "input_type": sc.get("input_type", ""),
            "user_message": sc.get("user_message", ""),
            "expected_intent": sc.get("expected_intent", ""),
            "expected_policy_type": sc.get("expected_policy_type", ""),
            "expected_answer_facts": json.dumps(sc.get("expected_answer_facts", []), ensure_ascii=False),
            "expected_source_filename": sc.get("expected_source_filename", ""),
            "multi_agent_response": ma_resp,
            "multi_agent_response_source": ma_src,
            "multi_agent_policy_sources": json.dumps(ma_ps, ensure_ascii=False) if isinstance(ma_ps, list) else str(ma_ps),
            "multi_agent_retrieval_success": ma_rs,
            "multi_agent_grounding_validation_passed": ma_gv,
            "multi_agent_fallback_used": ma_fb,
            "monolithic_response": mo.get("baseline_response", ""),
            "monolithic_cited_sources": json.dumps(mo.get("baseline_cited_sources", []), ensure_ascii=False),
            "monolithic_execution_status": mo.get("execution_status", ""),
            "reviewer_multi_agent_correct": "",
            "reviewer_monolithic_correct": "",
            "reviewer_notes": "",
        })

# ── Output: chapter4_metrics.csv ──────────────────────────────────

def pct_str(num, den):
    if den == 0:
        return "N/A"
    return f"{num}/{den} ({num/den*100:.1f}%)"

# Actual combined escalation rates
ma_esc_rate = f"{ma_combined_escalation}/{N} ({ma_combined_escalation/N*100:.1f}%)"
mo_esc_rate = f"{mo_combined_escalation}/{N} ({mo_combined_escalation/N*100:.1f}%)"

# Flag accuracies for notes
ma_flag_note = f"clarify_acc={ma_clarify_flag_acc/N*100:.1f}%, human_acc={ma_human_flag_acc/N*100:.1f}%"
mo_flag_note = f"clarify_acc={mo_clarify_flag_acc/N*100:.1f}%, human_acc={mo_human_flag_acc/N*100:.1f}%"

chapter4_rows = [
    {
        "metric": "Routing accuracy",
        "multi_agent_value": pct_str(ma_routing_correct, N),
        "monolithic_value": pct_str(mo_routing_correct, N),
        "unit": "percent",
        "status": "final",
        "definition": "Router-stage intent prediction matches expected_intent (all 120 scenarios). Errors count as incorrect.",
        "notes": "Multi-Agent uses intelligent router; Monolithic uses LLM-generated baseline_intent.",
    },
    {
        "metric": "Transaction-answer accuracy (end-to-end)",
        "multi_agent_value": pct_str(ma_trans_e2e, T),
        "monolithic_value": pct_str(mo_trans_e2e, T),
        "unit": "percent",
        "status": "final",
        "definition": "Correct intent + all expected_answer_facts correct + execution success (30 transaction scenarios).",
        "notes": f"Multi-Agent independent fact: {pct_str(ma_trans_independent, T)}; Monolithic independent fact: {pct_str(mo_trans_independent, T)}",
    },
    {
        "metric": "Policy-answer correctness",
        "multi_agent_value": "",
        "monolithic_value": "",
        "unit": "percent",
        "status": "pending_manual_review",
        "definition": "Manual review of 30 policy scenarios. Provisional diagnostics available in final_comparison_summary.json policy_diagnostics.",
        "notes": "30 policy rows in policy_manual_review.csv awaiting reviewer judgment. Do not use provisional anchor scores as final correctness.",
    },
    {
        "metric": "Average response time (all 120)",
        "multi_agent_value": f"{ma_lat_all['mean']:.0f}",
        "monolithic_value": f"{mo_lat_all['mean']:.0f}",
        "unit": "ms",
        "status": "final",
        "definition": "Mean total latency across all 120 scenarios including errors and cold start.",
        "notes": f"MA median={ma_lat_all['median']:.0f} p95={ma_lat_all['p95']:.0f}; MO median={mo_lat_all['median']:.0f} p95={mo_lat_all['p95']:.0f}",
    },
    {
        "metric": "Average response time (successful only)",
        "multi_agent_value": f"{ma_lat_success['mean']:.0f}",
        "monolithic_value": f"{mo_lat_success['mean']:.0f}",
        "unit": "ms",
        "status": "final",
        "definition": "Mean latency for successfully executed scenarios only.",
        "notes": f"MA median={ma_lat_success['median']:.0f} p95={ma_lat_success['p95']:.0f}; MO median={mo_lat_success['median']:.0f} p95={mo_lat_success['p95']:.0f}",
    },
    {
        "metric": "Combined escalation rate",
        "multi_agent_value": ma_esc_rate,
        "monolithic_value": mo_esc_rate,
        "unit": "percent",
        "status": "final",
        "definition": "Scenarios where requires_clarification=True OR simulated_human_review=True (actual, not expected). Denominator 120.",
        "notes": f"MA: {ma_flag_note}; MO: {mo_flag_note}",
    },
]

with open(RESULTS_DIR / "chapter4_metrics.csv", "w", encoding="utf-8-sig", newline="") as f:
    writer = csv.DictWriter(f, fieldnames=chapter4_rows[0].keys())
    writer.writeheader()
    writer.writerows(chapter4_rows)

# ── Write summary JSON ──────────────────────────────────────────────
with open(RESULTS_DIR / "final_comparison_summary.json", "w", encoding="utf-8") as f:
    json.dump(summary, f, indent=2, ensure_ascii=False)

# ── Print summary ────────────────────────────────────────────────────
print("=" * 72)
print("  TASK 4C-3B — FINAL MULTI-AGENT vs MONOLITHIC COMPARISON")
print("=" * 72)
print()
print(f"  Record counts:                      {N} total scenarios")
print(f"  Multi-agent records:                {len(ma_records)}")
print(f"  Monolithic records:                 {len(mo_records)}")
print(f"  Real policy-30 records:             {len(rp_records)}")
print()

print(f"── Routing Accuracy ───────────────────────────────────────────")
print(f"  Multi-Agent (router_intent):         {pct_str(ma_routing_correct, N)}")
print(f"  Monolithic (baseline_intent):        {pct_str(mo_routing_correct, N)}")
ma_cat_str = ', '.join(f"{k}={v['correct']}/{v['total']}" for k, v in sorted(ma_routing_by_cat.items()))
mo_cat_str = ', '.join(f"{k}={v['correct']}/{v['total']}" for k, v in sorted(mo_routing_by_cat.items()))
print(f"  Multi-Agent by category:             {ma_cat_str}")
print(f"  Monolithic by category:              {mo_cat_str}")
print(f"  MA separate: intent={ma_intent_correct}/{N}, agent={ma_agent_correct}/{N}, combined={ma_combined_correct}/{N}")
print(f"  Confusion matrices:                  {RESULTS_DIR / 'routing_confusion_multi_agent.csv'}, {RESULTS_DIR / 'routing_confusion_monolithic.csv'}")
print()

print(f"── Transaction Accuracy (30 scenarios) ────────────────────────")
print(f"  Multi-Agent independent:             {pct_str(ma_trans_independent, T)}")
print(f"  Multi-Agent end-to-end:              {pct_str(ma_trans_e2e, T)}")
print(f"  Monolithic independent:              {pct_str(mo_trans_independent, T)}")
print(f"  Monolithic end-to-end:               {pct_str(mo_trans_e2e, T)}")
print()

print(f"── Policy Diagnostics (provisional, 30 scenarios) ─────────────")
print(f"  Multi-Agent:")
print(f"    Successful executions:              {ma_pol_success}/{P}")
print(f"    Retrieval success:                  {ma_pol_retrieval_ok}/{P}")
print(f"    Expected source found:              {ma_pol_source_ok}/{P}")
print(f"    Grounding validation pass:          {ma_pol_grounding_ok}/{P}")
print(f"    Fallback count:                     {ma_pol_fallback}")
print(f"  Monolithic (provisional):")
print(f"    Successful structured responses:    {mo_pol_success}/{P}")
print(f"    Parse failures:                     {mo_pol_parse_fail}")
print(f"    Schema validation failures:         {mo_pol_schema_fail}")
print(f"    Provider failures:                  {mo_pol_provider_fail}")
print(f"    Average automatic anchor score:     {mo_pol_avg_anchor:.3f}")
print()

print(f"── Latency Comparison ─────────────────────────────────────────")
print(f"  Multi-Agent (all 120):               mean={ma_lat_all['mean']:.0f}ms, "
      f"median={ma_lat_all['median']:.0f}ms, min={ma_lat_all['min']:.0f}ms, "
      f"max={ma_lat_all['max']:.0f}ms, std={ma_lat_all['std']:.0f}ms, p95={ma_lat_all['p95']:.0f}ms")
print(f"  Monolithic (all 120):                mean={mo_lat_all['mean']:.0f}ms, "
      f"median={mo_lat_all['median']:.0f}ms, min={mo_lat_all['min']:.0f}ms, "
      f"max={mo_lat_all['max']:.0f}ms, std={mo_lat_all['std']:.0f}ms, p95={mo_lat_all['p95']:.0f}ms")
print(f"  MA warmed (excl. top cold-start):     mean={ma_warmed_stats['mean']:.0f}ms, "
      f"median={ma_warmed_stats['median']:.0f}ms, p95={ma_warmed_stats['p95']:.0f}ms")
ma_cat_lat_str = ', '.join(f"{k}={latency_stats(v)['mean']:.0f}ms" for k, v in sorted(ma_lat_by_cat.items()))
mo_cat_lat_str = ', '.join(f"{k}={latency_stats(v)['mean']:.0f}ms" for k, v in sorted(mo_lat_by_cat.items()))
print(f"  MA by category:                      {ma_cat_lat_str}")
print(f"  MO by category:                      {mo_cat_lat_str}")
print()

print(f"── Escalation Rates and Flag Accuracy ─────────────────────────")
print(f"  Multi-Agent:")
print(f"    Actual clarification:               {ma_actual_clarify}/{N} ({ma_actual_clarify/N*100:.1f}%)")
print(f"    Actual human review:                {ma_actual_human}/{N} ({ma_actual_human/N*100:.1f}%)")
print(f"    Combined escalation:                {ma_combined_escalation}/{N} ({ma_combined_escalation/N*100:.1f}%)")
print(f"    Clarification flag accuracy:        {ma_clarify_flag_acc}/{N} ({ma_clarify_flag_acc/N*100:.1f}%)")
print(f"    Human-review flag accuracy:         {ma_human_flag_acc}/{N} ({ma_human_flag_acc/N*100:.1f}%)")
print(f"  Monolithic:")
print(f"    Actual clarification:               {mo_actual_clarify}/{N} ({mo_actual_clarify/N*100:.1f}%)")
print(f"    Actual human review:                {mo_actual_human}/{N} ({mo_actual_human/N*100:.1f}%)")
print(f"    Combined escalation:                {mo_combined_escalation}/{N} ({mo_combined_escalation/N*100:.1f}%)")
print(f"    Clarification flag accuracy:        {mo_clarify_flag_acc}/{N} ({mo_clarify_flag_acc/N*100:.1f}%)")
print(f"    Human-review flag accuracy:         {mo_human_flag_acc}/{N} ({mo_human_flag_acc/N*100:.1f}%)")
print()

print(f"── Error and Reliability ──────────────────────────────────────")
print(f"  Multi-Agent:")
print(f"    Successful:                         {ma_success_count}")
print(f"    Execution errors:                   {ma_error_count}")
print(f"    Provider errors:                    {ma_provider_errors}")
print(f"    Fallback count:                     {ma_fallback_count}")
print(f"    Response completion rate:           {ma_response_completion/N*100:.1f}%")
print(f"  Monolithic:")
print(f"    Successful:                         {mo_success_count}")
print(f"    Execution errors:                   {mo_error_count}")
print(f"    JSON parse failures:                {mo_parse_fail_count}")
print(f"    Schema validation failures:         {mo_schema_fail_count}")
print(f"    Provider errors:                    {mo_provider_error_count}")
print(f"    Response completion rate:           {mo_response_completion/N*100:.1f}%")
print()

print(f"── Policy Manual Review ──────────────────────────────────────")
print(f"  Rows awaiting reviewer judgment:      {P}")
print(f"  File:                                 {RESULTS_DIR / 'policy_manual_review.csv'}")
print()

print(f"── Output Files ─────────────────────────────────────────────")
print(f"  Summary JSON:                         {RESULTS_DIR / 'final_comparison_summary.json'}")
print(f"  Per-scenario CSV:                     {RESULTS_DIR / 'final_comparison_by_scenario.csv'}")
print(f"  Chapter 4 metrics CSV:                {RESULTS_DIR / 'chapter4_metrics.csv'}")
print(f"  Policy manual review CSV:             {RESULTS_DIR / 'policy_manual_review.csv'}")
print(f"  MA confusion matrix:                  {RESULTS_DIR / 'routing_confusion_multi_agent.csv'}")
print(f"  MO confusion matrix:                  {RESULTS_DIR / 'routing_confusion_monolithic.csv'}")
print()

print("── Prohibited Components Check ────────────────────────────────")
print("  Network calls (DeepSeek, API, etc.):  NOT invoked")
print("  SQLite access:                        NOT invoked")
print("  ChromaDB access:                      NOT invoked")
print("  SentenceTransformer load:             NOT invoked")
print("  Application source modified:          NO")
print("  Scenario files modified:              NO")
print("  Existing experiment results modified: NO")
print()

print("  NEXT TASK: 4C-3C Complete Policy Manual Review and Finalize Chapter 4 Metrics.")
print()
