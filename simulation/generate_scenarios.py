"""
120 Synthetic Thai E-Commerce Customer Support Scenarios
For controlled simulation experiments comparing multi-agent vs monolithic baseline.

Scenario categories:
- Order status inquiries (45 cases)
- Return and refund requests (40 cases)
- Shipping delay complaints (20 cases)
- Ambiguous or out-of-scope queries (15 cases)

Each scenario: initial message, expected resolution path, ground-truth criteria.
"""
import json
import os
from typing import Dict, List
from app.config import SCENARIOS_DIR, INTENT_CATEGORIES


def generate_scenarios() -> List[Dict]:
    """Generate 120 synthetic test scenarios."""
    scenarios = []

    # ============ ORDER STATUS INQUIRIES (45 cases) ============
    order_scenarios = [
        # Order tracking with valid IDs
        {"category": "order_status", "message": "สวัสดีครับ ผมสั่งซื้อ ORD1001 ไป สถานะตอนนี้เป็นยังไงบ้าง", "expected_intent": "order_status", "expected_order_id": "ORD1001", "description": "Check order ORD1001 status"},
        {"category": "order_status", "message": "อยากทราบสถานะคำสั่งซื้อ ORD1002 ค่ะ สั่งไปวันที่ 10", "expected_intent": "order_status", "expected_order_id": "ORD1002", "description": "Check order ORD1002 status"},
        {"category": "order_status", "message": "ORD1003 ส่งถึงยังคะ", "expected_intent": "order_status", "expected_order_id": "ORD1003", "description": "Check if ORD1003 delivered"},
        {"category": "order_status", "message": "Where is my order ORD1001? It's been 3 days.", "expected_intent": "order_status", "expected_order_id": "ORD1001"},
        {"category": "order_status", "message": "เช็ค tracking ให้หน่อยครับ TRK1001", "expected_intent": "order_status", "expected_order_id": "TRK1001"},
        {"category": "order_status", "message": "ได้ส่งของ ORD1004 หรือยังคะ สั่งไว้หลายวันแล้ว", "expected_intent": "order_status", "expected_order_id": "ORD1004"},
        {"category": "order_status", "message": "ORD1002 จัดส่งถึงไหนแล้วครับ", "expected_intent": "order_status", "expected_order_id": "ORD1002"},
        {"category": "order_status", "message": "Check order ORD1005 please I want to know status", "expected_intent": "order_status", "expected_order_id": "ORD1005"},
        {"category": "order_status", "message": "เลข tracking TRK1002 ใช้ได้ไหมครับ", "expected_intent": "order_status", "expected_order_id": "TRK1002"},
        {"category": "order_status", "message": "อยากรู้ว่าสินค้า ORD1003 ส่งวันไหนคะ", "expected_intent": "order_status", "expected_order_id": "ORD1003"},

        # Order tracking without IDs (need clarification)
        {"category": "order_status", "message": "สั่งของไป อยากรู้ว่าส่งรึยัง", "expected_intent": "order_status", "expected_order_id": None, "needs_clarification": True},
        {"category": "order_status", "message": "Check my order please", "expected_intent": "order_status", "expected_order_id": None, "needs_clarification": True},
        {"category": "order_status", "message": "สินค้าที่สั่งไว้ถึงรึยังครับ", "expected_intent": "order_status", "expected_order_id": None, "needs_clarification": True},
        {"category": "order_status", "message": "Where is my package?", "expected_intent": "order_status", "expected_order_id": None, "needs_clarification": True},
        {"category": "order_status", "message": "อยากเช็คของหน่อยครับ ไม่รู้เลขออเดอร์", "expected_intent": "order_status", "expected_order_id": None, "needs_clarification": True},

        # Tracking with mixed Thai-English
        {"category": "order_status", "message": "Hi ka want to check order สั่งไว้ ORD1004 จ่ายไปแล้ว", "expected_intent": "order_status", "expected_order_id": "ORD1004"},
        {"category": "order_status", "message": "ORD1001 shipped yet?", "expected_intent": "order_status", "expected_order_id": "ORD1001"},
        {"category": "order_status", "message": "Tracking number TRK1005 ใช้ tracking ได้ไหมครับ", "expected_intent": "order_status", "expected_order_id": "TRK1005"},

        # Multiple orders
        {"category": "order_status", "message": "ผมมีสองออเดอร์ ORD1001 กับ ORD1002 อยากรู้สถานะทั้งสอง", "expected_intent": "order_status", "expected_order_id": None},
        {"category": "order_status", "message": "Check ORD1001 and ORD1002 for me", "expected_intent": "order_status", "expected_order_id": None},

        # Payment status
        {"category": "order_status", "message": "ORD1004 จ่ายเงินแล้วยังครับ สั่งไปตั้งแต่วันที่ 10", "expected_intent": "order_status", "expected_order_id": "ORD1004"},
        {"category": "order_status", "message": "ORD1002 จ่ายเรียบร้อยมั้ยคะ", "expected_intent": "order_status", "expected_order_id": "ORD1002"},
        {"category": "order_status", "message": "Did I pay for ORD1004 already?", "expected_intent": "order_status", "expected_order_id": "ORD1004"},

        # Deliver date inquiries
        {"category": "order_status", "message": "ORD1001 จะถึงวันไหนครับ", "expected_intent": "order_status", "expected_order_id": "ORD1001"},
        {"category": "order_status", "message": "ORD1003 ส่งถึงวันที่เท่าไหร่คะ", "expected_intent": "order_status", "expected_order_id": "ORD1003"},
        {"category": "order_status", "message": "เมื่อไหร่ของจะมาถึง ORD1002", "expected_intent": "order_status", "expected_order_id": "ORD1002"},
    ]
    scenarios.extend(order_scenarios)

    # Fill to 45 order scenarios with variations
    order_ids = ["ORD1001", "ORD1002", "ORD1003", "ORD1004", "ORD1005"]
    for i in range(len(order_scenarios), 45):
        oid = order_ids[i % len(order_ids)]
        variations = [
            {"message": f"สถานะ {oid} ล่าสุดครับ", "expected_order_id": oid},
            {"message": f"{oid} delivery date please", "expected_order_id": oid},
            {"message": f"อยากถามเรื่อง {oid} ค่ะ ส่งรึยัง", "expected_order_id": oid},
            {"message": f"Tracking {oid} please", "expected_order_id": oid},
            {"message": f"Check status of {oid}", "expected_order_id": oid},
        ]
        v = variations[i % len(variations)]
        scenarios.append({
            "category": "order_status",
            "message": v["message"],
            "expected_intent": "order_status",
            "expected_order_id": v["expected_order_id"],
        })

    # ============ RETURN & REFUND REQUESTS (40 cases) ============
    return_scenarios = [
        {"category": "return_refund", "message": "ต้องการคืนสินค้าค่ะ สั่ง ORD1005 มาแต่ไซต์ไม่พอดี", "expected_intent": "return_refund", "expected_order_id": "ORD1005"},
        {"category": "return_refund", "message": "ขอคืนเงิน ORD1005 ได้มั้ยคะ", "expected_intent": "return_refund", "expected_order_id": "ORD1005"},
        {"category": "return_refund", "message": "I want to return my order ORD1005 size doesn't fit", "expected_intent": "return_refund", "expected_order_id": "ORD1005"},
        {"category": "return_refund", "message": "คืนรองเท้าที่ซื้อไปได้ไหมครับ ใส่ไม่ได้", "expected_intent": "return_refund", "expected_order_id": None},
        {"category": "return_refund", "message": "สินค้ามีตำหนิ อยากได้เงินคืน", "expected_intent": "return_refund", "expected_order_id": None},
        {"category": "return_refund", "message": "Refund please item is defective", "expected_intent": "return_refund", "expected_order_id": None},
        {"category": "return_refund", "message": "เปลี่ยนสินค้าได้มั้ยคะ สั่งผิดสี", "expected_intent": "return_refund", "expected_order_id": None},
        {"category": "return_refund", "message": "ขอเปลี่ยน ORD1001 เป็นไซต์ใหญ่กว่าได้ไหม", "expected_intent": "return_refund", "expected_order_id": "ORD1001"},
        {"category": "return_refund", "message": "คืนเงินใช้เวลากี่วันครับ", "expected_intent": "return_refund", "expected_order_id": None},
        {"category": "return_refund", "message": "How long does refund take?", "expected_intent": "return_refund", "expected_order_id": None},
        {"category": "return_refund", "message": "สินค้าเสีย ใช้ไป 2 วันพังเลย ขอเปลี่ยน", "expected_intent": "return_refund", "expected_order_id": None},
        {"category": "return_refund", "message": "Return policy for electronics? My headphones broke", "expected_intent": "return_refund", "expected_order_id": None},
        {"category": "return_refund", "message": "ส่งคืน ORD1003 ยังไงครับ", "expected_intent": "return_refund", "expected_order_id": "ORD1003"},
        {"category": "return_refund", "message": "คืนเสื้อที่ซื้อ ORD1001 ได้มั้ยคะ ยังไม่ได้ใส่", "expected_intent": "return_refund", "expected_order_id": "ORD1001"},
        {"category": "return_refund", "message": "Can I return my order ORD1001? Unopened", "expected_intent": "return_refund", "expected_order_id": "ORD1001"},
        {"category": "return_refund", "message": "กางเกงที่ซื้อมาไม่พอดี เปลี่ยนได้ไหม", "expected_intent": "return_refund", "expected_order_id": None},
        {"category": "return_refund", "message": "Refund ต้องแจ้งภายในกี่วัน", "expected_intent": "return_refund", "expected_order_id": None},
        {"category": "return_refund", "message": "คืนเงิน ORD1005 ได้ยังครับ ส่งคืนไปแล้ว", "expected_intent": "return_refund", "expected_order_id": "ORD1005"},
        {"category": "return_refund", "message": "สินค้าที่ได้รับไม่ตรงกับที่สั่ง ORD1002 ค่ะ", "expected_intent": "return_refund", "expected_order_id": "ORD1002"},
        {"category": "return_refund", "message": "Wrong item received for ORD1002 need exchange", "expected_intent": "return_refund", "expected_order_id": "ORD1002"},
    ]
    scenarios.extend(return_scenarios)

    # Fill to 40
    return_topics = [
        "คืนสินค้าแฟชั่นได้กี่วันคะ",
        "Refund policy for defective items?",
        "เปลี่ยนของที่สั่ง ORD1004 ได้มั้ย",
        "ค่าส่งคืนใครออกครับ",
        "คืนเงินเต็มจำนวนรวมค่าส่งมั้ย",
        "Exchange window how many days?",
        "สินค้าลดราคาสามารถคืนได้มั้ย",
        "คืนกระเป๋าที่เพิ่งซื้อ ORD1001 ค่ะ",
        "Can I get a refund for ORD1005?",
        "Return without original box?",
        "換貨可以嗎？尺寸不對",
        "Refund to card or bank account?",
        "ของเสีย ถ่ายรูปให้ดูแล้ว ขอคืนเงิน",
        "คืนสินค้าอิเล็กทรอนิกส์ 7 วันจริงมั้ย",
        "Can I exchange for different color?",
        "Order ORD1003 received damaged",
        "เปลี่ยนไซต์ ORD1001 ค่ะ ใหญ่ไป",
        "มีตำหนิขอเปลี่ยนด่วน",
        "Return label ต้องขอจากร้านมั้ย",
        "คืนสินค้า ORD1005 สภาพดี แกะกล่องแล้ว",
    ]
    for i, msg in enumerate(return_topics):
        scenarios.append({
            "category": "return_refund",
            "message": msg,
            "expected_intent": "return_refund",
            "expected_order_id": None,
        })

    # ============ SHIPPING DELAY COMPLAINTS (20 cases) ============
    shipping_scenarios = [
        {"category": "shipping_delay", "message": "ORD1001 ส่งช้ามากครับ สั่งมา 7 วันแล้ว", "expected_intent": "shipping_delay", "expected_order_id": "ORD1001"},
        {"category": "shipping_delay", "message": "ของไม่มาสักที ORD1002 ส่งวันที่ 10 ถึงวันนี้ยังไม่ถึง", "expected_intent": "shipping_delay", "expected_order_id": "ORD1002"},
        {"category": "shipping_delay", "message": "Shipping delay ORD1004 is late", "expected_intent": "shipping_delay", "expected_order_id": "ORD1004"},
        {"category": "shipping_delay", "message": "ส่งของช้ามากค่ะ รอมา 5 วันแล้ว", "expected_intent": "shipping_delay", "expected_order_id": None},
        {"category": "shipping_delay", "message": "Package is very late where is it?", "expected_intent": "shipping_delay", "expected_order_id": None},
        {"category": "shipping_delay", "message": "ORD1001 ถึงไหนแล้วครับ ปกติไม่น่านานขนาดนี้", "expected_intent": "shipping_delay", "expected_order_id": "ORD1001"},
        {"category": "shipping_delay", "message": "Tracking ไม่ update หลายวันแล้ว ORD1002", "expected_intent": "shipping_delay", "expected_order_id": "ORD1002"},
        {"category": "shipping_delay", "message": "ของหายมั้ยครับ ส่งมา 10 วันแล้ว", "expected_intent": "shipping_delay", "expected_order_id": None},
        {"category": "shipping_delay", "message": "ส่งช้ามาก มีค่าชดเชยไหมครับ", "expected_intent": "shipping_delay", "expected_order_id": None},
        {"category": "shipping_delay", "message": "Delay compensation policy?", "expected_intent": "shipping_delay", "expected_order_id": None},
        {"category": "shipping_delay", "message": "Flash Express ช้ามาก ORD1001 ขึ้นรถส่งตั้งแต่วันที่ 8", "expected_intent": "shipping_delay", "expected_order_id": "ORD1001"},
        {"category": "shipping_delay", "message": "Kerry ส่งของช้าครับ ORD1002 ยังไม่มา", "expected_intent": "shipping_delay", "expected_order_id": "ORD1002"},
        {"category": "shipping_delay", "message": "J&T ORD1003 บอกส่งแล้วแต่ไม่เห็นของ", "expected_intent": "shipping_delay", "expected_order_id": "ORD1003"},
        {"category": "shipping_delay", "message": "When will ORD1002 arrive? It's very late", "expected_intent": "shipping_delay", "expected_order_id": "ORD1002"},
        {"category": "shipping_delay", "message": "ของถึงกรุงเทพวันนี้มั้ยครับ ORD1001", "expected_intent": "shipping_delay", "expected_order_id": "ORD1001"},
    ]
    scenarios.extend(shipping_scenarios)

    # Fill to 20
    shipping_more = [
        "J&T Express tracking TRK1003 not updating",
        "ยังไม่ได้รับของ ORD1002 เลย กลัวของหาย",
        "ส่งช้าทุกทีครับ ทำไมส่งช้าครับ",
        "ORD1005 return status? ส่งคืนไปแล้ว",
        "Delay 7 business days compensation?",
    ]
    for msg in shipping_more:
        scenarios.append({"category": "shipping_delay", "message": msg, "expected_intent": "shipping_delay", "expected_order_id": None})

    # ============ AMBIGUOUS / OUT-OF-SCOPE (15 cases) ============
    oos_scenarios = [
        {"category": "out_of_scope", "message": "ขายส่งได้มั้ยครับ ต้องการสั่งจำนวนมาก", "expected_intent": "out_of_scope", "description": "Bulk wholesale query"},
        {"category": "out_of_scope", "message": "I want to apply for a job at your company", "expected_intent": "out_of_scope"},
        {"category": "out_of_scope", "message": "ร้านเปิดกี่โมงถึงกี่โมง", "expected_intent": "out_of_scope"},
        {"category": "out_of_scope", "message": "Can you help me with my math homework?", "expected_intent": "out_of_scope"},
        {"category": "out_of_scope", "message": "สวัสดี", "expected_intent": "clarification", "description": "Greeting only, needs routing"},
        {"category": "out_of_scope", "message": "Hello", "expected_intent": "clarification"},
        {"category": "out_of_scope", "message": "มีโปรโมชั่นอะไรบ้าง", "expected_intent": "store_policy", "description": "Promotion query → policy"},
        {"category": "out_of_scope", "message": "What's your return address?", "expected_intent": "store_policy"},
        {"category": "out_of_scope", "message": "ขอส่วนลดหน่อยครับ", "expected_intent": "out_of_scope"},
        {"category": "out_of_scope", "message": "Are you a real person or AI?", "expected_intent": "clarification"},
        {"category": "out_of_scope", "message": "ขอยืมเงินหน่อยครับ", "expected_intent": "out_of_scope"},
        {"category": "out_of_scope", "message": "Complain about competitor's product", "expected_intent": "out_of_scope"},
        {"category": "out_of_scope", "message": "Help me hack an account", "expected_intent": "out_of_scope"},
        {"category": "out_of_scope", "message": "Can I speak to a human?", "expected_intent": "clarification", "needs_clarification": True},
        {"category": "out_of_scope", "message": ".", "expected_intent": "clarification", "needs_clarification": True},
    ]
    scenarios.extend(oos_scenarios)

    # Add IDs and metadata
    for i, s in enumerate(scenarios):
        s["id"] = f"SCENARIO_{i+1:03d}"
        if "needs_clarification" not in s:
            s["needs_clarification"] = False
        if "description" not in s:
            s["description"] = s["message"][:60]

    return scenarios


def save_scenarios():
    """Generate and save scenarios to JSON."""
    scenarios = generate_scenarios()
    os.makedirs(SCENARIOS_DIR, exist_ok=True)
    path = os.path.join(SCENARIOS_DIR, "scenarios_120.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(scenarios, f, ensure_ascii=False, indent=2)
    print(f"Generated {len(scenarios)} scenarios → {path}")

    # Category breakdown
    from collections import Counter
    cats = Counter(s["category"] for s in scenarios)
    print(f"\nCategory breakdown:")
    for cat, count in sorted(cats.items()):
        print(f"  {cat}: {count}")

    return scenarios


if __name__ == "__main__":
    save_scenarios()
