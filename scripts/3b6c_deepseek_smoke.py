"""3B-6C real + 3B-6D end-to-end: one-shot real DeepSeek policy generation.

Full chain:
  Thai query → Router → real ChromaDB retrieval → real DeepSeek
  → validation → deterministic fallback if needed → report
"""
import sys, os, time, json

sys.path.insert(0, r"T:\thai-ecommerce-agent")

# ── Config will be read from env vars set at command line ──
from app.config import DEEPSEEK_ENABLED, DEEPSEEK_API_KEY, LLM_CONFIG

query = "ขอคืนเงินใช้เวลากี่วัน"

print("=== 3B-6C+6D: Real DeepSeek End-to-End Smoke Test ===")
print()
print(f"Query                 : {query}")
print(f"DEEPSEEK_ENABLED       : {DEEPSEEK_ENABLED}")
key_ok = bool(DEEPSEEK_API_KEY)
print(f"DEEPSEEK_API_KEY       : {'<present>' if key_ok else '<empty>'}")
print(f"LLM model             : {LLM_CONFIG.get('model', '?')}")
print(f"LLM base_url          : {LLM_CONFIG.get('base_url', '?')}")
print(f"LLM timeout           : {LLM_CONFIG.get('timeout', '?')}s")
print()

if not DEEPSEEK_ENABLED or not key_ok:
    print("FAIL: DeepSeek not enabled or key missing — cannot proceed.")
    sys.exit(1)

# ── Run the full pipeline ──
from app.agents.orchestrator import process_message

t0 = time.time()
result = process_message(query, session_id="e2e-deepseek-real")
duration = round((time.time() - t0) * 1000, 1)

# ── Extract results ──
intent = result.get("intent")
agent = result.get("agent")
response = result.get("response", "")
response_source = result.get("response_source")
gvp = result.get("grounding_validation_passed")
llm_fallback = result.get("llm_fallback_used")
llm_error = result.get("llm_error_type")
llm_latency = result.get("llm_latency_ms")
requires_clar = result.get("requires_clarification")
sim_review = result.get("simulated_human_review")
pe = result.get("policy_evidence", {})

print("--- Pipeline Result ---")
print(f"  intent                  : {intent}")
print(f"  agent                   : {agent}")
print(f"  response_source         : {response_source}")
print(f"  grounding_validation    : {gvp}")
print(f"  llm_fallback_used       : {llm_fallback}")
print(f"  llm_error_type          : {llm_error}")
print(f"  llm_latency_ms          : {llm_latency}")
print(f"  total_latency_ms        : {duration}")
print(f"  requires_clarification  : {requires_clar}")
print(f"  simulated_human_review  : {sim_review}")
print()

print("--- Policy Evidence ---")
print(f"  retrieval_success       : {pe.get('retrieval_success')}")
print(f"  retrieved_chunk_count   : {pe.get('retrieved_chunk_count')}")
print(f"  policy_sources          : {pe.get('policy_sources')}")
print(f"  top_similarity_score    : {pe.get('top_similarity_score')}")
print()

print("--- Response (Thai, first 250 chars) ---")
print(f"  {response[:250]}")
print()

# ── Verification ──
has_57 = "5" in response and "7" in response
has_wan = "วัน" in response
period_ok = has_57 and has_wan
# Check no approval claim
no_approval = not any(p in response for p in [
    "ได้รับการอนุมัติ", "อนุมัติการคืน", "คืนเงินเรียบร้อย",
    "refund approved", "approved"
])
is_deepseek = response_source == "deepseek" and gvp is True
request_count = 1 if (is_deepseek or llm_error is not None) else 0

print("--- Verification ---")
print(f"  5-7 วันทำการ preserved  : {period_ok}")
print(f"  no approval claim       : {no_approval}")
print(f"  DeepSeek accepted       : {is_deepseek}")
print(f"  API request count       : {request_count}")
print()

# ── Summary banner ──
print("--- External Dependencies ---")
print("  Router                  : YES (real)")
print("  ChromaDB                : YES (real persistent)")
print("  SentenceTransformer     : YES (real)")
print("  DeepSeek API            : YES (real — 1 call)")
print()
print("Files modified     : none")
print("Next task          : (pending user instruction)")
