# Mentor Demo Final Checklist — SiamCart

Task 5D-2 — final mentor demonstration readiness checklist.
Cross off each item before / during / after the live demo.

---

## BEFORE DEMO

- [ ] Only ONE FastAPI server is running on port 8000 (do not start a second one).
- [ ] Only ONE Hermes session is active.
- [ ] No stale headless Chrome processes are running (close them first if any).
- [ ] Browser zoom is set to 100%.
- [ ] http://localhost:8000 is reachable (`curl -s -o /dev/null -w "%{http_code}" http://localhost:8000/health` → 200).
- [ ] ORD-1010 exists and is available (CloudStep Casual Sneakers ×1, ฿990, Pending / Processing / Not Shipped, COD).
- [ ] PROD-008 exists and is available (CloudStep Casual Sneakers, stock 47, ฿990).
- [ ] Product images load on the storefront (12 products, no broken images).
- [ ] My Orders page loads (`/orders?email=mentor.demo@example.com` shows ORD-1010).
- [ ] Browser console has zero JavaScript errors (no red messages; favicon 404 fixed).
- [ ] No API key is visible anywhere in the UI or browser devtools.
- [ ] Backup taken: `data/orders.db` copied to a safe location (e.g. `data/orders.db.bak-YYYYMMDD`).
- [ ] Confirm whether DEEPSEEK_ENABLED should remain `false` — product/order demos work fully without DeepSeek; keep it off for a deterministic demo.

## LIVE DEMO ORDER

- [ ] Use synthetic customer details only (e.g. mentor.demo@example.com / payment.demo@example.com).
- [ ] Create at most ONE new order during the live demo.
- [ ] Do NOT repeatedly click Submit (one click only — double submits create duplicate orders).
- [ ] Confirm the server returns HTTP 201 before proceeding.
- [ ] Record the returned order ID (e.g. ORD-XXXX) for the after-demo report.

## FALLBACK PLAN

- [ ] If DeepSeek is unavailable: demonstrate product and order lookup only — both work deterministically without DeepSeek.
- [ ] If checkout submission fails: fall back to the existing ORD-1010 for order/My Orders demos.
- [ ] If remote product images fail: continue with order and AI functionality (local images are used; check `app/static/images/products/`).
- [ ] If port 8000 is occupied: reuse the existing healthy server — do NOT start another server.

## AFTER DEMO

- [ ] Record the created order ID in the demo report.
- [ ] Back up the database again (`data/orders.db`) after the demo.
- [ ] Close the server when it is no longer needed.
- [ ] Do not expose `.env` or API keys (never paste .env contents, never screenshot them).
- [ ] Retain screenshots and the demo report (`data/demo_screenshots/`, `docs/`).

---

## Reference data used in this demo

| Item | Value |
|---|---|
| Product | PROD-008 — CloudStep Casual Sneakers, ฿990, stock 47 |
| Demo order | ORD-1010 — CloudStep ×1, ฿990, Pending / Processing / Not Shipped, Cash on Delivery |
| My Orders email | mentor.demo@example.com |
| Payment demo order | ORD-1030 — Linen Everyday Blouse ×1, ฿690, bank_transfer → Paid (paid_at set) |
| Payment demo email | payment.demo@example.com |
