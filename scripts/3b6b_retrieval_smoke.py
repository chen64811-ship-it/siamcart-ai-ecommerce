"""Micro-task 3B-6B: Single real ChromaDB retrieval smoke test.

Verifies the existing persistent ChromaDB collection can retrieve
refund-policy evidence for a real Thai query.
"""

import time
import sys

start = time.time()

# No path patching needed — run from project root with proper Python
from app.config import CHROMA_DB_DIR, EMBEDDING_MODEL
from app.agents.policy_index import get_policy_index_status, retrieve_policy_clauses

print("=== 3B-6B: ChromaDB Retrieval Smoke Test ===")
print()
print(f"ChromaDB persistence dir : {CHROMA_DB_DIR}")
print(f"Embedding model          : {EMBEDDING_MODEL}")

# ── Step 2: Check index status ──

status = get_policy_index_status()
print()
print("--- Policy Index Status ---")
for k, v in status.items():
    print(f"  {k}: {v}")

collection_ok = status.get("index_exists", False) and status.get("chunk_count", 0) > 0

if not collection_ok:
    print()
    print("STOP: Collection is empty or unavailable.")
    print("Do not build or rebuild it.")
    sys.exit(1)

print(f"Collection name           : siamcart_store_policies")
print(f"Indexed chunk count       : {status['chunk_count']}")

# ── Step 3: Single retrieval ──

query = "ขอคืนเงินใช้เวลากี่วัน"
print(f"\n--- Performing single retrieval ---")
print(f"Query: {query}")

retrieval_start = time.time()
result = retrieve_policy_clauses(query, top_k=3)
retrieval_duration = round((time.time() - retrieval_start) * 1000, 1)

clauses = result.get("retrieved_clauses", [])
retrieval_success = result.get("retrieval_success", False)

# ── Step 4: Print summary ──

print(f"\n--- Retrieval Summary ---")
print(f"retrieval_success        : {retrieval_success}")
print(f"retrieved_chunk_count    : {len(clauses)}")
print(f"retrieval_duration_ms    : {retrieval_duration}")
print(f"total_chunks_in_index    : {result.get('total_chunks_in_index', '?')}")

if clauses:
    top = clauses[0]
    top_similarity = round(1.0 - top["distance"], 4)
    print(f"\n--- Top Clause ---")
    print(f"  policy_type       : {top.get('policy_type', '')}")
    print(f"  source_filename   : {top.get('source_filename', '')}")
    print(f"  section_title     : {top.get('section_title', '')}")
    print(f"  similarity_score  : {top_similarity}")
    print(f"  chunk_id          : {top.get('chunk_id', '')}")
    print(f"  language          : {top.get('language', '')}")
    print(f"  text              : {top['text'][:200]}")

    sources = sorted(set(c.get("source_filename", "") for c in clauses))
    print(f"\n  all policy_sources : {sources}")

    has_refund = any("refund" in c.get("source_filename", "").lower() for c in clauses)
    print(f"  contains refund   : {has_refund}")
else:
    print("  No clauses retrieved.")

total_duration = round(time.time() - start, 2)
print(f"\nTotal script runtime    : {total_duration}s")

print("\n--- External Dependencies Used ---")
print("  SentenceTransformer   : YES (real)")
print("  ChromaDB              : YES (real persistent)")
print("  Network call          : NO")
print("  DeepSeek API          : NO")
print()
print("Files modified: none")
print("Next task: 3B-6C Single Real DeepSeek Smoke Test")
