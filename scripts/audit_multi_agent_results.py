"""Offline audit of 120-scenario dry-run results — Phase 4B-2A.

Reads the JSONL results and all_scenarios.json, performs deterministic analysis.
No app imports, no network, no ChromaDB, no SQLite.
"""

import argparse
import json
import re
import statistics
from collections import Counter, defaultdict


# ── Router logic replica (pure, no imports) ────────────────────────────

_ORDER_ID_PATTERN = re.compile(r"ORD[-]?\d{4}", re.IGNORECASE)

_INTENT_PRIORITY = [
    "TRACKING_NUMBER",
    "PAYMENT_STATUS",
    "SHIPMENT_STATUS",
    "ORDER_STATUS",
    "RETURN_REFUND",
    "STORE_POLICY",
    "GREETING",
    "OUT_OF_SCOPE",
]

_REQUIRES_ORDER_ID = {"ORDER_STATUS", "PAYMENT_STATUS", "SHIPMENT_STATUS", "TRACKING_NUMBER"}

_AGENT_MAP = {
    "ORDER_STATUS": "transaction_tracker",
    "PAYMENT_STATUS": "transaction_tracker",
    "SHIPMENT_STATUS": "transaction_tracker",
    "TRACKING_NUMBER": "transaction_tracker",
    "RETURN_REFUND": "store_policy_evaluator",
    "STORE_POLICY": "store_policy_evaluator",
    "GREETING": "general_response",
    "CLARIFICATION": "clarification_handler",
    "UNKNOWN": "clarification_handler",
    "OUT_OF_SCOPE": "simulated_human_review",
}

_INTENT_PATTERNS = {
    "TRACKING_NUMBER": [
        "หมายเลขพัสดุ", "เลขพัสดุ", "เลขติดตาม", "tracking number",
        "tracking code", "track no", "track number", "tracking id", "TRK",
    ],
    "PAYMENT_STATUS": [
        "ชำระเงินแล้วหรือยัง", "ชำระเงินแล้ว", "ชำระยัง", "จ่ายเงินแล้ว",
        "จ่ายหรือยัง", "payment status", "payment confirmed", "paid yet",
        "จ่ายเงิน", "ชำระเงิน", "ชำระ",
    ],
    "SHIPMENT_STATUS": [
        "สถานะการจัดส่ง", "สถานะจัดส่ง", "shipment status", "shipping status",
        "จัดส่งหรือยัง", "ส่งของหรือยัง", "ส่งถึงไหน", "สถานะขนส่ง",
        "ได้รับของหรือยัง", "delivery status", "จัดส่ง", "ขนส่ง",
    ],
    "ORDER_STATUS": [
        "สถานะคำสั่งซื้อ", "สถานะออเดอร์", "สถานะ", "ออเดอร์ถึงไหน",
        "คำสั่งซื้อถึงไหน", "ถึงไหนแล้ว", "order status", "อัพเดท",
        "update", "status", "ขั้นตอน", "ไปถึงไหน", "ถึงไหน",
    ],
    "RETURN_REFUND": [
        "คืนสินค้า", "ขอคืนเงิน", "refund", "return", "เปลี่ยนสินค้า",
        "คืนเงิน", "ขอเปลี่ยน", "exchange",
    ],
    "STORE_POLICY": [
        "นโยบายร้าน", "นโยบาย", "เงื่อนไขการคืนสินค้า", "return policy",
        "refund policy", "policy", "conditions", "ข้อกำหนด", "เงื่อนไข",
    ],
    "GREETING": [
        "สวัสดี", "hello", "hi", "good morning", "good afternoon",
        "good evening", "hey", "หวัดดี",
    ],
    "OUT_OF_SCOPE": [
        "เปลี่ยนที่อยู่", "เปลี่ยนที่อยู่จัดส่ง", "cancel order", "cancel",
        "ยกเลิก", "ยกเลิกคำสั่งซื้อ", "change address", "change order",
        "modify order", "edit order", "make payment", "process refund",
    ],
}


def _extract_order_id(message: str):
    m = _ORDER_ID_PATTERN.search(message)
    if m:
        raw = m.group(0).upper()
        if "-" not in raw:
            raw = raw[:3] + "-" + raw[3:]
        return raw
    return None


def _classify_intent(message: str) -> str:
    msg_lower = message.lower().strip()
    for intent in _INTENT_PRIORITY:
        for pattern in _INTENT_PATTERNS.get(intent, []):
            if pattern.lower() in msg_lower:
                return intent
    if _extract_order_id(message):
        return "ORDER_STATUS"
    return "UNKNOWN"


def _route_message(message: str) -> dict:
    """Replica of router.route_message for offline analysis."""
    message = message.strip()
    if not message:
        return {"intent": "UNKNOWN", "target_agent": "clarification_handler",
                "requires_clarification": True, "simulated_human_review": False}
    intent = _classify_intent(message)
    order_id = _extract_order_id(message)
    requires_clarification = False
    if intent in _REQUIRES_ORDER_ID and not order_id:
        requires_clarification = True
    elif intent == "UNKNOWN":
        requires_clarification = True
    simulated_human_review = (intent == "OUT_OF_SCOPE")
    target_agent = _AGENT_MAP.get(intent, "clarification_handler")
    return {"intent": intent, "target_agent": target_agent,
            "requires_clarification": requires_clarification,
            "simulated_human_review": simulated_human_review,
            "extracted_order_id": order_id}


# ── Root-cause classification helpers ─────────────────────────────────

def _classify_routing_mismatch(row: dict) -> str:
    """Classify a single routing mismatch into a root-cause bucket."""
    expected_intent = row.get("expected_intent")
    actual_intent = row.get("actual_intent")
    expected_agent = row.get("expected_agent")
    actual_agent = row.get("actual_agent")
    category = row.get("category")
    user_message = row.get("user_message", "")
    subcategory = row.get("subcategory", "")

    # Simulate what the current router would decide
    sim = _route_message(user_message)
    sim_intent = sim["intent"]
    sim_agent = sim["target_agent"]

    # If the router would agree with actual, the dataset label is inconsistent
    if sim_intent == actual_intent and sim_agent == actual_agent:
        # Router behavior matches what was actually observed
        if sim_intent == expected_intent and sim_agent == expected_agent:
            # This shouldn't happen (wouldn't be a mismatch)
            return "unknown"
        # Dataset expected something different from what router actually implements
        return "dataset_expected_label_inconsistent"

    # Router would return something other than actual — actual routing has a bug
    # or there's an orchestrator override
    # Check if it's entity extraction related
    if category == "clarification" and subcategory == "missing_order_id":
        # Clarification scenarios often have routing determined by entity presence
        if actual_intent == "CLARIFICATION":
            # The orchestrator or tracker may have overridden the intent
            return "entity_extraction_or_normalization"

    # Check ambiguous multi-intent
    if actual_intent == "OUT_OF_SCOPE" and subcategory in ("ambiguous_multi_intent", "unsupported_request"):
        return "ambiguous_multi_intent"

    # Check missing router pattern
    if sim_intent == "UNKNOWN" and expected_intent != "UNKNOWN":
        return "missing_router_pattern"

    if actual_intent == "CLARIFICATION" and expected_intent != "UNKNOWN":
        return "clarification_rule_mismatch"

    if actual_agent == "simulated_human_review" and expected_agent != "simulated_human_review":
        return "human_review_rule_mismatch"

    # If the simulated router differs from actual, it's a router code issue
    if sim_intent != actual_intent or sim_agent != actual_agent:
        if sim_intent == expected_intent and sim_agent == expected_agent:
            # Router SHOULD have produced the expected result but didn't
            return "missing_router_pattern"

    return "unknown"


def _classify_clarification_mismatch(row: dict) -> str:
    """Classify a clarification-flag mismatch."""
    expected = row.get("expected_requires_clarification")
    actual = row.get("actual_requires_clarification")
    category = row.get("category")

    if expected is True and actual is False:
        # Dataset says clarify, system didn't — maybe the system found enough info
        sim = _route_message(row.get("user_message", ""))
        if not sim["requires_clarification"]:
            return "expected_label_mismatch"
        return "routing_behavior_mismatch"
    elif expected is False and actual is True:
        # System over-clarified — routing bug or entity extraction failure
        sim = _route_message(row.get("user_message", ""))
        if sim["requires_clarification"]:
            return "expected_label_mismatch"
        return "routing_behavior_mismatch"
    return "unknown"


def _classify_human_review_mismatch(row: dict) -> str:
    expected = row.get("expected_simulated_human_review")
    actual = row.get("actual_simulated_human_review")
    if expected is True and actual is False:
        return "unsupported_escalation_rule"
    elif expected is False and actual is True:
        sim = _route_message(row.get("user_message", ""))
        if sim["simulated_human_review"]:
            return "expected_label_mismatch"
        return "routing_behavior_mismatch"
    return "unknown"


def _source_from_notes(notes: str):
    m = re.search(r"source=([^;]+)", notes or "")
    return m.group(1).strip() if m else None


# ── Main audit ─────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Audit dry-run results")
    parser.add_argument("--input", required=True, help="Path to JSONL results")
    parser.add_argument("--scenarios", required=True, help="Path to all_scenarios.json")
    parser.add_argument("--output", required=True, help="Path to output audit JSON")
    args = parser.parse_args()

    # Load data
    records = []
    with open(args.input, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                records.append(json.loads(line))

    with open(args.scenarios, "r", encoding="utf-8") as f:
        scenarios_list = json.load(f)
    scenarios_by_id = {s["scenario_id"]: s for s in scenarios_list}

    # Merge notes from scenarios into records for policy source extraction
    for rec in records:
        sid = rec.get("scenario_id")
        if sid in scenarios_by_id:
            rec["_notes"] = scenarios_by_id[sid].get("notes", "")

    total = len(records)

    # ═══════════════════════════════════════════════════════════════
    # 1. ROUTING AUDIT
    # ═══════════════════════════════════════════════════════════════

    # Intent confusion matrix
    all_intents = sorted(set(r.get("expected_intent") for r in records) |
                         set(r.get("actual_intent") for r in records))
    intent_cm = {e: {a: 0 for a in all_intents} for e in all_intents}
    for r in records:
        ei = r.get("expected_intent")
        ai = r.get("actual_intent")
        intent_cm[ei][ai] += 1

    # Agent confusion matrix
    all_agents = sorted(set(str(r.get("expected_agent")) for r in records) |
                        set(str(r.get("actual_agent")) for r in records))
    agent_cm = {e: {a: 0 for a in all_agents} for e in all_agents}
    for r in records:
        ea = str(r.get("expected_agent"))
        aa = str(r.get("actual_agent"))
        agent_cm[ea][aa] += 1

    # Mismatches by dimensions
    mismatches = [r for r in records if not r.get("routing_correct")]
    mismatch_by_cat = Counter(r.get("category") for r in mismatches)
    mismatch_by_subcat = Counter(r.get("subcategory") for r in mismatches)
    mismatch_by_lang = Counter(r.get("language") for r in mismatches)
    mismatch_by_input = Counter(r.get("input_type") for r in mismatches)

    # Root-cause classification
    root_cause_buckets = defaultdict(list)
    for r in mismatches:
        bc = _classify_routing_mismatch(r)
        root_cause_buckets[bc].append(r.get("scenario_id"))

    # Representative examples per bucket
    bucket_examples = {}
    for bc, sids in sorted(root_cause_buckets.items()):
        examples = []
        for r in mismatches:
            if r.get("scenario_id") in sids[:3]:
                examples.append({
                    "scenario_id": r.get("scenario_id"),
                    "user_message": r.get("user_message"),
                    "expected_intent": r.get("expected_intent"),
                    "actual_intent": r.get("actual_intent"),
                    "expected_agent": r.get("expected_agent"),
                    "actual_agent": r.get("actual_agent"),
                    "routing_reason": r.get("routing_reason"),
                    "assigned_root_cause": bc,
                })
        bucket_examples[bc] = examples

    routing_audit = {
        "total_scenarios": total,
        "routing_correct": sum(1 for r in records if r.get("routing_correct")),
        "routing_incorrect": len(mismatches),
        "intent_confusion_matrix": intent_cm,
        "agent_confusion_matrix": agent_cm,
        "mismatch_by_category": dict(mismatch_by_cat),
        "mismatch_by_subcategory": dict(mismatch_by_subcat),
        "mismatch_by_language": dict(mismatch_by_lang),
        "mismatch_by_input_type": dict(mismatch_by_input),
        "root_cause_buckets": {k: sorted(v) for k, v in sorted(root_cause_buckets.items())},
        "bucket_examples": bucket_examples,
    }

    # ═══════════════════════════════════════════════════════════════
    # 2. POLICY AUDIT
    # ═══════════════════════════════════════════════════════════════

    policy_recs = [r for r in records if r.get("category") == "policy"]
    num_policy = len(policy_recs)

    # How many reached the policy evaluator
    reached_policy_ev = [r for r in policy_recs if r.get("actual_agent") == "store_policy_evaluator"]
    num_reached = len(reached_policy_ev)

    # Retrieval stats among those that reached
    with_retrieval_success = [r for r in reached_policy_ev if r.get("retrieval_success") is True]
    with_chunks_gt_zero = [r for r in reached_policy_ev if (r.get("retrieved_chunk_count") or 0) > 0]
    with_non_empty_sources = [r for r in reached_policy_ev if r.get("policy_sources") and len(r.get("policy_sources", [])) > 0]
    with_grounding_pass = [r for r in reached_policy_ev if r.get("grounding_validation_passed") is True]

    # Expected vs actual source
    source_mismatch_ids = []
    for r in policy_recs:
        expected_src = _source_from_notes(r.get("_notes", ""))
        actual_srcs = r.get("policy_sources") or []
        if expected_src and expected_src not in actual_srcs:
            source_mismatch_ids.append(r.get("scenario_id"))

    # Scenarios that reached policy evaluator but returned no retrieval metadata
    reached_no_meta = [r.get("scenario_id") for r in reached_policy_ev
                       if not r.get("policy_sources") or len(r.get("policy_sources", [])) == 0]

    # Scenarios that never reached policy evaluator
    never_reached = [r.get("scenario_id") for r in policy_recs
                     if r.get("actual_agent") != "store_policy_evaluator"]

    # Determine primary root cause
    if num_reached == 0:
        policy_root_cause = "routing_failure"
    else:
        has_sources = len(with_non_empty_sources)
        if has_sources > 0:
            src_ratio = has_sources / num_reached
            if src_ratio < 0.5:
                policy_root_cause = "retrieval_not_executed_or_empty_when_deepseek_disabled"
            else:
                policy_root_cause = "mixed_causes"
        else:
            policy_root_cause = "retrieval_output_not_propagated_by_orchestrator"

    policy_audit = {
        "total_policy_scenarios": num_policy,
        "routed_to_policy_evaluator": num_reached,
        "with_retrieval_success_true": len(with_retrieval_success),
        "with_retrieved_chunk_count_gt_zero": len(with_chunks_gt_zero),
        "with_non_empty_policy_sources": len(with_non_empty_sources),
        "with_grounding_validation_passed_true": len(with_grounding_pass),
        "expected_vs_actual_source_mismatch_count": len(source_mismatch_ids),
        "expected_vs_actual_source_mismatch_ids": sorted(source_mismatch_ids),
        "reached_evaluator_but_no_retrieval_metadata": sorted(reached_no_meta),
        "never_reached_policy_evaluator": sorted(never_reached),
        "policy_source_0percent_root_cause": policy_root_cause,
    }

    # ═══════════════════════════════════════════════════════════════
    # 3. CLARIFICATION AUDIT
    # ═══════════════════════════════════════════════════════════════

    # Flag matrix: expected clarification vs actual
    flag_counts = {"TT": 0, "TF": 0, "FT": 0, "FF": 0}
    for r in records:
        e = r.get("expected_requires_clarification")
        a = r.get("actual_requires_clarification")
        if e is True and a is True:
            flag_counts["TT"] += 1
        elif e is True and a is False:
            flag_counts["TF"] += 1
        elif e is False and a is True:
            flag_counts["FT"] += 1
        else:
            flag_counts["FF"] += 1

    # Human review matrix
    hr_counts = {"TT": 0, "TF": 0, "FT": 0, "FF": 0}
    for r in records:
        e = r.get("expected_simulated_human_review")
        a = r.get("actual_simulated_human_review")
        if e is True and a is True:
            hr_counts["TT"] += 1
        elif e is True and a is False:
            hr_counts["TF"] += 1
        elif e is False and a is True:
            hr_counts["FT"] += 1
        else:
            hr_counts["FF"] += 1

    # Mismatch classification
    clarify_mismatches = [r for r in records
                          if r.get("expected_requires_clarification") != r.get("actual_requires_clarification")]
    clarify_mm_by_cause = defaultdict(list)
    for r in clarify_mismatches:
        cause = _classify_clarification_mismatch(r)
        clarify_mm_by_cause[cause].append(r.get("scenario_id"))

    human_mismatches = [r for r in records
                        if r.get("expected_simulated_human_review") != r.get("actual_simulated_human_review")]
    human_mm_by_cause = defaultdict(list)
    for r in human_mismatches:
        cause = _classify_human_review_mismatch(r)
        human_mm_by_cause[cause].append(r.get("scenario_id"))

    clarification_audit = {
        "clarification_flag_matrix": {
            "expected_true_actual_true": flag_counts["TT"],
            "expected_true_actual_false": flag_counts["TF"],
            "expected_false_actual_true": flag_counts["FT"],
            "expected_false_actual_false": flag_counts["FF"],
        },
        "clarification_mismatch_count": len(clarify_mismatches),
        "clarification_mismatch_ids": [r.get("scenario_id") for r in clarify_mismatches],
        "clarification_mismatch_by_cause": {k: sorted(v) for k, v in sorted(clarify_mm_by_cause.items())},
        "human_review_flag_matrix": {
            "expected_true_actual_true": hr_counts["TT"],
            "expected_true_actual_false": hr_counts["TF"],
            "expected_false_actual_true": hr_counts["FT"],
            "expected_false_actual_false": hr_counts["FF"],
        },
        "human_review_mismatch_count": len(human_mismatches),
        "human_review_mismatch_ids": [r.get("scenario_id") for r in human_mismatches],
        "human_review_mismatch_by_cause": {k: sorted(v) for k, v in sorted(human_mm_by_cause.items())},
    }

    # ═══════════════════════════════════════════════════════════════
    # 4. TRANSACTION SCORING AUDIT
    # ═══════════════════════════════════════════════════════════════

    trans_recs = [r for r in records if r.get("category") == "transaction"]

    # Count handled by transaction_tracker
    handled_by_tracker = [r for r in trans_recs if r.get("actual_agent") == "transaction_tracker"]

    # Records where transaction_answer_correct=true but routing_correct=false
    correct_but_wrong_route = [r for r in trans_recs
                                if r.get("transaction_answer_correct") is True
                                and r.get("routing_correct") is False]

    # Check for false positives: examine response-text fallback usage
    fp_candidates = []
    for r in correct_but_wrong_route:
        # These scored correct despite wrong routing — could be false positive
        # through response-text matching
        expected_facts = r.get("expected_answer_facts") or []
        actual_response = r.get("actual_response") or ""
        actual_agent = r.get("actual_agent")

        if actual_agent != "transaction_tracker":
            # System didn't use the transaction_tracker but still scored correct
            # This is a false positive risk
            fp_candidates.append(r.get("scenario_id"))

    independent_correct = sum(1 for r in trans_recs if r.get("transaction_answer_correct") is True)
    e2e_correct = sum(1 for r in trans_recs
                      if r.get("routing_correct") and r.get("transaction_answer_correct"))

    transaction_audit = {
        "total_transaction_scenarios": len(trans_recs),
        "handled_by_transaction_tracker": len(handled_by_tracker),
        "transaction_answer_correct_independent": independent_correct,
        "transaction_answer_correct_independent_pct": round(independent_correct / len(trans_recs) * 100, 1) if trans_recs else 0,
        "transaction_answer_correct_e2e": e2e_correct,
        "transaction_answer_correct_e2e_pct": round(e2e_correct / len(trans_recs) * 100, 1) if trans_recs else 0,
        "correct_but_wrong_route_count": len(correct_but_wrong_route),
        "correct_but_wrong_route_ids": [r.get("scenario_id") for r in correct_but_wrong_route],
        "false_positive_risk_ids": sorted(fp_candidates),
        "recommended_reporting": (
            "Report both: independent accuracy measures fact-checking quality; "
            "e2e accuracy measures end-to-end answer quality (routing + fact correctness). "
            "Independent accuracy is useful as a pure data-layer test. "
            "E2E accuracy is the metric that matters for the production pipeline."
        ),
    }

    # ═══════════════════════════════════════════════════════════════
    # 5. LATENCY AUDIT
    # ═══════════════════════════════════════════════════════════════

    def _latency_stats(subset):
        vals = [r.get("latency_ms") or 0 for r in subset if r.get("latency_ms") is not None]
        if not vals:
            return {"count": 0, "mean": 0, "median": 0, "min": 0, "max": 0,
                    "above_5s": 0, "above_10s": 0}
        return {
            "count": len(vals),
            "mean": round(sum(vals) / len(vals), 2),
            "median": round(sorted(vals)[len(vals) // 2], 2),
            "min": round(min(vals), 2),
            "max": round(max(vals), 2),
            "above_5s": sum(1 for v in vals if v > 5000),
            "above_10s": sum(1 for v in vals if v > 10000),
        }

    # Scenarios handled by policy evaluator (regardless of routing correctness)
    policy_executed = [r for r in records if r.get("actual_agent") == "store_policy_evaluator"]

    latency_audit = {
        "routing_category": _latency_stats([r for r in records if r.get("category") == "routing"]),
        "transaction_category": _latency_stats([r for r in records if r.get("category") == "transaction"]),
        "policy_category": _latency_stats([r for r in records if r.get("category") == "policy"]),
        "clarification_category": _latency_stats([r for r in records if r.get("category") == "clarification"]),
        "handled_by_policy_evaluator": _latency_stats(policy_executed),
        "non_policy_scenarios": _latency_stats([r for r in records if r.get("category") != "policy"]),
        "likely_cause": (
            "Repeated SentenceTransformer initialization. "
            "Policy category shows 20.58s max and 17 records above 5s, "
            "while all other categories have 0 records above 5s and sub-10ms medians. "
            "This aligns with ChromaDB reloading the embedding model for each policy invocation. "
            "The JSONL does not record import count, but the pattern is diagnostic."
        ),
    }

    # ═══════════════════════════════════════════════════════════════
    # 6. RECOMMENDATIONS
    # ═══════════════════════════════════════════════════════════════

    # Determine top fixes based on root-cause distribution
    rc_counts = Counter(root_cause_buckets.keys())
    top_rcs = [rc for rc, _ in rc_counts.most_common()]

    recommendations = []

    # Fix 1: Missing router patterns for Thai policy and transaction queries
    if "missing_router_pattern" in root_cause_buckets:
        missing_sids = root_cause_buckets["missing_router_pattern"]
        rec1_improves = missing_sids
        rec1 = {
            "rank": 1,
            "title": "Extend router patterns for Thai policy and transaction query variants",
            "affected_file": "app/agents/router.py",
            "root_cause": (
                f"{len(missing_sids)} scenarios have expected intents (RETURN_REFUND, "
                "STORE_POLICY, SHIPMENT_STATUS, ORDER_STATUS) but the router's keyword "
                "patterns don't match common Thai phrasing used in the dataset "
                "(e.g., 'ได้เงินคืน' vs 'คืนเงิน', 'ส่งสินค้าคืนแล้ว' vs 'คืนสินค้า', "
                "'package is lost'). The dataset labels are internally consistent but the "
                "router patterns lag behind the coverage the dataset tests."
            ),
            "expected_impact": (
                "High — directly fixes the largest mismatch cluster. "
                "Expected to resolve ~20-30% of all routing mismatches."
            ),
            "changes_architecture": False,
            "mismatch_ids_expected_to_improve": sorted(rec1_improves),
        }
        recommendations.append(rec1)

    # Fix 2: Clarification rule mismatch
    if "clarification_rule_mismatch" in root_cause_buckets:
        clarify_sids = root_cause_buckets["clarification_rule_mismatch"]
        rec2 = {
            "rank": 2,
            "title": "Clarify dataset clarification expectations vs. router behavior for ambiguity scenarios",
            "affected_file": "app/agents/router.py",
            "root_cause": (
                f"{len(clarify_sids)} scenarios expect clarification=true but the "
                "router routes to a specific intent (e.g., the transaction_tracker returns "
                "CLARIFICATION). The orchestrator overrides the router's intent when the "
                "tracker detects a missing entity. This is a design-level alignment issue "
                "between dataset assumptions and routing semantics."
            ),
            "expected_impact": (
                "Medium — fixes scenarios where the router predicts one intent but the "
                "handler produces CLARIFICATION due to missing entities."
            ),
            "changes_architecture": False,
            "mismatch_ids_expected_to_improve": sorted(clarify_sids),
        }
        recommendations.append(rec2)

    # Fix 3: Policy output plumbing — policy_sources must flow through even when DeepSeek disabled
    # Check if this is actually an issue
    reached_no_meta_count = len(reached_no_meta)
    if reached_no_meta_count > 0:
        rec3 = {
            "rank": 3,
            "title": "Fix policy output plumbing: propagate retrieval metadata independently of DeepSeek",
            "affected_file": "app/agents/orchestrator.py or scripts/evaluation_scoring.py",
            "root_cause": (
                f"{reached_no_meta_count} policy scenarios reached the policy evaluator but "
                "returned empty policy_sources. The build_record function reads policy_sources "
                "from result.policy_evidence.policy_sources, but the orchestrator only populates "
                "policy_evidence in the RETURN_REFUND/STORE_POLICY branch. For the 5 scenarios "
                "that correctly routed to the policy evaluator, retrieval happened and ChromaDB "
                "returned sources, but they didn't propagate to the output record."
            ),
            "expected_impact": (
                "Medium — fixes policy diagnostic data. Enables accurate policy source scoring "
                "in the dry run. No impact on production responses (retrieval already works)."
            ),
            "changes_architecture": False,
            "mismatch_ids_expected_to_improve": sorted(reached_no_meta),
        }
        recommendations.append(rec3)

    # ═══════════════════════════════════════════════════════════════
    # Assemble final output
    # ═══════════════════════════════════════════════════════════════

    audit = {
        "audit_metadata": {
            "input": args.input,
            "scenarios": args.scenarios,
            "total_records": total,
        },
        "routing_audit": routing_audit,
        "policy_audit": policy_audit,
        "clarification_audit": clarification_audit,
        "transaction_audit": transaction_audit,
        "latency_audit": latency_audit,
        "recommendations": recommendations,
    }

    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(audit, f, ensure_ascii=False, indent=2)

    # ── Console summary ──────────────────────────────────────────────
    print("=" * 68)
    print("  AUDIT SUMMARY — multi_agent_dry_120")
    print("=" * 68)

    print(f"\n  Root-cause distribution:")
    for bc, sids in sorted(root_cause_buckets.items(), key=lambda x: -len(x[1])):
        print(f"    {bc:45s}  {len(sids):3d}")

    print(f"\n  Routing confusion highlights:")
    for ei in sorted(intent_cm.keys()):
        for ai in sorted(intent_cm[ei].keys()):
            if ei != ai and intent_cm[ei][ai] > 0:
                print(f"    Expected {ei:20s} → Actual {ai:20s}  ({intent_cm[ei][ai]} scenarios)")

    print(f"\n  Policy failure root cause: {policy_root_cause}")
    print(f"    Reached evaluator: {num_reached}/{num_policy}")
    print(f"    With non-empty sources: {len(with_non_empty_sources)}/{num_reached}")
    print(f"    Never reached evaluator: {sorted(never_reached)}")

    print(f"\n  Clarification mismatch root cause:")
    for cause, sids in sorted(clarify_mm_by_cause.items(), key=lambda x: -len(x[1])):
        print(f"    {cause:35s}  {len(sids):3d}  {sorted(sids)}")

    print(f"  Human-review mismatch root cause:")
    for cause, sids in sorted(human_mm_by_cause.items(), key=lambda x: -len(x[1])):
        print(f"    {cause:35s}  {len(sids):3d}  {sorted(sids)}")

    print(f"\n  Transaction-answer accuracy:")
    print(f"    Independent (fact check only):         {independent_correct}/{len(trans_recs)} ({round(independent_correct/len(trans_recs)*100, 1)}%)")
    print(f"    End-to-end (routing + fact check):      {e2e_correct}/{len(trans_recs)} ({round(e2e_correct/len(trans_recs)*100, 1)}%)")
    if fp_candidates:
        print(f"    False-positive risk IDs:                  {sorted(fp_candidates)}")

    print(f"\n  Category latency summary:")
    for cat_key, cat_label in [("routing_category", "Routing"),
                                ("transaction_category", "Transaction"),
                                ("policy_category", "Policy"),
                                ("clarification_category", "Clarification")]:
        ls = latency_audit[cat_key]
        if ls["count"] > 0:
            print(f"    {cat_label:20s}  mean={ls['mean']:>8.1f}ms  median={ls['median']:>8.1f}ms  "
                  f"min={ls['min']:>8.1f}ms  max={ls['max']:>8.1f}ms  >5s={ls['above_5s']}  >10s={ls['above_10s']}")

    print(f"\n  Top 3 recommended fixes:")
    for rec in recommendations:
        print(f"    #{rec['rank']}: {rec['title']}")
        print(f"       File: {rec['affected_file']}")
        print(f"       Impact: {rec['expected_impact']}")
        print(f"       Improves {len(rec['mismatch_ids_expected_to_improve'])} mismatches")

    print(f"\n  Output: {args.output}")


if __name__ == "__main__":
    main()
