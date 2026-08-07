"""
Task 5D-2 — Capture mentor demo screenshots with Playwright (no vision AI).

Captures 8 required screenshots into data/demo_screenshots/ and verifies
key UI facts (COD button absence, paid_at visibility, console errors).
Browser zoom is 100% (device_scale_factor=1, default headless zoom).
"""
import json
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = "http://localhost:8000"
OUT = Path(r"T:\thai-ecommerce-agent\data\demo_screenshots")
OUT.mkdir(parents=True, exist_ok=True)

console_errors = []
page_errors = []
facts = {}


def log_fact(key, value):
    facts[key] = value
    print(f"[FACT] {key}: {value}")


def shot(page, name):
    path = OUT / name
    page.screenshot(path=str(path))
    print(f"[SHOT] {path}")


def wait_agent_reply(page, before_count):
    """Wait until a new agent message appears and typing indicator is gone."""
    page.wait_for_function(
        """(before) => {
            const typing = document.getElementById('typingIndicator');
            if (typing) return false;
            return document.querySelectorAll('.msg-agent').length > before;
        }""",
        arg=before_count,
        timeout=20000,
    )
    page.wait_for_timeout(400)


def ask_chat(page, question, before_count):
    page.fill("#chatInput", question)
    page.press("#chatInput", "Enter")
    page.wait_for_timeout(250)
    return wait_agent_reply(page, before_count)


with sync_playwright() as p:
    browser = p.chromium.launch(channel="chrome", headless=True)
    ctx = browser.new_context(
        viewport={"width": 1440, "height": 900},
        device_scale_factor=1.0,  # zoom 100%
    )
    page = ctx.new_page()

    page.on("console", lambda m: console_errors.append(m.text) if m.type == "error" else None)
    page.on("pageerror", lambda e: page_errors.append(str(e)))

    # ── 01 storefront home ──────────────────────────────────────────────
    page.goto(BASE + "/", wait_until="networkidle", timeout=30000)
    page.wait_for_selector(".product-name", timeout=15000)
    page.wait_for_function(
        "document.querySelectorAll('.product-name').length === 12", timeout=10000
    )
    page.wait_for_timeout(600)  # images settle
    log_fact("storefront_product_cards", page.locator(".product-name").count())
    shot(page, "01_storefront_home.png")

    # ── 02 product detail modal (PROD-008) ─────────────────────────────
    page.click('.product-name[data-id="PROD-008"]')
    page.wait_for_selector("#productModal:not([hidden])", timeout=10000)
    page.wait_for_timeout(500)
    title = page.locator("#pmTitle").inner_text()
    log_fact("modal_title", title)
    shot(page, "02_product_detail.png")

    # ── 03 product-aware AI (context card + deterministic answers) ─────
    page.click("#pmAskAi")
    page.wait_for_selector("#chatPanel:not([hidden])", timeout=10000)
    page.wait_for_selector("#chatProductContext:not([hidden])", timeout=10000)
    page.wait_for_timeout(500)
    ctx_name = page.locator("#chatProductContext strong").inner_text()
    ctx_line = page.locator("#chatProductContext .chat-product-context-info span").nth(0).inner_text()
    ctx_stock = page.locator("#chatProductContext .chat-product-context-info span").nth(1).inner_text()
    log_fact("product_context_card", f"{ctx_name} | {ctx_line} | {ctx_stock}")

    n_agent = page.locator(".msg-agent").count()
    answers = {}
    for q in ["Is this product in stock?",
              "How much does it cost?",
              "What are its main features?",
              "What sizes are available?"]:
        before = page.locator(".msg-agent").count()
        ask_chat(page, q, before)
        answers[q] = page.locator(".msg-agent").last.inner_text()
    for q, a in answers.items():
        log_fact("ai_answer", f"{q} -> {a[:120]}")

    # Expand research details so "Demo extension: Yes" is visible
    page.click("#researchDetails summary")
    page.wait_for_timeout(400)
    log_fact("rd_demo_ext", page.locator("#rdDemoExt").inner_text())
    log_fact("rd_handler", page.locator("#rdAgent").inner_text())
    log_fact("rd_db_source", page.locator("#rdDbSource").inner_text())
    shot(page, "03_product_ai_lookup.png")

    # ── 04 checkout summary (no submit -> no new order) ─────────────────
    page.click("#chatCloseBtn")
    page.click('.product-name[data-id="PROD-008"]')
    page.wait_for_selector("#productModal:not([hidden])", timeout=10000)
    page.click("#pmAddCart")
    page.wait_for_timeout(300)
    page.click("#pmClose")
    page.wait_for_selector("#productModal", state="hidden", timeout=10000)
    page.click('button[aria-label="Open shopping cart"]')
    page.wait_for_selector("#cartDrawer:not([hidden])", timeout=10000)
    page.click("#checkoutBtn")
    page.wait_for_selector("#checkoutModal:not([hidden])", timeout=10000)
    page.wait_for_timeout(500)
    log_fact("checkout_total", page.locator("#coTotal").inner_text())
    log_fact("checkout_items", page.locator("#coSummaryItems").inner_text().replace("\n", " | ")[:150])
    shot(page, "04_checkout_or_order_confirmation.png")

    # ── 05 My Orders (mentor.demo@example.com -> ORD-1010) ─────────────
    page.goto(BASE + "/orders?email=mentor.demo@example.com", wait_until="networkidle", timeout=30000)
    page.wait_for_selector(".order-card", timeout=15000)
    page.wait_for_timeout(500)
    log_fact("my_orders_cards", page.locator(".order-card").count())
    first_card = page.locator(".order-card").first.inner_text().replace("\n", " | ")[:220]
    log_fact("my_orders_first_card", first_card)
    shot(page, "05_my_orders.png")

    # ── 06 order details ORD-1010 ──────────────────────────────────────
    page.click('button[data-order-view="ORD-1010"]')
    page.wait_for_selector("#orderModal:not([hidden])", timeout=10000)
    page.wait_for_timeout(400)
    body = page.locator("#odBody").inner_text()
    log_fact("details_has_pending", "pending" in body.lower())
    log_fact("details_has_990", "990" in body)
    cod_btn = page.locator("#odDemoPayBtn").count()
    log_fact("cod_confirm_pay_button_offered", cod_btn > 0)
    shot(page, "06_order_details.png")
    page.click("#odClose")
    page.wait_for_selector("#orderModal", state="hidden", timeout=10000)

    # ── 07 Transaction Tracker (ORD-1010 via chat + research details) ──
    page.goto(BASE + "/", wait_until="networkidle", timeout=30000)
    page.wait_for_selector("#chatFab", timeout=15000)
    page.click("#chatFab")
    page.wait_for_selector("#chatPanel:not([hidden])", timeout=10000)
    page.wait_for_timeout(400)
    n_agent = page.locator(".msg-agent").count()
    ask_chat(page, "Where is order ORD-1010?", n_agent)
    log_fact("tt_answer", page.locator(".msg-agent").last.inner_text()[:160])
    page.click("#researchDetails summary")
    page.wait_for_timeout(400)
    log_fact("tt_rd_handler", page.locator("#rdAgent").inner_text())
    log_fact("tt_rd_intent", page.locator("#rdIntent").inner_text())
    log_fact("tt_rd_order", page.locator("#rdOrderId").inner_text())
    log_fact("tt_rd_demo_ext", page.locator("#rdDemoExt").inner_text())
    shot(page, "07_transaction_tracker.png")

    # ── 08 demo payment paid (ORD-1030) ────────────────────────────────
    page.goto(BASE + "/orders?email=payment.demo@example.com", wait_until="networkidle", timeout=30000)
    page.wait_for_selector(".order-card", timeout=15000)
    page.wait_for_timeout(500)
    log_fact("payment_orders_cards", page.locator(".order-card").count())
    page.click('button[data-order-view="ORD-1030"]')
    page.wait_for_selector("#orderModal:not([hidden])", timeout=10000)
    page.wait_for_timeout(400)
    pbody = page.locator("#odBody").inner_text()
    log_fact("paid_badge_visible", "Paid" in pbody or "paid" in pbody.lower())
    log_fact("paid_at_visible", "Paid at" in pbody)
    shot(page, "08_demo_payment_paid.png")

    browser.close()

print("\n=== CONSOLE ERRORS ===")
print(json.dumps(console_errors, ensure_ascii=False, indent=1) if console_errors else "none")
print("=== PAGE ERRORS ===")
print(json.dumps(page_errors, ensure_ascii=False, indent=1) if page_errors else "none")
log_fact("console_error_count", len(console_errors))
log_fact("page_error_count", len(page_errors))
print("\n=== FACTS ===")
print(json.dumps(facts, ensure_ascii=False, indent=1))
