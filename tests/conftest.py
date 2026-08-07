"""
Shared test fixtures for policy indexing and evaluation.

All tests are offline and deterministic.
No SentenceTransformer model is downloaded — embeddings are mocked.
No real DeepSeek API calls are made.
"""

import os
import shutil
import tempfile
import numpy as np
from unittest.mock import patch

import pytest

# ── Mock embedding model ────────────────────────────────────────────

EMBEDDING_DIM = 384  # matches paraphrase-multilingual-MiniLM-L12-v2


class _MockSentenceTransformer:
    """A deterministic fake that never loads PyTorch or HF models."""

    def encode(self, sentences, **kwargs):
        """Return a fixed-dimension vector based on hash of input text."""
        import hashlib

        if isinstance(sentences, str):
            sentences = [sentences]

        vectors = []
        for text in sentences:
            h = hashlib.sha256(text.encode("utf-8")).digest()
            raw = np.frombuffer(h, dtype=np.uint8)[:EMBEDDING_DIM].astype(np.float32)
            raw = (raw / 255.0) * 0.2 - 0.1
            vectors.append(raw)

        arr = np.stack(vectors)
        if len(sentences) == 1:
            return arr[0]
        return arr

    def tolist(self):
        return self


def _mock_get_embedding_model():
    """Return a mock embedding model — no PyTorch, no HF download."""
    return _MockSentenceTransformer()


# ── Session-scoped ChromaDB directory ───────────────────────────────


@pytest.fixture(scope="session")
def chroma_session_dir():
    """Create a single temp ChromaDB directory for the entire test session.

    Builds the index once with mocked embeddings.
    All policy tests share this same index.
    """
    tmp_dir = tempfile.mkdtemp(prefix="chroma_sess_")
    yield tmp_dir
    shutil.rmtree(tmp_dir, ignore_errors=True)


@pytest.fixture(scope="session")
def built_policy_index(chroma_session_dir):
    """Build the policy index once per session using mocked embeddings.

    Depends on chroma_session_dir.  The index is populated exactly once.
    """
    from app.agents.policy_index import build_policy_index

    with patch("app.agents.policy_index._get_embedding_model", _mock_get_embedding_model):
        n = build_policy_index(chroma_dir=chroma_session_dir)

    assert n > 0, (
        f"Index build added 0 chunks.  "
        f"Check that the mock encoder dimension ({EMBEDDING_DIM}) "
        f"matches the ChromaDB collection expectation."
    )
    return n
