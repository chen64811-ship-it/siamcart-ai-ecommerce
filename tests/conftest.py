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

# Tests must never inherit production integrations from a developer's .env.
# Individual tests may still monkeypatch app.config when explicitly exercising
# an enabled feature.
os.environ["DEEPSEEK_ENABLED"] = "false"
os.environ["DEEPSEEK_API_KEY"] = ""
os.environ["AUTH_REQUIRED"] = "false"

# ── Mock embedding model ────────────────────────────────────────────

EMBEDDING_DIM = 384  # matches paraphrase-multilingual-MiniLM-L12-v2


class _MockSentenceTransformer:
    """Deterministic bag-of-words fake that never loads PyTorch or HF models.

    Each token is hashed into fixed positions of an EMBEDDING_DIM vector and
    the result is L2-normalised. Texts that share vocabulary therefore get
    similar vectors, so policy-retrieval tests exercise real ranking
    semantics (return query → return policy) without a model download.
    """

    def encode(self, sentences, **kwargs):
        """Return an EMBEDDING_DIM bag-of-words vector per input text."""
        import hashlib
        import re

        if isinstance(sentences, str):
            sentences = [sentences]

        vectors = []
        for text in sentences:
            vec = np.zeros(EMBEDDING_DIM, dtype=np.float32)
            tokens = re.findall(r"[0-9A-Za-z\u0E00-\u0E7F]+", text.lower())
            for token in tokens:
                h = hashlib.sha256(token.encode("utf-8")).digest()
                for offset in range(0, EMBEDDING_DIM, len(h)):
                    chunk = h[: min(len(h), EMBEDDING_DIM - offset)]
                    vec[offset:offset + len(chunk)] += (
                        np.frombuffer(chunk, dtype=np.uint8).astype(np.float32) / 255.0
                    )
            norm = float(np.linalg.norm(vec))
            if norm > 0:
                vec /= norm
            vectors.append(vec)

        arr = np.stack(vectors)
        if len(sentences) == 1:
            return arr[0]
        return arr

    def tolist(self):
        return self


def _mock_get_embedding_model():
    """Return a mock embedding model — no PyTorch, no HF download."""
    return _MockSentenceTransformer()


@pytest.fixture(scope="session", autouse=True)
def _use_mock_embeddings_everywhere():
    """Patch the embedding model for the whole test session.

    build_policy_index() and retrieve_policy_clauses() must share the SAME
    vector space. If the index is built with the mock but queried with the
    real sentence-transformer (or vice versa), ChromaDB compares vectors
    from incompatible spaces and returns arbitrary policy types.
    """
    with patch("app.agents.policy_index._get_embedding_model", _mock_get_embedding_model):
        yield


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
