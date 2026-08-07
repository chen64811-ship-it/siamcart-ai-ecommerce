"""Deterministic scoring for multi-agent evaluation records — Phase 4B-1B.

Pure functions that add scoring fields to each evaluation record.
No network calls, no LLM, no database access.
"""

import json
import re
from typing import Dict, List, Optional, Tuple


# ── Helpers ─────────────────────────────────────────────────────────────

def _parse_facts(facts: list) -> Dict[str, str]:
    """Parse ['key=value', ...] into {key: value}."""
    result = {}
    for fact in facts:
        if "=" in fact:
            key, _, value = fact.partition("=")
            result[key.strip()] = value.strip()
    return result


def _extract_source_from_notes(notes: str) -> Optional[str]:
    """Extract source filename from notes like 'source=refund_policy.md; ...'."""
    m = re.search(r"source=([^;]+)", notes)
    return m.group(1).strip() if m else None


def _normalise_for_matching(text: str) -> str:
    """Normalise text for case-insensitive, whitespace-tolerant matching."""
    t = text.lower()
    t = re.sub(r"\s+", " ", t)
    t = t.replace("\u2010", "-").replace("\u2011", "-").replace("\u2012", "-").replace("\u2013", "-")
    t = t.replace("\u2014", "-").replace("\u2212", "-")
    return t.strip()


def _value_in_response(value: str, response: str) -> bool:
    """Check if a fact value appears in the normalised response."""
    norm_val = _normalise_for_matching(value)
    norm_resp = _normalise_for_matching(response)
    return norm_val in norm_resp


# ── Transaction fact scoring ───────────────────────────────────────────

def _score_transaction_facts(
    expected_facts: Dict[str, str],
    record: dict,
    evidence: Optional[dict],
) -> Tuple[int, int]:
    """Score transaction facts against structured evidence or response text.

    Returns (matches, total).
    """
    total = len(expected_facts)
    if total == 0:
        return 0, 0

    actual_response = record.get("actual_response") or ""
    actual_intent = record.get("actual_intent") or ""
    matches = 0

    for key, expected_value in expected_facts.items():
        # Special case: order_not_found=true
        if key == "order_not_found" and expected_value == "true":
            # Accept structured ORDER_NOT_FOUND intent
            if actual_intent == "ORDER_NOT_FOUND":
                matches += 1
                continue
            # Accept explicit not-found response text
            resp_lower = _normalise_for_matching(actual_response)
            if any(phrase in resp_lower for phrase in [
                "not found", "not found", "no information",
                "ไม่พบข้อมูล", "ไม่พบคำสั่งซื้อ",
            ]):
                matches += 1
                continue
            matches += 0
            continue

        # Try structured evidence first
        if evidence and key in evidence:
            ev_value = str(evidence[key])
            if ev_value == expected_value:
                matches += 1
                continue
            # Accept empty-matches-empty (e.g. tracking_number="" in both)
            if expected_value == "" and (ev_value == "" or ev_value is None):
                matches += 1
                continue

        # Fallback: response text matching
        if _value_in_response(expected_value, actual_response):
            matches += 1
            continue

        # No match
        matches += 0

    return matches, total


# ── Policy anchor scoring ──────────────────────────────────────────────

def _extract_policy_anchors(facts: List[str]) -> Dict[str, str]:
    """Extract factual anchors from expected_answer_facts for policy scenarios.

    Returns {anchor_key: normalized_value}.
    """
    anchors = {}
    for fact in facts:
        if "=" in fact:
            key, _, value = fact.partition("=")
            anchors[key.strip()] = value.strip()
    return anchors


def _score_policy_anchors(
    anchors: Dict[str, str],
    actual_response: str,
) -> Tuple[int, int]:
    """Score policy anchors by checking whether each value appears in the response.

    Number ranges, currencies, percentages, day/hour periods, and channel names
    are all matched via normalised substring matching.

    Returns (matches, total).
    """
    total = len(anchors)
    if total == 0:
        return 0, 0

    resp = _normalise_for_matching(actual_response)
    matches = 0

    for key, expected_value in anchors.items():
        nv = _normalise_for_matching(expected_value)
        if nv in resp:
            matches += 1
            continue
        # If the value contains "=" (unlikely), fall through
        matches += 0

    return matches, total


# ── Main scoring entry point ──────────────────────────────────────────

def score_record(record: dict, raw_result: Optional[dict] = None,
                 route_result: Optional[dict] = None) -> dict:
    """Add all scoring fields to a record. Mutates and returns it.

    Args:
        record: The built output record (from build_record).
        raw_result: The raw orchestrator return value for this scenario.
        route_result: The raw router return value (for router-stage scoring).

    Returns: The same record dict with scoring fields appended.
    """
    category = record.get("category")

    # ── Router-stage scoring (compare expected vs router prediction) ──
    router_intent = record.get("router_intent")
    expected_intent = record.get("expected_intent")
    router_agent = record.get("router_agent")
    expected_agent = record.get("expected_agent")

    record["intent_correct"] = (router_intent == expected_intent)
    record["agent_correct"] = (router_agent == expected_agent)
    record["routing_correct"] = record["intent_correct"] and record["agent_correct"]

    exp_clarify = record.get("expected_requires_clarification")
    act_clarify = record.get("actual_requires_clarification")
    record["clarification_flag_correct"] = (act_clarify == exp_clarify)

    exp_human = record.get("expected_simulated_human_review")
    act_human = record.get("actual_simulated_human_review")
    record["human_review_flag_correct"] = (act_human == exp_human)

    record["escalation_flags_correct"] = (
        record["clarification_flag_correct"] and record["human_review_flag_correct"]
    )

    # ── Transaction scoring ──────────────────────────────────────────
    if category == "transaction":
        expected_facts = record.get("expected_answer_facts") or []
        fact_dict = _parse_facts(expected_facts)

        # Get structured evidence from the raw orchestrator result
        evidence = (raw_result or {}).get("evidence") or {}

        matches, total = _score_transaction_facts(fact_dict, record, evidence)
        record["transaction_fact_matches"] = matches
        record["transaction_fact_total"] = total
        record["transaction_fact_score"] = matches / total if total > 0 else 0.0
        record["transaction_answer_correct"] = record["transaction_fact_score"] == 1.0
        record["transaction_end_to_end_correct"] = (
            record.get("routing_correct") is True
            and record["transaction_answer_correct"] is True
        )

        # Non-transaction fields → null
        record["policy_expected_source"] = None
        record["policy_source_correct"] = None
        record["policy_anchor_matches"] = None
        record["policy_anchor_total"] = None
        record["policy_anchor_score"] = None
        record["policy_grounding_correct"] = None
        record["policy_review_required"] = None

    # ── Policy diagnostic scoring ────────────────────────────────────
    elif category == "policy":
        notes = record.get("notes") or ""
        expected_source = _extract_source_from_notes(notes)
        record["policy_expected_source"] = expected_source

        actual_sources = record.get("policy_sources") or []
        if actual_sources is None:
            actual_sources = []
        record["policy_source_correct"] = (
            expected_source in actual_sources if expected_source else False
        )

        record["policy_grounding_correct"] = record.get("grounding_validation_passed")

        expected_facts = record.get("expected_answer_facts") or []
        anchors = _extract_policy_anchors(expected_facts)
        actual_response = record.get("actual_response") or ""
        anchor_matches, anchor_total = _score_policy_anchors(anchors, actual_response)
        record["policy_anchor_matches"] = anchor_matches
        record["policy_anchor_total"] = anchor_total
        record["policy_anchor_score"] = anchor_matches / anchor_total if anchor_total > 0 else 0.0

        # Every policy answer needs human review after real DeepSeek run
        record["policy_review_required"] = True

        # Non-policy fields → null
        record["transaction_fact_matches"] = None
        record["transaction_fact_total"] = None
        record["transaction_fact_score"] = None
        record["transaction_answer_correct"] = None
        record["transaction_end_to_end_correct"] = None

    # ── Routing / Clarification → null for specialised fields ────────
    else:
        record["transaction_fact_matches"] = None
        record["transaction_fact_total"] = None
        record["transaction_fact_score"] = None
        record["transaction_answer_correct"] = None
        record["transaction_end_to_end_correct"] = None
        record["policy_expected_source"] = None
        record["policy_source_correct"] = None
        record["policy_anchor_matches"] = None
        record["policy_anchor_total"] = None
        record["policy_anchor_score"] = None
        record["policy_grounding_correct"] = None
        record["policy_review_required"] = None

    return record
