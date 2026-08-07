"""
Multi-Agent Evaluation Runner — Phase 4B-1A.

Loads scenarios from all_scenarios.json, sends each user_message
through the existing production orchestrator, and records raw results
for later scoring.

Usage:
  py -3.11 scripts/run_multi_agent_evaluation.py --scenario-ids ROUTE-001 ... --run-name NAME --disable-llm
  py -3.11 scripts/run_multi_agent_evaluation.py --limit 10 --run-name NAME --disable-llm

Output:
  data/evaluation/results/<run-name>.jsonl
  data/evaluation/results/<run-name>.csv
"""

import argparse
import csv
import json
import os
import sys
import time
import io

# ── Early env override ────────────────────────────────────────────────
# Must happen before any application import.
parser = argparse.ArgumentParser(description="Multi-agent evaluation runner")
parser.add_argument("--scenario-ids", nargs="*", default=None,
                    help="Explicit scenario IDs to run (space-separated)")
parser.add_argument("--limit", type=int, default=None,
                    help="Max scenarios to run from the combined dataset")
parser.add_argument("--run-name", required=True,
                    help="Output name (JSONL and CSV file prefix)")
parser.add_argument("--disable-llm", action="store_true",
                    help="Force DeepSeek off before importing app modules")
args, _ = parser.parse_known_args()

if args.disable_llm:
    os.environ["DEEPSEEK_ENABLED"] = "false"

# ── Imports (after env override) ──────────────────────────────────────
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.agents.orchestrator import process_message, set_db_path
from app.agents.router import route_message
from app.config import DATA_DIR, DEEPSEEK_ENABLED
from evaluation_scoring import score_record


# ── Helpers ───────────────────────────────────────────────────────────

def load_scenarios(path: str) -> list[dict]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def resolve_scenarios(scenarios: list[dict], scenario_ids: list[str] | None, limit: int | None) -> list[dict]:
    if scenario_ids:
        # Build lookup
        lookup = {s["scenario_id"]: s for s in scenarios}
        missing = [sid for sid in scenario_ids if sid not in lookup]
        if missing:
            print(f"ERROR: scenario ID(s) not found: {', '.join(missing)}")
            sys.exit(1)
        return [lookup[sid] for sid in scenario_ids]

    if limit is not None:
        return scenarios[:limit]

    print("ERROR: provide either --scenario-ids or --limit to select scenarios")
    sys.exit(1)


def build_record(scenario: dict, result: dict | None = None,
                 error: str | None = None,
                 route_result: dict | None = None) -> dict:
    """Build one output record with expected + actual + router-stage fields."""
    expected = scenario

    record = {
        # Scenario metadata
        "scenario_id": expected.get("scenario_id"),
        "category": expected.get("category"),
        "subcategory": expected.get("subcategory"),
        "language": expected.get("language"),
        "input_type": expected.get("input_type"),
        "user_message": expected.get("user_message"),

        # Expected fields
        "expected_intent": expected.get("expected_intent"),
        "expected_agent": expected.get("expected_agent"),
        "expected_entities": expected.get("expected_entities"),
        "expected_requires_clarification": expected.get("requires_clarification"),
        "expected_simulated_human_review": expected.get("simulated_human_review"),
        "expected_answer_facts": expected.get("expected_answer_facts"),

        # Router-stage fields (pure router prediction)
        "router_intent": None,
        "router_agent": None,
        "router_confidence": None,
        "router_reason": None,
        "router_requires_clarification": None,
        "router_simulated_human_review": None,
        "router_extracted_order_id": None,
        "router_missing_entities": None,

        # Actual / final fields (null when error or unavailable)
        "actual_intent": None,
        "actual_agent": None,
        "final_intent": None,
        "final_agent": None,
        "actual_response": None,
        "actual_requires_clarification": None,
        "actual_simulated_human_review": None,
        "response_source": None,
        "retrieval_success": None,
        "retrieved_chunk_count": None,
        "policy_sources": None,
        "grounding_validation_passed": None,
        "llm_fallback_used": None,
        "llm_error_type": None,
        "routing_confidence": None,
        "routing_reason": None,
        "latency_ms": None,

        # Execution meta
        "execution_status": "error" if error else "success",
        "execution_error": error,
    }

    if route_result:
        record["router_intent"] = route_result.get("intent")
        record["router_agent"] = route_result.get("target_agent")
        record["router_confidence"] = route_result.get("confidence")
        record["router_reason"] = route_result.get("routing_reason")
        record["router_requires_clarification"] = route_result.get("requires_clarification")
        record["router_simulated_human_review"] = route_result.get("simulated_human_review")
        record["router_extracted_order_id"] = route_result.get("extracted_order_id")
        record["router_missing_entities"] = route_result.get("missing_entities")

    if result:
        policy_ev = result.get("policy_evidence") or {}

        record["actual_intent"] = result.get("intent")
        record["actual_agent"] = result.get("agent")
        record["final_intent"] = result.get("intent")
        record["final_agent"] = result.get("agent")
        record["actual_response"] = result.get("response")
        record["actual_requires_clarification"] = result.get("requires_clarification")
        record["actual_simulated_human_review"] = result.get("simulated_human_review")
        record["response_source"] = result.get("response_source")
        record["retrieval_success"] = policy_ev.get("retrieval_success")
        record["retrieved_chunk_count"] = policy_ev.get("retrieved_chunk_count")
        record["policy_sources"] = policy_ev.get("policy_sources")
        record["grounding_validation_passed"] = result.get("grounding_validation_passed")
        record["llm_fallback_used"] = result.get("llm_fallback_used")
        record["llm_error_type"] = result.get("llm_error_type")
        record["routing_confidence"] = result.get("routing_confidence")
        record["routing_reason"] = result.get("routing_reason")
        record["latency_ms"] = result.get("latency_ms")

    return record


def write_jsonl(records: list[dict], path: str):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for rec in records:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def write_csv(records: list[dict], path: str):
    """Write CSV with BOM; nested objects encoded as JSON strings."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if not records:
        return

    # Collect all keys, preserving order from first record
    fieldnames = list(records[0].keys())

    # Ensure all records have every key
    for rec in records:
        for k in fieldnames:
            rec.setdefault(k, None)

    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for rec in records:
            row = {}
            for k in fieldnames:
                v = rec.get(k)
                # Serialize dicts and lists as JSON strings
                if isinstance(v, (dict, list)):
                    row[k] = json.dumps(v, ensure_ascii=False)
                else:
                    row[k] = v
            writer.writerow(row)


# ── Main ──────────────────────────────────────────────────────────────

def main():
    t_start = time.perf_counter()

    # Resolve CLI args
    sid_list = args.scenario_ids
    limit = args.limit
    run_name = args.run_name
    llm_disabled = args.disable_llm or os.environ.get("DEEPSEEK_ENABLED", "").lower() != "true"

    # Resolve selection (exits on error)
    scenarios_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                  "data", "evaluation", "all_scenarios.json")
    all_scenarios = load_scenarios(scenarios_path)
    selected = resolve_scenarios(all_scenarios, sid_list, limit)

    # Set up database path for transaction tracker
    db_path = str(DATA_DIR / "orders.db")
    set_db_path(db_path)

    # Run
    records: list[dict] = []
    total_scenarios = len(selected)
    success_count = 0
    error_count = 0

    for scenario in selected:
        scenario_id = scenario["scenario_id"]
        session_id = f"evaluation-{scenario_id.lower()}"
        message = scenario["user_message"]

        try:
            t_scenario = time.perf_counter()
            # Step 1: Router-stage prediction (pure, stateless)
            route_result = route_message(message)
            # Step 2: Full orchestrator execution
            raw_result = process_message(message, session_id=session_id)
            # Use orchestrator's own latency for consistency
            record = build_record(scenario, result=raw_result, route_result=route_result)
            record = score_record(record, raw_result=raw_result, route_result=route_result)
            success_count += 1
        except Exception as exc:
            record = build_record(scenario, error=f"{type(exc).__name__}: {exc}")
            record = score_record(record, raw_result=None)
            error_count += 1
        finally:
            records.append(record)

    t_elapsed = time.perf_counter() - t_start
    output_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                              "data", "evaluation", "results")
    jsonl_path = os.path.join(output_dir, f"{run_name}.jsonl")
    csv_path = os.path.join(output_dir, f"{run_name}.csv")

    write_jsonl(records, jsonl_path)
    write_csv(records, csv_path)

    # ── Load audit for annotation review queue ─────────────────────────
    audit_path = os.path.join(output_dir, "multi_agent_dry_120_audit.json")
    annotation_review_queue = []
    try:
        with open(audit_path, "r", encoding="utf-8") as f:
            audit_data = json.load(f)
        rc_buckets = audit_data.get("routing_audit", {}).get("root_cause_buckets", {})
        annotation_review_queue = sorted(set(
            rc_buckets.get("dataset_expected_label_inconsistent", [])
        ))
    except (FileNotFoundError, KeyError, json.JSONDecodeError):
        annotation_review_queue = []

    # Category counts
    cat_counts: dict[str, int] = {}
    for r in records:
        cat = r.get("category", "unknown")
        cat_counts[cat] = cat_counts.get(cat, 0) + 1

    # ── Router-stage routing accuracy ────────────────────────────────
    router_routing_correct = sum(1 for r in records if r.get("routing_correct"))
    router_pct = router_routing_correct / total_scenarios * 100 if total_scenarios else 0.0

    cats = sorted(cat_counts.keys())
    router_by_cat = {}
    for cat in cats:
        total_cat = cat_counts[cat]
        correct_cat = sum(1 for r in records if r.get("category") == cat and r.get("routing_correct"))
        router_by_cat[cat] = f"{correct_cat}/{total_cat}"

    # ── Router → final intent transitions ────────────────────────────
    transitions: dict[tuple, int] = {}
    for r in records:
        ri = r.get("router_intent") or "NONE"
        fi = r.get("final_intent") or "NONE"
        key = (ri, fi)
        transitions[key] = transitions.get(key, 0) + 1
    sorted_transitions = sorted(transitions.items(), key=lambda x: -x[1])

    # ── Transaction metrics ──────────────────────────────────────────
    trans_records = [r for r in records if r.get("category") == "transaction"]
    if trans_records:
        trans_independent = sum(1 for r in trans_records if r.get("transaction_answer_correct"))
        trans_independent_pct = trans_independent / len(trans_records) * 100
        trans_e2e = sum(1 for r in trans_records if r.get("transaction_end_to_end_correct"))
        trans_e2e_pct = trans_e2e / len(trans_records) * 100
    else:
        trans_independent = trans_independent_pct = trans_e2e = trans_e2e_pct = 0

    # ── Flag accuracy ────────────────────────────────────────────────
    clarify_flag_correct = sum(1 for r in records if r.get("clarification_flag_correct"))
    clarify_pct = clarify_flag_correct / total_scenarios * 100 if total_scenarios else 0.0
    human_flag_correct = sum(1 for r in records if r.get("human_review_flag_correct"))
    human_pct = human_flag_correct / total_scenarios * 100 if total_scenarios else 0.0

    # ── Policy diagnostics ───────────────────────────────────────────
    policy_records = [r for r in records if r.get("category") == "policy"]
    if policy_records:
        pol_reached = sum(1 for r in policy_records if r.get("actual_agent") == "store_policy_evaluator")
        pol_source_correct = sum(1 for r in policy_records if r.get("policy_source_correct"))
        pol_source_pct = pol_source_correct / len(policy_records) * 100
    else:
        pol_reached = pol_source_correct = 0
        pol_source_pct = 0.0

    # ── Genuine router mismatches (not annotation-review) ────────────
    review_set = set(annotation_review_queue)
    genuine_mismatches = [
        r.get("scenario_id") for r in records
        if not r.get("routing_correct")
        and r.get("scenario_id") not in review_set
    ]

    # Latency
    latencies = [r.get("latency_ms") or 0 for r in records if r.get("latency_ms") is not None]
    if latencies:
        avg_latency = sum(latencies) / len(latencies)
        min_latency = min(latencies)
        max_latency = max(latencies)
        sorted_lat = sorted(latencies)
        median_latency = sorted_lat[len(sorted_lat) // 2]
    else:
        avg_latency = min_latency = max_latency = median_latency = 0.0

    # DeepSeek check
    deepseek_invoked = any(
        r.get("execution_status") == "success" and r.get("llm_fallback_used") for r in records
    )

    # ── Summary ──────────────────────────────────────────────────────
    print(f"Selected scenarios:    {total_scenarios}")
    print(f"Success:               {success_count}")
    print(f"Errors:                {error_count}")
    print(f"Categories:            {cat_counts}")
    print(f"")
    print(f"── Router-Stage Routing Accuracy ───────────────────────────")
    print(f"Router routing correct:    {router_routing_correct}/{total_scenarios} ({router_pct:.1f}%)")
    print(f"Router by category:        {router_by_cat}")
    print(f"")
    print(f"── Router → Final Intent Transitions ───────────────────────")
    print(f"Transition count:          {len(transitions)}")
    print(f"Most common transitions:")
    for (ri, fi), count in sorted_transitions[:10]:
        print(f"  {ri:20s} → {fi:20s}  ({count})")
    print(f"")
    print(f"── Transaction Accuracy ────────────────────────────────────")
    if trans_records:
        print(f"Independent (fact check):  {trans_independent}/{len(trans_records)} ({trans_independent_pct:.1f}%)")
        print(f"End-to-end (route+facts):  {trans_e2e}/{len(trans_records)} ({trans_e2e_pct:.1f}%)")
    else:
        print(f"Independent (fact check):  N/A")
        print(f"End-to-end (route+facts):  N/A")
    print(f"")
    print(f"── Flag Accuracy ───────────────────────────────────────────")
    print(f"Clarification flag:        {clarify_flag_correct}/{total_scenarios} ({clarify_pct:.1f}%)")
    print(f"Human-review flag:         {human_flag_correct}/{total_scenarios} ({human_pct:.1f}%)")
    print(f"")
    print(f"── Policy Diagnostics ──────────────────────────────────────")
    print(f"Reached policy evaluator:  {pol_reached}/{len(policy_records) if policy_records else 0}")
    print(f"Policy source correct:     {pol_source_correct}/{len(policy_records) if policy_records else 0} ({pol_source_pct:.1f}%)")
    print(f"")
    print(f"── Latency ─────────────────────────────────────────────────")
    print(f"Mean:                      {avg_latency:.2f}ms")
    print(f"Median:                    {median_latency:.2f}ms")
    print(f"Maximum:                   {max_latency:.2f}ms")
    print(f"")
    print(f"── Mismatches ──────────────────────────────────────────────")
    print(f"Annotation review queue:   {len(annotation_review_queue)}")
    print(f"  IDs (first 20):          {annotation_review_queue[:20]}")
    print(f"Genuine router mismatches: {len(genuine_mismatches)}")
    print(f"  IDs (first 20):          {genuine_mismatches[:20]}")
    print(f"")
    print(f"── DeepSeek & Output ───────────────────────────────────────")
    print(f"DeepSeek disabled:         {llm_disabled}")
    print(f"DeepSeek invoked:          {deepseek_invoked}")
    print(f"Output JSONL:              {jsonl_path}")
    print(f"Output CSV:                {csv_path}")
    print(f"Total runtime:             {t_elapsed:.2f}s")
    print(f"Output fields/record:      {len(records[0]) if records else 0}")


if __name__ == "__main__":
    main()
