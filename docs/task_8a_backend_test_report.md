# Task 8A-2B — Backend Chat-Context Test Report

Date: 2026-08-04
Repo: T:\thai-ecommerce-agent

## Scope

Focused backend tests for the Task 8A chat-context feature (active order
reuse, ORDER_TOTAL / PURCHASED_ITEMS, remove-context / reset endpoints,
honest cancellation guidance). No server started, no browser/live smoke, no
frontend tests, no broad regression run, no changes to orders.js / chat.js /
templates / CSS, no changes to data/orders.db, no real cancellation endpoint.

## Files changed

| File | Change |
|---|---|
| `tests/test_task8a_chat_context.py` | **Created** — 28 focused backend tests (new) |
| `app/agents/orchestrator.py` | **Fixed** — English pronoun ("it") follow-up did not reuse the active order (see Defects) |
| `docs/task_8a_backend_test_report.md` | **Created** — this report |

No source files other than the orchestrator defect fix were modified.

## Seeded temporary orders

Two Task 8A orders were inserted directly into the **temporary** SQLite DB
(never into `data/orders.db`), in addition to the 5 production sample orders
(ORD-1001..ORD-1005) and 12 seeded products created by the real
`init_database()`:

| Order | payment_status | order_status | shipment_status | Items | total_amount | payment_method |
|---|---|---|---|---|---|---|
| ORD-8001 | paid (paid_at set) | processing | not_shipped | 2 (Linen Everyday Blouse x1, Classic Denim Jacket x1) | 1980.0 | card |
| ORD-8002 | pending | processing | not_shipped | 1 (Classic Denim Jacket x1) | 1290.0 | cash_on_delivery |

`order_items` rows are FK-safe against the seeded products (PROD-001,
PROD-002). The suite never depends on the real ORD-1035.

## Exact commands

```
T:\thai-ecommerce-agent\.venv\Scripts\python.exe -m pytest tests/test_task8a_chat_context.py -q
T:\thai-ecommerce-agent\.venv\Scripts\python.exe -m pytest tests/test_task8a_chat_context.py -vv
```

## Result

First focused run: **25 passed, 3 failed** (1 real backend defect + 2 test
assertion issues, see below).
Final focused runs: **28 passed, 0 failed** on both `-q` and `-vv`.

No other test files were run.

## Backend defects found and fixed

1. **English "it" follow-up never reused the active order (case 7).**
   A pronoun follow-up such as `Where is it?` / `Is it shipped?` routed to
   UNKNOWN; the router has no keyword for it and the strong-referential
   pattern only covered "this/that/my/the order" and Thai phrases. With an
   active order the reply fell through to the generic Thai "I don't
   understand" instead of answering about ORD-8001.
   **Fix** (`app/agents/orchestrator.py`): added `_EN_PRONOUN_FOLLOWUP_RE`
   (`\b(it|it's)\b`) to the Task 8A UNKNOWN-message refinement, guarded by a
   `_EN_PRICE_PHRASE_RE` exclusion (`how much`, `price`, `cost`, `buy`,
   `ราคา`, `เท่าไร`, `เท่าไหร่`, `กี่บาท`, `ซื้อ`) so product-price / buying
   questions are never hijacked into order-status work. The refinement only
   fires when a session active order exists.

No other backend defects surfaced — all 28 required behaviours passed with
the existing implementation (active-order storage, reuse, override, totals,
purchased items, session endpoints, honest cancellation).

### Test-side corrections (not backend defects)

- `test_remove_context_preserves_same_session`: the backend intentionally
  keeps no server-side transcript (`session["messages"]` stays empty; the
  transcript lives in the frontend). Rewritten to assert the actual contract:
  same session object preserved (identity), `active_order_id` cleared,
  pending workflow state untouched, same `session_id` continues to work.
- `test_cancellation_reply_is_thai`: the fixed UI menu name "My Orders" is a
  proper noun inside an otherwise Thai sentence. Changed to a Thai-dominance
  check (Thai characters outnumber Latin characters) rather than banning all
  Latin text.

## LLM mocking method

- `orch.DEEPSEEK_ENABLED = False` and `llm_generator.DEEPSEEK_ENABLED = False`
  (monkeypatched per test).
- `openai.OpenAI` is patched to **raise** if ever constructed — any attempt
  to make an external LLM call fails the test.
- The experiment-log writer (`app.api.server.write_experiment_log`) is
  neutralised so `logs/experiment.jsonl` is not mutated by the suite.
- Policy retrieval (used only by the pending-workflow priority test, case 25)
  goes through the canned-evaluator pattern (Thai canned clause) — no
  ChromaDB, no SentenceTransformer, no DeepSeek.

## Session isolation method

- An autouse fixture replaces the global orchestrator `SessionManager`
  (`orch._SESSION_STORE = SessionManager()`) before every test, so session
  state from one test can never contaminate another (case 28).
- Every test also uses a unique `session_id`; case 28 additionally proves two
  sessions in the same run do not share `active_order_id`.

## Real DB before/after evidence

`data/orders.db` (the only database the live app uses) was stat'd before and
after the focused runs:

```
before: size=49152 mtime=1785766649
after:  size=49152 mtime=1785766649
```

Identical size and modified timestamp — untouched. The test module also
asserts this at teardown: the `tmp_db` fixture records the real DB stat at
startup and asserts it is unchanged after the module completes, printing
`[isolation] real DB before=... after=... — untouched`. All writes (orders,
order_items) went to a `tempfile.mkstemp` SQLite file redirected via
`store_db.STORE_DB_PATH` and `set_db_path()`; the temp file is deleted at
teardown.

## Remaining work

- **Task 8A-2C** — frontend/static tests (orders.js openWithOrder wiring,
  chat.js session/context UI, cache-version bumps) and relevant regressions.
- Live smoke test (server + browser) after 8A-2C passes.

## Continuation point

Task 8A-2C: frontend/static tests and relevant regressions.
