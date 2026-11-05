"""Intent-relaxation + product-compare flexibility tests.

Deterministic-only: no LLM, no embeddings, no network. Order-ID extraction,
colloquial phrasing, and the PRODUCT_COMPARE demo extension are exercised at
the router, handler, and HTTP-integration levels.
"""

import os

# Deterministic-only: must be set BEFORE any app import reads .env.
os.environ["DEEPSEEK_ENABLED"] = "false"

import pytest
from fastapi.testclient import TestClient

from app.agents.router import route_message
from app.services.product_catalog import product_compare_lookup


# ── Order-ID extraction (relaxed matching) ──────────────────────────────

class TestRelaxedOrderId:
    @pytest.mark.parametrize("msg,expected", [
        ("1001", "ORD-1001"),
        ("#1001", "ORD-1001"),
        ("คำสั่งซื้อ 1001", "ORD-1001"),
        ("order 1001", "ORD-1001"),
        ("ORD 1001", "ORD-1001"),
        ("ord1002", "ORD-1002"),
    ])
    def test_relaxed_order_id_extraction(self, msg, expected):
        r = route_message(msg)
        assert r["extracted_order_id"] == expected

    @pytest.mark.parametrize("msg", [
        "I paid 1290 baht",
        "โอนเงิน 1290 บาท",
    ])
    def test_currency_amount_not_order_id(self, msg):
        r = route_message(msg)
        assert r["extracted_order_id"] is None


# ── Colloquial phrasing ─────────────────────────────────────────────────

class TestColloquialPhrasing:
    def test_where_is_my_order(self):
        r = route_message("where is my order")
        assert r["intent"] == "ORDER_STATUS"
        assert r["requires_clarification"] is True
        assert "order_id" in r["missing_entities"]

    def test_has_it_shipped(self):
        r = route_message("has it shipped")
        assert r["intent"] == "SHIPMENT_STATUS"


# ── PRODUCT_COMPARE routing ─────────────────────────────────────────────

class TestCompareRouting:
    def test_compare_routes_to_product_compare(self):
        r = route_message("compare with other headphones")
        assert r["intent"] == "PRODUCT_COMPARE"
        assert r["requires_clarification"] is False

    def test_compare_with_order_id_prefers_compare(self):
        r = route_message("compare headphones ORD-1001")
        assert r["intent"] == "PRODUCT_COMPARE"

    def test_return_policy_still_store_policy(self):
        r = route_message("What is SiamCart's return policy?")
        assert r["intent"] == "STORE_POLICY"

    def test_compare_return_policy_still_store_policy(self):
        r = route_message("compare return policy")
        assert r["intent"] == "STORE_POLICY"


# ── Compare handler (pure function, no DB) ──────────────────────────────

def _make_products():
    data = [
        ("PROD-001", "Linen Everyday Blouse", "Fashion", 690),
        ("PROD-002", "Classic Denim Jacket", "Fashion", 1290),
        ("PROD-003", "Summer Floral Midi Dress", "Fashion", 890),
        ("PROD-004", "Aura Wireless Headphones", "Electronics", 1290),
        ("PROD-005", "Pulse Smartwatch S2", "Electronics", 2490),
        ("PROD-006", "SiamPhone X5", "Electronics", 12990),
        ("PROD-007", "Velocity Running Sneakers", "Footwear", 1790),
        ("PROD-008", "CloudStep Casual Sneakers", "Footwear", 990),
        ("PROD-009", "Riviera Leather Sandals", "Footwear", 1190),
        ("PROD-010", "Metro Leather Handbag", "Bags & Accessories", 1590),
        ("PROD-011", "Nomad Travel Backpack", "Bags & Accessories", 1390),
        ("PROD-012", "Sunridge Aviator Sunglasses", "Bags & Accessories", 790),
    ]
    products = []
    for i, (pid, name, category, price) in enumerate(data):
        products.append({
            "product_id": pid,
            "name": name,
            "short_description": f"{name} demo description",
            "full_description": f"{name} full description",
            "category": category,
            "price": price,
            "original_price": None,
            "rating": 4.0 + (i % 10) / 10,
            "review_count": 10 + i,
            "stock_quantity": 5 + i,
            "features": [],
            "active": True,
        })
    return products


class TestCompareHandler:
    def test_multiple_candidates(self):
        result = product_compare_lookup("compare sneakers", "flex-s1", _make_products())
        assert result["intent"] == "PRODUCT_COMPARE"
        assert result["agent"] == "product_catalog_compare"
        assert result["evidence"]["comparison_type"] == "matched"
        ids = result["evidence"]["compare_product_ids"]
        assert "PROD-007" in ids and "PROD-008" in ids
        assert "PROD-" in result["response"]

    def test_single_candidate_expands_category(self):
        result = product_compare_lookup("compare headphones", "flex-s2", _make_products())
        assert result["evidence"]["comparison_type"] == "category_expanded"
        ids = result["evidence"]["compare_product_ids"]
        assert ids[0] == "PROD-004"
        assert "PROD-005" in ids and "PROD-006" in ids
        assert "Aura Wireless Headphones" in result["response"]

    def test_thai_keyword(self):
        result = product_compare_lookup("หูฟัง", "flex-s3", _make_products())
        assert result["evidence"]["comparison_type"] == "category_expanded"
        assert result["evidence"]["compare_product_ids"][0] == "PROD-004"

    def test_no_match(self):
        result = product_compare_lookup("compare xyzabc", "flex-s4", _make_products())
        assert result["evidence"]["comparison_type"] == "no_match"
        assert result["evidence"]["compare_product_ids"] == []
        assert "หมวดหมู่" in result["response"]


# ── HTTP integration ────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def client():
    from app.api.server import app
    with TestClient(app) as c:
        yield c


class TestCompareIntegration:
    def test_compare_chat(self, client):
        r = client.post("/api/chat", json={
            "message": "compare with other headphones", "session_id": "flex-1",
        })
        assert r.status_code == 200
        d = r.json()
        assert d["intent"] == "PRODUCT_COMPARE"
        assert "PROD-" in d["response"]
        assert "comparison_type" in d["evidence"]
