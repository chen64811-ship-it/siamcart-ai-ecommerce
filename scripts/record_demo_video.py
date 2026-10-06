"""Record the SiamCart public demo journey for the portfolio GIF/MP4.

Fully JS-driven (page.evaluate) to bypass Playwright actionability quirks on
the public site. Uses local Chrome, viewport 1440x900, records video.
Creates exactly one disposable public order through the real UI.
"""
import os
import re
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = os.getenv("DEMO_BASE_URL", "http://127.0.0.1:8000")
OUT_DIR = Path(__file__).resolve().parent.parent / "docs" / "assets"
VIDEO_DIR = OUT_DIR / "video_tmp"
VIDEO_DIR.mkdir(parents=True, exist_ok=True)

CUSTOMER = {
    "coName": "Demo Customer",
    "coEmail": "customer.demo@example.com",
    "coPhone": "081-000-0000",
    "coAddress": "10 Demo Street",
    "coDistrict": "Watthana",
    "coProvince": "Bangkok",
    "coPostal": "10110",
}


def main():
    for old in VIDEO_DIR.glob("*.webm"):
        old.unlink()

    def step(msg):
        print("STEP:", msg, flush=True)

    try:
        _run(step)
    except Exception as exc:  # pragma: no cover
        print("FAILED:", type(exc).__name__, str(exc)[:300], flush=True)
        raise


def _run(step):
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome", headless=True)
        context = browser.new_context(
            viewport={"width": 1440, "height": 900},
            record_video_dir=str(VIDEO_DIR),
            record_video_size={"width": 1440, "height": 900},
            locale="en-US",
        )
        page = context.new_page()
        page.set_default_timeout(12000)
        page.on("dialog", lambda d: d.accept())

        def js(expr):
            return page.evaluate(expr)

        def jclick(sel):
            js(f"document.querySelector('{sel}').click()")

        def jfill(sel, val):
            js(f"""
                (() => {{
                    const el = document.querySelector('{sel}');
                    if (!el) return;
                    const proto = Object.getPrototypeOf(el);
                    const setter = Object.getOwnPropertyDescriptor(proto, 'value').set;
                    setter.call(el, {val!r});
                    el.dispatchEvent(new Event('input', {{ bubbles: true }}));
                    el.dispatchEvent(new Event('change', {{ bubbles: true }}));
                }})()
            """)

        # 1. Storefront home (2s)
        step("1 storefront")
        page.goto(BASE, wait_until="domcontentloaded")
        page.wait_for_timeout(2000)

        # 2. Open a product (2s)
        step("2 quick view")
        page.wait_for_selector(".product-card", timeout=10000)
        page.evaluate("document.querySelector('.product-card[data-id=\"PROD-001\"] .quick-view-btn').click()")
        page.wait_for_selector("#pmAddCart", timeout=8000)
        page.wait_for_timeout(1200)
        step("2 add to cart")
        jclick("#pmAddCart")
        page.wait_for_timeout(1200)

        # 3. Cart drawer (2s)
        step("3 cart drawer")
        page.keyboard.press("Escape")
        page.wait_for_timeout(400)
        jclick("#cartToggle")
        page.wait_for_timeout(1500)

        # 4. Checkout (3s)
        step("4 checkout")
        jclick("#checkoutBtn")
        page.wait_for_selector("#checkoutForm", timeout=8000)
        page.wait_for_timeout(1600)

        # 5. Fill + place order + demo payment (3s)
        step("5 fill checkout")
        for fid, val in CUSTOMER.items():
            jfill(f"#{fid}", val)
        js("document.querySelector('input[name=payment][value=\"Bank Transfer\"]').click()")
        page.wait_for_timeout(600)
        jclick("#coPlaceOrder")
        page.wait_for_selector("#confirmRef", timeout=20000)
        page.wait_for_timeout(1400)
        ref = page.locator("#confirmRef").inner_text()
        m = re.search(r"ORD-\d+", ref)
        order_id = m.group(0) if m else None
        print("ORDER_ID=" + (order_id or "UNKNOWN"), flush=True)
        step("5 demo payment")
        jclick("#confirmDemoPayBtn")
        page.wait_for_timeout(2200)

        # 6. My Orders (3s)
        step("6 my orders")
        page.goto(BASE + "/orders?email=" + CUSTOMER["coEmail"], wait_until="domcontentloaded")
        page.wait_for_selector(".order-card", timeout=12000)
        page.wait_for_timeout(1600)
        if order_id:
            # Wait for the target card (email page lists newest first).
            for _ in range(24):
                if js(f"!!document.querySelector('.order-card[data-order-id=\"{order_id}\"]')"):
                    break
                page.wait_for_timeout(250)
            js(f"document.querySelector('.order-card[data-order-id=\"{order_id}\"] [data-order-view]')"
               ".click()")
            page.wait_for_timeout(1500)

        # 7. Ask AI order-status (4s)
        step("7 ask AI")
        jclick("#chatFab")
        page.wait_for_timeout(700)
        jfill("#chatInput", f"Where is order {order_id}?")
        page.wait_for_timeout(400)
        js("(() => { const el = document.getElementById('chatInput'); el.focus(); el.dispatchEvent(new KeyboardEvent('keydown', {key:'Enter', code:'Enter', bubbles:true})); })()")
        page.wait_for_timeout(3000)

        # 8. Simulate Shipment / tracking (4s)
        step("8 simulate shipment")
        jclick("#odShipBtn")
        page.wait_for_timeout(1800)
        page.wait_for_timeout(1600)

        # 9. Delivered state (4s)
        step("9 mark delivered")
        jclick("#odDeliverBtn")
        page.wait_for_timeout(1800)
        page.wait_for_timeout(1600)

        # 10. Finish on My Orders (2s)
        step("10 finish")
        jclick("#odClose")
        page.wait_for_timeout(1500)
        jclick("#chatCloseBtn")
        page.wait_for_timeout(1200)

        context.close()
        browser.close()

    videos = list(VIDEO_DIR.glob("*.webm"))
    if not videos:
        print("NO_VIDEO")
        sys.exit(1)
    print("VIDEO=" + str(videos[0]))


if __name__ == "__main__":
    main()
