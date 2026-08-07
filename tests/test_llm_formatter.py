"""
Tests for the LLM Response Formatter — Phase 1B.

All tests use mocks. No real API calls are made.
"""

import json
import pytest
from unittest.mock import patch, Mock, MagicMock
from typing import Dict, Any, Optional

import app.agents.llm_client as formatter_module
from app.agents.llm_client import format_response, _evidence_matches

# ── Helpers ───────────────────────────────────────────────────────────

SAMPLE_EVIDENCE = {
    "order_id": "ORD-1001",
    "order_status": "shipped",
    "payment_status": "paid",
    "shipment_status": "in_transit",
    "tracking_number": "FLASH-TRK-1001",
    "shipping_provider": "Flash Express",
    "product_name": "เสื้อเชิ้ตผู้หญิง",
    "estimated_delivery_date": "2026-07-14",
}

DETERMINISTIC_RESPONSE = (
    "คำสั่งซื้อ ORD-1001 ขณะนี้มีสถานะเป็น 'shipped' "
    "สถานะการชำระเงิน: paid ค่ะ"
)


def _make_mock_chat(content: str):
    """Create a mock OpenAI chat completion response."""
    choice = MagicMock()
    choice.message.content = content
    choice.finish_reason = "stop"
    usage = MagicMock()
    usage.prompt_tokens = 50
    usage.completion_tokens = 30
    mock_response = MagicMock()
    mock_response.choices = [choice]
    mock_response.usage = usage
    return mock_response


class TestLLMFormatterConfig:
    """Tests for the configuration-level behaviour."""

    def test_disabled_returns_deterministic(self):
        """When DEEPSEEK_ENABLED is false, response is deterministic."""
        with patch.object(formatter_module, "DEEPSEEK_ENABLED", False):
            result = format_response(DETERMINISTIC_RESPONSE, SAMPLE_EVIDENCE)
        assert result["response_source"] == "deterministic"
        assert result["response"] == DETERMINISTIC_RESPONSE
        assert result["llm_fallback_used"] is False
        assert result["llm_error_type"] == "disabled"

    def test_no_api_key_returns_deterministic(self):
        """When API key is empty, response is deterministic."""
        config = dict(formatter_module.LLM_CONFIG)
        config["api_key"] = ""
        with patch.object(formatter_module, "DEEPSEEK_ENABLED", True):
            with patch.object(formatter_module, "LLM_CONFIG", config):
                result = format_response(DETERMINISTIC_RESPONSE, SAMPLE_EVIDENCE)
        assert result["response_source"] == "deterministic"
        assert result["response"] == DETERMINISTIC_RESPONSE
        assert result["llm_error_type"] == "no_key"


class TestLLMFormatterSuccess:
    """Tests for successful LLM formatting."""

    def test_successful_format_returns_deepseek_source(self):
        """A successful DeepSeek response returns response_source='deepseek'."""
        llm_text = (
            "คำสั่งซื้อ ORD-1001 ของคุณอยู่ระหว่างการจัดส่ง "
            "(สถานะ: shipped) ชำระเงิน: paid "
            "สถานะการจัดส่ง: in_transit "
            "หมายเลขพัสดุ: FLASH-TRK-1001 "
            "กำหนดรับ: 2026-07-14 ค่ะ"
        )
        config = dict(formatter_module.LLM_CONFIG)
        config["api_key"] = "sk-test-key"
        mock_response = _make_mock_chat(llm_text)
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = mock_response
        patches = [
            patch.object(formatter_module, "DEEPSEEK_ENABLED", True),
            patch.object(formatter_module, "LLM_CONFIG", config),
            patch.object(formatter_module, "OpenAI", return_value=mock_client),
        ]
        for p in patches:
            p.start()
        try:
            result = format_response(DETERMINISTIC_RESPONSE, SAMPLE_EVIDENCE)
        finally:
            for p in patches:
                p.stop()
        assert result["response_source"] == "deepseek"
        assert "ORD-1001" in result["response"]
        assert result["llm_fallback_used"] is False
        assert result["llm_error_type"] is None
        assert result["llm_latency_ms"] >= 0

    def test_clarification_preserved(self):
        """When the deterministic response is a clarification, it remains one."""
        clar_response = (
            "กรุณาระบุหมายเลขคำสั่งซื้อของคุณด้วยค่ะ "
            "(Please provide your order ID.)"
        )
        llm_text = "กรุณาระบุหมายเลขคำสั่งซื้อของคุณด้วยค่ะ"
        config = dict(formatter_module.LLM_CONFIG)
        config["api_key"] = "sk-test-key"
        mock_response = _make_mock_chat(llm_text)
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = mock_response
        patches = [
            patch.object(formatter_module, "DEEPSEEK_ENABLED", True),
            patch.object(formatter_module, "LLM_CONFIG", config),
            patch.object(formatter_module, "OpenAI", return_value=mock_client),
        ]
        for p in patches:
            p.start()
        try:
            result = format_response(clar_response, {})
        finally:
            for p in patches:
                p.stop()
        assert result["response_source"] == "deepseek"
        assert "กรุณาระบุ" in result["response"]


class TestLLMFormatterFallback:
    """Tests for graceful fallback when DeepSeek fails."""

    def _setup_fail(self, side_effect):
        config = dict(formatter_module.LLM_CONFIG)
        config["api_key"] = "sk-test-key"
        mock_client = MagicMock()
        mock_client.chat.completions.create.side_effect = side_effect
        patches = [
            patch.object(formatter_module, "DEEPSEEK_ENABLED", True),
            patch.object(formatter_module, "LLM_CONFIG", config),
            patch.object(formatter_module, "OpenAI", return_value=mock_client),
        ]
        for p in patches:
            p.start()
        try:
            return format_response(DETERMINISTIC_RESPONSE, SAMPLE_EVIDENCE)
        finally:
            for p in patches:
                p.stop()

    def test_timeout_returns_deterministic(self):
        """A timeout from the provider returns deterministic fallback."""
        from openai import APITimeoutError
        result = self._setup_fail(APITimeoutError("Request timed out"))
        assert result["response_source"] == "deterministic"
        assert result["response"] == DETERMINISTIC_RESPONSE
        assert result["llm_fallback_used"] is True
        assert result["llm_error_type"] == "timeout"

    def test_http_error_returns_deterministic(self):
        """A provider HTTP error returns deterministic fallback."""
        from openai import APIStatusError
        resp = MagicMock(status_code=401)
        result = self._setup_fail(
            APIStatusError("Unauthorized", response=resp, body={})
        )
        assert result["response_source"] == "deterministic"
        assert result["response"] == DETERMINISTIC_RESPONSE
        assert result["llm_fallback_used"] is True
        assert result["llm_error_type"] == "http_error"

    def test_empty_output_returns_deterministic(self):
        """An empty provider response returns deterministic fallback."""
        config = dict(formatter_module.LLM_CONFIG)
        config["api_key"] = "sk-test-key"
        mock_response = _make_mock_chat("")
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = mock_response
        patches = [
            patch.object(formatter_module, "DEEPSEEK_ENABLED", True),
            patch.object(formatter_module, "LLM_CONFIG", config),
            patch.object(formatter_module, "OpenAI", return_value=mock_client),
        ]
        for p in patches:
            p.start()
        try:
            result = format_response(DETERMINISTIC_RESPONSE, SAMPLE_EVIDENCE)
        finally:
            for p in patches:
                p.stop()
        assert result["response_source"] == "deterministic"
        assert result["response"] == DETERMINISTIC_RESPONSE
        assert result["llm_fallback_used"] is True
        assert result["llm_error_type"] == "empty_output"


class TestFactualValidation:
    """Tests for the factual validation layer."""

    def _call_with_mock(self, llm_text: str, evidence: Optional[Dict] = None):
        config = dict(formatter_module.LLM_CONFIG)
        config["api_key"] = "sk-test-key"
        mock_response = _make_mock_chat(llm_text)
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = mock_response
        patches = [
            patch.object(formatter_module, "DEEPSEEK_ENABLED", True),
            patch.object(formatter_module, "LLM_CONFIG", config),
            patch.object(formatter_module, "OpenAI", return_value=mock_client),
        ]
        for p in patches:
            p.start()
        try:
            return format_response(
                DETERMINISTIC_RESPONSE,
                evidence or SAMPLE_EVIDENCE,
            )
        finally:
            for p in patches:
                p.stop()

    def test_changed_order_id_rejected(self):
        """An LLM that changes the order ID is rejected."""
        result = self._call_with_mock(
            "คำสั่งซื้อ ORD-9999 อยู่ระหว่างจัดส่ง (shipped) "
            "ชำระเงิน: paid วันที่จัดส่ง: 2026-07-14"
        )
        assert result["response_source"] == "deterministic"
        assert result["response"] == DETERMINISTIC_RESPONSE
        assert result["llm_fallback_used"] is True
        assert result["llm_error_type"] == "validation"

    def test_changed_tracking_number_rejected(self):
        """An LLM that changes the tracking number is rejected."""
        result = self._call_with_mock(
            "คำสั่งซื้อ ORD-1001 อยู่ระหว่างจัดส่ง "
            "หมายเลขพัสดุ: FAKE-123 "
            "payment: paid shipment: in_transit วันที่: 2026-07-14"
        )
        assert result["response_source"] == "deterministic"
        assert result["llm_error_type"] == "validation"

    def test_changed_payment_status_rejected(self):
        """An LLM that changes the payment status is rejected."""
        result = self._call_with_mock(
            "คำสั่งซื้อ ORD-1001 shipped "
            "ยังไม่ได้ชำระเงิน "
            "FLASH-TRK-1001 2026-07-14"
        )
        assert result["response_source"] == "deterministic"
        assert result["llm_error_type"] == "validation"

    def test_empty_tracking_accepted(self):
        """Empty evidence fields are accepted (not validated)."""
        evidence_no_tracking = {**SAMPLE_EVIDENCE, "tracking_number": ""}
        result = self._call_with_mock(
            "คำสั่งซื้อ ORD-1001 ของคุณชำระเงินแล้ว "
            "shipped paid in_transit 2026-07-14",
            evidence_no_tracking,
        )
        assert result["response_source"] == "deepseek"


class TestEvidenceMatches:
    """Unit tests for the _evidence_matches helper."""

    @staticmethod
    def _make_evidence(**overrides):
        ev = dict(SAMPLE_EVIDENCE)
        ev.update(overrides)
        return ev

    def test_all_fields_present(self):
        """All critical evidence fields present in text."""
        ev = self._make_evidence()
        text = "ORD-1001 shipped paid in_transit FLASH-TRK-1001 2026-07-14"
        assert _evidence_matches(ev, text) is True

    def test_missing_field(self):
        """Missing a critical field returns False."""
        ev = self._make_evidence()
        text = "ORD-1001 shipped paid in_transit"
        assert _evidence_matches(ev, text) is False

    def test_empty_fields_skipped(self):
        """Empty string fields are skipped in validation."""
        ev = self._make_evidence(tracking_number="")
        text = "ORD-1001 shipped paid in_transit 2026-07-14"
        assert _evidence_matches(ev, text) is True
