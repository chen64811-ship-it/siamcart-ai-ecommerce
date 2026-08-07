"""Intent-boundary hotfix — focused tests.

FINAL INTENT-BOUNDARY HOTFIX:
  - Policy questions route to STORE_POLICY (never OUT_OF_SCOPE / RETURN_REFUND),
    answered from deterministic local sources (ChromaDB → keyword → Thai fallback),
    never requiring an order ID, with DEEPSEEK_ENABLED=false.
  - Research/about questions route to RESEARCH_INFO with a deterministic Thai
    answer from static application metadata (no SQLite, no DeepSeek).
  - An explicit new topic overrides active_order_id: policy/research questions
    in a session with an active order are never forced into order clarification.

Run ONLY this file (focused): the thesis evaluation suite is out of scope.
"""

import os

# Deterministic-only: must be set BEFORE any app import reads .env.
os.environ["DEEPSEEK_ENABLED"] = "false"

import pytest
from fastapi.testclient import TestClient

from app.agents.router import route_message
from app.config import DEEPSEEK_ENABLED


@pytest.fixture(scope="module")
def client():
    from app.api.server import app
    with TestClient(app) as c:
        yield c


SESSION = "hotfix-session-1"

POLICY_PHRASES = [
    "What is the return policy?",
    "refund policy",
    "store policy",
    "cancellation policy",
    "What policies does SiamCart follow?",
    "นโยบายการคืนสินค้า",
    "นโยบายร้าน",
    "คืนสินค้าได้ไหม",
]

RESEARCH_PHRASES = [
    "What research is SiamCart part of?",
    "What is SiamCart?",
    "what is this project",
    "what is this research about",
    "is this a research prototype",
    "งานวิจัยนี้เกี่ยวกับอะไร",
    "SiamCart คืออะไร",
]


class TestRouterClassification:
    """All spec phrases classify deterministically."""

    def test_policy_phrases_route_to_store_policy(self):
        for q in POLICY_PHRASES:
            r = route_message(q)
            assert r["intent"] == "STORE_POLICY", f"{q!r} -> {r['intent']}"
            assert r["requires_clarification"] is False, q
            assert r["extracted_order_id"] is None, q

    def test_research_phrases_route_to_research_info(self):
        for q in RESEARCH_PHRASES:
            r = route_message(q)
            assert r["intent"] == "RESEARCH_INFO", f"{q!r} -> {r['intent']}"
            assert r["requires_clarification"] is False, q

    def test_order_queries_unchanged(self):
        # Regression guards: order intents keep their existing routes.
        r = route_message("Where is order ORD-1001?")
        assert r["intent"] == "ORDER_STATUS"
        assert r["extracted_order_id"] == "ORD-1001"
        r = route_message("Where is my order?")
        assert r["intent"] == "UNKNOWN"  # asks for order ID via clarification
        assert r["requires_clarification"] is True
        # A bare refund REQUEST keeps the RETURN_REFUND flow.
        r = route_message("ขอคืนสินค้า")
        assert r["intent"] == "RETURN_REFUND"

    def test_policy_question_with_order_id_is_still_policy(self):
        # "What is SiamCart's return policy?" must NOT become a research
        # question, and never demands the order ID.
        r = route_message("What is SiamCart's return policy?")
        assert r["intent"] == "STORE_POLICY"


class TestOrchestratorSessionFlow:
    """Exact live-query flow: one session, order first, then topic switches."""

    def test_full_session_flow(self, client):
        assert DEEPSEEK_ENABLED is False

        # Establish the active order first (Transaction Tracker still works).
        r0 = client.post("/api/chat", json={
            "message": "Where is order ORD-1001?", "session_id": SESSION,
        })
        assert r0.status_code == 200
        d0 = r0.json()
        assert d0["intent"] == "ORDER_STATUS"
        assert d0["order_id"] == "ORD-1001"
        assert d0["active_order_id"] == "ORD-1001"
        assert d0["response_source"] == "deterministic"
        assert d0["llm_enabled"] is False

        # 1. Policy question → STORE_POLICY, grounded Thai, no order-ID request.
        r1 = client.post("/api/chat", json={
            "message": "What is the return policy?", "session_id": SESSION,
        })
        assert r1.status_code == 200
        d1 = r1.json()
        assert d1["intent"] == "STORE_POLICY"
        assert d1["requires_clarification"] is False
        assert "นโยบายการคืนสินค้า" in d1["response"] or "คืนสินค้า" in d1["response"]
        assert d1["order_id"] is None
        assert d1["response_source"] == "deterministic"
        assert d1["policy_evidence"]["retrieval_success"] is True
        assert d1["policy_evidence"]["retrieval_source"] in ("chromadb", "keyword")

        # 2. General policy question → STORE_POLICY, grounded Thai overview.
        r2 = client.post("/api/chat", json={
            "message": "What policies does SiamCart follow?", "session_id": SESSION,
        })
        assert r2.status_code == 200
        d2 = r2.json()
        assert d2["intent"] == "STORE_POLICY"
        assert d2["requires_clarification"] is False
        assert "นโยบาย" in d2["response"]
        assert d2["order_id"] is None

        # 3. Research question → RESEARCH_INFO, Thai, no order-ID request.
        r3 = client.post("/api/chat", json={
            "message": "What research is SiamCart part of?", "session_id": SESSION,
        })
        assert r3.status_code == 200
        d3 = r3.json()
        assert d3["intent"] == "RESEARCH_INFO"
        assert d3["requires_clarification"] is False
        assert "งานวิจัย" in d3["response"] or "research" in d3["response"]
        assert d3["order_id"] is None
        assert d3["response_source"] == "deterministic"

        # 4. Identity question → research/about answer.
        r4 = client.post("/api/chat", json={
            "message": "What is SiamCart?", "session_id": SESSION,
        })
        assert r4.status_code == 200
        d4 = r4.json()
        assert d4["intent"] == "RESEARCH_INFO"
        assert d4["requires_clarification"] is False
        assert "SiamCart" in d4["response"]

        # 5. Order question with genuinely missing ID (reset session) → asks.
        r5 = client.post("/api/chat", json={
            "message": "Where is my order?", "session_id": "hotfix-fresh-session",
        })
        assert r5.status_code == 200
        d5 = r5.json()
        assert d5["requires_clarification"] is True
        assert "หมายเลขคำสั่งซื้อ" in d5["response"]

        # 6. Explicit order ID → Transaction Tracker still works.
        r6 = client.post("/api/chat", json={
            "message": "Where is order ORD-1001?", "session_id": SESSION,
        })
        assert r6.status_code == 200
        d6 = r6.json()
        assert d6["intent"] == "ORDER_STATUS"
        assert d6["order_id"] == "ORD-1001"
        assert "ORD-1001" in d6["response"]

    def test_research_question_overrides_pending_order_workflow(self, client):
        # Start a workflow that is waiting for an order ID, then switch topic.
        client.post("/api/chat", json={
            "message": "Where is my order?", "session_id": "hotfix-pending-session",
        })
        r = client.post("/api/chat", json={
            "message": "What research is SiamCart part of?",
            "session_id": "hotfix-pending-session",
        })
        assert r.status_code == 200
        d = r.json()
        assert d["intent"] == "RESEARCH_INFO"
        assert d["requires_clarification"] is False
        assert "หมายเลขคำสั่งซื้อ" not in d["response"]
