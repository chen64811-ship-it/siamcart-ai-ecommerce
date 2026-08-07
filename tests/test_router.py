"""
Tests for the Intelligent Router — Phase 2A.

All tests are deterministic, offline, and require no LLM or internet access.
"""

import pytest
from app.agents.router import route_message, _extract_order_id, _classify_intent


class TestRouterTransactionIntents:
    """Transaction intents route to transaction_tracker."""

    def test_thai_order_status(self):
        """Thai order-status inquiry routes to transaction_tracker."""
        r = route_message("คำสั่งซื้อ ORD-1001 ถึงไหนแล้ว")
        assert r["intent"] == "ORDER_STATUS"
        assert r["target_agent"] == "transaction_tracker"
        assert r["extracted_order_id"] == "ORD-1001"
        assert r["requires_clarification"] is False

    def test_thai_payment_inquiry(self):
        """Thai payment inquiry routes to transaction_tracker."""
        r = route_message("ORD-1002 ชำระเงินแล้วหรือยัง")
        assert r["intent"] == "PAYMENT_STATUS"
        assert r["target_agent"] == "transaction_tracker"

    def test_thai_shipment_inquiry(self):
        """Thai shipment inquiry routes to transaction_tracker."""
        r = route_message("ตรวจสอบสถานะการจัดส่ง ORD-1004")
        assert r["intent"] == "SHIPMENT_STATUS"
        assert r["target_agent"] == "transaction_tracker"

    def test_thai_tracking_inquiry(self):
        """Thai tracking-number inquiry routes to transaction_tracker."""
        r = route_message("ขอหมายเลขพัสดุ ORD-1003")
        assert r["intent"] == "TRACKING_NUMBER"
        assert r["target_agent"] == "transaction_tracker"

    def test_english_order_status(self):
        """English order-status variant routes correctly."""
        r = route_message("What is the status of ORD-1001")
        assert r["intent"] == "ORDER_STATUS"

    def test_english_payment_status(self):
        """English payment-status variant routes correctly."""
        r = route_message("payment status for ORD-1002")
        assert r["intent"] == "PAYMENT_STATUS"


class TestRouterPriority:
    """Tracking intent has priority over generic order intent."""

    def test_tracking_priority_over_order(self):
        """A message with tracking keywords is TRACKING_NUMBER, not ORDER_STATUS."""
        r = route_message("ขอหมายเลขพัสดุ ORD-1003")
        assert r["intent"] == "TRACKING_NUMBER"

    def test_payment_priority_over_order(self):
        """A message with payment keywords is PAYMENT_STATUS, not ORDER_STATUS."""
        r = route_message("ORD-1001 จ่ายหรือยัง")
        assert r["intent"] == "PAYMENT_STATUS"

    def test_order_id_alone_does_not_determine_intent(self):
        """An order ID without keywords still routes to ORDER_STATUS via fallback."""
        r = route_message("ORD-1001")
        assert r["intent"] == "ORDER_STATUS"
        assert r["confidence"] == 1.0

    def test_bare_numeric_without_ord_format_is_unknown(self):
        """A numeric-only message without ORD prefix is UNKNOWN."""
        r = route_message("1001")
        assert r["intent"] == "UNKNOWN"


class TestRouterClarification:
    """Missing order ID produces clarification without querying SQLite."""

    def test_missing_order_id_clarification(self):
        """ORDER_STATUS without order ID returns clarification."""
        r = route_message("สอบถามสถานะคำสั่งซื้อ")
        assert r["intent"] == "ORDER_STATUS"
        assert r["requires_clarification"] is True
        assert "order_id" in r["missing_entities"]
        # Should still identify intent as ORDER_STATUS, not fall back
        assert r["target_agent"] == "transaction_tracker"

    def test_missing_order_id_payment(self):
        """PAYMENT_STATUS without order ID returns clarification."""
        r = route_message("payment status")
        assert r["intent"] == "PAYMENT_STATUS"
        assert r["requires_clarification"] is True

    def test_missing_order_id_shipment(self):
        """SHIPMENT_STATUS without order ID returns clarification."""
        r = route_message("shipment status")
        assert r["intent"] == "SHIPMENT_STATUS"
        assert r["requires_clarification"] is True

    def test_missing_order_id_tracking(self):
        """TRACKING_NUMBER without order ID returns clarification."""
        r = route_message("tracking number")
        assert r["intent"] == "TRACKING_NUMBER"
        assert r["requires_clarification"] is True


class TestRouterPolicyIntents:
    """Policy intents route to policy_evaluator_pending."""

    def test_return_refund_routes_to_pending(self):
        """RETURN_REFUND routes to store_policy_evaluator."""
        r = route_message("ขอคืนสินค้า")
        assert r["intent"] == "RETURN_REFUND"
        assert r["target_agent"] == "store_policy_evaluator"
        assert r["requires_clarification"] is False

    def test_store_policy_routes_to_pending(self):
        """STORE_POLICY routes to store_policy_evaluator."""
        r = route_message("อยากทราบนโยบายของร้าน")
        assert r["intent"] == "STORE_POLICY"
        assert r["target_agent"] == "store_policy_evaluator"
        assert r["requires_clarification"] is False


class TestRouterGreeting:
    """GREETING routes to general_response."""

    def test_thai_greeting(self):
        """Thai greeting routes to general_response."""
        r = route_message("สวัสดี")
        assert r["intent"] == "GREETING"
        assert r["target_agent"] == "general_response"

    def test_english_greeting(self):
        """English greeting routes to general_response."""
        r = route_message("Hello")
        assert r["intent"] == "GREETING"
        assert r["target_agent"] == "general_response"


class TestRouterOutOfScope:
    """Unsupported operations route to simulated_human_review."""

    def test_cancel_order(self):
        """Cancel order routes to simulated_human_review."""
        r = route_message("ยกเลิกคำสั่งซื้อ ORD-1001")
        assert r["intent"] == "OUT_OF_SCOPE"
        assert r["target_agent"] == "simulated_human_review"
        assert r["simulated_human_review"] is True

    def test_change_address(self):
        """Change address routes to simulated_human_review."""
        r = route_message("change address")
        assert r["intent"] == "OUT_OF_SCOPE"
        assert r["simulated_human_review"] is True


class TestRouterUnknown:
    """Unknown text produces controlled UNKNOWN classification."""

    def test_gibberish_is_unknown(self):
        """Gibberish text returns UNKNOWN with clarification."""
        r = route_message("asdfghjkl")
        assert r["intent"] == "UNKNOWN"
        assert r["requires_clarification"] is True

    def test_empty_message(self):
        """Empty message returns UNKNOWN."""
        r = route_message("")
        assert r["intent"] == "UNKNOWN"

    def test_whitespace_only(self):
        """Whitespace-only message returns UNKNOWN."""
        r = route_message("   ")
        assert r["intent"] == "UNKNOWN"


class TestRouterStructure:
    """Router returns a valid structured RouteDecision."""

    REQUIRED_KEYS = {
        "intent", "target_agent", "confidence", "required_entities",
        "missing_entities", "extracted_order_id", "requires_clarification",
        "simulated_human_review", "routing_reason",
    }

    @pytest.mark.parametrize("msg", [
        "ORD-1001",
        "สวัสดี",
        "ขอคืนสินค้า",
        "cancel order",
        "gibberish",
    ])
    def test_route_decision_has_all_keys(self, msg):
        """Every route decision contains all required keys."""
        r = route_message(msg)
        assert self.REQUIRED_KEYS.issubset(r.keys()), f"Missing keys in {r.keys()}"
        assert isinstance(r["confidence"], float)
        assert isinstance(r["requires_clarification"], bool)
        assert isinstance(r["simulated_human_review"], bool)
        assert isinstance(r["routing_reason"], str)
