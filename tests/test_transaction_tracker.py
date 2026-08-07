"""
Tests for Transaction Tracker Agent — Phase 1A.

All tests use a temporary in-memory or file-based SQLite database to
avoid contaminating the real data/orders.db.
"""

import os
import sys
import json
import sqlite3
import tempfile

import pytest

from app.db.orders import init_database, query_order, database_available, SAMPLE_ORDERS
# Phase 4A refactor: response generation moved to the orchestrator, so the
# full chat pipeline (route -> data fetch -> deterministic Thai response)
# is exercised through app.agents.orchestrator.process_message.
from app.agents.orchestrator import process_message, set_db_path


@pytest.fixture(scope="function")
def tmp_db():
    """Create a temporary orders database for test isolation."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    init_database(path)
    # Orchestrator reads its SQLite path from _DB_PATH — point it at the
    # temporary database so the real data/orders.db is never touched.
    set_db_path(path)
    yield path
    set_db_path(None)
    try:
        os.unlink(path)
    except OSError:
        pass


class TestTransactionTracker:
    """Focused tests for the Transaction Tracker agent."""

    def test_health_schema(self, tmp_db):
        """The database is initialised and queryable after init."""
        assert database_available(tmp_db) is True
        row = query_order(tmp_db, "ORD-1001")
        assert row is not None
        assert row["order_id"] == "ORD-1001"

    def test_valid_order_status(self, tmp_db):
        """A valid ORDER_STATUS query returns correct evidence."""
        result = process_message("คำสั่งซื้อ ORD-1001 ถึงไหนแล้ว", tmp_db)
        assert result["intent"] == "ORDER_STATUS"
        assert result["order_id"] == "ORD-1001"
        assert result["agent"] == "transaction_tracker"
        assert result["evidence"]["order_status"] == "shipped"
        assert result["requires_clarification"] is False
        assert "shipped" in result["response"] or "ส่ง" in result["response"]

    def test_payment_status(self, tmp_db):
        """A PAYMENT_STATUS query returns the correct payment status."""
        result = process_message("ออเดอร์ ORD-1002 ชำระเงินแล้วหรือยัง", tmp_db)
        assert result["intent"] == "PAYMENT_STATUS"
        assert result["order_id"] == "ORD-1002"
        assert result["evidence"]["payment_status"] == "paid"

    def test_tracking_number(self, tmp_db):
        """A TRACKING_NUMBER query returns the correct tracking number."""
        result = process_message("ขอหมายเลขพัสดุ ORD-1003", tmp_db)
        assert result["intent"] == "TRACKING_NUMBER"
        assert result["order_id"] == "ORD-1003"
        assert "JNT-TRK-1003" in result["response"]

    def test_shipment_status(self, tmp_db):
        """A SHIPMENT_STATUS query returns correct shipment info."""
        result = process_message("ตรวจสอบสถานะการจัดส่ง ORD-1004", tmp_db)
        assert result["intent"] == "SHIPMENT_STATUS"
        assert result["order_id"] == "ORD-1004"
        assert result["evidence"]["shipment_status"] == "not_shipped"

    def test_missing_order_id_returns_clarification(self, tmp_db):
        """When no order ID is provided, clarification is requested."""
        result = process_message("สอบถามสถานะคำสั่งซื้อ")
        # The router keeps the matched intent (ORDER_STATUS here) and flags
        # requires_clarification=True when order_id is missing.
        assert result["intent"] == "ORDER_STATUS"
        assert result["requires_clarification"] is True
        assert result["order_id"] is None
        assert "กรุณาระบุ" in result["response"]

    def test_unknown_order_id_returns_not_found(self, tmp_db):
        """An unknown order ID returns ORDER_NOT_FOUND with no invented info."""
        result = process_message("ORD-9999 ถึงไหนแล้ว", tmp_db)
        assert result["intent"] == "ORDER_NOT_FOUND"
        assert result["order_id"] == "ORD-9999"
        assert "ไม่พบข้อมูล" in result["response"]
        # Evidence should be empty for unknown orders
        assert result["evidence"] == {}

    def test_no_database_write(self, tmp_db):
        """The agent performs no write operations on the database."""
        # Capture the current state
        before = query_order(tmp_db, "ORD-1001")
        assert before is not None

        # Run a variety of queries
        for msg in [
            "ORD-1001 สถานะอะไร",
            "ORD-1002 จ่ายหรือยัง",
            "ORD-1003 เลขพัสดุ",
            "ORD-9999 ไม่มีอยู่",
        ]:
            process_message(msg, tmp_db)

        # Verify no data was changed
        after = query_order(tmp_db, "ORD-1001")
        assert after == before

    def test_latency_ms_present(self, tmp_db):
        """POST /api/chat response includes latency_ms."""
        result = process_message("ORD-1001", tmp_db)
        assert isinstance(result["latency_ms"], float)
        assert result["latency_ms"] >= 0

    def test_ord_without_dash_format(self, tmp_db):
        """Order IDs entered without dash (ORD1001) are still recognised."""
        result = process_message("ORD1001 ถึงไหน", tmp_db)
        assert result["order_id"] == "ORD-1001"
        assert result["intent"] in ("ORDER_STATUS",)
