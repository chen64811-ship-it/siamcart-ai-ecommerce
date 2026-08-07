"""
Tests for the FastAPI web chat endpoint — Phase 1A.

Uses httpx TestClient for HTTP-level testing.
"""

import os
import json
import tempfile
from unittest.mock import MagicMock

import pytest
from httpx import ASGITransport, AsyncClient

# Patch config paths BEFORE importing the app module
import app.config as cfg
cfg.DATA_DIR = cfg.BASE_DIR / "data"
cfg.LOGS_DIR = cfg.BASE_DIR / "logs"
cfg.STATIC_DIR = cfg.BASE_DIR / "app" / "static"

from app.api.server import app
from app.db.orders import init_database


@pytest.fixture(scope="module")
def test_client():
    """Provide an async test client for the FastAPI app."""
    # Ensure database exists
    from app.api.server import DB_PATH
    init_database(DB_PATH)
    transport = ASGITransport(app=app)
    client = AsyncClient(transport=transport, base_url="http://test")
    yield client


class TestWebChat:
    """Focused tests for the web chat API."""

    @pytest.mark.asyncio
    async def test_get_health_returns_200(self, test_client):
        """GET /health returns HTTP 200 and correct schema."""
        resp = await test_client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["database"] == "available"
        assert data["active_agent"] == "transaction_tracker"

    @pytest.mark.asyncio
    async def test_get_index_returns_html(self, test_client):
        """GET / returns the HTML page."""
        resp = await test_client.get("/")
        assert resp.status_code == 200
        content_type = resp.headers.get("content-type", "")
        assert "text/html" in content_type
        body = resp.text
        assert "SiamCart" in body
        assert "chat" in body.lower()

    @pytest.mark.asyncio
    async def test_chat_valid_order(self, test_client):
        """A valid order-status query returns correct evidence."""
        resp = await test_client.post(
            "/api/chat",
            json={"message": "ORD-1001 ถึงไหนแล้ว", "session_id": "test-001"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["intent"] == "ORDER_STATUS"
        assert data["order_id"] == "ORD-1001"
        assert data["agent"] == "transaction_tracker"
        assert data["requires_clarification"] is False
        assert isinstance(data["latency_ms"], float)

    @pytest.mark.asyncio
    async def test_chat_payment_status(self, test_client):
        """Payment-status query returns correct payment_status."""
        resp = await test_client.post(
            "/api/chat",
            json={"message": "ORD-1002 ชำระเงินแล้วหรือยัง", "session_id": "test-002"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["intent"] == "PAYMENT_STATUS"
        assert data["evidence"]["payment_status"] == "paid"

    @pytest.mark.asyncio
    async def test_chat_tracking(self, test_client):
        """Tracking query returns correct tracking number."""
        resp = await test_client.post(
            "/api/chat",
            json={"message": "ขอหมายเลขพัสดุ ORD-1003", "session_id": "test-003"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["intent"] == "TRACKING_NUMBER"
        assert "JNT-TRK-1003" in data["response"]

    @pytest.mark.asyncio
    async def test_missing_order_id(self, test_client):
        """Missing order ID returns clarification."""
        resp = await test_client.post(
            "/api/chat",
            json={"message": "สอบถามสถานะ", "session_id": "test-004"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["intent"] == "CLARIFICATION"
        assert data["requires_clarification"] is True

    @pytest.mark.asyncio
    async def test_unknown_order_id(self, test_client):
        """Unknown order returns ORDER_NOT_FOUND with no invented info."""
        resp = await test_client.post(
            "/api/chat",
            json={"message": "ORD-9999 ถึงไหน", "session_id": "test-005"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["intent"] == "ORDER_NOT_FOUND"
        assert data["order_id"] == "ORD-9999"
        assert "ไม่พบข้อมูล" in data["response"]

    @pytest.mark.asyncio
    async def test_chat_returns_latency(self, test_client):
        """POST /api/chat returns latency_ms field."""
        resp = await test_client.post(
            "/api/chat",
            json={"message": "ORD-1001", "session_id": "test-latency"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data["latency_ms"], float)
        assert data["latency_ms"] >= 0

    @pytest.mark.asyncio
    async def test_chat_writes_experiment_log(self, test_client):
        """POST /api/chat writes a record to the experiment log."""
        from app.api.server import EXPERIMENT_LOG
        # Clear log
        if os.path.exists(EXPERIMENT_LOG):
            os.remove(EXPERIMENT_LOG)
        # Send a message
        resp = await test_client.post(
            "/api/chat",
            json={"message": "ORD-1001", "session_id": "test-log"},
        )
        assert resp.status_code == 200
        # Check log exists and has content
        assert os.path.exists(EXPERIMENT_LOG)
        with open(EXPERIMENT_LOG, "r", encoding="utf-8") as f:
            lines = f.readlines()
        assert len(lines) >= 1
        record = json.loads(lines[-1])
        assert record["session_id"] == "test-log"
        assert record["intent"] == "ORDER_STATUS"
        assert record["selected_agent"] == "transaction_tracker"
        assert record["latency_ms"] >= 0

    # ── Frontend verification tests ─────────────────────────────────

    @pytest.mark.asyncio
    async def test_index_contains_stylesheet_link(self, test_client):
        """GET / returns HTML containing the stylesheet link."""
        resp = await test_client.get("/")
        body = resp.text
        assert "styles.css" in body
        assert "/static/css/styles.css" in body

    @pytest.mark.asyncio
    async def test_static_css_returns_200(self, test_client):
        """GET /static/css/styles.css returns 200 and non-empty CSS."""
        resp = await test_client.get("/static/css/styles.css")
        assert resp.status_code == 200
        content_type = resp.headers.get("content-type", "")
        assert "css" in content_type
        assert len(resp.text) > 100
        assert ".navbar" in resp.text

    @pytest.mark.asyncio
    async def test_static_js_returns_200(self, test_client):
        """GET /static/js/chat.js returns 200 and non-empty JS."""
        resp = await test_client.get("/static/js/chat.js")
        assert resp.status_code == 200
        content_type = resp.headers.get("content-type", "")
        assert "javascript" in content_type
        assert len(resp.text) > 50
        assert "sendMessage" in resp.text


class TestPolicyAPIWiring:
    """Verify policy evaluator results flow through orchestrator → POST /api/chat.

    Uses monkeypatching to avoid real retrieval, ChromaDB, DeepSeek, or network.
    """

    @staticmethod
    def _make_policy_result(**overrides):
        """Build a realistic completed policy result dict."""
        result = {
            "intent": "STORE_POLICY",
            "agent": "store_policy_evaluator",
            "query": "นโยบายการคืนสินค้า",
            "response": "ตามนโยบายการคืนสินค้าของ SiamCart: สามารถคืนสินค้าได้ภายใน 30 วันนับจากวันที่ได้รับสินค้า",
            "retrieved_clauses": [
                {
                    "text": "สามารถคืนสินค้าได้ภายใน 30 วันนับจากวันที่ได้รับสินค้า",
                    "policy_type": "return",
                    "source_filename": "return_policy.pdf",
                    "chunk_id": "chunk-001",
                    "section_title": "การคืนสินค้า",
                    "similarity_score": 0.95,
                }
            ],
            "retrieval_success": True,
            "requires_clarification": False,
            "simulated_human_review": False,
            "deterministic_response": "ตามนโยบายการคืนสินค้าของ SiamCart: สามารถคืนสินค้าได้ภายใน 30 วันนับจากวันที่ได้รับสินค้า",
            "evidence": {
                "policy_type": "return",
                "primary_source": "return_policy.pdf",
                "retrieved_chunks": 1,
                "best_distance": 0.35,
                "top_clause_section": "การคืนสินค้า",
                "policy_sources": ["return_policy.pdf"],
                "retrieved_chunk_count": 1,
                "top_similarity_score": 0.65,
            },
            "policy_sources": ["return_policy.pdf"],
            "retrieved_chunk_count": 1,
            "top_similarity_score": 0.65,
            "response_source": "deepseek",
            "llm_enabled": True,
            "llm_fallback_used": False,
            "llm_latency_ms": 423.15,
            "llm_error_type": None,
            "grounding_validation_passed": True,
        }
        result.update(overrides)
        return result

    @pytest.fixture(autouse=True)
    def _patch_evaluator(self, monkeypatch):
        """Mock the policy evaluator at its import site in the orchestrator."""
        self.mock_result = self._make_policy_result()

        def fake_evaluate(query, top_k=3):
            return self.mock_result

        monkeypatch.setattr(
            "app.agents.orchestrator.evaluate_policy_query",
            fake_evaluate,
        )

    @pytest.fixture(scope="class")
    def test_client(self):
        """Provide an async test client for the FastAPI app."""
        from app.api.server import DB_PATH
        from app.db.orders import init_database
        init_database(DB_PATH)
        transport = ASGITransport(app=app)
        client = AsyncClient(transport=transport, base_url="http://test")
        yield client

    # ── Test 1: Policy response text ──

    @pytest.mark.asyncio
    async def test_policy_response_text_preserved(self, test_client):
        """The policy evaluator's response text appears verbatim in API output."""
        resp = await test_client.post(
            "/api/chat",
            json={"message": "นโยบายการคืนสินค้า", "session_id": "pol-test-1"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["response"] == self.mock_result["response"]

    # ── Test 2: agent field ──

    @pytest.mark.asyncio
    async def test_policy_agent_field(self, test_client):
        """The agent field is store_policy_evaluator for policy queries."""
        resp = await test_client.post(
            "/api/chat",
            json={"message": "นโยบายการคืนสินค้า", "session_id": "pol-test-2"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["agent"] == "store_policy_evaluator"

    # ── Test 3: response_source ──

    @pytest.mark.asyncio
    async def test_policy_response_source(self, test_client):
        """response_source field shows the origin of the response."""
        resp = await test_client.post(
            "/api/chat",
            json={"message": "นโยบายการคืนสินค้า", "session_id": "pol-test-3"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["response_source"] == "deepseek"

    # ── Test 4: grounding_validation_passed ──

    @pytest.mark.asyncio
    async def test_policy_grounding_validation(self, test_client):
        """grounding_validation_passed is preserved in API response."""
        resp = await test_client.post(
            "/api/chat",
            json={"message": "นโยบายการคืนสินค้า", "session_id": "pol-test-4"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["grounding_validation_passed"] is True

    # ── Test 5: policy_sources and retrieved_chunk_count ──

    @pytest.mark.asyncio
    async def test_policy_sources_and_chunk_count(self, test_client):
        """policy_sources and retrieved_chunk_count appear in policy_evidence."""
        resp = await test_client.post(
            "/api/chat",
            json={"message": "นโยบายการคืนสินค้า", "session_id": "pol-test-5"},
        )
        assert resp.status_code == 200
        data = resp.json()
        pe = data["policy_evidence"]
        assert pe["policy_sources"] == ["return_policy.pdf"]
        assert pe["retrieved_chunk_count"] == 1

    # ── Test 6: Fallback and error fields ──

    @pytest.mark.asyncio
    async def test_fallback_and_error_fields(self, test_client):
        """llm_fallback_used, llm_error_type, llm_latency_ms reach the API."""
        resp = await test_client.post(
            "/api/chat",
            json={"message": "นโยบายการคืนสินค้า", "session_id": "pol-test-6"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["llm_fallback_used"] is False
        assert data["llm_error_type"] is None
        assert data["llm_latency_ms"] == 423.15

    # ── Test 7: Clarification and human-review flags ──

    @pytest.mark.asyncio
    async def test_clarification_and_human_review_flags(self, test_client):
        """requires_clarification and simulated_human_review reach the API."""
        resp = await test_client.post(
            "/api/chat",
            json={"message": "นโยบายการคืนสินค้า", "session_id": "pol-test-7"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["requires_clarification"] is False
        assert data["simulated_human_review"] is False

    # ── Test 8: Transaction response unchanged ──

    @pytest.mark.asyncio
    async def test_transaction_response_unchanged(self, test_client):
        """Existing order queries still return transaction_tracker agent."""
        resp = await test_client.post(
            "/api/chat",
            json={"message": "ORD-1001 ถึงไหนแล้ว", "session_id": "pol-test-8"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["agent"] == "transaction_tracker"

    @pytest.mark.asyncio
    async def test_page_contains_thai_text(self, test_client):
        """The HTML page contains English interface text (Phase 3B)."""
        resp = await test_client.get("/")
        body = resp.text
        # The page is English now — verify key English strings
        assert "Welcome to SiamCart" in body
        assert "Customer Support" in body

    @pytest.mark.asyncio
    async def test_page_has_chat_input_and_send(self, test_client):
        """The HTML page includes the chat input and send button."""
        resp = await test_client.get("/")
        body = resp.text
        assert "chatInput" in body
        assert "sendBtn" in body
        assert "chat.js" in body

    @pytest.mark.asyncio
    async def test_chat_endpoint_remains_functional(self, test_client):
        """POST /api/chat still works after frontend changes."""
        resp = await test_client.post(
            "/api/chat",
            json={"message": "ORD-1001", "session_id": "func-test"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["intent"] == "ORDER_STATUS"
        assert data["agent"] == "transaction_tracker"
        assert "shipped" in data["response"]

    @pytest.mark.asyncio
    async def test_chat_includes_llm_fields(self, test_client):
        """POST /api/chat includes the new Phase 1B LLM fields and Phase 2A router fields."""
        resp = await test_client.post(
            "/api/chat",
            json={"message": "ORD-1001", "session_id": "llm-fields-test"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "response_source" in data
        assert "llm_enabled" in data
        assert "llm_fallback_used" in data
        assert "llm_latency_ms" in data
        assert "routing_confidence" in data
        assert "routing_reason" in data
        # With default config (DEEPSEEK_ENABLED=false), source is always deterministic
        assert data["response_source"] == "deterministic"
        assert data["llm_enabled"] is False
        assert data["llm_fallback_used"] is False
        assert data["llm_latency_ms"] == 0.0
        assert isinstance(data["routing_confidence"], (int, float))
        assert isinstance(data["routing_reason"], str)


class TestEnglishStaticUI:
    """Verify all static webpage interface text has been converted to English.

    Reads the rendered index.html directly — no browser automation needed.
    """

    _html = None

    @pytest.fixture(scope="class")
    def test_client(self):
        """Provide an async test client for the FastAPI app."""
        from app.api.server import DB_PATH
        from app.db.orders import init_database
        init_database(DB_PATH)
        transport = ASGITransport(app=app)
        client = AsyncClient(transport=transport, base_url="http://test")
        yield client

    async def _fetch_html(self, test_client):
        """Lazy-load the HTML once for the whole class."""
        if TestEnglishStaticUI._html is None:
            resp = await test_client.get("/")
            assert resp.status_code == 200
            TestEnglishStaticUI._html = resp.text
        return TestEnglishStaticUI._html

    @pytest.mark.asyncio
    async def test_hero_title_is_english(self, test_client):
        """1. Hero title is English."""
        body = await self._fetch_html(test_client)
        assert "Welcome to SiamCart Demo Store" in body

    @pytest.mark.asyncio
    async def test_nav_and_section_headings_are_english(self, test_client):
        """2. Navigation and section headings are English."""
        body = await self._fetch_html(test_client)
        assert "Post-Purchase Customer Support Demo" in body
        assert "Recommended Products" in body
        assert "Customer Support" in body

    @pytest.mark.asyncio
    async def test_all_six_product_names_are_english(self, test_client):
        """3. All six product names are English."""
        body = await self._fetch_html(test_client)
        assert "Women's Shirt" in body
        assert "Bluetooth Headphones" in body
        assert "Smartphone Model A" in body
        assert "Notebook" in body
        assert "Running Shoes" in body
        assert "Shoulder Bag" in body

    @pytest.mark.asyncio
    async def test_chat_title_and_welcome_are_english(self, test_client):
        """4. Chat title and welcome message are English."""
        body = await self._fetch_html(test_client)
        assert "Customer Support Chat" in body
        assert "Hello! Welcome to SiamCart." in body

    @pytest.mark.asyncio
    async def test_six_quick_question_labels_are_english(self, test_client):
        """5. Six quick-question labels are English."""
        body = await self._fetch_html(test_client)
        assert "Where is order ORD-1001?" in body
        assert "Has order ORD-1002 been paid?" in body
        assert "Show the tracking number for ORD-1003" in body
        assert "Check the shipment status of ORD-1004" in body
        assert "Can I return an item?" in body
        assert "How long does a refund take?" in body

    @pytest.mark.asyncio
    async def test_input_placeholder_and_send_button_are_english(self, test_client):
        """6. Input placeholder and send button are English."""
        body = await self._fetch_html(test_client)
        assert 'placeholder="Type your message..."' in body
        assert ">Send</button>" in body

    @pytest.mark.asyncio
    async def test_dev_metadata_labels_are_english(self, test_client):
        """7. Developer metadata labels are English."""
        body = await self._fetch_html(test_client)
        assert "Intent:" in body
        assert "Agent:" in body
        assert "Order ID:" in body
        assert "Latency:" in body
        assert "Source:" in body
        assert "Conf:" in body or "Confidence:" in body
        assert "Policy:" in body
        assert "Chunks:" in body

    @pytest.mark.asyncio
    async def test_footer_is_english(self, test_client):
        """8. Footer is English."""
        body = await self._fetch_html(test_client)
        assert "Thai E-commerce Multi-Agent Customer Support Framework" in body

    @pytest.mark.asyncio
    async def test_known_old_thai_labels_absent(self, test_client):
        """9. Known old Thai static labels are absent."""
        body = await self._fetch_html(test_client)
        old_thai = [
            "สินค้าแนะนำ",         # Recommended Products
            "เสื้อเชิ้ต",           # Women's Shirt
            "หูฟังบลูทูธ",         # Bluetooth Headphones
            "สนับสนุนลูกค้า",      # Customer Support
            "บริการลูกค้า",        # Customer Service
            "สอบถามเกี่ยวกับ",      # Ask about
            "พิมพ์ข้อความ",        # Type your message
            "ส่ง",                 # Send
            "ที่อยู่คำสั่งซื้อ",   # Where is order
            "ชำระเงินแล้วหรือยัง",  # Has order been paid
            "หมายเลขพัสดุ",        # Tracking number
            "สถานะการจัดส่ง",      # Shipment status
            "คืนสินค้า",           # Return item
            "คืนเงิน",             # Refund
        ]
        for thai in old_thai:
            assert thai not in body, f"Old Thai label found: {thai}"

    @pytest.mark.asyncio
    async def test_post_api_chat_behavior_not_modified(self, test_client):
        """10. POST /api/chat behavior is not modified."""
        resp = await test_client.post(
            "/api/chat",
            json={"message": "ORD-1001", "session_id": "eng-ui-test"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["intent"] == "ORDER_STATUS"
        assert data["agent"] == "transaction_tracker"
        assert isinstance(data["latency_ms"], float)


class TestMockedPolicyEndToEnd:
    """Micro-task 3B-6A: mocked end-to-end policy integration verification.

    Mocks only external boundaries:
      - retrieve_policy_clauses() in policy_evaluator
      - DeepSeek OpenAI client

    Exercises the full application pipeline:
      query -> route -> orchestrator -> policy evaluator -> DeepSeek mock
      -> validation -> FastAPI JSON response

    No real ChromaDB, SentenceTransformer, or network calls.
    """

    # Fixed mocked evidence matching the task spec
    # Maps to retrieve_policy_clauses() return format with distance
    _MOCKED_RETRIEVAL_RESULT = {
        "query": "ขอคืนเงินใช้เวลากี่วัน",
        "retrieved_clauses": [
            {
                "text": "Approved refunds are processed within 7 business days.",
                "policy_type": "refund",
                "source_filename": "refund_policy.md",
                "chunk_id": "refund-test-001",
                "section_title": "Refund Processing",
                "language": "en",
                "distance": 0.05,  # 1.0 - 0.95 similarity
            }
        ],
        "retrieval_success": True,
        "total_chunks_in_index": 5,
    }

    # Valid Thai DeepSeek response preserving 7-business-day value
    _EXPECTED_THAI_RESPONSE = (
        "การคืนเงินจะดำเนินการภายใน 7 วันทำการค่ะ"
    )

    @pytest.fixture(scope="class")
    def test_client(self):
        """Provide an async test client for the FastAPI app."""
        from app.api.server import DB_PATH
        from app.db.orders import init_database
        init_database(DB_PATH)
        transport = ASGITransport(app=app)
        client = AsyncClient(transport=transport, base_url="http://test")
        yield client

    @pytest.fixture(autouse=True)
    def _mock_external_boundaries(self, monkeypatch):
        """Mock only external dependencies: retrieve_policy_clauses + DeepSeek.

        Does NOT mock the orchestrator, policy evaluator, router,
        validation, or API response path.
        """
        # ── 1. Mock retrieval (no ChromaDB, no SentenceTransformer) ──
        self.mock_retrieval = MagicMock(return_value=self._MOCKED_RETRIEVAL_RESULT)
        monkeypatch.setattr(
            "app.agents.policy_evaluator.retrieve_policy_clauses",
            self.mock_retrieval,
        )

        # ── 2. Enable DeepSeek with a fake api_key ──
        monkeypatch.setattr(
            "app.agents.policy_evaluator.DEEPSEEK_ENABLED",
            True,
        )
        monkeypatch.setattr(
            "app.agents.policy_evaluator.LLM_CONFIG",
            {
                "api_key": "sk-test-mock-key",
                "base_url": "https://api.deepseek.com",
                "model": "deepseek-chat",
                "temperature": 0.1,
                "max_tokens": 512,
                "timeout": 8,
            },
        )

        # ── 3. Mock DeepSeek OpenAI client (no real network call) ──
        self.mock_deepseek_create = MagicMock()

        def fake_create(**kwargs):
            class FakeMessage:
                def __init__(self, content):
                    self.content = content
                    self.role = "assistant"

            class FakeChoice:
                def __init__(self, content):
                    self.message = FakeMessage(content)
                    self.finish_reason = "stop"
                    self.index = 0

            class FakeResponse:
                def __init__(self, content):
                    self.choices = [FakeChoice(content)]
                    self.model = "deepseek-chat"
                    self.usage = None
                    self.id = "mock-chat-001"
                    self.object = "chat.completion"
                    self.created = 0

            return FakeResponse(self._EXPECTED_THAI_RESPONSE)

        self.mock_deepseek_create.side_effect = fake_create

        mock_client = MagicMock()
        mock_client.chat.completions.create = self.mock_deepseek_create

        monkeypatch.setattr(
            "app.agents.policy_evaluator.openai.OpenAI",
            MagicMock(return_value=mock_client),
        )

    # ── Assertion 1: HTTP 200 ──

    @pytest.mark.asyncio
    async def test_http_status_200(self, test_client):
        """POST /api/chat returns HTTP 200 for a policy refund query."""
        resp = await test_client.post(
            "/api/chat",
            json={"message": "ขอคืนเงินใช้เวลากี่วัน", "session_id": "e2e-pol-1"},
        )
        assert resp.status_code == 200

    # ── Assertion 2: Intent is RETURN_REFUND ──

    @pytest.mark.asyncio
    async def test_intent_is_return_refund(self, test_client):
        """The router classifies a refund query as RETURN_REFUND."""
        resp = await test_client.post(
            "/api/chat",
            json={"message": "ขอคืนเงินใช้เวลากี่วัน", "session_id": "e2e-pol-2"},
        )
        data = resp.json()
        assert data["intent"] == "RETURN_REFUND"

    # ── Assertion 3: Agent is store_policy_evaluator ──

    @pytest.mark.asyncio
    async def test_agent_is_store_policy_evaluator(self, test_client):
        """The policy evaluator agent is selected for refund queries."""
        resp = await test_client.post(
            "/api/chat",
            json={"message": "ขอคืนเงินใช้เวลากี่วัน", "session_id": "e2e-pol-3"},
        )
        data = resp.json()
        assert data["agent"] == "store_policy_evaluator"

    # ── Assertion 4: Response source is deepseek ──

    @pytest.mark.asyncio
    async def test_response_source_is_deepseek(self, test_client):
        """When DeepSeek succeeds with validation, source reports deepseek."""
        resp = await test_client.post(
            "/api/chat",
            json={"message": "ขอคืนเงินใช้เวลากี่วัน", "session_id": "e2e-pol-4"},
        )
        data = resp.json()
        assert data["response_source"] == "deepseek"

    # ── Assertion 5: Thai response reaches API unchanged ──

    @pytest.mark.asyncio
    async def test_thai_response_preserved(self, test_client):
        """The mocked DeepSeek Thai response appears verbatim in API output."""
        resp = await test_client.post(
            "/api/chat",
            json={"message": "ขอคืนเงินใช้เวลากี่วัน", "session_id": "e2e-pol-5"},
        )
        data = resp.json()
        assert data["response"] == self._EXPECTED_THAI_RESPONSE

    # ── Assertion 6: grounding_validation_passed is true ──

    @pytest.mark.asyncio
    async def test_grounding_validation_passed(self, test_client):
        """A valid policy-grounded response passes grounding validation."""
        resp = await test_client.post(
            "/api/chat",
            json={"message": "ขอคืนเงินใช้เวลากี่วัน", "session_id": "e2e-pol-6"},
        )
        data = resp.json()
        assert data["grounding_validation_passed"] is True

    # ── Assertion 7: policy_sources contains refund_policy.md ──

    @pytest.mark.asyncio
    async def test_policy_sources_contains_refund_policy(self, test_client):
        """The retrieved policy source filename reaches the API."""
        resp = await test_client.post(
            "/api/chat",
            json={"message": "ขอคืนเงินใช้เวลากี่วัน", "session_id": "e2e-pol-7"},
        )
        data = resp.json()
        pe = data["policy_evidence"]
        assert "refund_policy.md" in pe["policy_sources"]

    # ── Assertion 8: retrieved_chunk_count is 1 ──

    @pytest.mark.asyncio
    async def test_retrieved_chunk_count_is_one(self, test_client):
        """Chunk count reflects the single retrieved clause."""
        resp = await test_client.post(
            "/api/chat",
            json={"message": "ขอคืนเงินใช้เวลากี่วัน", "session_id": "e2e-pol-8"},
        )
        data = resp.json()
        pe = data["policy_evidence"]
        assert pe["retrieved_chunk_count"] == 1

    # ── Assertion 9: retrieval_success is true ──

    @pytest.mark.asyncio
    async def test_retrieval_success_is_true(self, test_client):
        """Retrieval success flag reaches the API."""
        resp = await test_client.post(
            "/api/chat",
            json={"message": "ขอคืนเงินใช้เวลากี่วัน", "session_id": "e2e-pol-9"},
        )
        data = resp.json()
        pe = data["policy_evidence"]
        assert pe["retrieval_success"] is True

    # ── Assertion 10: requires_clarification is false ──

    @pytest.mark.asyncio
    async def test_requires_clarification_false(self, test_client):
        """Successful retrieval + generation does not require clarification."""
        resp = await test_client.post(
            "/api/chat",
            json={"message": "ขอคืนเงินใช้เวลากี่วัน", "session_id": "e2e-pol-10"},
        )
        data = resp.json()
        assert data["requires_clarification"] is False

    # ── Assertion 11: simulated_human_review is false ──

    @pytest.mark.asyncio
    async def test_simulated_human_review_false(self, test_client):
        """Successful pipeline does not trigger human review."""
        resp = await test_client.post(
            "/api/chat",
            json={"message": "ขอคืนเงินใช้เวลากี่วัน", "session_id": "e2e-pol-11"},
        )
        data = resp.json()
        assert data["simulated_human_review"] is False

    # ── Assertion 12: DeepSeek called exactly once ──

    @pytest.mark.asyncio
    async def test_deepseek_called_once(self, test_client):
        """The DeepSeek client is invoked exactly one time per request."""
        await test_client.post(
            "/api/chat",
            json={"message": "ขอคืนเงินใช้เวลากี่วัน", "session_id": "e2e-pol-12"},
        )
        assert self.mock_deepseek_create.call_count == 1

    # ── Assertion 13: Retrieval called exactly once ──

    @pytest.mark.asyncio
    async def test_retrieval_called_once(self, test_client):
        """The retrieval function is invoked exactly one time per request."""
        await test_client.post(
            "/api/chat",
            json={"message": "ขอคืนเงินใช้เวลากี่วัน", "session_id": "e2e-pol-13"},
        )
        assert self.mock_retrieval.call_count == 1

    # ── Assertion 14: No production ChromaDB accessed ──

    @pytest.mark.asyncio
    async def test_no_chromadb_access(self, test_client):
        """No real ChromaDB directory is accessed during the mocked flow.

        The mock of retrieve_policy_clauses() replaces the real
        implementation entirely, so ChromaDB/SentenceTransformer code
        never executes.
        """
        # Make the actual request (exercising the mocked pipeline)
        await test_client.post(
            "/api/chat",
            json={"message": "ขอคืนเงินใช้เวลากี่วัน", "session_id": "e2e-pol-14"},
        )
        # Verify the mock was used — no real ChromaDB code ran
        assert self.mock_retrieval.call_count == 1
        # Confirm the mock was not bypassed by checking self.mock_deepseek_create too
        assert self.mock_deepseek_create.call_count == 1
