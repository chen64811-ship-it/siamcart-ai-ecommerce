"""Independent verifier for final_revision_2026 artifacts.

This file intentionally does not import the experiment runner or application
modules. It recomputes counts, scoring aggregates, confusion matrices, and
latency statistics from the raw JSONL records.
"""

from __future__ import annotations

import csv
import hashlib
import json
import re
import statistics
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple


BASE_DIR = Path(__file__).resolve().parents[2]
OUT_DIR = BASE_DIR / "evaluation" / "final_revision_2026"
FROZEN = OUT_DIR / "scenarios_120_frozen.json"
SOURCE_DATASET = BASE_DIR / "data" / "evaluation" / "all_scenarios.json"
PRODUCTION_DB = BASE_DIR / "data" / "orders.db"
EXPECTED_DATASET_SHA256 = "ea45e38b068285dfd0ae174047773b05654313faac718a19d981b991900bb137"
RUN_IDS = ("run_1", "run_2", "run_3")


REQUIRED_FIELDS = {
    "run_id", "scenario_id", "system_type", "category", "language", "input_type",
    "user_input", "expected_intent", "expected_answer_criteria", "actual_intent",
    "actual_response", "routing_correct", "transaction_answer_correct",
    "policy_answer_correct", "clarification_triggered", "escalation_triggered",
    "execution_success", "latency_ms", "llm_model", "llm_call_count", "retry_count",
    "error_type", "error_message_sanitized", "timestamp", "manual_review_required",
}


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def read_jsonl(path: Path) -> List[Dict[str, Any]]:
    with path.open("r", encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


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


def confusion_metric(records: List[Dict[str, Any]], expected_field: str, actual_field: str) -> Dict[str, Any]:
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
        "sample_standard_deviation_ms": round(statistics.stdev(values), 6) if len(values) > 1 else 0.0,
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
        "clarification": confusion_metric(records, "expected_requires_clarification", "clarification_triggered"),
        "escalation": confusion_metric(records, "expected_simulated_human_review", "escalation_triggered"),
        "latency": latency_metrics(records),
        "manual_review_case_count": sum(1 for r in records if r.get("manual_review_required") is True),
        "llm_call_count": {
            "total": sum(int(r.get("llm_call_count") or 0) for r in records),
            "scenarios_with_calls": sum(1 for r in records if int(r.get("llm_call_count") or 0) > 0),
        },
        "error_counts": dict(sorted(Counter(str(r.get("error_type")) for r in records).items())),
    }


def bootstrap_mean_ci(values: List[float]) -> Dict[str, Any]:
    import random

    rng = random.Random(20260901)
    means = []
    for _ in range(10_000):
        means.append(sum(rng.choice(values) for _ in values) / len(values))
    return {
        "method": "nonparametric bootstrap confidence interval for the mean",
        "resamples": 10_000,
        "seed": 20260901,
        "confidence_level": 0.95,
        "lower_ms": linear_percentile(means, 0.025),
        "upper_ms": linear_percentile(means, 0.975),
    }


def pooled_metrics(records: List[Dict[str, Any]]) -> Dict[str, Any]:
    result = compute_system_metrics(records)
    result["latency"] = latency_metrics(records)
    result["latency"]["bootstrap_mean_ci_95"] = bootstrap_mean_ci(
        [float(record.get("latency_ms") or 0.0) for record in records]
    )
    return result


def secret_hits() -> List[str]:
    patterns = [
        re.compile(r"sk-[A-Za-z0-9_-]{12,}", re.IGNORECASE),
        re.compile(r"Bearer\s+[A-Za-z0-9._~+/=-]{12,}", re.IGNORECASE),
        re.compile(r"DEEPSEEK_API_KEY\s*=\s*(?!\[REDACTED\])\S+", re.IGNORECASE),
        re.compile(r"JWT_SECRET_KEY\s*=\s*(?!\[REDACTED\])\S+", re.IGNORECASE),
        re.compile(r"RAILWAY_TOKEN\s*=\s*(?!\[REDACTED\])\S+", re.IGNORECASE),
    ]
    hits = []
    for path in OUT_DIR.rglob("*"):
        if not path.is_file() or path.suffix.lower() in {".db", ".sqlite", ".sqlite3"}:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        if any(pattern.search(text) for pattern in patterns):
            hits.append(str(path))
    return hits


def compare_expected_ids(records: List[Dict[str, Any]], scenarios: List[Dict[str, Any]], run_id: str, system_type: str) -> List[str]:
    errors = []
    expected_ids = [scenario["scenario_id"] for scenario in scenarios]
    actual_ids = [record.get("scenario_id") for record in records]
    if actual_ids != expected_ids:
        errors.append(f"{run_id}/{system_type} scenario order or IDs differ")
    if len(actual_ids) != len(set(actual_ids)):
        errors.append(f"{run_id}/{system_type} duplicate scenario IDs")
    for record in records:
        missing = sorted(REQUIRED_FIELDS - set(record))
        if missing:
            errors.append(f"{run_id}/{system_type}/{record.get('scenario_id')} missing fields: {','.join(missing)}")
        if record.get("run_id") != run_id or record.get("system_type") != system_type:
            errors.append(f"{run_id}/{system_type}/{record.get('scenario_id')} tuple labels mismatch")
    return errors


def main() -> int:
    errors: List[str] = []
    try:
        source_hash = sha256_file(SOURCE_DATASET)
        frozen_hash = sha256_file(FROZEN)
        if source_hash != EXPECTED_DATASET_SHA256:
            errors.append("authoritative dataset hash differs from expected")
        if frozen_hash != EXPECTED_DATASET_SHA256:
            errors.append("frozen scenario hash differs from expected")
        scenarios = read_json(FROZEN)
        if not isinstance(scenarios, list) or len(scenarios) != 120:
            errors.append("frozen scenario count is not 120")
        if scenarios != read_json(SOURCE_DATASET):
            errors.append("frozen scenarios are not semantically identical to source")

        all_system_records: Dict[str, List[Dict[str, Any]]] = {"multi_agent": [], "monolithic": []}
        for run_id in RUN_IDS:
            run_dir = OUT_DIR / run_id
            for system_type, filename in (("multi_agent", "multi_agent_raw.jsonl"), ("monolithic", "monolithic_raw.jsonl")):
                records = read_jsonl(run_dir / filename)
                if len(records) != 120:
                    errors.append(f"{run_id}/{system_type} record count is {len(records)}, expected 120")
                errors.extend(compare_expected_ids(records, scenarios, run_id, system_type))
                all_system_records[system_type].extend(records)
            combined_path = run_dir / "combined_results.csv"
            with combined_path.open("r", encoding="utf-8-sig", newline="") as handle:
                combined = list(csv.DictReader(handle))
            if len(combined) != 240:
                errors.append(f"{run_id} combined CSV row count is {len(combined)}, expected 240")
            tuples = {(row.get("run_id"), row.get("scenario_id"), row.get("system_type")) for row in combined}
            if len(tuples) != len(combined):
                errors.append(f"{run_id} combined CSV has duplicate run/scenario/system tuples")
            run_metrics = read_json(run_dir / "run_metrics.json")
            recomputed = {
                "multi_agent": compute_system_metrics(read_jsonl(run_dir / "multi_agent_raw.jsonl")),
                "monolithic": compute_system_metrics(read_jsonl(run_dir / "monolithic_raw.jsonl")),
            }
            if run_metrics.get("systems") != recomputed:
                errors.append(f"{run_id} run_metrics.json does not match recomputed raw metrics")
            manifest = read_json(run_dir / "run_manifest.json")
            if manifest.get("scenario_file_sha256") != EXPECTED_DATASET_SHA256:
                errors.append(f"{run_id} manifest scenario hash mismatch")

        pooled_expected_rows = sum(len(records) for records in all_system_records.values())
        if pooled_expected_rows != 720:
            errors.append(f"pooled raw row count is {pooled_expected_rows}, expected 720")
        if len({(r.get("run_id"), r.get("scenario_id"), r.get("system_type")) for records in all_system_records.values() for r in records}) != 720:
            errors.append("pooled raw records contain duplicate tuples")

        with (OUT_DIR / "policy_manual_review.csv").open("r", encoding="utf-8-sig", newline="") as handle:
            policy_rows = list(csv.DictReader(handle))
        if len(policy_rows) != 180:
            errors.append(f"policy manual-review row count is {len(policy_rows)}, expected 180")
        for row in policy_rows:
            if row.get("reviewer_score", "") != "" or row.get("reviewer_notes", "") != "":
                errors.append("policy manual-review reviewer fields are not blank")
                break

        summary = read_json(OUT_DIR / "final_metrics_summary.json")
        recomputed_summary = {
            "multi_agent": {
                "per_run": {
                    run_id: compute_system_metrics(read_jsonl(OUT_DIR / run_id / "multi_agent_raw.jsonl"))
                    for run_id in RUN_IDS
                },
                "pooled": pooled_metrics(all_system_records["multi_agent"]),
            },
            "monolithic": {
                "per_run": {
                    run_id: compute_system_metrics(read_jsonl(OUT_DIR / run_id / "monolithic_raw.jsonl"))
                    for run_id in RUN_IDS
                },
                "pooled": pooled_metrics(all_system_records["monolithic"]),
            },
        }
        if summary.get("systems") != recomputed_summary:
            errors.append("final_metrics_summary.json systems do not match recomputed raw metrics")
        if summary.get("policy_manual_review_rows") != 180:
            errors.append("final summary policy manual-review count is not 180")

        integrity = read_json(OUT_DIR / "environment" / "production_db_integrity.json")
        if not integrity.get("unchanged"):
            errors.append("production database integrity check is not unchanged")
        if integrity.get("exists_after") and sha256_file(PRODUCTION_DB) != integrity.get("sha256_before"):
            errors.append("production data/orders.db current hash differs from before hash")

        source_snapshot = read_json(OUT_DIR / "environment" / "source_hashes.json")
        for entry in source_snapshot.get("files", []):
            path = Path(entry["path"])
            if not path.exists() or sha256_file(path) != entry.get("sha256"):
                errors.append(f"source hash mismatch: {entry.get('relative_path')}")
        errors.extend(f"secret detected in {path}" for path in secret_hits())
    except Exception as exc:
        errors.append(f"verifier exception: {type(exc).__name__}: {exc}")

    if errors:
        print("VERIFICATION FAILED")
        for error in errors:
            print(error)
        return 1
    print("VERIFICATION PASSED")
    print("frozen_scenarios=120")
    print("multi_agent_per_run=3x120")
    print("monolithic_per_run=3x120")
    print("pooled_rows=720")
    print("policy_manual_review_rows=180")
    print(f"scenario_file_sha256={sha256_file(FROZEN)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
