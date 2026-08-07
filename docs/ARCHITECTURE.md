# Architecture — Thai E-Commerce Multi-Agent System

## High-Level Architecture (Phase 3A)

```
Browser → FastAPI (app/api/server.py)
           GET  /health          → health check
           GET  /                → SiamCart Thai e-commerce page
           POST /api/chat
             → Orchestrator (app/agents/orchestrator.py)
               → Router (app/agents/router.py)
                 → deterministic intent classification
                 → entity validation
                 → RouteDecision
               → selected handler:
                 → TransactionTracker (SQLite — read-only)
                 → StorePolicyEvaluator (ChromaDB RAG)
                   → policy documents (data/policies/*.md)
                   → section-based chunking
                   → multilingual embedding
                   → ChromaDB persistent index (data/chroma/)
                   → similarity search → structured evidence
                 → general_response (greeting templates)
                 → clarification_handler (missing-entity templates)
                 → simulated_human_review (out-of-scope templates)
               → evidence + deterministic response
               → optional DeepSeek formatter (app/agents/llm_client.py)
               → factual validation
               → final response
```

## Processing Sequence

```
Customer message
→ route_message()              deterministic keyword patterns + priority
→ route decision               intent, target_agent, confidence, entities
→ if transaction intent:
    → tracker_process()        deterministic order-ID + intent detection
    → SQLite query             parameterised, read-only
    → Thai response template   deterministic
→ if policy intent (RETURN_REFUND, STORE_POLICY):
    → evaluate_policy_query()  ChromaDB RAG pipeline
    → policy_index.load()      load data/policies/*.md
    → policy_index.chunk()     section-based chunking with metadata
    → sentence-transformers    multilingual embedding (384-dim)
    → ChromaDB similarity search
    → structured evidence      clauses + source metadata + distances
    → Thai response template   deterministic, based on policy type
→ if greeting:
    → welcome template
→ if out-of-scope:
    → simulated-human-review   "prototype cannot perform this operation"
→ if clarification/unknown:
    → ask-for-order-ID or     rephrase request
→ optional LLM formatting      cosmetic polish only (Phase 1B)
→ factual validation           reject if any critical value changed
→ final response
```

## Agent Responsibilities

### Intelligent Router (app/agents/router.py)
- Deterministic keyword-based intent classification
- 10 intent categories: ORDER_STATUS, PAYMENT_STATUS, SHIPMENT_STATUS, TRACKING_NUMBER, RETURN_REFUND, STORE_POLICY, GREETING, CLARIFICATION, UNKNOWN, OUT_OF_SCOPE
- Explicit priority order (tracking > payment > shipment > order > policy)
- Entity validation (order_id required for transaction intents)
- Returns structured RouteDecision dict

### Orchestrator (app/agents/orchestrator.py)
- Calls Router then dispatches to the correct handler
- Controlled response templates for non-transaction intents
- Delegates transaction intents to Transaction Tracker
- Delegates policy intents to Store Policy Evaluator
- Calls optional DeepSeek formatter on all responses
- Returns complete result dict for API serialization

### Store Policy Evaluator (app/agents/policy_index.py + app/agents/policy_evaluator.py)
- **policy_index.py**: Core indexing engine
  - load_policy_documents() — read 5 policy .md files
  - chunk_policy_documents() — section-based chunking (H2 headings)
  - build_policy_index() — embed + ChromaDB persistent add (idempotent)
  - retrieve_policy_clauses() — query → embedding → ChromaDB search
  - get_policy_index_status() — index health/status
- **policy_evaluator.py**: Agent component
  - evaluate_policy_query() — full pipeline: retrieve → structure → deterministic response
  - Confidence threshold detection (low-confidence → clarification)
  - Empty index detection (→ simulated human review)
  - Never invents policy

### Transaction Tracker (app/agents/transaction_tracker.py)
- Unchanged from Phase 1A
- SQLite query via app/db/orders.py
- Deterministic Thai response templates

### LLM Formatter (app/agents/llm_client.py)
- Unchanged from Phase 1B
- Optional DeepSeek-Chat cosmetic formatting
- Factual validation via _evidence_matches()
- Deterministic fallback on all failure modes

## Data Flow

1. Customer sends message (Thai or English)
2. FastAPI receives POST /api/chat
3. Orchestrator calls Router → RouteDecision
4. Handler selected based on intent + entity validation
5. Transaction intents → read-only SQLite query
6. Policy intents → ChromaDB RAG pipeline:
   a. Query embedded with sentence-transformers
   b. Cosine similarity search in siamcart_store_policies collection
   c. Top-k clauses retrieved with source metadata
   d. Policy type detected from dominant clause type
   e. Deterministic Thai response generated
7. Evidence + deterministic response assembled
8. Optional DeepSeek formatting (disabled by default)
9. Factual validation on LLM output
10. Final response returned via API
11. Experiment log written to JSONL

## Directory Structure (Phase 3A additions)

```
data/
├── chroma/              ← Persisted ChromDB index (Phase 3A)
├── policies/            ← Store policy documents (Phase 3A)
│   ├── exchange_policy.md
│   ├── payment_policy.md
│   ├── refund_policy.md
│   ├── return_policy.md
│   └── shipping_policy.md
app/agents/
├── policy_index.py      ← Core indexing engine (Phase 3A)
├── policy_evaluator.py  ← Policy evaluator agent (Phase 3A)
tests/
├── test_policy_evaluator.py  ← 35 tests (Phase 3A)
```

## API Response Fields (Phase 3A additions)

- `policy_evidence`: {
    `retrieved_clauses`: [{text, policy_type, source_filename, chunk_id, section_title, distance}],
    `retrieval_success`: bool
  }
