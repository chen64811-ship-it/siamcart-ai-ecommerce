#!/usr/bin/env python3
"""Reusable per-turn evidence logger for the Phase 7 continuous-conversation audit.

Waits for logs/experiment.jsonl to grow to a target line count, then prints the
parsed POST /api/chat evidence for the newly appended line(s).

Usage: py -3.11 tools/phase7_logger.py <target_line_count> [--show-raw]
"""
import json
import sys
import time

LOG = r"T:\thai-ecommerce-agent\logs\experiment.jsonl"

def main():
    target = int(sys.argv[1])
    show_raw = "--show-raw" in sys.argv
    deadline = time.time() + 150  # LLM turns can take up to ~90s
    while time.time() < deadline:
        try:
            with open(LOG, encoding="utf-8") as f:
                lines = f.readlines()
        except FileNotFoundError:
            lines = []
        if len(lines) >= target:
            break
        time.sleep(1)
    else:
        print("TIMEOUT waiting for log line %d (have %d)" % (target, len(lines)))
        return

    with open(LOG, encoding="utf-8") as f:
        lines = f.readlines()

    start = max(0, target - 1)
    for raw in lines[start:target]:
        raw = raw.strip()
        if not raw:
            continue
        try:
            d = json.loads(raw)
        except Exception as e:
            print("PARSE_ERR", e, raw[:200])
            continue
        keys = [
            "timestamp", "session_id", "user_message", "intent",
            "selected_agent", "order_id", "product_id", "pending_workflow",
            "pending_subtype", "missing_slots", "refund_reason",
            "desired_colour", "response_source", "policy_sources",
            "response_language", "input_language_detected", "latency_ms",
            "requires_clarification", "routing_confidence", "routing_reason",
            "success", "llm_enabled", "llm_fallback_used",
            "grounding_validation_passed", "retrieved_chunk_count",
        ]
        out = {}
        for k in keys:
            if k in d:
                out[k] = d[k]
        print("===TURN===")
        print(json.dumps(out, ensure_ascii=False, indent=1))
        if show_raw:
            print("RAW:", raw[:4000])

if __name__ == "__main__":
    main()
