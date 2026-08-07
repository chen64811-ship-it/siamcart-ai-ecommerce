# Project State — Phase 3A Complete

## Repository Location
T:/thai-ecommerce-agent/

## Phase 3A Architecture

```
Customer message
  → Router (deterministic patterns + priority)
  → RouteDecision (intent, target_agent, confidence, entities)
  → Orchestrator dispatches to handler:
      → TransactionTracker (SQLite) for order intents
      → StorePolicyEvaluator (ChromaDB RAG) for policy intents
          → policy documents (data/policies/*.md)
          → section-based chunking (55 chunks, th + en bilingual)
          → multilingual embedding (paraphrase-multilingual-MiniLM-L12-v2)
          → ChromaDB persistent index (data/chroma/)
          → retrieval → structured evidence with source metadata
      → Controlled templates for greetings, out-of-scope
      → Clarification handler for missing entities or unknown intents
  → evidence + deterministic response
  → optional DeepSeek formatter (unchanged from Phase 1B)
  → factual validation
  → final response
```

## Files Changed (Phase 3A)

### Created (3)
| File | Purpose |
|------|---------|
| `data/policies/return_policy.md` | Synthetic Thai/English return policy (5 sections) |
| `data/policies/refund_policy.md` | Synthetic Thai/English refund policy (5 sections) |
| `data/policies/exchange_policy.md` | Synthetic Thai/English exchange policy (5 sections) |
| `data/policies/shipping_policy.md` | Synthetic Thai/English shipping policy (5 sections) |
| `data/policies/payment_policy.md` | Synthetic Thai/English payment policy (5 sections) |
| `app/agents/policy_index.py` | Core indexing engine: load, chunk, embed, ChromaDB CRUD, retrieval |
| `app/agents/policy_evaluator.py` | Store Policy Evaluator agent with structured evidence output |
| `tests/test_policy_evaluator.py` | 35 deterministic policy evaluator tests |

### Modified (6)
| File | Changes |
|------|---------|
| `app/config.py` | CHROMA_DB_DIR changed to `data/chroma` |
| `app/agents/router.py` | Policy intents route to `store_policy_evaluator` (was `policy_evaluator_pending`) |
| `app/agents/orchestrator.py` | Policy intents delegated to evaluate_policy_query(); added policy_evidence |
| `app/api/server.py` | Added `policy_evidence` to ChatResponse model and endpoint |
| `app/templates/index.html` | Added Policy Source and Chunks fields in dev panel |
| `app/static/js/chat.js` | Fills policy metadata in dev panel from API response |

### Unchanged
- `app/agents/transaction_tracker.py` — unchanged
- `app/agents/llm_client.py` — unchanged
- `app/db/orders.py` — unchanged
- `app/db/__init__.py` — unchanged
- `requirements.txt` — unchanged (chromadb and sentence-transformers already installed)

## Test Results

**103/103 tests passing:**
- `tests/test_transaction_tracker.py`: 10 tests (unchanged)
- `tests/test_llm_formatter.py`: 13 tests (unchanged)
- `tests/test_web_chat.py`: 17 tests (unchanged)
- `tests/test_router.py`: 28 tests (updated target_agent names)
- `tests/test_policy_evaluator.py`: 35 tests (new — Phase 3A)

## ChromaDB Index Details

| Metric | Value |
|--------|-------|
| Collection name | `siamcart_store_policies` |
| Storage path | `data/chroma/` |
| Total chunks | 55 |
| Policy types | exchange, payment, refund, return, shipping |
| Languages | th (Thai), en (English) |
| Embedding model | `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2` (384-dim) |
| Index space | cosine similarity |
| Idempotent | Yes — re-running build adds 0 chunks |

## Acceptance Criteria — All Met

- [x] Policy-document loading (data/policies/*.md)
- [x] Document chunking with full metadata preservation
- [x] Multilingual embedding (Thai + English)
- [x] Persistent ChromaDB indexing (data/chroma/)
- [x] Deterministic policy retrieval
- [x] Source metadata in every retrieved clause
- [x] Tests proving retrieval correctness
- [x] No DeepSeek-Chat in policy retrieval (Phase 3A)
- [x] No LangChain dependency
- [x] No SQLite replacement
- [x] Policy intents route to Store Policy Evaluator
- [x] Transaction intents still route to Transaction Tracker
- [x] 68 existing tests continue to pass
- [x] 35 new policy evaluator tests pass
- [x] Frontend dev panel shows policy source and chunk count

## Retrieval Examples (verified)

| Query | Primary Policy Type | Top Section | Distance |
|-------|-------------------|-------------|----------|
| "can I return an item" | return | Section 1: Standard Return Window | ~0.39 |
| "how long does refund take" | refund | Section 1: Refund Processing Timeline | ~0.22 |
| "shipping delay" | shipping | Section 4: Shipping Delay Handling | ~0.45 |
| "payment methods" | payment | Section 1: Accepted Payment Methods | ~0.24 |
| "exchange damaged item" | exchange | Section 2: Defective Item Exchange | ~0.43 |

## Unresolved Issues

None for Phase 3A.

## Next Phase

Phase 3B — Real-time policy RAG response generation using DeepSeek-Chat with evidence validation and deterministic fallback.
