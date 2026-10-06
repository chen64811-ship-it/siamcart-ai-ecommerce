"""
Store Policy Index Engine — Phase 3A.

Offline indexing flow:
  policy documents → text cleaning → section-based chunking
  → multilingual embedding → ChromaDB persistent collection

Real-time retrieval flow:
  query → normalized query → query embedding → ChromaDB similarity search
  → top policy clauses with source metadata

All functions are deterministic for tests.
No LangChain dependency — uses ChromaDB directly.
"""

import os
import re
import hashlib
from functools import lru_cache
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from app.config import CHROMA_DB_DIR, POLICIES_DIR, EMBEDDING_MODEL

# ── Constants ─────────────────────────────────────────────────────────

CHROMA_COLLECTION_NAME = "siamcart_store_policies"
MAX_CHUNK_CHARS = 800  # Maximum characters per chunk

# ── Process-level cache ───────────────────────────────────────────────
# Prevents repeated initialisation of embedding model, ChromaDB client,
# and collection within one Python process.
_chroma_client_cache: Optional["chromadb.PersistentClient"] = None
_chroma_collection_cache = None
_chroma_path_cache: Optional[str] = None

# ── Public API ────────────────────────────────────────────────────────


def load_policy_documents(policies_dir: Optional[str] = None) -> List[Dict]:
    """Load all .md policy documents from the policies directory.

    Returns a list of dicts:
      {"filename": str, "type": str, "content": str}
    """
    if policies_dir is None:
        policies_dir = str(POLICIES_DIR)

    base = Path(policies_dir)
    if not base.exists():
        raise FileNotFoundError(f"Policies directory not found: {base}")

    policy_files = sorted(base.glob("*.md"))
    if not policy_files:
        raise FileNotFoundError(f"No .md policy files found in {base}")

    documents = []
    for fpath in policy_files:
        content = fpath.read_text(encoding="utf-8")
        # Derive policy type from filename (e.g. "return_policy.md" → "return")
        policy_type = fpath.stem.replace("_policy", "")
        documents.append({
            "filename": fpath.name,
            "type": policy_type,
            "content": content,
        })

    return documents


def chunk_policy_documents(documents: List[Dict]) -> List[Dict]:
    """Split policy documents into deterministic section-based chunks.

    Each section starts with '## ' (markdown H2 heading).
    Each chunk preserves complete metadata.

    Returns list of dicts:
      {
        "document_id": str,
        "policy_type": str,
        "source_filename": str,
        "section_title": str,
        "chunk_id": str,
        "language": str,
        "text": str,
      }
    """
    chunks = []
    chunk_index = 0

    for doc in documents:
        content = doc["content"]
        policy_type = doc["type"]
        filename = doc["filename"]

        # Split on H2 headings (## Section Title)
        sections = re.split(r"\n(?=## )", content)

        for section in sections:
            section = section.strip()
            if not section:
                continue

            # Extract section title from the first line
            first_line = section.split("\n")[0].strip()
            section_title = first_line.lstrip("#").strip()

            # Remove the heading line for the text body
            lines = section.split("\n")
            body_lines = [l for l in lines if not l.startswith("##")]
            body_text = "\n".join(body_lines).strip()

            if not body_text:
                continue

            # Determine language: if body contains "**Thai:**" it's a bilingual section
            # We create separate Thai and English chunks
            thai_blocks = _extract_language_blocks(body_text, "Thai")
            english_blocks = _extract_language_blocks(body_text, "English")

            if thai_blocks or english_blocks:
                # Bilingual section — one chunk per language
                for tb in thai_blocks:
                    chunk_index += 1
                    chunks.append({
                        "document_id": f"{policy_type}_{chunk_index}",
                        "policy_type": policy_type,
                        "source_filename": filename,
                        "section_title": section_title,
                        "chunk_id": f"{policy_type}-{chunk_index:03d}",
                        "language": "th",
                        "text": tb,
                    })
                for eb in english_blocks:
                    chunk_index += 1
                    chunks.append({
                        "document_id": f"{policy_type}_{chunk_index}",
                        "policy_type": policy_type,
                        "source_filename": filename,
                        "section_title": section_title,
                        "chunk_id": f"{policy_type}-{chunk_index:03d}",
                        "language": "en",
                        "text": eb,
                    })
            else:
                # Monolingual section — chunk as-is
                chunk_index += 1
                # Detect language roughly
                lang = _detect_language(body_text)
                chunks.append({
                    "document_id": f"{policy_type}_{chunk_index}",
                    "policy_type": policy_type,
                    "source_filename": filename,
                    "section_title": section_title,
                    "chunk_id": f"{policy_type}-{chunk_index:03d}",
                    "language": lang,
                    "text": body_text,
                })

    return chunks


def build_policy_index(
    policies_dir: Optional[str] = None,
    chroma_dir: Optional[str] = None,
) -> int:
    """Load documents, chunk them, embed, and store in persistent ChromaDB.

    Idempotent: safe to re-run — skips duplicate chunk IDs.
    Uses a hash-based mechanism to detect duplicates.

    Returns the number of new chunks added.
    """
    if policies_dir is None:
        policies_dir = str(POLICIES_DIR)
    if chroma_dir is None:
        chroma_dir = CHROMA_DB_DIR

    os.makedirs(chroma_dir, exist_ok=True)

    # 1. Load and chunk
    documents = load_policy_documents(policies_dir)
    chunks = chunk_policy_documents(documents)
    if not chunks:
        return 0

    # 2. Get embedding model
    model = _get_embedding_model()

    # 3. Connect to ChromaDB (persistent)
    import chromadb
    client = chromadb.PersistentClient(path=chroma_dir)
    collection = client.get_or_create_collection(
        name=CHROMA_COLLECTION_NAME,
        metadata={"hnsw:space": "cosine"},
    )

    # 4. Check which chunk IDs already exist — idempotent add
    existing_ids = set(collection.get()["ids"])

    new_chunks = [c for c in chunks if c["chunk_id"] not in existing_ids]
    if not new_chunks:
        return 0  # Everything already indexed

    # 5. Prepare data for ChromaDB
    ids = [c["chunk_id"] for c in new_chunks]
    texts = [c["text"] for c in new_chunks]
    metadatas = [
        {
            "document_id": c["document_id"],
            "policy_type": c["policy_type"],
            "source_filename": c["source_filename"],
            "section_title": c["section_title"],
            "language": c["language"],
        }
        for c in new_chunks
    ]

    # 6. Compute embeddings in batch
    embeddings = model.encode(texts).tolist()

    # 7. Add to ChromaDB
    collection.add(
        embeddings=embeddings,
        documents=texts,
        ids=ids,
        metadatas=metadatas,
    )

    return len(new_chunks)


def retrieve_policy_clauses(
    query: str,
    top_k: int = 3,
    chroma_dir: Optional[str] = None,
) -> Dict:
    """Retrieve top-k policy clauses relevant to the query.

    Embedding model, ChromaDB client, and collection are cached at the
    process level so only the first call performs initialisation.

    Returns a dict:
      {
        "query": str,
        "retrieved_clauses": [
          {
            "text": str,
            "policy_type": str,
            "source_filename": str,
            "chunk_id": str,
            "section_title": str,
            "language": str,
            "distance": float,
          }
        ],
        "retrieval_success": bool,
        "total_chunks_in_index": int,
      }
    """
    global _chroma_client_cache, _chroma_collection_cache, _chroma_path_cache

    if chroma_dir is None:
        chroma_dir = CHROMA_DB_DIR

    # Reuse cached ChromaDB resources when the directory hasn't changed
    if _chroma_path_cache != chroma_dir:
        import chromadb
        _chroma_client_cache = chromadb.PersistentClient(path=chroma_dir)
        _chroma_collection_cache = _chroma_client_cache.get_or_create_collection(
            name=CHROMA_COLLECTION_NAME,
            metadata={"hnsw:space": "cosine"},
        )
        _chroma_path_cache = chroma_dir

    client = _chroma_client_cache
    collection = _chroma_collection_cache

    # Check if index has any data
    count = collection.count()
    if count == 0:
        return {
            "query": query,
            "retrieved_clauses": [],
            "retrieval_success": False,
            "total_chunks_in_index": 0,
        }

    # Embed query
    model = _get_embedding_model()
    query_embedding = model.encode(query).tolist()

    # Search
    results = collection.query(
        query_embeddings=[query_embedding],
        n_results=min(top_k, count),
    )

    clauses = []
    if results.get("ids") and results["ids"][0]:
        for i, chunk_id in enumerate(results["ids"][0]):
            text = results["documents"][0][i] if results.get("documents") else ""
            meta = results["metadatas"][0][i] if results.get("metadatas") else {}
            distance = results["distances"][0][i] if results.get("distances") else 0.0

            clauses.append({
                "text": text,
                "policy_type": meta.get("policy_type", ""),
                "source_filename": meta.get("source_filename", ""),
                "chunk_id": chunk_id,
                "section_title": meta.get("section_title", ""),
                "language": meta.get("language", ""),
                "distance": round(distance, 4),
            })

    return {
        "query": query,
        "retrieved_clauses": clauses,
        "retrieval_success": len(clauses) > 0,
        "total_chunks_in_index": count,
    }


# ── Deterministic keyword retrieval (intent-boundary hotfix) ──────────
# The local policy markdown files under data/policies/ are the fallback
# source of truth when ChromaDB retrieval is unavailable, the index is
# empty, the top match is low-confidence, or the top chunks are
# title-only. Clause text always comes VERBATIM from the local files —
# no policy terms are ever invented.

# Policy-type keyword lexicon (English + Thai).
POLICY_TYPE_KEYWORDS: Dict[str, List[str]] = {
    "return": ["return", "returns", "คืนสินค้า", "คืนของ", "ส่งคืน", "ส่งสินค้าคืน", "ขอคืนสินค้า"],
    "refund": ["refund", "refunds", "คืนเงิน", "เงินคืน", "ขอคืนเงิน"],
    "exchange": ["exchange", "exchanges", "เปลี่ยนสินค้า", "เปลี่ยนไซส์", "เปลี่ยนสี"],
    "shipping": ["shipping", "shipment", "delivery", "จัดส่ง", "ขนส่ง", "ส่งของ", "การจัดส่ง"],
    "payment": ["payment", "payments", "ชำระเงิน", "จ่ายเงิน", "การชำระเงิน"],
}

# General policy questions ("What policies does SiamCart follow?", "store
# policy", "นโยบายร้าน") → a grounded overview of every policy area.
GENERAL_POLICY_KEYWORDS: List[str] = [
    "policy", "policies", "store rules", "rules", "conditions", "terms",
    "นโยบาย", "กฎของร้าน", "ข้อกำหนด", "เงื่อนไข",
]

# Customer-facing order for the general-policy overview.
_OVERVIEW_POLICY_ORDER: List[str] = ["return", "refund", "exchange", "shipping", "payment"]


def is_title_only_chunk(text: str) -> bool:
    """True when a chunk is just a markdown heading with no policy body.

    The index contains document-header chunks (e.g. "# Return Policy —
    นโยบายการคืนสินค้า") that win embedding similarity for policy
    questions but carry no policy content. They must never be used as
    grounded evidence.
    """
    t = (text or "").strip()
    if not t:
        return True
    if len(t) < 12:
        return True
    lines = [ln for ln in t.splitlines() if ln.strip()]
    if len(lines) == 1 and lines[0].lstrip().startswith("#"):
        return True
    return False


def _keyword_clause(chunk: Dict, distance: float) -> Dict:
    """Convert a chunk dict into the same clause shape ChromaDB returns."""
    return {
        "text": chunk["text"],
        "policy_type": chunk["policy_type"],
        "source_filename": chunk["source_filename"],
        "chunk_id": chunk["chunk_id"],
        "section_title": chunk["section_title"],
        "language": chunk["language"],
        "distance": distance,
    }


def keyword_policy_retrieval(query: str) -> Dict:
    """Deterministic keyword retrieval from the local policy markdown files.

    Returns the same shape as retrieve_policy_clauses, plus:
      - policy_type_hint: str | None — explicitly named policy type
      - is_general_policy: bool — True when only general policy keywords matched

    Priority inside this function:
      1. explicit policy type named in the query → that policy's Thai sections;
      2. general policy question → one Thai section per policy area (overview);
      3. nothing matched → retrieval_success=False (caller falls back).
    """
    norm = (query or "").lower().strip()

    hits: Dict[str, int] = {}
    for ptype, kws in POLICY_TYPE_KEYWORDS.items():
        hits[ptype] = sum(1 for kw in kws if kw.lower() in norm)
    best_type = max(hits, key=hits.get) if any(hits.values()) else None
    best_score = hits.get(best_type, 0) if best_type else 0

    general = any(kw in norm for kw in GENERAL_POLICY_KEYWORDS)

    try:
        documents = load_policy_documents()
        chunks = chunk_policy_documents(documents)
    except Exception:
        chunks = []
    thai_by_type: Dict[str, List[Dict]] = {}
    for c in chunks:
        if c.get("language") == "th" and not is_title_only_chunk(c.get("text", "")):
            thai_by_type.setdefault(c.get("policy_type", ""), []).append(c)

    clauses: List[Dict] = []
    is_general = False

    if best_type and best_score > 0:
        # Explicit policy type → its Thai sections, keyword-relevant first.
        candidates = thai_by_type.get(best_type, [])
        kws = POLICY_TYPE_KEYWORDS[best_type]

        def _kw_hits(c: Dict) -> int:
            return sum(1 for kw in kws if kw.lower() in c["text"].lower())

        candidates = sorted(candidates, key=_kw_hits, reverse=True)
        for c in candidates[:3]:
            clauses.append(_keyword_clause(c, 0.0))
    elif general:
        # General policy question → overview: first Thai section per area.
        is_general = True
        for ptype in _OVERVIEW_POLICY_ORDER:
            pool = thai_by_type.get(ptype, [])
            if pool:
                clauses.append(_keyword_clause(pool[0], 0.0))

    return {
        "query": query,
        "retrieved_clauses": clauses,
        "retrieval_success": len(clauses) > 0,
        "total_chunks_in_index": len(chunks),
        "policy_type_hint": best_type if best_score > 0 else None,
        "is_general_policy": is_general,
    }


def get_policy_index_status(chroma_dir: Optional[str] = None) -> Dict:
    """Return the current status of the ChromaDB policy index."""
    if chroma_dir is None:
        chroma_dir = CHROMA_DB_DIR

    import chromadb
    client = chromadb.PersistentClient(path=chroma_dir)

    try:
        collection = client.get_collection(name=CHROMA_COLLECTION_NAME)
        count = collection.count()
        # Get a sample of metadata to show what's in the index
        sample = collection.get(limit=min(count, 5))
        policy_types = set()
        languages = set()
        for m in (sample.get("metadatas") or []):
            if m:
                policy_types.add(m.get("policy_type", ""))
                languages.add(m.get("language", ""))
        return {
            "index_exists": True,
            "chunk_count": count,
            "policy_types": sorted(policy_types),
            "languages": sorted(languages),
            "chroma_db_path": chroma_dir,
            "collection_name": CHROMA_COLLECTION_NAME,
        }
    except ValueError:
        return {
            "index_exists": False,
            "chunk_count": 0,
            "policy_types": [],
            "languages": [],
            "chroma_db_path": chroma_dir,
            "collection_name": CHROMA_COLLECTION_NAME,
        }


# ── Internal Helpers ──────────────────────────────────────────────────


def _extract_language_blocks(text: str, label: str) -> List[str]:
    """Extract text blocks prefixed with '**label:**' in the markdown.

    Returns a list of block bodies (text after the label prefix).
    """
    pattern = rf'\*\*{label}:\*\*\s*(.*?)(?=\n\*\*(?:Thai|English):\*\*|\Z)'
    matches = re.findall(pattern, text, re.DOTALL)
    return [m.strip() for m in matches if m.strip()]


def _detect_language(text: str) -> str:
    """Simple heuristic: if Thai characters present, label 'th', else 'en'."""
    # Thai Unicode range: U+0E00–U+0E7F
    if re.search(r'[\u0E00-\u0E7F]', text):
        return "th"
    return "en"


def _set_offline_env_if_model_cached() -> None:
    """Force HuggingFace offline mode when the embedding model is already cached.

    Without this, SentenceTransformer's first load sends ~20 serial HEAD/GET
    requests to huggingface.co to validate file metadata, adding 20+ seconds of
    network latency to the first policy-search request. The model weights are
    already on disk (458 MB), so the network round-trips are pure waste.

    Only enables offline mode when a local snapshot exists — otherwise we must
    stay online to download the model on first use.
    """
    cache_root = os.environ.get("HF_HOME")
    if cache_root:
        hub_dir = Path(cache_root) / "hub"
    else:
        hub_dir = Path.home() / ".cache" / "huggingface" / "hub"

    # EMBEDDING_MODEL may be "org/name" or a bare path.
    if "/" in EMBEDDING_MODEL:
        org, name = EMBEDDING_MODEL.split("/", 1)
        snapshot_glob = f"models--{org}--{name}"
    else:
        return  # local filesystem path — no hub access needed

    if (hub_dir / snapshot_glob).exists():
        os.environ.setdefault("HF_HUB_OFFLINE", "1")
        os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")


def _get_embedding_model():
    """Lazy-load the sentence-transformers embedding model — cached once per process."""
    _set_offline_env_if_model_cached()
    return _get_sentence_transformer()


@lru_cache(maxsize=1)
def _get_sentence_transformer():
    """Cached model loader — the SentenceTransformer constructor is called at most once."""
    from sentence_transformers import SentenceTransformer
    return SentenceTransformer(EMBEDDING_MODEL)


def warm_up() -> bool:
    """Pre-load the embedding model so the first live request skips the cold start.

    Returns True when the model is ready. Never raises — a failure to warm up
    must not stop the server from starting (the request path still lazy-loads).
    """
    try:
        _get_embedding_model()
        return True
    except Exception:
        return False
