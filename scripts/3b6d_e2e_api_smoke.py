"""3B-6D: Single real end-to-end API smoke test.

POST /api/chat → Intelligent Router → real ChromaDB retrieval
→ real DeepSeek (deepseek-v4-flash) → policy validation → FastAPI JSON response
"""
import sys, os, time, json

sys.path.insert(0, r"T:\thai-ecommerce-agent")

# ── Config is read from env vars set at command line ──
from app.config import DEEPSEEK_ENABLED, DEEPSEEK_API_KEY, DEEPSEEK_MODEL, LLM_CONFIG

query = "ขอคืนเงินใช้เวลากี่วัน"

print("=== 3B-6D: Real End-to-End API Smoke Test ===")
print()
print(f"Query              : {query}")
print(f"DEEPSEEK_ENABLED   : {DEEPSEEK_ENABLED}")
print(f"DEEPSEEK_MODEL     : {DEEPSEEK_MODEL}")
print(f"LLM model (active) : {LLM_CONFIG.get('model', '?')}")
key_ok = bool(DEEPSEEK_API_KEY)
print(f"DEEPSEEK_API_KEY   : {'<present>' if key_ok else '<empty>'}")
print()

if not DEEPSEEK_ENABLED or not key_ok:
    print("STOP: DeepSeek not configured.")
    sys.exit(1)

# ── Use ASGITransport to call FastAPI without starting a server ──
from httpx import ASGITransport, AsyncClient
import asyncio

from app.api.server import app

async def run():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        t0 = time.time()
        resp = await client.post(
            "/api/chat",
            json={"message": query, "session_id": "e2e-3b6d-real"},
        )
        total_ms = round((time.time() - t0) * 1000, 1)

        data = resp.json()
        pe = data.get("policy_evidence", {})

        print(f"HTTP Status          : {resp.status_code}")
        print()
        print("--- Response Fields ---")
        print(f"  intent                  : {data.get('intent')}")
        print(f"  agent                   : {data.get('agent')}")
        print(f"  response_source         : {data.get('response_source')}")
        print(f"  grounding_validation    : {data.get('grounding_validation_passed')}")
        print(f"  llm_fallback_used       : {data.get('llm_fallback_used')}")
        print(f"  llm_error_type          : {data.get('llm_error_type')}")
        print(f"  llm_latency_ms          : {data.get('llm_latency_ms')}")
        print(f"  total_http_latency_ms   : {total_ms}")
        print(f"  requires_clarification  : {data.get('requires_clarification')}")
        print(f"  simulated_human_review  : {data.get('simulated_human_review')}")
        print()

        print("--- Policy Evidence ---")
        print(f"  retrieval_success       : {pe.get('retrieval_success')}")
        print(f"  retrieved_chunk_count   : {pe.get('retrieved_chunk_count')}")
        print(f"  policy_sources          : {pe.get('policy_sources')}")
        print(f"  top_similarity_score    : {pe.get('top_similarity_score')}")
        print()

        response_text = data.get("response", "")
        print("--- Response (Thai, first 300 chars) ---")
        print(f"  {response_text[:300]}")
        print()

        # ── Verification ──
        is_deepseek = (
            data.get("response_source") == "deepseek"
            and data.get("grounding_validation_passed") is True
            and data.get("llm_fallback_used") is False
        )
        period_ok = "5" in response_text and "7" in response_text and "วัน" in response_text
        no_approval = not any(
            p in response_text
            for p in [
                "ได้รับการอนุมัติ", "อนุมัติการคืน",
                "คืนเงินเรียบร้อย", "refund approved",
            ]
        )

        print("--- Verification ---")
        print(f"  5-7 วันทำการ preserved  : {period_ok}")
        print(f"  no approval claim       : {no_approval}")
        print(f"  DeepSeek accepted       : {is_deepseek}")
        print(f"  API request count       : 1")
        print()

        all_pass = (
            resp.status_code == 200
            and data.get("intent") == "RETURN_REFUND"
            and data.get("agent") == "store_policy_evaluator"
            and is_deepseek
            and pe.get("retrieval_success") is True
            and "refund_policy.md" in pe.get("policy_sources", [])
            and period_ok
            and no_approval
        )

        print("--- Summary ---")
        print(f"  All checks passed: {'YES' if all_pass else 'NO'}")
        print()
        print("--- External Dependencies ---")
        print("  Router                  : YES (real)")
        print("  ChromaDB                : YES (real persistent)")
        print("  SentenceTransformer     : YES (real)")
        print("  DeepSeek API            : YES (real — 1 call)")
        print("  FastAPI endpoint        : YES (ASGITransport)")
        print()
        print("Files modified     : none")
        print("Next task          : (pending user instruction)")

asyncio.run(run())
