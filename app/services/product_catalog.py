"""Product Catalog Lookup — deterministic SQLite-backed product Q&A.

Task 5D-1 storefront demo extension. This module is NOT part of the evaluated
three-agent experimental framework (transaction_tracker, store_policy_evaluator,
general_response). It answers factual product questions directly from the
current SQLite product record:

- never calls DeepSeek (or any LLM);
- never initializes ChromaDB or SentenceTransformer;
- returns immediately from local data (millisecond-level latency);
- never invents unsupported information — sizes, colors, materials, warranty
  and specifications absent from the product record are answered as not
  provided by the catalogue.

The caller (app/api/server.py) loads the product from SQLite by product_id and
passes the verified record here. Nothing sent by the browser is trusted.
"""

import re
import time
from typing import Any, Dict, Optional, Tuple

from app.agents.language_policy import (
    CUSTOMER_RESPONSE_LANGUAGE,
    detect_input_language,
)

# ── Research metadata (Task 5D-1 Objective 5) ────────────────────────────
HANDLER = "product_catalog_lookup"
RESPONSE_SOURCE = "product_catalog_deterministic"
DATABASE_SOURCE = "SQLite"
DEMO_EXTENSION = True
INTENT = "PRODUCT_LOOKUP"

# ── Product comparison (demo UX extension) ──────────────────────────────
COMPARE_INTENT = "PRODUCT_COMPARE"
COMPARE_HANDLER = "product_catalog_compare"

# Thai → English catalog-vocabulary tokens used to expand a Thai compare
# query into the English token space the catalogue is written in.
_THAI_CATEGORY_TOKENS: Dict[str, str] = {
    "หูฟัง": "headphones",
    "นาฬิกา": "watch",
    "สมาร์ทวอทช์": "smartwatch",
    "โทรศัพท์": "phone",
    "มือถือ": "phone",
    "รองเท้า": "sneakers",
    "รองเท้าแตะ": "sandals",
    "เสื้อ": "blouse",
    "เสื้อผ้า": "fashion",
    "กางเกงยีนส์": "denim",
    "แจ็คเก็ต": "jacket",
    "กระเป๋า": "bag",
    "แว่น": "sunglasses",
    "แว่นตา": "sunglasses",
}

# The three evaluated thesis agents — Product Catalog Lookup must never be
# labelled as one of them.
EVALUATED_AGENTS = ("transaction_tracker", "store_policy_evaluator", "general_response")


# ── Question classification ──────────────────────────────────────────────

_STOCK_KEYWORDS = [
    "in stock", "stock", "available", "availability", "sold out", "out of stock",
    "how many", "มีสต็อก", "มีของ", "ของเหลือ", "เหลืออยู่", "มีไหม", "มีมั้ย",
    "มีสินค้า",
]
_PRICE_KEYWORDS = [
    "price", "cost", "how much", "expensive", "cheap",
    "ราคา", "เท่าไหร่", "กี่บาท", "ค่าใช้จ่าย",
]
_DISCOUNT_KEYWORDS = [
    "discount", "discounted", "on sale", "sale", "ลด", "ส่วนลด",
    "โปรโมชั่น", "promotion", "โปร", "save", "deal",
]
_FEATURE_KEYWORDS = [
    "feature", "คุณสมบัติ", "จุดเด่น", "ฟีเจอร์", "highlight", "ไฮไลท์",
]
_CATEGORY_KEYWORDS = ["category", "หมวด", "ประเภท", "ชนิด"]
_RATING_KEYWORDS = ["rating", "rated", "star", "rate", "คะแนน", "เรตติ้ง", "ดาว"]
_REVIEW_KEYWORDS = ["review", "รีวิว", "ความคิดเห็น"]
_NAME_KEYWORDS = ["name", "called", "what is this product", "ตัวนี้ชื่อ"]
_ID_KEYWORDS = ["product id", "product code", "รหัสสินค้า", "code"]
_DESCRIPTION_KEYWORDS = [
    "describe", "description", "tell me about", "about this product",
    "about the", "what is this", "what's this", "what can you tell",
    "overview", "summary", "introduce", "รายละเอียด", "เกี่ยวกับ",
    "คืออะไร", "แนะนำ",
]

# Unsupported information: present only if the catalogue stores it. The demo
# products table has no size/color/material/warranty/specification columns, so
# these questions must be answered as "catalogue does not provide that info".
_UNSUPPORTED_PATTERNS: Tuple[Tuple[str, str], ...] = (
    (r"\bsizes?\b", "size"),
    (r"\bcolors?\b|\bcolours?\b", "color"),
    (r"\bmaterial\b", "material"),
    (r"\bfabric\b", "fabric"),
    (r"\bwarranty\b", "warranty"),
    (r"\bguarantee\w*\b", "guarantee"),
    (r"\bspecs?\b|specification", "specifications"),
    (r"\bdimensions?\b", "dimensions"),
    (r"\bbattery\b", "battery"),
    (r"\bwaterproof\b", "waterproof"),
    (r"\bcharging\b", "charging"),
)


def _has_any(text: str, keywords) -> bool:
    return any(k in text for k in keywords)


def _match_unsupported(text: str) -> Optional[str]:
    for pattern, label in _UNSUPPORTED_PATTERNS:
        if re.search(pattern, text):
            return label
    return None


def _classify(text: str) -> Tuple[Optional[str], Optional[str]]:
    """Return (question_type, unsupported_label). label is set only for
    'unsupported'; question_type is None for the general fallback."""
    if _has_any(text, _FEATURE_KEYWORDS):
        return "features", None
    label = _match_unsupported(text)
    if label:
        return "unsupported", label
    if _has_any(text, _STOCK_KEYWORDS):
        return "stock", None
    if _has_any(text, _PRICE_KEYWORDS):
        return "price", None
    if _has_any(text, _DISCOUNT_KEYWORDS):
        return "discount", None
    if _has_any(text, _CATEGORY_KEYWORDS):
        return "category", None
    if _has_any(text, _RATING_KEYWORDS):
        return "rating", None
    if _has_any(text, _REVIEW_KEYWORDS):
        return "reviews", None
    if _has_any(text, _NAME_KEYWORDS):
        return "name", None
    if _has_any(text, _ID_KEYWORDS) or re.search(r"\bid\b", text):
        return "product_id", None
    if _has_any(text, _DESCRIPTION_KEYWORDS):
        return "description", None
    return None, None


# ── Answer builder ───────────────────────────────────────────────────────
# Task 5D-7 Objective 3: Product Catalog Lookup stays deterministic and
# SQLite-backed, but every customer-facing answer is written in natural Thai.
# Product names, IDs, prices and numbers are preserved verbatim.

def _answer(product: Dict[str, Any], qtype: Optional[str], label: Optional[str]) -> str:
    pid = product["product_id"]
    name = product["name"]
    category = product["category"]
    price = float(product["price"])
    original = product.get("original_price")
    original = float(original) if original else None
    stock = int(product.get("stock_quantity") or 0)
    rating = product.get("rating")
    review_count = int(product.get("review_count") or 0)
    features = product.get("features") or []
    short = product.get("short_description") or ""

    if qtype == "stock":
        if stock <= 0:
            return f"ขออภัยค่ะ {name} ({pid}) สินค้าหมดสต็อกในขณะนี้ค่ะ"
        return (
            f"มีสินค้าค่ะ {name} ({pid}) มีอยู่ในสต็อก {stock} ชิ้น "
            f"พร้อมจัดส่งทันทีค่ะ"
        )
    if qtype == "price":
        return f"ราคาของ {name} ({pid}) อยู่ที่ {price:,.0f} บาทค่ะ"
    if qtype == "discount":
        if original and original > price:
            savings = round(original - price, 2)
            pct = round(savings / original * 100)
            return (
                f"มีโปรโมชั่นค่ะ {name} ({pid}) ลดราคาจาก {original:,.0f} บาท "
                f"เหลือ {price:,.0f} บาท (ประหยัด {savings:,.0f} บาท "
                f"คิดเป็นประมาณ {pct}%) ค่ะ"
            )
        return (
            f"ไม่มีส่วนลดค่ะ {name} ({pid}) จำหน่ายในราคาปกติ "
            f"{price:,.0f} บาทค่ะ"
        )
    if qtype == "features":
        if not features:
            return f"แคตตาล็อกสินค้าไม่ได้ระบุคุณสมบัติของ {name} ({pid}) ค่ะ"
        return f"คุณสมบัติเด่นของ {name} ({pid}): " + "; ".join(features) + " ค่ะ"
    if qtype == "unsupported":
        return (
            f"แคตตาล็อกสินค้าไม่ได้ให้ข้อมูลเกี่ยวกับ {label} "
            f"สำหรับ {name} ({pid}) ค่ะ"
        )
    if qtype == "category":
        return f"{name} ({pid}) อยู่ในหมวดหมู่ {category} ค่ะ"
    if qtype == "rating":
        return f"{name} ({pid}) ได้รับคะแนน {rating} จาก 5 ค่ะ"
    if qtype == "reviews":
        return f"{name} ({pid}) มีรีวิวจากลูกค้า {review_count} รายการค่ะ"
    if qtype == "name":
        return f"สินค้าชิ้นนี้คือ {name} ({pid}) ค่ะ"
    if qtype == "product_id":
        return f"รหัสสินค้าของ {name} คือ {pid} ค่ะ"
    if qtype == "description":
        base = (
            f"{name} ({pid}) — {short} สินค้าอยู่ในหมวดหมู่ {category} "
            f"ราคา {price:,.0f} บาท"
        )
        if original and original > price:
            base += f" (จากเดิม {original:,.0f} บาท)"
        base += (
            f" คะแนน {rating}/5 จากรีวิว {review_count} รายการ "
            f"และมีสินค้าในสต็อก {stock} ชิ้นค่ะ"
        )
        return base

    # General fallback — never invent unsupported information.
    return (
        f"ขออภัยค่ะ ไม่พบข้อมูลดังกล่าวในแคตตาล็อกสินค้าของ {name} ({pid}) ค่ะ "
        f"ฉันสามารถตอบคำถามเกี่ยวกับชื่อสินค้า รหัสสินค้า ราคา ส่วนลด หมวดหมู่ "
        f"สต็อก ความพร้อมจำหน่าย คำอธิบาย คุณสมบัติ คะแนน และจำนวนรีวิวได้ค่ะ"
    )


def _availability(product: Dict[str, Any]) -> str:
    return "in_stock" if int(product.get("stock_quantity") or 0) > 0 else "out_of_stock"


def classify_product_question(message: str) -> Optional[str]:
    """Return the catalog question type when the message looks like a factual
    product question, else None.

    The server uses this to decide whether an ambiguous message (e.g. one the
    existing router mislabels as GREETING because of substring keywords like
    "hi" inside "this") should be answered by Product Catalog Lookup while a
    product context is active. Pure string classification — no I/O.
    """
    text = (message or "").lower().strip()
    qtype, _label = _classify(text)
    return qtype


# ── Public handler ───────────────────────────────────────────────────────

def product_catalog_lookup(
    product: Dict[str, Any],
    message: str,
    session_id: str,
) -> Dict[str, Any]:
    """Answer a factual product question deterministically from the product record.

    Args:
        product: verified product dict freshly read from SQLite by the caller.
        message: the customer's question.
        session_id: chat session identifier.

    Returns:
        A result dict matching the POST /api/chat response schema, with
        response_source=product_catalog_deterministic and demo-extension
        research metadata. No LLM, no embeddings, no network.
    """
    start = time.perf_counter()
    text = (message or "").lower().strip()
    qtype, label = _classify(text)
    response_text = _answer(product, qtype, label)
    latency_ms = round((time.perf_counter() - start) * 1000, 2)

    original = product.get("original_price")
    original = float(original) if original else None
    price = float(product["price"])
    discount = round(original - price, 2) if original and original > price else None

    evidence = {
        "product_id": product["product_id"],
        "product_name": product["name"],
        "category": product["category"],
        "price": price,
        "original_price": original,
        "discount": discount,
        "stock_quantity": int(product.get("stock_quantity") or 0),
        "availability": _availability(product),
        "rating": product.get("rating"),
        "review_count": int(product.get("review_count") or 0),
        "question_type": qtype or "general",
        "demo_extension": DEMO_EXTENSION,
    }

    return {
        "session_id": session_id,
        "intent": INTENT,
        "agent": HANDLER,
        "order_id": None,
        "response": response_text,
        "evidence": evidence,
        "policy_evidence": {},
        "requires_clarification": False,
        "simulated_human_review": False,
        "latency_ms": latency_ms,
        "response_source": RESPONSE_SOURCE,
        "llm_enabled": False,
        "llm_fallback_used": False,
        "llm_latency_ms": 0.0,
        "llm_error_type": None,
        "routing_confidence": 1.0,
        "routing_reason": "Product Catalog Lookup (demo extension) answered from SQLite product data",
        "grounding_validation_passed": None,
        "product_id": product["product_id"],
        "database_source": DATABASE_SOURCE,
        "demo_extension": DEMO_EXTENSION,
        # Task 5D-7 Objective 7: fixed-Thai response metadata
        "response_language": CUSTOMER_RESPONSE_LANGUAGE,
        "input_language_detected": detect_input_language(message),
    }


# ── Product comparison handler (demo UX extension) ──────────────────────
# Deterministic token-overlap matching against the caller-supplied product
# list. No SQLite, no LLM, no embeddings. The server loads all active
# products once and passes them here, matching this module's "caller loads
# the data" style.

def _comparison_tokens(text: str) -> set:
    """Lowercased English tokens (len >= 3) with a naive plural→singular strip.

    Thai category words are expanded into their English catalogue equivalent
    first so a Thai compare query ("หูฟัง") matches the English catalogue.
    """
    normalized = (text or "").lower()
    for thai, english in _THAI_CATEGORY_TOKENS.items():
        if thai in normalized:
            normalized += f" {english}"
    tokens: set = set()
    for raw in re.findall(r"[a-z0-9]+", normalized):
        if len(raw) >= 3:
            tokens.add(raw)
            if raw.endswith("s") and len(raw) > 3:
                tokens.add(raw[:-1])
    return tokens


def _product_comparison_tokens(product: Dict[str, Any]) -> set:
    """Token set from a product's name + category + short description."""
    text = (
        f"{product.get('name') or ''} {product.get('category') or ''} "
        f"{product.get('short_description') or ''}"
    )
    return _comparison_tokens(text)


def _compare_discount(product: Dict[str, Any]) -> Optional[float]:
    original = product.get("original_price")
    original = float(original) if original else None
    price = float(product["price"])
    return round(original - price, 2) if original and original > price else None


def _compare_line(product: Dict[str, Any]) -> str:
    pid = product["product_id"]
    name = product["name"]
    price = float(product["price"])
    original = product.get("original_price")
    original = float(original) if original else None
    rating = product.get("rating")
    review_count = int(product.get("review_count") or 0)
    stock = int(product.get("stock_quantity") or 0)
    discount_part = ""
    if original and original > price:
        discount_part = f" (ลดจาก {original:,.0f} บาท)"
    return (
        f"• {name} ({pid}) — {price:,.0f} บาท{discount_part} — "
        f"คะแนน {rating}/5 ({review_count} รีวิว) — สต็อก {stock} ชิ้น"
    )


def product_compare_lookup(
    message: str,
    session_id: str,
    products: list,
    context_product: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Deterministically compare products matching the customer's request.

    Args:
        message: the customer's comparison request.
        session_id: chat session identifier.
        products: the active product list (loaded by the caller from SQLite).
        context_product: optional current product to bias toward (from the
            browser product context, always reloaded by the caller).

    Returns:
        A result dict matching the POST /api/chat response schema, keyed
        identically to product_catalog_lookup.
    """
    start = time.perf_counter()
    query_tokens = _comparison_tokens(message)

    scored = []
    for product in products:
        score = len(query_tokens & _product_comparison_tokens(product))
        if context_product and product["product_id"] == context_product["product_id"]:
            score += 1
        scored.append((product, score))

    candidates = [(p, s) for p, s in scored if s > 0]
    candidates.sort(key=lambda item: (-item[1], float(item[0]["price"])))
    selected = [p for p, _ in candidates[:4]]

    response = ""
    summary = ""
    lines: list = []
    cheapest = None
    top_rated = None

    if not selected:
        comparison_type = "no_match"
        categories = sorted({p["category"] for p in products if p.get("category")})
        cat_text = ", ".join(categories) if categories else "สินค้าทั่วไป"
        response = (
            f"ขออภัยค่ะ ฉันไม่พบสินค้าที่ตรงกับสิ่งที่คุณต้องการเปรียบเทียบค่ะ "
            f"กรุณาลองระบุหมวดหมู่สินค้า เช่น {cat_text} ค่ะ"
        )
    else:
        if len(selected) == 1:
            comparison_type = "category_expanded"
            single = selected[0]
            same_category = [
                p for p in products
                if p.get("category") == single.get("category")
                and p["product_id"] != single["product_id"]
            ]
            same_category.sort(key=lambda p: float(p["price"]))
            selected = [single] + same_category[:3]
            note = (
                f"ในแคตตาล็อกมีสินค้าประเภทนี้เพียงรุ่นเดียว ({single['name']}) "
                f"ฉันจึงแสดงสินค้าอื่นในหมวดเดียวกันให้เปรียบเทียบเพิ่มเติมค่ะ"
            )
            lines = [note] + [_compare_line(p) for p in selected]
        else:
            comparison_type = "matched"
            lines = [_compare_line(p) for p in selected]

        cheapest = min(selected, key=lambda p: float(p["price"]))
        top_rated = max(
            selected,
            key=lambda p: (float(p["rating"]) if p.get("rating") is not None else 0.0),
        )
        if cheapest["product_id"] == top_rated["product_id"]:
            summary = (
                f"สินค้าราคาต่ำสุดและได้คะแนนสูงสุดคือ {cheapest['name']} "
                f"({cheapest['product_id']}) ค่ะ"
            )
        else:
            summary = (
                f"สินค้าราคาต่ำสุดคือ {cheapest['name']} ({cheapest['product_id']}) "
                f"ส่วนสินค้าที่ได้คะแนนสูงสุดคือ {top_rated['name']} "
                f"({top_rated['product_id']}) ค่ะ"
            )
        response = "\n".join(lines) + "\n\n" + summary

    latency_ms = round((time.perf_counter() - start) * 1000, 2)

    evidence = {
        "comparison_type": comparison_type,
        "compare_product_ids": [p["product_id"] for p in selected],
        "compare_products": [
            {
                "id": p["product_id"],
                "name": p["name"],
                "category": p.get("category"),
                "price": float(p["price"]),
                "original_price": (
                    float(p["original_price"]) if p.get("original_price") else None
                ),
                "discount": _compare_discount(p),
                "rating": p.get("rating"),
                "review_count": int(p.get("review_count") or 0),
                "stock_quantity": int(p.get("stock_quantity") or 0),
                "availability": _availability(p),
            }
            for p in selected
        ],
        "cheapest_product_id": cheapest["product_id"] if cheapest else None,
        "top_rated_product_id": top_rated["product_id"] if top_rated else None,
        "demo_extension": DEMO_EXTENSION,
    }

    return {
        "session_id": session_id,
        "intent": COMPARE_INTENT,
        "agent": COMPARE_HANDLER,
        "order_id": None,
        "response": response,
        "evidence": evidence,
        "policy_evidence": {},
        "requires_clarification": False,
        "simulated_human_review": False,
        "latency_ms": latency_ms,
        "response_source": RESPONSE_SOURCE,
        "llm_enabled": False,
        "llm_fallback_used": False,
        "llm_latency_ms": 0.0,
        "llm_error_type": None,
        "routing_confidence": 1.0,
        "routing_reason": (
            "Product comparison (demo extension) answered deterministically "
            "from SQLite product data"
        ),
        "grounding_validation_passed": None,
        "product_id": None,
        "database_source": DATABASE_SOURCE,
        "demo_extension": DEMO_EXTENSION,
        "response_language": CUSTOMER_RESPONSE_LANGUAGE,
        "input_language_detected": detect_input_language(message),
    }
