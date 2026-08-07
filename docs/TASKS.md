# Implementation Tasks — Phased Plan

## Phase 0: Repository Audit (DONE)
- [x] Read PROJECT_SPEC.md
- [x] Inspect repository structure
- [x] Identify implemented/incomplete code
- [x] Identify blockers and issues
- [x] Write PROJECT_STATE.md, ARCHITECTURE.md, TASKS.md

## Phase 1A: Thai E-Commerce Web Interface + Single Transaction Tracker (DONE)
- [x] FastAPI web server with GET /health, GET /, POST /api/chat
- [x] SiamCart Thai e-commerce demo page (Jinja2)
- [x] Deterministic Transaction Tracker Agent
- [x] SQLite read-only module (app/db/orders.py)
- [x] JSONL experiment logging
- [x] Static CSS and JavaScript files
- [x] 25 tests passing

## Phase 1B: Optional DeepSeek-Chat Response Formatting (DONE)
- [x] DEEPSEEK_ENABLED env var (default false)
- [x] Single-purpose format_response() with FORMATTER_SYSTEM_PROMPT
- [x] 6 graceful failure modes (disabled, no_key, timeout, http_error, empty_output, validation)
- [x] Factual validation via _evidence_matches()
- [x] 4 new API response fields (response_source, llm_enabled, llm_fallback_used, llm_latency_ms)
- [x] Extended JSONL logging
- [x] openai dependency added
- [x] 13 mock-based tests
- [x] 40 total tests passing

## Phase 2A: Intelligent Router with Deterministic Routing (DONE)
- [x] Router (app/agents/router.py): 10 intent categories, priority order, entity validation
- [x] Orchestrator (app/agents/orchestrator.py): Route -> handler dispatch
- [x] Controlled handlers for all non-transaction intents
- [x] 28 router tests
- [x] 68/68 tests passing

## Phase 3A: Store Policy Evaluator with Offline Policy-Document Indexing (DONE)

### Policy Documents
- [x] 5 synthetic Thai/English policy documents created (return, refund, exchange, shipping, payment)
- [x] Each document has deterministic rules (time windows, eligibility, exclusions)
- [x] Documents stored under data/policies/
- [x] Not copied from real platforms (Shopee, Lazada, etc.)

### Chunking
- [x] Section-based chunking (## H2 headings)
- [x] Every chunk preserves: document_id, policy_type, source_filename, section_title, chunk_id, language, text
- [x] Bilingual sections split into separate Thai and English chunks
- [x] No arbitrary character splitting

### ChromaDB Indexing
- [x] Persistent storage under data/chroma/
- [x] Collection name: siamcart_store_policies
- [x] Idempotent indexing (re-run adds 0 chunks)
- [x] No duplicate chunk IDs
- [x] Functions: load_policy_documents(), chunk_policy_documents(), build_policy_index(), retrieve_policy_clauses(), get_policy_index_status()

### Embedding Model
- [x] Multilingual sentence-embedding: paraphrase-multilingual-MiniLM-L12-v2
- [x] Thai + English support
- [x] Configurable through app/config.py
- [x] Already installed in environment (no new dependencies)

### Store Policy Evaluator
- [x] app/agents/policy_evaluator.py implemented
- [x] Receives normalized query, retrieves ChromaDB chunks
- [x] Returns structured evidence with source metadata
- [x] Detects no-result and low-confidence retrieval
- [x] Requests clarification when evidence insufficient
- [x] Never invents a policy (returns "not found" template)
- [x] No DeepSeek-Chat in Phase 3A

### Router Integration
- [x] RETURN_REFUND, STORE_POLICY → Store Policy Evaluator
- [x] policy_evaluator_pending placeholder removed
- [x] Transaction intents continue to use Transaction Tracker

### API and Frontend
- [x] All existing response fields preserved
- [x] Policy retrieval metadata added to dev panel (Policy Source, Chunks)
- [x] Policy evidence in API response (policy_evidence field)
- [x] Frontend page redesigned minimally — only dev panel additions

### Tests (35 new, all offline/deterministic)
- [x] Policy documents load successfully (5 tests)
- [x] Chunks include complete metadata (5 tests)
- [x] Indexing is idempotent (2 tests)
- [x] No duplicate chunk IDs (2 tests)
- [x] Return query retrieves return policy (2 tests)
- [x] Refund query retrieves refund policy (2 tests)
- [x] Shipping query retrieves shipping policy (2 tests)
- [x] Thai query works (2 tests: thai-only, thai-english mixed)
- [x] Irrelevant query produces clarification (2 tests)
- [x] Policy intents route to Store Policy Evaluator (2 tests)
- [x] Transaction intents still route to Transaction Tracker (4 tests)
- [x] Existing 68 tests continue to pass
- [x] No real DeepSeek request occurs (2 tests)
- [x] Evaluator structure validation (3 tests)
- [x] **103/103 total tests passing**

### Other
- [x] PROJECT_STATE.md updated
- [x] TASKS.md updated
- [x] ARCHITECTURE.md updated
- [x] No DeepSeek-Chat used (Phase 3B)

## Phase 3B: Real-time policy RAG response generation using DeepSeek-Chat with evidence validation and deterministic fallback

## Phase 4A: ChromaDB Policy Retrieval Enhancement
## Phase 4B: Transaction Tracker Expansion
## Phase 5: FastAPI Integration and End-to-End Tests
## Phase 6: Simulation and Evaluation
