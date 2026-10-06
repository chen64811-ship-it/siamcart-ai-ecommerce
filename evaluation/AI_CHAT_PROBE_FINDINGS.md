# AI Chat Probe — Findings (2026-10-06)

Live behavioural test of POST /api/chat against a running server on :8000.
Probe script: `evaluation/ai_chat_probe.py` (re-runnable).

**STATUS: 23/23 probe cases pass. 107/107 pytest pass. All confirmed bugs fixed.**

## Coverage (after fixes)
| Dimension | Cases | Result |
|-----------|-------|--------|
| Intent routing | 8 | 8 pass |
| Business logic (facts vs SQLite) | 4 | 4 pass |
| Context / multi-turn | 2 | 2 pass |
| Language handling (Thai output) | 3 | 3 pass |
| Edge cases | 6 | 6 pass |

---

## FIXED BUGS

### BUG-1 (HIGH) — Refunded order: LLM denied the refund state that exists in SQLite
- **Repro**: `ORD-1065 คืนเงินแล้วหรือยัง` / "is ORD-1065 refunded?"
- **Data truth** (SQLite): `order_status=cancelled`, `payment_status=refunded`.
- **Before**: LLM replied "ยังไม่พบข้อมูลสถานะการคืนเงิน" / "ข้อมูลไม่เพียงพอ"
  (refunded order answered as unknown). 3/3 reproducible.
- **Root cause (two layers)**:
  1. `_is_refund_request()` returned False for a refund *status* question
     ("has it been refunded?") — only refund *commands* ("I want a refund")
     matched. So the query skipped `_run_refund_flow` and fell into the pure
     policy-RAG branch, which passes `order_evidence=None` (orchestrator.py:1523).
     No order facts reached the LLM → it honestly said "insufficient info".
  2. Even when reached, `_refund_flow_response` had no terminal-state branch
     and `SYSTEM_PROMPT` never asserted evidence authority, so the model
     deflected to a policy lecture instead of stating "already refunded".
- **Fix**:
  - orchestrator.py:1481 — enter the refund workflow whenever an order ID is
    present (`or effective_order_id`), so refund-STATUS questions fetch
    authoritative order evidence.
  - orchestrator.py:997 — new terminal-state branch in `_refund_flow_response`
    that states cancelled/refunded plainly.
  - llm_generator.py SYSTEM_PROMPT rule 2 — evidence is authoritative; never
    say "not found" when the evidence contains the status.
- **Verified**: all refunded/cancelled/delivered orders now report true state;
  `order_ev=True` in every refund-query response.

### BUG-2 (MEDIUM) — `_evidence_preserved` validator too weak
- Only checked order_id/tracking_number substrings; a "not found" claim about a
  refunded order passed validation and was served as `source=deepseek`.
- **Fix**: added `_has_terminal_status()` + `_CONTRADICTION_PHRASES`. When the
  evidence holds a terminal status (cancelled/refunded/delivered) and the model
  emits a not-found/insufficient phrase, validation fails → deterministic
  fallback wins.

### BUG-3 (MEDIUM) — Out-of-scope requests fell to UNKNOWN + "give me your order number"
- Repro: "What is the weather in Bangkok?", "Tell me a joke", prompt-injection.
- **Before**: `intent=UNKNOWN` → "กรุณาระบุหมายเลขคำสั่งซื้อของคุณด้วยค่ะ".
  The 120-scenario suite expects `OUT_OF_SCOPE` (13 scenarios); live never emitted it.
- **Fix**: `_looks_out_of_scope()` in router.py — conservative detector that
  fires only when a clear non-store marker is present AND no store-domain
  vocabulary exists. Off-topic → `OUT_OF_SCOPE`, `simulated_human_review=True`,
  safe Thai fallback. Prompt injection no longer answered as an order request.

### BUG-4 (HIGH, discovered during fix) — Reasoning leakage into customer replies
- **Symptom**: `deepseek-v4-pro` emitted its English chain-of-thought as the
  response ("We need answer in Thai only. Need use evidence... Rule 2: ...").
- **Fix**: `_looks_like_reasoning_leak()` in llm_generator.py — rejects output
  that is dominated by English + carries meta-planning markers; falls back to
  the deterministic Thai reply (`error_type=reasoning_leak`). Also wired into
  policy_evaluator's separate LLM path, which additionally gained the missing
  `reasoning_content` fallback.

### BUG-5 (COSMETIC / test-only) — probe expectations
- GREETING agent label is `general_response`; OUT_OF_SCOPE handler is
  `simulated_human_review`. Probe expectations corrected. Not product bugs.

---

## PASSING (verified working)
- Intent routing: ORDER_STATUS / TRACKING_NUMBER / SHIPMENT_STATUS /
  PAYMENT_STATUS / RETURN_REFUND / STORE_POLICY / GREETING / OUT_OF_SCOPE.
- Factual grounding for processing/delivered/refunded orders + tracking numbers.
- Multi-turn session memory; remove-context clears `active_order_id`.
- Language: EN / TH / mixed inputs all produce Thai responses with Thai metadata.
- Edge cases graceful: typo id → UNKNOWN; unknown id → ORDER_NOT_FOUND;
  prompt injection → flagged, no leak; long input → handled.
- Latency: 2.0-2.5s deterministic, 6-10s LLM replies (was 84s cold start —
  fixed earlier via startup warm-up + HF offline mode).

---

## Files changed
- `app/agents/orchestrator.py` — refund-status routing + terminal-state branch.
- `app/agents/llm_generator.py` — authoritative prompt, terminal-state
  validator, reasoning-leak guard.
- `app/agents/policy_evaluator.py` — reasoning-leak guard + reasoning_content.
- `app/agents/router.py` — out-of-scope detector.
- `evaluation/ai_chat_probe.py` — new probe (test harness).

