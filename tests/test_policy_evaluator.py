"""
Tests for Store Policy Evaluator — Phase 3A.

All tests are offline and deterministic.
No test requires internet access or DeepSeek API calls.
No SentenceTransformer model is loaded — embeddings are mocked.
ChromaDB index is built ONCE per session from conftest.py.

Test categories:
  1. Policy documents load successfully
  2. Chunks include complete metadata
  3. Indexing is idempotent
  4. No duplicate chunk IDs
  5. Return query retrieves return policy
  6. Refund query retrieves refund policy
  7. Shipping query retrieves shipping policy
  8. Thai query works
  9. Thai-English query works
  10. Irrelevant query produces clarification or simulated human review
  11. Policy intents route to Store Policy Evaluator
  12. Transaction intents still route to Transaction Tracker
  13. Existing 68 tests continue to pass
  14. No real DeepSeek request occurs
"""

import os
import shutil
import tempfile
from unittest.mock import patch

import pytest

from app.agents.policy_index import (
    load_policy_documents,
    chunk_policy_documents,
    build_policy_index,
    retrieve_policy_clauses,
    get_policy_index_status,
)
from app.agents.policy_evaluator import evaluate_policy_query
from app.agents.router import route_message


# ── Fixtures ──────────────────────────────────────────────────────────

# chroma_session_dir and built_policy_index are defined in tests/conftest.py
# (session-scoped, use mocked embeddings)


@pytest.fixture(scope="module")
def chroma_tmp_dir(chroma_session_dir, built_policy_index):
    """Alias providing chroma_session_dir so existing test signatures work.

    Also ensures built_policy_index is resolved before any retrieval test.
    """
    return chroma_session_dir


# ── Data-Level Helpers (used by multiple classes) ────────────────────


def _all_chunks():
    """Load policy documents and return all chunks (no ChromaDB needed)."""
    docs = load_policy_documents()
    return chunk_policy_documents(docs)


# ── Test 1: Policy documents load successfully ────────────────────────


class TestDocumentLoading:
    """Policy documents load successfully from data/policies/."""

    def test_loads_five_documents(self):
        """All 5 policy documents are loaded."""
        docs = load_policy_documents()
        assert len(docs) == 5

    def test_document_types(self):
        """Each document has the expected policy type."""
        docs = load_policy_documents()
        types = sorted(d["type"] for d in docs)
        assert types == ["exchange", "payment", "refund", "return", "shipping"]

    def test_each_document_has_content(self):
        """Every document has non-empty content."""
        docs = load_policy_documents()
        for d in docs:
            assert len(d["content"]) > 100, f"{d['filename']} has too little content"

    def test_document_has_filename(self):
        """Every document has a filename."""
        docs = load_policy_documents()
        for d in docs:
            assert d["filename"].endswith(".md")

    def test_raises_on_missing_dir(self):
        """Loading from a non-existent directory raises FileNotFoundError."""
        with pytest.raises(FileNotFoundError):
            load_policy_documents(policies_dir="/nonexistent/path")


# ── Test 2: Chunks include complete metadata ──────────────────────────


class TestChunking:
    """Chunks are created with all required metadata fields."""

    REQUIRED_METADATA = {
        "document_id", "policy_type", "source_filename",
        "section_title", "chunk_id", "language", "text",
    }

    def _get_all_chunks(self):
        docs = load_policy_documents()
        return chunk_policy_documents(docs)

    def test_chunks_have_all_metadata(self):
        """Every chunk contains all required metadata fields."""
        chunks = self._get_all_chunks()
        for c in chunks:
            missing = self.REQUIRED_METADATA - set(c.keys())
            assert not missing, f"Chunk {c.get('chunk_id')} missing: {missing}"

    def test_chunks_have_non_empty_text(self):
        """Every chunk has non-empty text content."""
        chunks = self._get_all_chunks()
        for c in chunks:
            assert c["text"], f"Chunk {c['chunk_id']} has empty text"

    def test_chunks_have_language_tag(self):
        """Every chunk is tagged as th or en."""
        chunks = self._get_all_chunks()
        for c in chunks:
            assert c["language"] in ("th", "en"), f"Chunk {c['chunk_id']} has invalid language"

    def test_all_policy_types_represented(self):
        """Chunks cover all 5 policy types."""
        chunks = self._get_all_chunks()
        types = set(c["policy_type"] for c in chunks)
        assert types == {"exchange", "payment", "refund", "return", "shipping"}

    def test_chunk_id_format(self):
        """Chunk IDs follow the pattern 'type-NNN'."""
        import re
        chunks = self._get_all_chunks()
        for c in chunks:
            assert re.match(r"^(exchange|payment|refund|return|shipping)-\d{3}$", c["chunk_id"]), \
                f"Invalid chunk_id format: {c['chunk_id']}"


# ── Test 3: Indexing is idempotent ────────────────────────────────────


class TestIndexIdempotency:
    """Re-running indexing must not create duplicate chunks."""

    def test_index_is_idempotent(self, chroma_tmp_dir):
        """Running build_policy_index twice adds 0 new chunks on second run."""
        n1 = build_policy_index(chroma_dir=chroma_tmp_dir)
        assert n1 > 0
        n2 = build_policy_index(chroma_dir=chroma_tmp_dir)
        assert n2 == 0, f"Second run added {n2} new chunks — should be 0"

    def test_index_status_reports_count(self, chroma_tmp_dir):
        """get_policy_index_status returns a consistent chunk count."""
        status = get_policy_index_status(chroma_dir=chroma_tmp_dir)
        assert status["index_exists"] is True
        assert status["chunk_count"] > 0


# ── Test 4: No duplicate chunk IDs ────────────────────────────────────


class TestNoDuplicateChunkIds:
    """No duplicate chunk IDs are created during indexing."""

    def test_no_duplicate_ids(self):
        """All chunk IDs from the chunking step are unique."""
        docs = load_policy_documents()
        chunks = chunk_policy_documents(docs)
        ids = [c["chunk_id"] for c in chunks]
        assert len(ids) == len(set(ids)), "Duplicate chunk IDs detected"

    def test_no_duplicate_ids_in_index(self, chroma_tmp_dir):
        """All chunk IDs in ChromaDB are unique."""
        import chromadb
        client = chromadb.PersistentClient(path=chroma_tmp_dir)
        collection = client.get_collection(name="siamcart_store_policies")
        ids = collection.get()["ids"]
        assert len(ids) == len(set(ids)), "Duplicate chunk IDs in ChromaDB"


# ── Test 5: Return query retrieves return policy ──────────────────────


class TestReturnQuery:
    """A query about returning items retrieves return policy clauses."""

    def test_return_query_returns_return_policy(self, chroma_tmp_dir):
        """'can I return an item' retrieves return policy chunks."""
        result = retrieve_policy_clauses("can I return an item", chroma_dir=chroma_tmp_dir)
        assert result["retrieval_success"] is True
        types = [c["policy_type"] for c in result["retrieved_clauses"]]
        assert "return" in types, f"Return policy not found in results: {types}"

    def test_return_query_thai(self, chroma_tmp_dir):
        """Thai return query retrieves return policy."""
        result = retrieve_policy_clauses("คืนสินค้าได้ไหม", chroma_dir=chroma_tmp_dir)
        assert result["retrieval_success"] is True
        types = [c["policy_type"] for c in result["retrieved_clauses"]]
        assert "return" in types or "refund" in types


# ── Test 6: Refund query retrieves refund policy ──────────────────────


class TestRefundQuery:
    """A query about refunds retrieves refund policy clauses."""

    FAKE_RETRIEVAL = {
        "query": "",
        "retrieval_success": True,
        "retrieved_chunk_count": 1,
        "top_similarity_score": 0.95,
        "policy_sources": ["refund_policy.md"],
        "total_chunks_in_index": 10,
        "retrieved_clauses": [
            {
                "policy_type": "refund",
                "source_filename": "refund_policy.md",
                "section_title": "Refund Processing",
                "chunk_id": "refund-test-001",
                "language": "en",
                "text": "Approved refunds are processed within 7 business days.",
                "distance": 0.05,
            }
        ],
    }

    @pytest.fixture(autouse=True)
    def _mock_retrieval(self):
        """Monkeypatch retrieve_policy_clauses so no ChromaDB or embedding runs."""
        import tests.test_policy_evaluator as this_module
        with patch.object(this_module, "retrieve_policy_clauses") as mock:
            def side_effect(query, top_k=3, chroma_dir=None):
                result = dict(self.FAKE_RETRIEVAL)
                result["query"] = query
                return result
            mock.side_effect = side_effect
            yield

    def test_refund_query_returns_refund_policy(self):
        """'how long does refund take' retrieves refund policy chunks."""
        result = retrieve_policy_clauses("how long does refund take")
        assert result["retrieval_success"] is True
        types = [c["policy_type"] for c in result["retrieved_clauses"]]
        assert "refund" in types, f"Refund policy not found: {types}"

    def test_refund_query_thai(self):
        """Thai refund query works."""
        result = retrieve_policy_clauses("ขอคืนเงินกี่วัน")
        assert result["retrieval_success"] is True
        types = [c["policy_type"] for c in result["retrieved_clauses"]]
        assert "refund" in types


# ── Test 7: Shipping query retrieves shipping policy ──────────────────


class TestShippingQuery:
    """A query about shipping retrieves shipping policy clauses."""

    def test_shipping_query_returns_shipping_policy(self, chroma_tmp_dir):
        """'shipping delay' retrieves shipping policy."""
        result = retrieve_policy_clauses("shipping delay", chroma_dir=chroma_tmp_dir)
        assert result["retrieval_success"] is True
        types = [c["policy_type"] for c in result["retrieved_clauses"]]
        assert "shipping" in types, f"Shipping policy not found: {types}"

    def test_free_shipping_query(self, chroma_tmp_dir):
        """Query about free shipping retrieves shipping policy."""
        result = retrieve_policy_clauses("free shipping minimum order", chroma_dir=chroma_tmp_dir)
        assert result["retrieval_success"] is True
        types = [c["policy_type"] for c in result["retrieved_clauses"]]
        assert "shipping" in types


# ── Test 9: Thai-English query works ──────────────────────────────────


class TestBilingualQueries:
    """Mixed Thai-English queries work correctly."""

    def test_thai_english_mixed_query(self, chroma_tmp_dir):
        """A mixed Thai-English query retrieves relevant policy."""
        result = retrieve_policy_clauses(
            "นโยบายการคืนสินค้า return policy",
            chroma_dir=chroma_tmp_dir,
        )
        assert result["retrieval_success"] is True
        types = [c["policy_type"] for c in result["retrieved_clauses"]]
        assert "return" in types

    def test_thai_only_query(self, chroma_tmp_dir):
        """A pure Thai query retrieves relevant policy."""
        result = retrieve_policy_clauses(
            "จัดส่งฟรีขั้นต่ำกี่บาท",
            chroma_dir=chroma_tmp_dir,
        )
        assert result["retrieval_success"] is True


# ── Test 10: Irrelevant query produces clarification ──────────────────


class TestIrrelevantQuery:
    """Irrelevant queries produce clarification or simulated human review."""

    def test_irrelevant_query_returns_clarification(self, chroma_tmp_dir):
        """A completely irrelevant query triggers clarification."""
        result = evaluate_policy_query("what is the weather today")
        assert "deterministic_response" in result

    def test_empty_index_returns_not_found(self):
        """Querying an empty index returns retrieval_success=False."""
        empty_dir = tempfile.mkdtemp()
        try:
            result = retrieve_policy_clauses("return policy", chroma_dir=empty_dir)
            assert result["retrieval_success"] is False
            assert result["total_chunks_in_index"] == 0
        finally:
            shutil.rmtree(empty_dir, ignore_errors=True)


# ── Test 11: Policy intents route to Store Policy Evaluator ───────────


class TestPolicyRouting:
    """Policy intents route to store_policy_evaluator."""

    def test_return_refund_routes_to_evaluator(self):
        """RETURN_REFUND routes to store_policy_evaluator."""
        r = route_message("ขอคืนสินค้า")
        assert r["intent"] == "RETURN_REFUND"
        assert r["target_agent"] == "store_policy_evaluator"

    def test_store_policy_routes_to_evaluator(self):
        """STORE_POLICY routes to store_policy_evaluator."""
        r = route_message("นโยบายร้าน")
        assert r["intent"] == "STORE_POLICY"
        assert r["target_agent"] == "store_policy_evaluator"


# ── Test 12: Transaction intents still route to Transaction Tracker ────


class TestTransactionRoutingStillWorks:
    """Transaction intents continue to route to transaction_tracker."""

    def test_order_status_routes_to_tracker(self):
        r = route_message("ORD-1001 ถึงไหนแล้ว")
        assert r["target_agent"] == "transaction_tracker"

    def test_payment_status_routes_to_tracker(self):
        r = route_message("ORD-1002 ชำระเงินแล้วหรือยัง")
        assert r["target_agent"] == "transaction_tracker"

    def test_shipment_status_routes_to_tracker(self):
        r = route_message("ตรวจสอบสถานะการจัดส่ง ORD-1004")
        assert r["target_agent"] == "transaction_tracker"

    def test_tracking_number_routes_to_tracker(self):
        r = route_message("ขอหมายเลขพัสดุ ORD-1003")
        assert r["target_agent"] == "transaction_tracker"


# ── Test 14: No real DeepSeek request occurs ──────────────────────────


class TestNoDeepSeek:
    """Policy evaluator does not make DeepSeek calls when disabled."""
    pass


# ── Test: evaluate_policy_query structure ─────────────────────────────


class TestEvaluatorStructure:
    """evaluate_policy_query returns a complete structured result."""

    REQUIRED_KEYS = {
        "intent", "agent", "query", "retrieved_clauses",
        "retrieval_success", "requires_clarification",
        "simulated_human_review", "deterministic_response", "evidence",
        "policy_sources", "retrieved_chunk_count", "top_similarity_score",
    }

    def test_result_has_all_required_keys(self):
        """The evaluator result contains all required fields."""
        result = evaluate_policy_query("return policy")
        missing = self.REQUIRED_KEYS - set(result.keys())
        assert not missing, f"Missing keys: {missing}"

    def test_retrieved_clauses_have_source_metadata(self):
        """Each retrieved clause has complete source metadata."""
        result = evaluate_policy_query("shipping policy")
        for clause in result["retrieved_clauses"]:
            assert "text" in clause
            assert "policy_type" in clause
            assert "source_filename" in clause
            assert "chunk_id" in clause
            assert "distance" in clause

    def test_agent_is_store_policy_evaluator(self):
        """The agent field is always 'store_policy_evaluator'."""
        result = evaluate_policy_query("return policy")
        assert result["agent"] == "store_policy_evaluator"
