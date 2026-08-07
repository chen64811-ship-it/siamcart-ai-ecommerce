# Full Customer Journey Audit — SiamCart (thai-ecommerce-agent)

**Audit scope:** Full customer journey through the SiamCart Thai e-commerce storefront + AI assistant, acting as a real customer (journey.tester@example.com).
**Audit mode:** AUDIT ONLY. No source fixes, no pytest, no schema changes, no order deletion, single healthy server on port 8000, one browser session.
**Document:** Continuation checkpoint — Part 2A (started from previous session, resumed at Phase 2 Q7).
**Audit order (Phase 4):** ORD-1034 (created 2026-08-03, Bank Transfer, ฿1,980 backend total).

---

## 1. Executive Summary

The audit covers the complete customer journey: storefront discovery → product detail → product-aware AI chat → cart → checkout → payment → My Orders → order-aware AI → conversation stress → error resilience → mobile. Part 1 (previous session) completed Preparation, Phase 1, and Phase 2 questions Q1–Q6. The storefront and product-aware AI are largely functional; the issues found so far are mostly P3 polish items, plus one suspected product-context payload inconsistency (card visible while request payload carries product_id=null after a greeting) that Part 2A must verify. No order has been created yet as of the checkpoint; Phase 4 creates exactly one real audit order.

**Process notes:** The live server was observed running under global Python 3.11 rather than the project `.venv` (recorded as ENV-001). The Part 2A session found the server had stopped (previous session's background process ended) and restarted the single server on port 8000 with the same interpreter to preserve audit state.

## 2. Customer Journey Outcome

| Stage | Status (as of Part 2A start) |
|---|---|
| Preparation | Mostly passed (ENV-001) |
| Phase 1 Storefront | Passed with P3 findings |
| Phase 2 Product + AI Q1–Q6 | Passed with P3 + suspected CONV-004 |
| Phase 2 Q7–Q11 (context lifecycle) | Part 2A |
| Phase 3 Cart | Part 2A |
| Phase 4 Checkout & Payment | Completed (ORD-1034) |
| Phase 5 My Orders | Completed (ORD-1034) |
| Phase 6 Order-aware AI | Pending |
| Phase 7 Conversation stress | Pending |
| Phase 8 Error/resilience | Pending |
| Phase 9 Mobile 390px | Pending |

## 3. Critical Failures

- **CART-003 — CONFIRMED (P1 Major, upgraded from P2 in Phase 4).** The customer sees ฿2,029 at checkout but the persisted order records ฿1,980 (฿49 shipping never sent to the backend). Reproduced with a real audit order (ORD-1034): UI grand total ฿2,029 vs backend `total_amount` 1,980. No other confirmed failures; CONV-004 closed as NOT REPRODUCED.

## 4. Conversation Intelligence Problems

- **CONV-002 (P3)** — English feature names embedded inside Thai assistant replies.
- **CONV-003 (P3)** — Assistant used English word "color" instead of Thai "สี" when honestly refusing to answer an attribute question.
- **CONV-004 (severity pending verification)** — CLOSED — NOT REPRODUCED in Phase 2 Q11 (context card and payload share `state.product`).
- **CONTEXT-001 (P2, Phase 5E)** — "Ask AI" on an order card does not retain internal order context: it only sends visible text "Where is order ORD-1034?"; no order-context card, no Remove-order-context action, and the /api/chat payload carries no order_id field.
- **CONTEXT-002 (P2, Phase 6A)** — An explicit order query ("Where is order ORD-1034?") does not establish a reusable active_order_id: the very next pronoun turn ("Has it been paid?") is refused with an order-ID request.
- **CONTEXT-003 (P2, Phase 6A)** — Pronouns "this order" / "it" / "คำสั่งซื้อนี้" / "ตอนนี้" are not resolved; all follow-up pronoun turns fail with "กรุณาระบุหมายเลขคำสั่งซื้อของคุณด้วยค่ะ".
- **CONV-006 (P2, Phase 6A)** — The identical order-ID clarification is served five times consecutively with no reference to the customer's visible order.
- **ORDER-003 (P2, Phase 6A)** — No TOTAL/ITEMS intent exists: "What is the total?" / "ฉันซื้อสินค้าอะไรบ้าง" / "What did I buy?" are unanswerable even with order evidence in the chat.
- **META-001 (P3, Phase 6A)** — Research-details `intent` field carries a stale label for unrecognized input (e.g. "What is the total?" → SHIPMENT_STATUS while routing_reason says "No matching intent pattern found").
- **ROUTE-001 (P1, Phase 6B)** — No EXCHANGE/CANCELLATION handler: "I want to change the colour" → clarification_handler (every exchange step fails); "cancel it" → OUT_OF_SCOPE/human review or folded into RETURN_REFUND. Exchange journey cannot be completed.
- **CONTEXT-004 (P2, Phase 6B)** — Refund workflow holds ORD-1034 internally but never promotes it to a session-level active order; payment/shipment follow-ups in the same session are refused.
- **CONTEXT-005 (P2, Phase 6B)** — Exchange workflow never establishes or preserves order context (ORD-1011 given in X2 not retained for X3–X5).
- **CONV-007 (P2, Phase 6B)** — Explicit cancellation does not override refund/exchange (R4 → OUT_OF_SCOPE; X5 → RETURN_REFUND re-ask).
- **CONV-008 (P2, Phase 6B)** — Bare order ID with no pending workflow hijacks into ORDER_STATUS (exchange attempt answered with a status dump).
- **CONV-009 (P2, Phase 6B)** — Desired colour not retained ("black"/"red" → clarification); refund reason IS retained.
- **POLICY-001 (P2, Phase 6B)** — Cancellation guidance missing (no cancellation_policy.md) and exchange policy (exchange_policy.md) never retrieved; refund guidance properly grounded.
- **CONV-010 (P2, Phase 7A)** — Already supplied order information is repeatedly requested: after ORD-1034 was supplied, turns 5–8 each re-requested the order number (4 consecutive identical requests in one session).
- **CONV-011 (P2, Phase 7A)** — Refund → exchange switch fails in a long continuous conversation: "actually I want to change the colour instead" → clarification_handler (no EXCHANGE intent).

## 5. Checkout and Payment Problems

- **CART-003 (P1, CONFIRMED in Phase 4)** — Frontend/backend grand-total mismatch: UI shows ฿2,029 (subtotal ฿1,980 + shipping ฿49) but the created order ORD-1034 recorded `total_amount` = 1,980. POST /api/orders payload carries no shipping/subtotal/total; backend sums line totals only. Customer is charged/persisted ฿49 less than displayed. Confirmation screen shows no total at all, so the mismatch is only visible in My Orders / order details.
- **ORDER-001 (P2)** — Order-confirmation screen renders no order items, no total, no customer name: `renderConfirmation` (store.js:975) reads `order.order_items`, but both POST /api/orders and GET /api/orders/<id> return the key `items`. `#confirmOrderItems` stays hidden/empty.
- **CHECKOUT-001 (P3)** — After a failed submit, focus does NOT move to the first invalid field (stays where the user was, e.g. Postal code while Full name is the first error).
- **CHECKOUT-002 (P3)** — Field errors persist after the user corrects the value until the NEXT submit attempt (no live re-validation on input).

## 6. Order and My Orders Problems

- **TOTAL-001 (P2)** — Order-details modal fabricates a subtotal/shipping breakdown: for ORD-1034 it shows Subtotal ฿1,931 + Shipping ฿49 = Total ฿1,980. The real items sum to ฿1,980 and the backend never charged shipping (CART-003), so the displayed subtotal is wrong and the ฿49 shipping line is invented — actively masking CART-003 instead of explaining it.
- **ORDER-002 (P3)** — Item thumbnails in the order-details modal always render the generic SVG placeholder: the detail API (GET /api/orders/<id>) returns items without `image_url`, while the list API (GET /api/orders) returns `order_items` WITH `image_url`.
- **UI-003 (P3)** — After closing the order-details modal, keyboard focus stays on the now-hidden `#odClose` button (no focus restore).

## 7. Product Experience Problems

- **CONV-001 (P3)** — Chat suggestion chips hard-code order IDs ORD-1001/ORD-1002/ORD-1003 that do not belong to the current customer.

## 8. UI and Responsive Problems

- **UI-001 (P3)** — Skip-link is the only element with a negative offset (keyboard accessibility target offset issue).
- **UI-002 (P3)** — Footer exposes research-prototype links ("About the research", "Multi-agent framework", "System architecture") on the customer-facing storefront.

## 9. Performance Problems

None observed. Server API responses served with ~0.5 ms elapsed for chat lookups; product grid renders 12 cards without issue. Page loads clean (0 JS errors, no horizontal overflow at ~101% zoom). PERF section to be expanded in Phase 7/8.

## 10. Language and Thai Quality Problems

- Responses are consistently Thai (passed).
- **CONV-002 (P3)** — English feature names leak into Thai replies (mixed-language UX).
- **CONV-003 (P3)** — "color" used instead of "สี".

## 11. Error Handling Problems

None observed in Phases 1–2 (search "no results" state handled with a working "Clear filters" restore). Full error/resilience coverage is Phase 8.

## 12. Passed Behaviors

- Storefront browsing, search (full/partial/empty), category filter (All/Fashion/Electronics/Footwear/Bags & Accessories = 12/3/3/3/3), price sort asc/desc, grid/list toggle — PASS.
- Product detail modal open/close (X, Escape, backdrop) — PASS.
- Quantity stepper in modal capped at stock max (38) per order; resets to 1 on fresh open — PASS.
- Add to Cart (badge + toast) — PASS.
- Buy Now opens checkout while preserving the normal cart — PASS.
- Checkout summary: Subtotal ฿1,290 + Shipping ฿49 = Total ฿1,339; submit disabled with "Select a Payment Method" until a method is chosen — PASS (Phase 2 observation).
- Product-aware AI: price/stock/features questions answered with factual lookups (product_catalog_lookup handler, PROD-004, HTTP 200) — PASS.
- Product context card displayed correctly — PASS (with CONV-004 caveat).
- Greeting intent handled ("hello" → GREETING) — PASS.
- Colour question honestly refused without fabricating data — PASS (language mix caveat CONV-003).

## 13. Prioritized Repair Plan

| Priority | Issue | Suggested fix direction (NOT implemented — audit only) |
|---|---|---|
| P1 | CART-003 shipping never sent to backend (CONFIRMED, UI ฿2,029 vs order ฿1,980) | Send shipping (or total) in the POST /api/orders payload and include it in backend `total_amount`, or stop displaying shipping under the threshold. |
| P2 (pending verify) | CONV-004 stale product context | CLOSED — NOT REPRODUCED in Phase 2 Q11 (card and payload share `state.product`). |
| P2 | ENV-001 server interpreter mismatch | Start the server via `.venv\Scripts\python.exe run.py server` (documented deployment path). |
| P2 | CART-001 double-click Add to Cart | Add an in-flight/lock guard to `#pmAddCart` handler (store.js:511-513). |
| P2 | CART-002 cart not restored on refresh | Call `loadCart()`/re-render after `fetchProducts()` resolves (store.js init ordering). |
| P2 | ORDER-001 confirmation screen shows no items/total/name | Read `order.items || order.order_items` in `renderConfirmation` (store.js:975). |
| P3 | CONV-001 fake suggested order IDs | Bind suggestion chips to the customer's real orders or remove specific IDs. |
| P3 | CONV-002 English feature names | Localize feature labels in product catalog responses. |
| P3 | CONV-003 "color" | Use Thai "สี" in refusal/attribute templates. |
| P3 | UI-001 skip-link offset | Give skip-link a proper visible focus target/offset. |
| P3 | UI-002 research footer links | Move research links behind an admin/demo flag. |
| P3 | CHECKOUT-001 focus not moved to first invalid field | Focus the first `.invalid` input after a failed submit. |
| P3 | CHECKOUT-002 errors persist until next submit | Re-validate fields live on `input` (delegated listener on `#checkoutForm`). |

**Repair batches (Phase 5 closeout):**

**BATCH A — Broken customer journeys**

- **CART-003 (P1):** create one canonical server-side pricing model including shipping; return `subtotal`, `shipping_amount` and `total_amount`; use the same values in checkout, confirmation, My Orders and details.

**BATCH B — Conversation intelligence and context**

- **CONTEXT-001 (P2):** add optional `order_id`/order context to chat state and request schema; show an order-context card; preserve it across follow-ups; provide a Remove order context action.

**BATCH C — Payment and order accuracy**

- **TOTAL-001 (P2):** never derive a fake subtotal by subtracting a frontend shipping constant from persisted `total_amount`.
- **ORDER-001 (P2):** normalize `items` versus `order_items` API response naming.

**BATCH D — UI polish**

- **ORDER-002 (P3):** include product `image_url` in order-detail responses.
- **UI-003 (P3):** return focus to the control that opened the details modal.

## 14. Final Customer Verdict

PENDING — will be written after Phases 3–9 complete.

---

# ISSUE LOG (detailed)

## CONV-001 — Hard-coded fake suggested order IDs

- **Phase:** 1 (chat suggestion chips on storefront)
- **Severity:** P3
- **Reproduction:** Open the storefront chat FAB. Read the suggestion chips.
- **Expected:** Suggestions reference the current customer's real orders, or no order IDs are hard-coded.
- **Actual:** Chips hard-code ORD-1001 / ORD-1002 / ORD-1003, which do not belong to the current customer (journey.tester).
- **Evidence:** Browser snapshot of chat FAB chips; prior session notes.
- **Suspected layer:** Frontend (chat widget suggestion configuration) — likely `app/static/js/chat.js` or template constants.
- **Suggested fix direction:** Populate chips from the customer's own orders via the orders API, or generic non-ID prompts.

## CONV-002 — English feature names inside Thai replies

- **Phase:** 2 (Q3 features question)
- **Severity:** P3
- **Reproduction:** Ask the AI for product features; read the Thai response.
- **Expected:** Fully Thai feature description.
- **Actual:** Thai sentence structure with English feature labels embedded (mixed-language).
- **Evidence:** Chat response text captured in prior session.
- **Suspected layer:** Product catalog data / LLM prompt (feature names stored in English).
- **Suggested fix direction:** Add Thai feature labels to catalog data or translate in the response template.

## CONV-003 — "color" used instead of Thai "สี"

- **Phase:** 2 (Q4 colour question)
- **Severity:** P3
- **Reproduction:** Ask the AI about the product's color.
- **Expected:** Thai "สี" in the refusal/explanation.
- **Actual:** English word "color" used.
- **Evidence:** Chat response text captured in prior session.
- **Suspected layer:** Intent/refusal template strings.
- **Suggested fix direction:** Replace English attribute nouns with Thai in templates.

## CONV-004 — Suspected stale product-context (card vs payload disagreement)

- **Phase:** 2 (after Q6 greeting)
- **Severity:** P2 (pending verification in Part 2A)
- **Reproduction:** With PROD-004 context card visible, send "hello". Inspect the last `/api/chat` request body.
- **Expected:** If the context card is visible, the request should carry the product_id; if context is cleared, the card should disappear.
- **Actual:** Card remained visible, but request body contained `product_id: null`.
- **Evidence:** Network log / request-body capture from prior session.
- **Suspected layer:** Chat context state management (frontend card rendering vs payload assembly) or session context expiry server-side.
- **Suggested fix direction:** Single source of truth for context: render the card from the same state that builds the payload; clear both together.

## ENV-001 — Server running under global Python instead of project .venv

- **Phase:** Preparation
- **Severity:** P2 (process/environment — no customer impact, deployment risk)
- **Reproduction:** Inspect the command line of the process listening on port 8000.
- **Expected:** `.venv\Scripts\python.exe run.py server`.
- **Actual:** `C:\Users\ChengXingYu\AppData\Local\Programs\Python\Python311\python.exe run.py server` (global Python 3.11.9; venv is also 3.11.9 — same version, so runtime behavior matches).
- **Evidence:** Process inspection in previous session.
- **Suspected layer:** Deployment/run procedure.
- **Suggested fix direction:** Document and use `.venv\Scripts\python.exe run.py server`.

## UI-001 — Skip-link offset/accessibility issue

- **Phase:** 1
- **Severity:** P3
- **Reproduction:** Inspect the "Skip to content" link's CSS offset vs other focusable elements.
- **Expected:** Skip link is positioned consistently with other focus targets.
- **Actual:** Skip link is the only element with a negative offset.
- **Evidence:** Computed styles captured in previous session.
- **Suspected layer:** CSS (`app/static/css/styles.css`).
- **Suggested fix direction:** Normalize skip-link focus offset.

## UI-002 — Research links exposed in customer footer

- **Phase:** 1
- **Severity:** P3
- **Reproduction:** Scroll to the storefront footer; inspect "Research prototype links" section.
- **Expected:** Customer storefront shows only customer-facing links.
- **Actual:** Footer shows "About the research", "Multi-agent framework", "System architecture" research links.
- **Evidence:** Browser snapshot of footer in previous session.
- **Suspected layer:** Template (`app/templates/index.html`).
- **Suggested fix direction:** Hide research links behind a demo/admin flag.

## CART-001 — Double-click Add to Cart double-adds quantity

- **Phase:** 3 (cart — modal Add to Cart)
- **Severity:** P2
- **Reproduction:** With PROD-001 (qty 1) in the cart, open the product modal and fire two rapid clicks on `#pmAddCart`.
- **Expected:** One deliberate add action, or the button temporarily locks while the click is handled.
- **Actual:** Two click events both execute — PROD-001 quantity 1 → 3, badge increments twice (2 → 4), localStorage saved `{"PROD-004":1,"PROD-001":3}`. Toast shows only the last "Added 1 × …".
- **Evidence:** Live browser run (2026-08): `btn.click(); btn.click();` → badge 4, qty 3. Source confirms no guard: `#pmAddCart` handler (store.js:511-513) calls `addToCart(p.id, state.pmQty)` directly with no in-flight state check, unlike the checkout submit which has `state.submittingOrder` (store.js:834).
- **Suspected layer:** Frontend `app/static/js/store.js` modal Add to Cart event handling.
- **Suggested fix direction:** Disable the button during click handling or add a short per-product click lock. NOT implemented (audit only).

## CART-002 — Cart not restored from localStorage on page refresh (persistence broken)

- **Phase:** 3 (cart — refresh persistence, check #6)
- **Severity:** P2
- **Reproduction:** Add PROD-004 + PROD-001 (localStorage `siamcart.cart.v1` = `{"PROD-004":1,"PROD-001":1}`, badge 2). Reload the page. Read the badge and open the cart drawer.
- **Expected:** Cart restored from localStorage — badge 2, both items listed.
- **Actual:** Badge hidden/0, drawer shows "Your cart is empty", totals ฿0/—/฿0, while localStorage STILL contains `{"PROD-004":1,"PROD-001":1}`. The saved data is never rendered again.
- **Evidence:** Live browser run (2026-08): after reload, badge text "0" + empty drawer + `localStorage.getItem('siamcart.cart.v1')` = `{"PROD-004":1,"PROD-001":1}`. Source: `state.cart = loadCart()` (store.js:41) runs at state init BEFORE `fetchProducts()` populates `state.products`; `loadCart()` (store.js:51-62) filters every saved ID through `getProduct(id)`, which returns undefined against the empty array → `clean = {}`. After products load, the reconcile step (store.js:140-152) only prunes `state.cart` (already `{}`) — no second `loadCart()` call exists.
- **Suspected layer:** Frontend `app/static/js/store.js` init ordering (`loadCart()` before products fetch).
- **Suggested fix direction:** Call `loadCart()`/re-render after `fetchProducts()` resolves, or defer cart hydration until products are available. NOT implemented (audit only).

## CART-003 — Shipping not sent to backend; frontend/backend grand-total mismatch (CONFIRMED, P1)

- **Phase:** 3 (suspected) → 4 (CONFIRMED with real order ORD-1034)
- **Severity:** **P1 Major** (upgraded from P2 — customer sees a different amount than the persisted order)
- **Reproduction:** Add PROD-004 (฿1,290) + PROD-001 (฿690), open checkout, submit with Bank Transfer. UI: Subtotal ฿1,980 + Shipping ฿49 = Total ฿2,029. Order ORD-1034 persisted with `total_amount` 1,980.
- **Expected:** If shipping is charged, the backend total should match the UI total (฿2,029), or the UI should not display shipping.
- **Actual:** POST /api/orders payload (store.js:848-858) contains customer fields + `payment_method` + `items[{product_id, quantity}]` only — **no shipping, subtotal, or total fields**. Backend computes `total_amount = round(sum(i["line_total"] for i in resolved), 2)` (app/db/store.py:361) — line totals only, no shipping. For carts under ฿2,000 the frontend displays ฿2,029 but the backend records ฿1,980 (฿49 mismatch). For carts ≥ ฿2,000 (shipping shown "Free") the totals agree.
- **Evidence (Phase 4 live):** UI `#coSubtotal` ฿1,980 · `#coShipping` ฿49 · `#coTotal` ฿2,029. POST /api/orders → 201 (322 ms), response `total_amount: 1980`; GET /api/orders/ORD-1034 → `total_amount: 1980.0`; DB row ORD-1034 `total_amount` 1980.0. Classification: **Case B** (backend ฿1,980 while frontend showed ฿2,029) → CART-003 CONFIRMED.
- **Suspected layer:** Frontend `app/static/js/store.js` (shippingFor, store.js:570; order payload) / backend `app/db/store.py` pricing.
- **Suggested fix direction:** Send a shipping (or total) field in the order payload and have the backend include it in `total_amount`, or remove shipping from the UI for carts under the threshold. NOT implemented (audit only).

## CHECKOUT-001 — Focus does not move to the first invalid field after failed submit

- **Phase:** 4 (checkout form validation)
- **Severity:** P3
- **Reproduction:** In checkout, focus the Postal code field, leave Full name empty, submit. Observe `document.activeElement`.
- **Expected:** Focus moves to the first invalid field (Full name) so the customer can fix errors in order.
- **Actual:** Focus stays where it was (Postal code); only the toast "Please fix the highlighted fields" and inline errors appear. `validateCheckout()` (store.js:795-830) never calls `.focus()`.
- **Evidence:** Live run 2026-08: after submit with empty name, `activeElement.id === "coPostal"` while `#coName` shows "Please enter your full name." and has `.invalid`.
- **Suspected layer:** Frontend `app/static/js/store.js` `validateCheckout` / `placeDemoOrder`.
- **Suggested fix direction:** After a failed validation, focus the first marked `.invalid` input. NOT implemented (audit only).

## CHECKOUT-002 — Field errors persist until next submit; no live re-validation on input

- **Phase:** 4 (checkout form validation)
- **Severity:** P3
- **Reproduction:** Submit with an empty Full name → error shown. Type a valid name into the field. Read the error text without submitting again.
- **Expected:** The error clears as soon as the value becomes valid (or on `input`).
- **Actual:** The error remains visible until the next submit attempt re-runs `validateCheckout()`; the `mark()` error-clearing only happens inside validation (store.js:797-802). Corrections ARE applied correctly on the next submit (verified: fixing each field clears its error on the following validation pass).
- **Evidence:** Live run 2026-08: after fixing a field, its `.co-error` text stayed until the next submit; after the next invalid submit, the corrected field's error was empty.
- **Suspected layer:** Frontend `app/static/js/store.js` `validateCheckout` (no input listeners for error clearing).
- **Suggested fix direction:** Add `input` listeners per field (or a single delegated listener on `#checkoutForm`) that re-validate that field live. NOT implemented (audit only).

## ORDER-001 — Order-confirmation screen shows no items, no total, no customer name

- **Phase:** 4 (order confirmation, ORD-1034)
- **Severity:** P2
- **Reproduction:** Create an order, view the confirmation screen (`#confirmationView`). Inspect `#confirmOrderItems`.
- **Expected:** The confirmation lists the ordered items with quantities and line totals, the order total, and the customer name (mirroring the checkout summary).
- **Actual:** `#confirmOrderItems` is hidden and empty; the confirmation shows only payment method + Payment/Order/Shipment statuses + the demo-pay button. No total amount and no customer name are displayed anywhere on the screen.
- **Evidence:** Live run 2026-08: `renderConfirmation` (store.js:975) reads `order.order_items`, but the POST /api/orders response and GET /api/orders/ORD-1034 both return the key `items` (2 entries: PROD-004 1290, PROD-001 690) → `Array.isArray(undefined)` is false → items = [] → `#confirmOrderItems.hidden = true`. Confirmation DOM: `confirmItemsHidden: true`, `confirmItemsHTML: ""`.
- **Suspected layer:** Frontend `app/static/js/store.js` `renderConfirmation` (key mismatch `order_items` vs `items`).
- **Suggested fix direction:** Read `order.items || order.order_items` (and normalize unit_price/line_total names if needed). NOT implemented (audit only).

## TOTAL-001 — Order-details modal fabricates subtotal/shipping breakdown

- **Phase:** 5 (order details modal, ORD-1034)
- **Severity:** P2
- **Reproduction:** Open My Orders → View Details for ORD-1034 (Bank Transfer, paid, total_amount 1980). Read the Totals block.
- **Expected:** Subtotal = sum of line totals (฿1,980), Shipping = ฿0/Free (shipping was never charged — CART-003), Total = ฿1,980; the ฿49 difference vs checkout (฿2,029) explained or flagged.
- **Actual:** Modal shows Subtotal ฿1,931 + Shipping ฿49 = Total ฿1,980. `renderDetails` (orders.js:408-409) computes `ship = shippingFor(total_amount)` (=49 for totals < ฿2,000) and `sub = total_amount - ship` (=1,931). The ฿1,931 subtotal was never true (items line totals sum to ฿1,980) and the ฿49 shipping was never charged — the modal invents a shipping line to reconcile the numbers, which masks CART-003 instead of explaining the missing ฿49.
- **Network/API evidence:** GET /api/orders/ORD-1034 → `total_amount: 1980.0`, items line totals 1290 + 690 = 1980. Modal DOM: `Subtotal ฿1,931 | Shipping ฿49 | Total ฿1,980`.
- **Screenshot:** `data/customer_journey_audit/10_order_details_ORD-1034.png`.
- **Suspected layer:** Frontend `app/static/js/orders.js` `renderDetails` (shippingFor guesswork on total_amount).
- **Suggested fix direction:** Derive subtotal from item line totals and shipping from the payload/order record (store real subtotal/shipping on the order, or send them in the order payload); do not reverse-engineer shipping from total_amount. NOT implemented (audit only).

## ORDER-002 — Order-details modal item thumbnails always show placeholder SVG

- **Phase:** 5 (order details modal, ORD-1034)
- **Severity:** P3
- **Reproduction:** Open My Orders → View Details for any order. Inspect the Items section images.
- **Expected:** Items show the product images (as the order card thumbnail does).
- **Actual:** Every item image is the generic SVG fallback (`window.SiamCart.fallbackImage`), because the detail API items have no `image_url` key.
- **Network/API evidence:** GET /api/orders/ORD-1034 → item keys `[item_id, line_total, order_id, product_id, product_name, quantity, unit_price]` (no image_url). GET /api/orders?order_id=ORD-1034 → `order_items[0]` HAS `image_url: '/static/images/products/prod-004.jpg'` (card thumbnail loads correctly). Modal DOM: both `.od-item-img img` naturalWidth>0 but src is the encoded SVG fallback.
- **Screenshot:** `data/customer_journey_audit/10_order_details_ORD-1034.png`.
- **Suspected layer:** Backend `app/db/store.py` order-detail serializer (missing image_url on detail items) — inconsistent with the list serializer.
- **Suggested fix direction:** Include `image_url` in detail-API items (join products), matching the list API. NOT implemented (audit only).

## CONTEXT-001 — Ask AI on order card does not retain internal order context

- **Phase:** 5 (Phase 5E — Ask AI context setup; full conversation impact is Phase 6)
- **Severity:** P2
- **Reproduction:** On the ORD-1034 card click Ask AI. Inspect the chat widget, the /api/chat payload, and the DOM for an order-context card.
- **Expected:** Chat opens with an order-context card (order ID ORD-1034, first product, Payment Paid / Order Processing / Shipment Not Shipped), a Remove-order-context action, and the order_id retained internally in subsequent requests.
- **Actual:** Chat opens and sends exactly ONE message "Where is order ORD-1034?" (correct Thai transaction-tracker reply: paid 2026-08-03T10:56:10, processing, not shipped — so the SERVER parses the ID from the message text), but there is no order-context card anywhere (`#chatOrderContext`, `.chat-order-context`, `#orderCtxClose`, `[data-order-context]`, `.chat-context` all absent), no Remove-order-context action, and the payload is `{"message":"Where is order ORD-1034?","session_id":null}` — no order_id field. The UI only prefills visible text (orders.js:541-547 `askAiAboutOrder` → `Chat.open()` + `Chat.sendText("Where is order X?")`); chat.js has no order-context state at all (only the product-context card).
- **Network/API evidence:** Captured POST /api/chat body: `{"message":"Where is order ORD-1034?","session_id":null}`. Research panel: intent ORDER_STATUS, agent transaction_tracker, orderId ORD-1034 (parsed server-side from the message text).
- **Screenshot:** `data/customer_journey_audit/11_order_context_missing_ORD-1034.png`.
- **Suspected layer:** Frontend `app/static/js/orders.js` (Ask AI wiring) + `app/static/js/chat.js` (no order-context state/card, mirroring the product-context pattern).
- **Suggested fix direction:** Add order context to chat.js (state.order + card + remove action, analogous to `setProductContext`), set it from the card/order-details Ask AI buttons, and include `order_id` in the chat payload while active. NOT implemented (audit only).

## UI-003 — Focus not restored after closing the order-details modal

- **Phase:** 5 (order details modal behavior)
- **Severity:** P3
- **Reproduction:** Open View Details (close button receives focus), close via X / Escape / backdrop. Read `document.activeElement`.
- **Expected:** Focus returns to the element that opened the modal (or a sensible page element).
- **Actual:** Focus stays on `#odClose`, which is now hidden — keyboard focus is effectively lost to a hidden element. orders.js `closeDetails()` (376-380) has no focus restore (unlike store.js's rememberFocus/restoreFocus pattern).
- **Evidence:** Live run 2026-08: after X close, `activeElement.id === "odClose"` while the modal is hidden.
- **Suspected layer:** Frontend `app/static/js/orders.js` `closeDetails`.
- **Suggested fix direction:** Remember the trigger element on open and restore focus on close. NOT implemented (audit only).

---

# PHASE 6A ISSUE LOG (added 2026-08-03)

## CONTEXT-002 — Explicit order query does not establish reusable active_order_id

- **Phase:** 6A (explicit order ID, clean chat — session demo-a7ffd896)
- **Severity:** P2
- **Reproduction:** New chat → send "Where is order ORD-1034?" (answered correctly) → follow with "Has it been paid?".
- **Expected:** The explicit ORD-1034 in turn 1 creates session context; "it" resolves to ORD-1034 and the payment answer is served.
- **Actual:** Turn E2 matched PAYMENT_STATUS but `order_id: None` — "Matched PAYMENT_STATUS pattern but missing required entity: order_id" → "กรุณาระบุหมายเลขคำสั่งซื้อของคุณด้วยค่ะ". E3 "What did I buy?" → clarification_handler. The server session persists but carries no active order entity; the payload schema has no order_id field.
- **Evidence:** `logs/experiment.jsonl` entries 2026-08-03T14:59:16 / 14:59:25 / 14:59:37 (session demo-a7ffd896); screenshot `data/customer_journey_audit/13_explicit_order_followup.png`.
- **Suspected layer:** Chat request schema / server session state (no active_order_id stored per session); frontend chat.js `_doSend` payload.
- **Suggested fix direction:** Store a resolved `active_order_id` in session state when an order ID is parsed, carry it in subsequent requests, and let intent handlers fall back to it. NOT implemented (audit only).

## CONTEXT-003 — Pronouns (this order / it / คำสั่งซื้อนี้ / ตอนนี้) are not resolved

- **Phase:** 6A (five-turn follow-up — session demo-012141b6; explicit-ID chat — demo-a7ffd896)
- **Severity:** P2
- **Reproduction:** After "Where is order ORD-1034?", send "คำสั่งซื้อนี้ชำระเงินแล้วหรือยัง", "ส่งของหรือยัง", "แล้วสถานะตอนนี้ล่ะ", "Has it been paid?".
- **Expected:** "this order" / "it" / "คำสั่งซื้อนี้" / "ตอนนี้" refer to ORD-1034 and are answered without re-entering the ID.
- **Actual:** Thai pronoun turns match PAYMENT_STATUS/SHIPMENT_STATUS/ORDER_STATUS but fail on `order_id: None`; English "it" fails the same way. Every turn returns "กรุณาระบุหมายเลขคำสั่งซื้อของคุณด้วยค่ะ".
- **Evidence:** `logs/experiment.jsonl` entries 2026-08-03T14:57:28 … 14:58:12 (demo-012141b6) and 14:59:25 (demo-a7ffd896); screenshot `data/customer_journey_audit/12_order_pronoun_failure.png`.
- **Suspected layer:** Intent/entity layer — pronoun resolution has no target (no active order), router has no fallback to the preceding turn's parsed order.
- **Suggested fix direction:** Resolve pronouns against the session's active order (see CONTEXT-002) or the customer's single/visible order. NOT implemented (audit only).

## CONV-006 — Repeated order-ID request; no reference to the customer's visible order

- **Phase:** 6A
- **Severity:** P2
- **Reproduction:** Ask five natural follow-up questions after Ask AI; read the responses.
- **Expected:** Clarification (when needed) is informative — e.g. names the order in view or explains which order is needed — and is not repeated verbatim five times.
- **Actual:** The identical "กรุณาระบุหมายเลขคำสั่งซื้อของคุณด้วยค่ะ" is served five times consecutively (turns 1–5), then twice more in the explicit-ID chat, even though the customer is looking at ORD-1034 on screen and opened chat from that card.
- **Evidence:** DOM message log + `logs/experiment.jsonl` 14:57–14:59 (requires_clarification true, identical response text).
- **Suspected layer:** clarification_handler copy / intent-entity fallback messaging.
- **Suggested fix direction:** Vary/contextualize clarification copy and reference the customer's visible order; do not loop the same request. NOT implemented (audit only).

## ORDER-003 — System cannot answer purchased-items or total questions despite order evidence

- **Phase:** 6A
- **Severity:** P2
- **Reproduction:** Send "What is the total?" / "ฉันซื้อสินค้าอะไรบ้าง" / "What did I buy?" with ORD-1034 visible and its full evidence already served in the same chat.
- **Expected:** Total ฿1,980 and item list (Aura Wireless Headphones ×1, Linen Everyday Blouse ×1) answered from order evidence.
- **Actual:** All three hit clarification_handler with routing_reason "No matching intent pattern found" — no TOTAL or ITEMS intent pattern exists, so the questions are unanswerable even when the order ID is present in the conversation (E3 failed right after a successful explicit-ID turn).
- **Evidence:** `logs/experiment.jsonl` 2026-08-03T14:57:50 (total), 14:58:00 (items), 14:59:37 ("What did I buy?").
- **Suspected layer:** Router intent patterns / transaction_tracker coverage (no total/items handlers).
- **Suggested fix direction:** Add TOTAL_QUERY and ORDER_ITEMS (or PURCHASED_ITEMS) intent patterns + handlers that answer from the order record's items and total_amount. NOT implemented (audit only).

## META-001 — Research details show stale/misleading intent label for unrecognized input

- **Phase:** 6A
- **Severity:** P3
- **Reproduction:** Send an unrecognized message ("What is the total?", "What did I buy?"); read the response `intent` field / research details.
- **Expected:** intent reflects the actual classification (UNKNOWN) when no pattern matches.
- **Actual:** The response `intent` carries a stale label — "What is the total?" → SHIPMENT_STATUS, "What did I buy?" → PAYMENT_STATUS — while routing_reason correctly says "No matching intent pattern found" (routing_confidence 0.3). Research details would show the wrong intent.
- **Evidence:** `logs/experiment.jsonl` 2026-08-03T14:57:50, 14:58:00, 14:59:37.
- **Suspected layer:** Server response serializer / router intent default (retains previous or default intent on no-match).
- **Suggested fix direction:** Set intent to UNKNOWN (or the actual fallback label) whenever no pattern matches. NOT implemented (audit only).

---

# PHASE 6B ISSUE LOG (added 2026-08-03)

## ROUTE-001 — Wrong/missing handler for exchange and cancellation

- **Phase:** 6B (conversations 1–3)
- **Severity:** P1
- **Reproduction:** Send "I want to change the colour" (fresh chat) — routed to clarification_handler; send "Actually, cancel it instead" during a refund workflow — routed to OUT_OF_SCOPE/simulated_human_review.
- **Expected:** Exchange intent recognized and handled (exchange_policy.md exists); cancellation intent handled with guidance.
- **Actual:** No EXCHANGE intent pattern exists at all — X1 ("I want to change the colour") → UNKNOWN/clarification_handler; X3/X4 (black, red) → clarification; cancellation → OUT_OF_SCOPE ("forwarded to staff") or folded into RETURN_REFUND. The normal exchange journey cannot be completed (every step of conversation 2 failed); cancellation has no automated path.
- **Evidence:** `logs/experiment.jsonl` 2026-08-03T18:03:16 … 18:04:38 (session demo-c31e9c94), 18:01:49 (demo-fbd2007b); screenshots 14–16.
- **Suspected layer:** Router intent patterns (no EXCHANGE/CANCELLATION pattern) / agent registry (exchange_policy.md never routed).
- **Suggested fix direction:** Add EXCHANGE and CANCELLATION intent patterns + handlers wired to exchange_policy.md and a cancellation policy; keep OUT_OF_SCOPE for genuinely out-of-scope requests. NOT implemented (audit only).

## CONTEXT-004 — Refund workflow does not preserve active order beyond the workflow

- **Phase:** 6B (sessions demo-fbd2007b, demo-4ef9aa15)
- **Severity:** P2
- **Reproduction:** Refund workflow for ORD-1034 (T1–T3), then ask a shipment/payment question in the same session ("Has it already been shipped?", "Has it been paid?").
- **Expected:** The workflow's ORD-1034 becomes the session's active order; status handlers answer without re-entering the ID.
- **Actual:** The refund workflow holds ORD-1034 internally (T3 evidence) but never promotes it to a session-level active_order_id: S3 (payment) and R5 (shipment) were refused with "กรุณาระบุหมายเลขคำสั่งซื้อของคุณด้วยค่ะ"; R5's context was also cleared by the T4 OUT_OF_SCOPE detour.
- **Evidence:** `logs/experiment.jsonl` 2026-08-03T18:02:27 (R5), 18:05:42 (S3).
- **Suspected layer:** Session state (active order not shared across handlers); workflow state scoped to store_policy_evaluator only.
- **Suggested fix direction:** Promote the collected order_id to session-level active order state shared by all handlers (see CONTEXT-002). NOT implemented (audit only).

## CONTEXT-005 — Exchange workflow does not preserve order context

- **Phase:** 6B (session demo-c31e9c94)
- **Severity:** P2
- **Reproduction:** "I want to change the colour" → "ORD-1011" → "black" → "What about red instead?".
- **Expected:** ORD-1011 retained across the exchange turns; colour slot updated black → red.
- **Actual:** No exchange workflow exists; ORD-1011 provided in X2 was answered as ORDER_STATUS and never retained — X3/X4 (black, red) hit clarification_handler with no order context.
- **Evidence:** `logs/experiment.jsonl` 2026-08-03T18:03:36 … 18:04:17.
- **Suspected layer:** No exchange workflow state (consequence of ROUTE-001).
- **Suggested fix direction:** Implement the exchange workflow first (ROUTE-001), then persist its order and colour slots. NOT implemented (audit only).

## CONV-007 — Explicit cancellation does not override refund/exchange

- **Phase:** 6B (sessions demo-fbd2007b, demo-c31e9c94)
- **Severity:** P2
- **Reproduction:** During a refund workflow send "Actually, cancel it instead"; during an exchange attempt send "I don't want to exchange it anymore, cancel it".
- **Expected:** Explicit cancellation intent overrides the previous workflow, reuses the stored order, and provides policy-grounded cancellation guidance.
- **Actual:** R4 → OUT_OF_SCOPE/simulated_human_review ("คำขอของคุณถูกบันทึกและจะถูกส่งต่อไปยังเจ้าหน้าที่...") with no cancellation guidance, no order context, no policy; X5 → matched RETURN_REFUND (DeepSeek) and asked again for the order number and product details, ignoring ORD-1011 already given.
- **Evidence:** `logs/experiment.jsonl` 2026-08-03T18:01:49, 18:04:38.
- **Suspected layer:** Intent patterns (no CANCELLATION intent; "cancel" folds into RETURN_REFUND or OUT_OF_SCOPE).
- **Suggested fix direction:** Add a distinct CANCELLATION intent with higher priority than RETURN_REFUND, and route it to a cancellation handler that reuses the active order. NOT implemented (audit only).

## CONV-008 — Bare order ID hijacks the route into ORDER_STATUS when no workflow is pending

- **Phase:** 6B (session demo-c31e9c94)
- **Severity:** P2
- **Reproduction:** "I want to change the colour" (UNKNOWN) → reply "ORD-1011".
- **Expected:** The order ID is absorbed by the exchange workflow or asked-for in context of the exchange.
- **Actual:** The bare ID matched the ORDER_STATUS pattern (confidence 1.0) and returned a full ORD-1011 status dump — the exchange attempt was silently converted into a status query. (Contrast: with a pending refund workflow the bare ID IS absorbed — R2 stayed RETURN_REFUND — so the hijack happens only when no workflow is pending.)
- **Evidence:** `logs/experiment.jsonl` 2026-08-03T18:03:36 (X2) vs 17:59:16 (R2).
- **Suspected layer:** Router order-ID regex → ORDER_STATUS priority before workflow slot-filling.
- **Suggested fix direction:** Let pending workflow slot-filling take precedence over bare-ID ORDER_STATUS matching; ask "which order do you want to change?" instead of answering status. NOT implemented (audit only).

## CONV-009 — Desired colour is not retained

- **Phase:** 6B (session demo-c31e9c94)
- **Severity:** P2
- **Reproduction:** "I want to change the colour" → "ORD-1011" → "black" → "What about red instead?".
- **Expected:** desired colour captured (black), then updated (red) while ORD-1011 stays active.
- **Actual:** "black" and "What about red instead?" both routed to clarification_handler (UNKNOWN, 0.3) — no colour slot exists. (Refund reason IS captured and used — T3 reply referenced "เป็นการเปลี่ยนใจคืนสินค้า" — so the reason half of CONV-009 is not confirmed.)
- **Evidence:** `logs/experiment.jsonl` 2026-08-03T18:04:01, 18:04:17.
- **Suspected layer:** No exchange workflow slot state (consequence of ROUTE-001).
- **Suggested fix direction:** Add a colour slot to the exchange workflow and recognize bare colour words as slot values while the workflow is pending. NOT implemented (audit only).

## POLICY-001 — Policy guidance missing for cancellation, unused for exchange

- **Phase:** 6B
- **Severity:** P2
- **Reproduction:** Request a cancellation (R4) or an exchange (X1–X5); inspect policy_sources / retrieved chunks.
- **Expected:** Cancellation and exchange answers cite the relevant store policy.
- **Actual:** Cancellation: no cancellation_policy.md exists in data/policies/; the OUT_OF_SCOPE reply carried policy_sources [] and no grounding. Exchange: exchange_policy.md exists but was never retrieved across X1–X5 (retrieved_chunk_count 0). Refund was properly grounded (refund_policy.md, 3 chunks, grounding_validation_passed true).
- **Evidence:** `data/policies/` listing; `logs/experiment.jsonl` 2026-08-03T18:01:49 (R4), 18:03:16–18:04:38 (X1–X5), 18:00:56 (T3, grounded).
- **Suspected layer:** Policy registry/retrieval wiring — no cancellation policy source; exchange policy not attached to any handler.
- **Suggested fix direction:** Add a cancellation policy document and wire exchange_policy.md to the exchange handler; verify retrieval on both flows. NOT implemented (audit only).

---

# PHASE 7A ISSUE LOG (added 2026-08-03)

## CONV-010 — Already supplied order information is repeatedly requested

- **Phase:** 7A (session demo-c2092222, continuous turns 1–8)
- **Severity:** P2
- **Reproduction:** Refund workflow → supply ORD-1034 (turn 4) → then send reason / colour-change / colour / shipment messages (turns 5–8).
- **Expected:** Once ORD-1034 is supplied and becomes the workflow's active order, later turns answer without re-requesting the order number.
- **Actual:** After the workflow completed at turn 4, turns 5–8 ALL returned the identical "กรุณาระบุหมายเลขคำสั่งซื้อของคุณด้วยค่ะ" — 4 consecutive order-ID requests in one session for an order already supplied. No session-level active order exists to reuse.
- **Evidence:** `logs/experiment.jsonl` 2026-08-03T18:41:08 (T5), 18:41:43 (T6), 18:42:05 (T7), 18:42:27 (T8); screenshot `data/customer_journey_audit/17_phase7a_verified_turns_1_8.png`.
- **Suspected layer:** Session state — no active_order_id carried across turns/handlers after a workflow completes (same root as CONTEXT-002/004).
- **Suggested fix direction:** Keep the resolved order as session-level active context and let handlers answer without re-requesting. NOT implemented (audit only).

## CONV-011 — Refund → exchange switch fails in a long conversation

- **Phase:** 7A (session demo-c2092222, turn 6)
- **Severity:** P2
- **Reproduction:** After a refund workflow, send "actually I want to change the colour instead".
- **Expected:** Explicit colour-change intent overrides the refund, reuses ORD-1034, and asks only for the desired colour.
- **Actual:** Routed to clarification_handler (UNKNOWN, "No matching intent pattern found") → "กรุณาระบุหมายเลขคำสั่งซื้อของคุณด้วยค่ะ". No exchange/colour-change intent pattern exists (exchange_policy.md never routed) — same root cause as ROUTE-001, now confirmed in a long continuous conversation.
- **Evidence:** `logs/experiment.jsonl` 2026-08-03T18:41:43; screenshot `data/customer_journey_audit/17_phase7a_verified_turns_1_8.png`.
- **Suspected layer:** Router intent patterns — no EXCHANGE/COLOUR_CHANGE pattern (see ROUTE-001).
- **Suggested fix direction:** Add EXCHANGE/COLOUR_CHANGE intent handling and allow it to supersede a completed/ongoing refund workflow. NOT implemented (audit only).

---

# AUDIT STATE — Part 1 complete (carried in)

- **Server:** port 8000 healthy (restarted at Part 2A start; PID 8252).
- **DB:** backed up to `data/orders_before_customer_audit.db` (33 orders, latest ORD-1033). No order created by the audit yet.
- **Browser:** clean session at ~100–101% zoom; localStorage cart key `siamcart.cart.v1` cleared; console clean; no horizontal overflow.
- **Target product:** PROD-004 Aura Wireless Headphones, ฿1,290 (orig ฿1,890), stock 38, Electronics.
- **Checkout DOM ids:** coName/coEmail/coPhone/coAddress/coDistrict/coProvince/coPostal; payment radios `COD | Bank Transfer | Card`; chat ids chatInput/sendBtn; message class `.msg`; cart key `siamcart.cart.v1`.
- **Screenshots:** `data/customer_journey_audit/01_storefront_home.png`.

---

<!-- APPENDED SECTIONS BELOW (Part 2A) -->

## Phase 2 (Part 2A) — Q7–Q11 Product-Context Lifecycle

### Q7–Q10 — completed (previous continuation session)

Executed live in the prior session per continuation state: order-intent override (ORD-1011 → ORDER_STATUS / transaction_tracker / Thai / payload carried PROD-004 while card visible), product question with context, context removal, reload persistence. Results consistent with Q11 findings below. (Not re-run — do not repeat earlier Phase 2 questions.)

### Q11 — Greeting while PROD-004 product context is active (completed 2026-08)

Setup: opened PROD-004 → "Ask AI About This Product" → context card visible: "Aura Wireless Headphones · PROD-004 · ฿1,290 · 38 in stock".

1. **"hello" with context active** — captured POST /api/chat body: `{"message":"hello","session_id":null,"product_id":"PROD-004"}`.
   - product_id included: YES (PROD-004)
   - intent: GREETING; handler: general_response
   - response language: Thai ("สวัสดีค่ะ ยินดีต้อนรับสู่ SiamCart …")
   - context card: still visible after the greeting.
2. **"สินค้านี้ราคาเท่าไร"** — captured body: `{"message":"สินค้านี้ราคาเท่าไร","session_id":"demo-d16640c1","product_id":"PROD-004"}`.
   - intent: PRODUCT_LOOKUP; handler: product_catalog_lookup; panel rdProductId=PROD-004
   - product lookup STILL USES PROD-004: "ราคาของ Aura Wireless Headphones (PROD-004) อยู่ที่ 1,290 บาทค่ะ" (Thai).

**CONV-004 final status: NOT REPRODUCED — CLOSED.** Card visible + product_id present on both messages. Card and payload render from the same `state.product` (chat.js:217-218); `#productCtxClose` → `setProductContext(null)` clears both (chat.js:169-170). The original suspicion was a false positive from comparing the research panel's `rdProductId` (server response, null for non-product intents) against the payload.

Context removal: clicked `#productCtxClose` → card hidden immediately. Then "สินค้านี้มีของไหม" → captured body `{"message":"สินค้านี้มีของไหม","session_id":"demo-d16640c1"}` — **no product_id sent**; intent UNKNOWN, handler clarification_handler, Thai. Reset chat (reload) → widget closed, card hidden/empty, `state.product=null`. UI + internal context both cleared — PASS.

Known P3 (carried, not new): without context, product questions get order-number clarification copy "กรุณาระบุหมายเลขคำสั่งซื้อของคุณด้วยค่ะ".

## Phase 3 (Part 2A) — Cart Experience

PENDING — executed live, results appended below immediately after run.

### Phase 3 — Cart Experience (completed 2026-08)

**Products tested:** PROD-004 Aura Wireless Headphones ฿1,290 (stock 38) + PROD-001 Linen Everyday Blouse ฿690 (stock 24), qty 1 each.

**Baseline totals:** subtotal ฿1,980 · shipping ฿49 (subtotal < ฿2,000 threshold) · grand total ฿2,029.

| # | Check | Result |
|---|---|---|
| 1 | Restore PROD-001 qty to 1 | PASS — cart rebuilt to `{"PROD-004":1,"PROD-001":1}` (browser context reset between sessions wiped localStorage; re-added both products, no order created) |
| 2 | Product IDs / names / prices / qty | PASS — PROD-004 Aura Wireless Headphones, Electronics · PROD-004, ฿1,290 × 1; PROD-001 Linen Everyday Blouse, Fashion · PROD-001, ฿690 × 1; both images loaded (naturalWidth > 0) |
| 3 | Subtotal | PASS — ฿1,980 |
| 4 | Shipping | PASS — ฿49 under ฿2,000; "Free" at/above ฿2,000 (observed ฿3,270 / ฿2,670 in qty tests) |
| 5 | Grand total | PASS — ฿2,029 |
| 6 | Refresh persistence | **FAIL → CART-002** — localStorage kept `{"PROD-004":1,"PROD-001":1}` but UI rendered badge 0 + empty drawer after reload |
| 7 | Drawer close/open | PASS — `#cartClose` hides, `#cartToggle` reopens |
| 8 | Continue Shopping | PASS — `#continueShoppingBtn` closes the drawer |
| 9 | Proceed to Checkout, no submit | PASS — modal lists both items (1 × ฿1,290 + 1 × ฿690), subtotal ฿1,980 / shipping ฿49 / total ฿2,029; submit disabled "Select a Payment Method", no payment selected |
| 10 | Close checkout, cart remains | PASS — badge 2, drawer shows both items, ls intact |
| 11 | Buy Now while cart has items | PASS — checkout opens, normal cart untouched |
| 12 | Buy Now summary = Buy Now product only | PASS — only "SiamPhone X5 1 × ฿12,990 ฿12,990"; note "2 more items kept in your cart"; subtotal ฿12,990 / shipping Free / total ฿12,990 |
| 13 | Normal cart unchanged | PASS — badge 2, ls `{"PROD-004":1,"PROD-001":1}` |
| 14 | Close Buy Now, original cart restored | PASS — badge 2, both items qty 1, totals ฿1,980/฿49/฿2,029, ls intact |
| 15 | Shipping in POST /api/orders payload | **NOT SENT** → CART-003 — payload (store.js:848-858) has customer + payment_method + items only; no shipping/subtotal/total |
| 16 | Frontend/backend total mismatch | **LIKELY** → CART-003 — UI total ฿2,029 vs backend `total_amount` = line sums only (app/db/store.py:361) = ฿1,980 for this cart; no order created |
| 17 | Drawer overflow / scrolling | PASS — 2 items: no overflow (scrollH 318 = clientH 318); temporary 3rd item: scrollH 439 > clientH 318, `overflow-y:auto`, scrollTop moves; item removed, cart restored to exactly 2 products |

**Screenshots:** `data/customer_journey_audit/04_cart_two_products.png` (drawer with both products + totals), `data/customer_journey_audit/05_buy_now_preserves_cart.png` (Buy Now checkout: SiamPhone X5 only + "2 more items kept in your cart").

## Audit Checkpoint — Phase 3 Complete

- **Phase 2:** complete (Q1–Q11). CONV-004 final status: **NOT REPRODUCED — CLOSED** (card visible + product_id present after greeting; card and payload share `state.product`).
- **Phase 3:** complete.
- **Products tested:** PROD-004 Aura Wireless Headphones (฿1,290) + PROD-001 Linen Everyday Blouse (฿690), qty 1 each.
- **Cart totals:** subtotal ฿1,980 · shipping ฿49 · grand total ฿2,029 (UI).
- **CART-001 (P2):** double-click Add to Cart double-adds (PROD-001 1 → 3). Logged; fix NOT implemented.
- **Other issues found this phase:** CART-002 (P2) — cart not restored from localStorage on refresh (loadCart runs before products fetch); CART-003 (P2, suspected) — shipping never sent to backend, backend total = line sums only → UI/backend total mismatch for carts < ฿2,000.
- **Cumulative counts:** P0 = 0 · P1 = 0 · P2 = 4 (ENV-001, CART-001, CART-002, CART-003) · P3 = 5 (CONV-001, CONV-002, CONV-003, UI-001, UI-002).
- **Continuation point:** Phase 4 Checkout and Payment — create exactly one real audit order; verify backend `total_amount` vs UI total (confirms CART-003); payment-method flows (COD / Bank Transfer / Card); demo payment button behavior; confirmation screen. Do NOT begin until the next session.

## Phase 4 (Part 2A) — Checkout and Payment (completed 2026-08-03)

**Audit order:** ORD-1034 (created exactly once). Products: PROD-004 Aura Wireless Headphones ฿1,290 × 1 + PROD-001 Linen Everyday Blouse ฿690 × 1. Cart rebuilt via modal re-add (CART-002 workaround — fresh session had empty cart; no other product added).

### Initial checkout state

No payment method selected · submit button disabled · label "Select a Payment Method" · focus on `#coName` (auto). Summary: 2 items, Subtotal ฿1,980 · Shipping ฿49 · Total ฿2,029. PASS.

### Checkout form validation (10 checks, zero orders created)

| # | Check | Result |
|---|---|---|
| 1 | Empty Full name | PASS — inline "Please enter your full name.", `.invalid` class applied, toast "Please fix the highlighted fields" |
| 2 | Invalid email (not-an-email) | PASS — "Please enter a valid email address." |
| 3 | Missing phone | PASS — "Please enter a valid phone number." |
| 4 | Missing address | PASS — "Please enter your delivery address." |
| 5 | Missing district | PASS — "Please enter your district." |
| 6 | Missing province | PASS — "Please enter your province." |
| 7 | Missing postal code | PASS — "Postal code must be 5 digits." (regex `^\d{5}$`; field also `maxlength=5`) |
| 8 | No payment method | PASS — button disabled + "Select a Payment Method"; click is a no-op; no POST; no order |
| 9 | Correct each field | PASS — each corrected field's error clears on the next validation pass (verified sequentially for all 7 fields) |
| 10 | Invalid submission sends no POST | PASS — fetch-wrapped evidence: **0** requests to /api/orders across all invalid attempts (log node empty until the real submit) |

- Inline errors: YES — understandable English messages under each field (`.co-error[data-for=…]`), plus a toast. `novalidate` form, no native browser bubbles.
- Focus moves to first invalid field: **NO** → CHECKOUT-001 (P3). Active element stayed on `#coPostal` while `#coName` was the first error.
- Valid fields retain values: YES — untouched fields kept their values after every failed submit.
- Repeated clicks prevented while invalid: YES — 3 rapid clicks on submit re-ran validation only; zero POSTs.

### Payment option audit (inspected without submitting)

| Option | Radio copy | Button label after selecting | Card/account fields | Verdict |
|---|---|---|---|---|
| Cash on Delivery | "Pay when your order arrives" | "Place Cash on Delivery Order" | none | PASS — payment collected at delivery; no "payment successful" claim; no demo-pay button before order creation (confirmation shows "No payment has been collected. You will pay when the order is delivered.") |
| Bank Transfer | "Transfer to our demo bank account" | "Place Order & Continue to Demo Payment" | none (0 account fields) | PASS — no real bank credentials requested; wording says "demo bank account" (no account details given — acceptable for a research prototype, recorded verbatim); research simulation notice "This simulates payment for the research prototype. No real money will be charged." appears on confirmation before demo payment |
| Demo Card Payment | "Simulated payment — no card number required" | "Place Order & Continue to Demo Payment" | none (form has exactly 7 data inputs + 3 radios; no card/expiry/CVV) | PASS — no card fields, no real-charge claim |

### Create the ONE audit order (Bank Transfer)

Pre-submit snapshot: cart `{"PROD-004":1,"PROD-001":1}` · fields per spec (Customer Journey Tester / journey.tester@example.com / 0811112222 / 88 Customer Journey Road / Huai Khwang / Bangkok / 10310) · payment Bank Transfer · button enabled "Place Order & Continue to Demo Payment" · UI Subtotal ฿1,980 · Shipping ฿49 · Grand total ฿2,029 · stock PROD-004=38, PROD-001=24.

- **Request payload (captured):** `{"customer_name":"Customer Journey Tester","customer_email":"journey.tester@example.com","customer_phone":"0811112222","shipping_address":"88 Customer Journey Road","district":"Huai Khwang","province":"Bangkok","postal_code":"10310","payment_method":"Bank Transfer","items":[{"product_id":"PROD-004","quantity":1},{"product_id":"PROD-001","quantity":1}]}` — **no shipping/subtotal/total** (CART-003).
- **HTTP status:** 201 · **latency:** 322 ms.
- **Response JSON (key fields):** `order_id: "ORD-1034"`, `total_amount: 1980`, `payment_method: "Bank Transfer"`, `payment_status: "pending"`, `order_status: "processing"`, `shipment_status: "not_shipped"`, `paid_at: null`, `items: [{item_id:29, product_id:"PROD-004", quantity:1, unit_price:1290, line_total:1290}, {item_id:30, product_id:"PROD-001", quantity:1, unit_price:690, line_total:690}]`.
- **Submit disabled while pending:** YES — immediately after click: `disabled=true`, label "Creating Order...".
- **Second click prevented:** YES — second programmatic click while pending was a no-op (disabled + `state.submittingOrder` guard, store.js:834); exactly ONE order persisted (DB count 33 → 34; only ORD-1034 added; the repeated `|/api/orders` strings in the capture log were a harness re-evaluation artifact, not real requests).
- **Stock after:** PROD-004 38 → **37**; PROD-001 24 → **23** (each decremented exactly 1).
- **Cart after success:** cleared — badge 0/hidden, `localStorage.siamcart.cart.v1` = `{}` (cart clears only on success — PASS; validation errors never cleared it: it stayed `{"PROD-004":1,"PROD-001":1}` throughout all invalid submits).

### CART-003 confirmation — Case B (CONFIRMED, upgraded to P1)

| Source | Value |
|---|---|
| Frontend subtotal | ฿1,980 |
| Frontend shipping | ฿49 |
| Frontend grand total | ฿2,029 |
| Backend `total_amount` (POST response) | 1,980 |
| Backend `total_amount` (GET /api/orders/ORD-1034) | 1980.0 |
| DB row ORD-1034 `total_amount` | 1980.0 |

**Classification: B** — backend total ฿1,980 while frontend showed ฿2,029. CART-003 CONFIRMED with a real order; severity **upgraded P2 → P1 Major** (customer sees a different amount than the persisted order). The confirmation screen displays no total at all, so the mismatch is first visible in My Orders / order details (Phase 5).

### Order confirmation audit (ORD-1034)

- Real ORD-XXXX (not DEMO-XXXX): PASS — ORD-1034.
- Title "Order created successfully": PASS (`#confirmTitle`); does NOT say payment completed: PASS.
- Payment = Pending · Order = Processing · Shipment = Not Shipped: PASS (confirm status block + API).
- Items and quantities correct: **FAIL (display)** — `#confirmOrderItems` hidden/empty (ORDER-001); API items are correct (PROD-004 ×1 ฿1,290, PROD-001 ×1 ฿690).
- Customer name correct: PASS in API (`customer_name: "Customer Journey Tester"`) but NOT displayed on the confirmation screen (ORDER-001).
- Payment method Bank Transfer: PASS (displayed + API).
- Confirm Demo Payment button appears: PASS.
- Research-only payment notice: PASS — "This simulates payment for the research prototype. No real money will be charged."
- Cart clears only after success: PASS (validation failures kept the cart; success cleared it).
- Confirmation total vs frontend checkout total / backend response total: the confirmation screen shows **no total** (ORDER-001); backend total 1,980 ≠ frontend checkout total 2,029 (CART-003).

### Demo payment audit (clicked exactly once)

- Request: POST /api/orders/ORD-1034/demo-payment, body `{"confirm_demo_payment":true}` → **HTTP 200, 315 ms**, exactly ONE request.
- Payment status: pending → **paid** · `paid_at`: **2026-08-03T10:56:10** (populated; shown on screen as "Aug 3, 2026, 10:56 AM").
- Order status: processing → processing (unchanged) · Shipment: not_shipped → not_shipped (unchanged). PASS.
- Button behavior: immediately disabled with "Confirming..." on click; after success the button is REMOVED (re-render replaces it with "Payment received for this research prototype." note). A second click attempt during flight was a no-op; no second request in the log.
- Page clearly says simulated: PASS — "Payment received for this research prototype." + the earlier simulation note. No wording implies real money was charged.

### Error and duplicate safety

- Checkout submit cannot fire twice: PASS (`state.submittingOrder` + disabled; 1 order).
- Demo payment cannot fire twice: PASS (disabled during flight, then button removed; 1 request).
- Errors do not clear the cart: PASS (cart intact after all invalid submits).
- Successful order clears the cart: PASS.
- Browser console: **0 JS errors, 0 messages** for the whole Phase 4 session.
- No HTTP 500: PASS — all captured requests 201/200; GETs 200.

**Screenshots:** `data/customer_journey_audit/06_checkout_validation.png` (name-field error + invalid class), `data/customer_journey_audit/07_order_confirmation.png` (ORD-1034, Pending/Processing/Not Shipped, demo-pay button, no items list), `data/customer_journey_audit/08_demo_payment_paid.png` (Paid + paid_at, button gone, research notice).

## Audit Checkpoint — Phase 4 Complete

- **Audit order ID:** ORD-1034 (created exactly once, 2026-08-03).
- **Products and quantities:** PROD-004 Aura Wireless Headphones ฿1,290 × 1; PROD-001 Linen Everyday Blouse ฿690 × 1.
- **UI subtotal:** ฿1,980 · **UI shipping:** ฿49 · **UI grand total:** ฿2,029.
- **Backend total:** ฿1,980 (POST response, GET API, and DB row all 1980).
- **CART-003 final status:** **CONFIRMED — Case B — upgraded to P1 Major** (UI ฿2,029 vs persisted ฿1,980; ฿49 shipping never reaches the backend).
- **Payment transition:** Pending → Paid (POST /api/orders/ORD-1034/demo-payment, 200, 315 ms; paid_at 2026-08-03T10:56:10); Order stays Processing; Shipment stays Not Shipped.
- **Stock before/after:** PROD-004 38 → 37; PROD-001 24 → 23.
- **Issues found this phase:** CART-003 upgraded P2→P1 (confirmed); ORDER-001 (P2, confirmation items missing); CHECKOUT-001 (P3, no focus to first invalid field); CHECKOUT-002 (P3, errors persist until next submit).
- **Cumulative counts:** **P0 = 0 · P1 = 1 (CART-003) · P2 = 4 (ENV-001, CART-001, CART-002, ORDER-001) · P3 = 7 (CONV-001, CONV-002, CONV-003, UI-001, UI-002, CHECKOUT-001, CHECKOUT-002).**
- **Continuation point:** Phase 5 My Orders and Order Details — open My Orders (Orders button / "View My Orders" on confirmation), verify ORD-1034 appears with total ฿1,980 (CART-003 visible to the customer), statuses (paid/processing/not_shipped), items; then order details view. Do NOT create another order. Do NOT begin Phase 5 in this session.

## Phase 5 (Part 2A) — My Orders (completed 2026-08-03)

**Audit order:** ORD-1034 (created in Phase 4; no new order created in Phase 5).

### Phase 5A — Automatic lookup

- Headless browser localStorage had been lost between audit sessions.
- Re-seeded only the values previously stored by the real Phase 4 checkout:
  - `siamcart.customer.email` = `journey.tester@example.com`
  - `siamcart.customer.orders.v1` = `["ORD-1034"]`
- `/orders` automatically selected **Email** as the search type.
- Search field contained `journey.tester@example.com`.
- `GET /api/orders?email=journey.tester%40example.com` returned **HTTP 200**.
- Exactly **ORD-1034** appeared (no other orders).
- Result text: **"1 order for journey.tester@example.com"**.
- SQLite remained the source of truth (list data served from the DB via the API).
- Browser console: **zero errors**.

### Phase 5B — Search modes

All recorded as **PASS**:

- email lookup;
- phone lookup;
- uppercase order ID;
- lowercase order ID;
- trimmed email whitespace;
- unknown email empty state;
- unknown order empty state;
- empty-search validation;
- latest 20 demo orders, newest first;
- loading message and spinner;
- duplicate Search clicks produced one request only;
- invalid search produced no request;
- Retry control exists;
- no raw JSON displayed.

### Phase 5C — ORD-1034 card

- Order ID: **ORD-1034**;
- Date: **Aug 3, 2026, 10:54 AM**;
- Customer: **Customer Journey Tester**;
- Items: **Aura Wireless Headphones ×1**; **Linen Everyday Blouse ×1**;
- Payment method: **Bank Transfer**;
- Payment status: **Paid**;
- Order status: **Processing**;
- Shipment status: **Not Shipped**;
- Card total: **฿1,980**;
- Product image loaded (naturalWidth > 0);
- **View Details** and **Ask AI** actions present.

**Price-discrepancy statement:** Checkout previously displayed **฿2,029** (subtotal ฿1,980 + shipping ฿49), while My Orders displays **฿1,980**. **CART-003 remains P1** (CONFIRMED, visible to the customer in My Orders).

### Phase 5D — Order details

Recorded as **PASS**:

- order ID / date / status badges;
- customer name / email / phone;
- complete delivery address;
- Bank Transfer;
- Paid;
- paid_at;
- Processing;
- Not Shipped;
- both order items and correct item calculations;
- Confirm Demo Payment absent (order already paid);
- X / Escape / backdrop close;
- internal scrolling;
- body scroll lock;
- no page-level overflow.

**TOTAL-001 (P2):**

- Items sum to **฿1,980**.
- Persisted `total_amount` is **฿1,980**.
- Modal displays:
  - Subtotal **฿1,931**
  - Shipping **฿49**
  - Total **฿1,980**
- The modal invents a shipping line by subtracting ฿49 from `total_amount`.
- This **masks CART-003** rather than explaining it.
- Severity: **P2**.
- Suspected layer: `orders.js` total rendering (`renderDetails` shippingFor/subtraction on `total_amount`).

**ORDER-002 (P3):**

- Detail API items do **not** contain `image_url`.
- Modal item thumbnails use the SVG fallback.
- List API includes image URLs.
- Severity: **P3**.

**UI-003 (P3):**

- Focus remains on hidden `#odClose` after closing the modal.
- Severity: **P3**.

### Phase 5E — Order context

**CONTEXT-001 (P2):**

- Ask AI opens chat.
- Exactly **one visible message** is sent: **"Where is order ORD-1034?"**
- Transaction Tracker gives the correct Thai deterministic answer (paid 2026-08-03T10:56:10; processing; not shipped — order ID parsed server-side from the message text).
- **No order-context card exists.**
- **No Remove order context action exists.**
- `POST /api/chat` payload contains **only** `message`, `session_id` — no order_id field.
- **No order_id is stored or transmitted.**
- UI is using **visible text prefill** rather than persistent order context.
- Severity: **P2**.
- Suspected layer: `orders.js` / `chat.js` / chat request schema.

**Screenshot (captured live this closeout):** `data/customer_journey_audit/11_order_context_missing_ORD-1034.png` — chat open, welcome + "Where is order ORD-1034?" + Thai reply; verified via DOM that `#chatOrderContext` / `.chat-order-context` / `#orderCtxClose` / `[data-order-context]` / `.chat-context` are all absent and no remove/clear context button exists.

---

## Audit Checkpoint — Phase 5 Complete

- **Audit order:** ORD-1034
- **My Orders total:** ฿1,980
- **Checkout total:** ฿2,029
- **Difference:** ฿49
- **Search modes:** passed
- **Details modal:** functionally passed, with TOTAL-001 / ORDER-002 / UI-003
- **Order-context setup:** failed, CONTEXT-001
- **Severity counts:** P0 = 0 · P1 = 1 (CART-003) · P2 = 6 (ENV-001, CART-001, CART-002, ORDER-001, TOTAL-001, CONTEXT-001) · P3 = 9 (CONV-001, CONV-002, CONV-003, UI-001, UI-002, CHECKOUT-001, CHECKOUT-002, ORDER-002, UI-003)
- **Completed phases:** 1–5
- **Continuation point:** Phase 6 Order-Aware AI Conversation Audit

---

## Audit Checkpoint — Part 2A Complete

Part 2A complete — Phases 2 (Q7–Q11), 3, 4 and 5 documented above; Phase 5 closeout checkpoint (above) written at end of Part 2A. Do NOT begin Phase 6 until the next session.

---

## Phase 6A — Order Context and Pronoun Resolution (completed 2026-08-03)

**Audit order:** ORD-1034 (no new order created). **Mode:** audit only — CONTEXT-001 was NOT fixed; its customer impact is what this phase documents. Server healthy on port 8000; single browser session.

**Method notes:** Client payloads are reconstructed from `app/static/js/chat.js` (`_doSend`: payload = `{message, session_id}` plus `product_id` only when product context is active — never an `order_id` field) plus server-side session values; all intent/handler/order_id/latency/source/language/response evidence is taken from the server's own per-request log `logs/experiment.jsonl` (one line per POST /api/chat). The research-details panel is only rendered on the storefront template, so the server log is the authoritative metadata source for the orders page.

### 6A.1 — Initial Ask AI turn from the ORD-1034 card

Ask AI on the ORD-1034 card (fresh page load, chat state clean):

- Visible prefilled/sent message: **"Where is order ORD-1034?"** (sent automatically by `askAiAboutOrder` → `Chat.open()` + `Chat.sendText(...)`).
- Request payload: `{"message": "Where is order ORD-1034?", "session_id": null}` — **no `order_id`**, no `product_id`.
- session_id: null on the wire (fresh state); server assigned **demo-012141b6** and the client echoed it from turn 2 onward.
- order_id present in payload: **NO**.
- Order-context card: **NO** (`#chatOrderContext` / `.chat-order-context` / `#orderCtxClose` / `[data-order-context]` / `.chat-context` all absent; product-context card hidden).
- Initial assistant response (Thai): "คำสั่งซื้อ ORD-1034 ได้รับชำระเงินแล้ว (ชำระเมื่อ 2026-08-03T10:56:10) ขณะนี้คำสั่งซื้ออยู่ระหว่างดำเนินการ และยังไม่ได้จัดส่ง ค่ะ"
- Intent: **ORDER_STATUS** · Handler: **transaction_tracker** · Response source: **deterministic** · Language: **th** (input detected: en) · Latency: **2.01 ms** · routing_confidence 1.0 · requires_clarification: false.
- Facts match SQLite (via GET /api/orders): paid, processing, not_shipped, paid_at 2026-08-03T10:56:10, products "Aura Wireless Headphones, Linen Everyday Blouse".

**Verdict:** the turn succeeds only because the order ID is literally in the message text. The server parses `ORD-1034` from the visible string; nothing is retained in session state (confirmed below).

### 6A.2 — Five-turn follow-up conversation (same session demo-012141b6)

| Turn | User message | Intent | Handler | Order ID | Actual | Expected | Result |
|---|---|---|---|---|---|---|---|
| 1 | คำสั่งซื้อนี้ชำระเงินแล้วหรือยัง | PAYMENT_STATUS | transaction_tracker | None | "กรุณาระบุหมายเลขคำสั่งซื้อของคุณด้วยค่ะ" | Paid answer for ORD-1034 | FAIL |
| 2 | ส่งของหรือยัง | SHIPMENT_STATUS | transaction_tracker | None | "กรุณาระบุหมายเลขคำสั่งซื้อของคุณด้วยค่ะ" | Not Shipped answer | FAIL |
| 3 | What is the total? | SHIPMENT_STATUS (mislabel) | clarification_handler | None | "กรุณาระบุหมายเลขคำสั่งซื้อของคุณด้วยค่ะ" | ฿1,980 | FAIL |
| 4 | ฉันซื้อสินค้าอะไรบ้าง | SHIPMENT_STATUS (mislabel) | clarification_handler | None | "กรุณาระบุหมายเลขคำสั่งซื้อของคุณด้วยค่ะ" | both products | FAIL |
| 5 | แล้วสถานะตอนนี้ล่ะ | ORDER_STATUS | transaction_tracker | None | "กรุณาระบุหมายเลขคำสั่งซื้อของคุณด้วยค่ะ" | reuse ORD-1034 status | FAIL |

Per-turn payloads (client → server, chat.js shape): `{message: "<user message>", session_id: "demo-012141b6"}` for turns 1–5 (turn 1 used the session_id returned by the initial turn). **No turn carried `order_id` or `product_id`.** Server routing reasons: turns 1, 2, 5 — "Matched {PAYMENT/SHIPMENT/ORDER}_STATUS pattern but missing required entity: order_id"; turns 3, 4 — "No matching intent pattern found" (no TOTAL/ITEMS intent exists at all). All five responses: deterministic, Thai, `requires_clarification: true`, latency 0–1 ms, routing_confidence 1.0 (turns 1/2/5) and 0.3 (turns 3/4).

**Customer impact:** every natural follow-up question fails; the assistant repeats the identical order-ID request five times. The customer must re-type the full order number into every message (which works only for the three status intents — total and items are unanswerable even so).

### 6A.3 — Explicit order ID, clean chat (session demo-a7ffd896)

New chat (page reload — chat state reset, `session_id: null`, empty log).

| Turn | User message | Intent | Handler | Order ID | Actual | Expected | Result |
|---|---|---|---|---|---|---|---|
| E1 | Where is order ORD-1034? | ORDER_STATUS | transaction_tracker | ORD-1034 | Thai status answer (2nd message of chat) | same | PASS |
| E2 | Has it been paid? | PAYMENT_STATUS | transaction_tracker | None | "กรุณาระบุหมายเลขคำสั่งซื้อของคุณด้วยค่ะ" | Paid answer | FAIL |
| E3 | What did I buy? | PAYMENT_STATUS (mislabel) | clarification_handler | None | "กรุณาระบุหมายเลขคำสั่งซื้อของคุณด้วยค่ะ" | both products | FAIL |

Payloads: E1 `{"message": "Where is order ORD-1034?", "session_id": null}`; E2/E3 `{"message": "...", "session_id": "demo-a7ffd896"}` — no order_id. E1 routed ORDER_STATUS (3.0 ms, deterministic, th); E2 matched PAYMENT_STATUS but "missing required entity: order_id"; E3 "No matching intent pattern found".

**Verdict:** an explicit order ID in the first turn does **not** create reusable session context — the very next pronoun turn ("it") still asks for the order number.

### 6A.4 — Audit questions answered

- **Does the system remember ORD-1034 after the initial Ask AI message?** NO. Turn 1 still demanded order_id.
- **Does it rely only on the visible previous message?** YES. The order ID is only ever obtained by parsing it out of the current message text; the preceding turn's ID is not carried forward.
- **Is order context stored in session state?** NO. chat.js state has no order field; the server session (demo-012141b6 / demo-a7ffd896) persists across turns but carries no active order entity; payload schema has no order_id.
- **Thai pronouns (คำสั่งซื้อนี้, ตอนนี้)?** Intent patterns match (PAYMENT_STATUS / ORDER_STATUS), but pronoun → order_id resolution does not happen; both turns failed with the order-ID request.
- **English pronouns (this order, it)?** "Has it been paid?" (it) matched PAYMENT_STATUS but failed on missing order_id. Not resolved.
- **Does the assistant repeatedly ask for an order ID?** YES — the identical "กรุณาระบุหมายเลขคำสั่งซื้อของคุณด้วยค่ะ" five times in a row, then twice more in the explicit-ID chat.
- **Does it accidentally switch to another order?** NO — it never resolves ANY order in follow-ups (no switching, no wrong-order answers).
- **Does it give generic order status instead of answering payment/shipment/total/items?** NO — it gives a generic *clarification request*; payment/shipment/status intents were correctly classified but blocked on the missing order_id; total/items had no matching intent.
- **Are all customer-facing answers Thai?** YES — every assistant reply was Thai (`response_language: th`). LANG-001 NOT found.
- **Are facts consistent with SQLite?** YES — every served fact (paid, paid_at 2026-08-03T10:56:10, processing, not_shipped, both products, ฿1,980) matches GET /api/orders / the orders DB.
- **Does Research details accurately identify the handler and order ID?** PARTLY — handler and order_id are accurate when an intent matches; but for unrecognized input the response `intent` field carries a stale/misleading label ("What is the total?" → SHIPMENT_STATUS, "What did I buy?" → PAYMENT_STATUS while routing_reason says "No matching intent pattern found") → META-001.

### 6A.5 — Issues found (severity per audit rubric)

- **CONTEXT-002 (P2)** — Explicit order query does not establish a reusable active_order_id. Evidence: E2/E3 in session demo-a7ffd896 failed immediately after a successful explicit-ID turn.
- **CONTEXT-003 (P2)** — Pronouns "this order" / "it" / "คำสั่งซื้อนี้" / "ตอนนี้" are not resolved to the order in view; every pronoun turn ends in the order-ID request.
- **CONV-006 (P2)** — Repeated order-ID request: the identical clarification copy is served five times consecutively with no reference to the customer's visible order.
- **ORDER-003 (P2)** — System cannot answer purchased-items or total questions despite order evidence: "What is the total?", "ฉันซื้อสินค้าอะไรบ้าง", "What did I buy?" all hit clarification_handler ("No matching intent pattern found") — no TOTAL/ITEMS intent pattern exists, so these are unanswerable even with an explicit order ID.
- **META-001 (P3)** — Research-details metadata shows a stale intent label for unrecognized input (SHIPMENT_STATUS/PAYMENT_STATUS with "No matching intent pattern found").
- LANG-001: NOT found — all customer-facing replies are Thai.

**Screenshots:** `data/customer_journey_audit/12_order_pronoun_failure.png` (five-turn pronoun conversation: welcome + initial pair + 5 × question → "กรุณาระบุหมายเลขคำสั่งซื้อของคุณด้วยค่ะ"); `data/customer_journey_audit/13_explicit_order_followup.png` (explicit-ID chat: "Where is order ORD-1034?" answered, then "Has it been paid?" and "What did I buy?" both refused with the order-ID request).

## Audit Checkpoint — Phase 6A Complete

- **Ask AI establishes internal order context:** NO — visible-text prefill only; follow-up pronouns fail (CONTEXT-001 confirmed as customer-facing breakage).
- **Explicit ORD-1034 establishes context:** NO — "Has it been paid?" right after a successful explicit-ID turn still asks for the order number (CONTEXT-002).
- **Thai pronoun result:** FAIL — คำสั่งซื้อนี้ / ตอนนี้ intents match but no order resolution (CONTEXT-003).
- **English pronoun result:** FAIL — "it" not resolved (CONTEXT-003).
- **Payment / shipment / total / items:** payment & shipment intents correctly classified but blocked on missing order_id; total & items unanswerable (no intent) → ORDER-003.
- **New issues:** CONTEXT-002 (P2), CONTEXT-003 (P2), CONV-006 (P2), ORDER-003 (P2), META-001 (P3). LANG-001 not found.
- **Cumulative severity counts:** **P0 = 0 · P1 = 1 (CART-003) · P2 = 10 (ENV-001, CART-001, CART-002, ORDER-001, TOTAL-001, CONTEXT-001, CONTEXT-002, CONTEXT-003, CONV-006, ORDER-003) · P3 = 10 (CONV-001, CONV-002, CONV-003, UI-001, UI-002, CHECKOUT-001, CHECKOUT-002, ORDER-002, UI-003, META-001).**
- **Completed phases:** 1–6A.
- **Continuation point:** Phase 6B Refund, Cancellation and Exchange Intent Switching.

---

## Phase 6B — Refund, Cancellation, Exchange and Intent Switching (completed 2026-08-03)

**Audit orders:** ORD-1034 (Bank Transfer / Paid / Processing / Not Shipped) and ORD-1011 (Cash on Delivery / Pending / Processing / Not Shipped). **Mode:** audit only — no order was created, cancelled, refunded, exchanged, or modified; SQLite untouched; server healthy on port 8000 (single server). No Phase 6A re-testing.

**Method:** three fresh chat sessions (page reload per session — chat state and `session_id` reset); messages sent exactly per script via `Chat.sendText`. Evidence per turn: `logs/experiment.jsonl` (intent, selected_agent, order_id, latency, response_source, response_language, routing_reason, requires_clarification, policy sources, grounding) + chat.js payload shape (`{message, session_id}` — never order_id) + DOM message text. `store_policy_evaluator` turns that reach the LLM (DeepSeek) take ~5–86 s; deterministic turns are <4 ms.

### 6B.1 — Conversation 1: Clean refund workflow (session demo-fbd2007b)

| Turn | User message | Intent | Handler | Order ID | Pending workflow | Actual | Expected | Result |
|---|---|---|---|---|---|---|---|---|
| 1 | I want a refund | RETURN_REFUND | store_policy_evaluator | None | created (order + reason slots open) | "ยินดีช่วยตรวจสอบคำขอคืนสินค้าหรือยกเลิกคำสั่งซื้อค่ะ กรุณาระบุหมายเลขคำสั่งซื้อ และเหตุผลสั้น ๆ ที่ต้องการคืนสินค้า ค่ะ" (asks order + reason) | pending refund workflow created | PASS |
| 2 | ORD-1034 | RETURN_REFUND | store_policy_evaluator | ORD-1034 | order slot filled | "ขอบคุณค่ะ กรุณาระบุเหตุผลที่ต้องการคืนสินค้าหรือยกเลิกด้วยค่ะ" (asks reason only) | keeps refund intent, stores ORD-1034, no ORDER_STATUS hijack | PASS |
| 3 | I don't like the headphones | RETURN_REFUND | store_policy_evaluator | None (workflow state: ORD-1034) | reason captured ("change of mind") | Policy-grounded Thai reply (DeepSeek, 86.4 s): "เป็นการเปลี่ยนใจคืนสินค้า ตามนโยบายจะคืนเงินเฉพาะราคาสินค้า... ค่าดำเนินการคืนสินค้า 50 บาท... ออเดอร์ ORD-1034 สินค้า Aura Wireless Headphones ราคา 1,290 บาท... กรุณาแจ้งยืนยันอีกครั้งนะคะ" — asks for confirmation, does NOT auto-approve | SQLite evidence + refund_policy.md (3 chunks, grounding passed); Thai; not ORDER_STATUS; not auto-approved | PASS (86 s latency noted) |
| 4 | Actually, cancel it instead | OUT_OF_SCOPE | simulated_human_review | None | lost | "คำขอของคุณถูกบันทึกและจะถูกส่งต่อไปยังเจ้าหน้าที่ กรุณารอการติดต่อกลับภายใน 24 ชั่วโมงทำการค่ะ" (no cancellation guidance, no policy, no paid/not-shipped consideration) | explicit cancellation overrides refund; ORD-1034 reused; policy-grounded cancellation guidance; no real cancellation | FAIL |
| 5 | Has it already been shipped? | UNKNOWN | clarification_handler | None | cleared | "กรุณาระบุหมายเลขคำสั่งซื้อของคุณด้วยค่ะ" | shipment question reuses ORD-1034 → Not Shipped; no repeated ID request | FAIL |

Payloads (client → server, chat.js shape): T1 `{message:"I want a refund", session_id:null}`; T2–T5 `{message:"...", session_id:"demo-fbd2007b"}` — no order_id ever. Session facts: T1 deterministic, requires_clarification true; T2 kept RETURN_REFUND even though the raw ID matched the ORDER_STATUS pattern (pending workflow absorbed the bare ID — refund state survives a bare order-ID reply); T3 LLM + policy (refund_policy.md, retrieved_chunk_count 3, grounding_validation_passed true), response_source deepseek, latency 86,365 ms; T4 OUT_OF_SCOPE (confidence 0.6, simulated_human_review true, policy_sources []); T5 UNKNOWN (confidence 0.3, requires_clarification true).

**Workflow persistence verdict:** the refund workflow preserves ORD-1034 internally through T1–T3 (bare ID absorbed, reason turn served full ORD-1034 evidence), but the context is NOT promoted to a session-level active order: after the T4 OUT_OF_SCOPE detour, T5's shipment question had no order context. The completed refund/cancellation workflow does not leave reusable context behind.

### 6B.2 — Conversation 2: Colour change / exchange workflow (session demo-c31e9c94)

| Turn | User message | Intent | Handler | Order ID | Pending workflow | Actual | Expected | Result |
|---|---|---|---|---|---|---|---|---|
| 1 | I want to change the colour | UNKNOWN | clarification_handler | None | never created | "กรุณาระบุหมายเลขคำสั่งซื้อของคุณด้วยค่ะ" | exchange/colour-change intent recognized; asks for order | FAIL |
| 2 | ORD-1011 | ORDER_STATUS | transaction_tracker | ORD-1011 | — | Full ORD-1011 status answer (COD, pending, processing, not shipped) | remains exchange workflow; ORD-1011 retained for the exchange | FAIL |
| 3 | black | UNKNOWN | clarification_handler | None | — | "กรุณาระบุหมายเลขคำสั่งซื้อของคุณด้วยค่ะ" | captures desired colour = black | FAIL |
| 4 | What about red instead? | UNKNOWN | clarification_handler | None | — | "กรุณาระบุหมายเลขคำสั่งซื้อของคุณด้วยค่ะ" | colour changes black → red; ORD-1011 stays active; not a new unrelated query | FAIL |
| 5 | I don't want to exchange it anymore, cancel it | RETURN_REFUND | store_policy_evaluator | None | — | DeepSeek (5.6 s): "หากต้องการยกเลิกการแลกสินค้า กรุณาแจ้งเลขคำสั่งซื้อและรายละเอียดสินค้า... ค่ะ" (asks for order + product details again) | explicit cancellation overrides exchange; ORD-1011 (COD/Pending) facts used; guidance provided; no real cancellation | FAIL |

Payloads: X1 `{message:"I want to change the colour", session_id:null}`; X2–X5 with `session_id:"demo-c31e9c94"`. X1 UNKNOWN (no EXCHANGE intent pattern exists; exchange_policy.md is never routed to); X2 bare ID hijacked into ORDER_STATUS (no pending workflow to absorb it); X3/X4 UNKNOWN; X5 "cancel" matched RETURN_REFUND (confidence 1.0, DeepSeek, no policy sources retrieved) and re-asked for the order ID despite ORD-1011 having been provided in X2.

### 6B.3 — Conversation 3: Intent switching between orders (session demo-4ef9aa15)

| Turn | User message | Intent | Handler | Order ID | Pending workflow | Actual | Expected | Result |
|---|---|---|---|---|---|---|---|---|
| 1 | Where is order ORD-1011? | ORDER_STATUS | transaction_tracker | ORD-1011 | — | ORD-1011 status answer (COD, pending, processing, not shipped) | uses ORD-1011 | PASS |
| 2 | I want a refund for ORD-1034 | RETURN_REFUND | store_policy_evaluator | ORD-1034 | created (reason slot open) | "ขอบคุณค่ะ กรุณาระบุเหตุผลที่ต้องการคืนสินค้าหรือยกเลิกด้วยค่ะ" | explicit switch to ORD-1034 | PASS |
| 3 | Has it been paid? | PAYMENT_STATUS | transaction_tracker | None | held ORD-1034, unused | "กรุณาระบุหมายเลขคำสั่งซื้อของคุณด้วยค่ะ" | "it" refers to ORD-1034 (Paid) | FAIL |
| 4 | Go back to ORD-1011 | ORDER_STATUS (label stale: PAYMENT_STATUS) | transaction_tracker | ORD-1011 | — | ORD-1011 status answer | switch back to ORD-1011 | PASS (label = META-001) |
| 5 | I want to change its colour | UNKNOWN (label stale: PAYMENT_STATUS) | clarification_handler | None | — | "กรุณาระบุหมายเลขคำสั่งซื้อของคุณด้วยค่ะ" | refers to ORD-1011; exchange intent | FAIL |

Payloads: S1 `{message:"Where is order ORD-1011?", session_id:null}`; S2–S5 with `session_id:"demo-4ef9aa15"`. S2 stored ORD-1034 in the refund workflow; S3's PAYMENT_STATUS handler did not read the workflow state — "it" unresolved (CONTEXT-004/006); S4 explicit ORD-1011 routed correctly (intent label stale = META-001 recurrence); S5 exchange intent absent.

### 6B.4 — Audit questions answered

- **Does pending refund state survive a bare order-ID reply?** YES — T2 stayed RETURN_REFUND and stored ORD-1034 (the raw ID matched ORDER_STATUS but the pending workflow absorbed it).
- **Does pending exchange state survive a bare order-ID reply?** N/A — no pending exchange state exists (X1 was UNKNOWN); the bare ID X2 went straight to ORDER_STATUS.
- **Does the system store active_order_id after collecting it?** PARTIALLY — only inside the refund workflow (T3 evidence); never as a session-level active order usable by other handlers (S3, R5, S5 all failed).
- **Can the user change refund → cancellation?** NO — R4 routed OUT_OF_SCOPE → simulated human review (CONV-007).
- **Can the user change exchange → cancellation?** NO — X5 folded into RETURN_REFUND and re-asked the order ID (CONV-007).
- **Does "it" resolve to the most recently active order?** NO — S3 failed although the refund workflow held ORD-1034; R5 and S5 failed too.
- **Can the user switch ORD-1011 → ORD-1034 → ORD-1011?** Only by re-typing explicit IDs (S1→S2→S4 worked); any pronoun turn in between fails. No wrong-order contamination occurred.
- **Does an explicit new intent override the previous workflow?** PARTIALLY — RETURN_REFUND → OUT_OF_SCOPE (R4) did switch; "cancel" produced no cancellation workflow in either flow (CONV-007); exchange intent is absent entirely.
- **Are refund reason and desired colour stored separately?** Refund reason: YES (captured and used in T3's reply). Desired colour: NO — never captured (X3/X4 UNKNOWN) → CONV-009.
- **Does the system ask only for genuinely missing slots?** Refund flow: YES (T1 asked order+reason; T2 asked reason only). Exchange flow: NO — it kept asking for the order ID after ORD-1011 was already provided (X3/X4/X5).
- **Does any ORD ID hijack the route into ORDER_STATUS?** YES when no pending workflow exists (X2); NO when a refund workflow is pending (T2). CONV-008 confirmed for the no-workflow case.
- **Are order facts SQLite-backed?** YES — every served fact for ORD-1034 (paid 2026-08-03T10:56:10, processing, not shipped, items, ฿1,290 headphones) and ORD-1011 (COD, pending, processing, not shipped) matches GET /api/orders / the orders DB.
- **Is policy guidance grounded?** Refund: YES — refund_policy.md, 3 chunks retrieved, grounding_validation_passed true. Cancellation: NO policy exists (no cancellation_policy.md; OUT_OF_SCOPE had policy_sources []) → POLICY-001. Exchange: exchange_policy.md exists but was never retrieved (no exchange flow) → POLICY-001.
- **Are all customer-facing replies Thai?** YES — every response `response_language: th`. LANG-002 NOT found.
- **Is any refund, cancellation, or exchange falsely approved?** NO — T3 asked for explicit confirmation, R4 escalated to human review, X5 asked for more details; zero order mutations occurred (no POST to order-mutation endpoints; DB untouched).

### 6B.5 — Issues found

- **ROUTE-001 (P1)** — Wrong/missing handler for exchange and cancellation: "I want to change the colour" → clarification_handler (no EXCHANGE intent pattern exists although exchange_policy.md ships); "Actually, cancel it instead" → OUT_OF_SCOPE/simulated_human_review. The normal exchange journey cannot be completed (every step of conversation 2 failed); cancellation has no automated path.
- **CONTEXT-004 (P2)** — Refund workflow holds ORD-1034 internally but never promotes it to a session-level active order: S3 (payment question) and R5 (shipment question) in the same sessions were refused with the order-ID request.
- **CONTEXT-005 (P2)** — Exchange workflow never establishes or preserves order context: ORD-1011 provided in X2 was not retained for X3–X5.
- **CONV-007 (P2)** — Explicit cancellation does not override refund/exchange: R4 → OUT_OF_SCOPE/human review (no cancellation guidance); X5 → folded into RETURN_REFUND and re-asked the order ID.
- **CONV-008 (P2)** — Bare order ID with no pending workflow hijacks into ORDER_STATUS (X2: exchange attempt answered with a status dump instead of continuing the exchange).
- **CONV-009 (P2)** — Desired colour is not retained (X3 "black", X4 "red" both UNKNOWN → clarification); refund reason IS retained (partial — colour half confirmed).
- **POLICY-001 (P2)** — Policy guidance missing for cancellation (no cancellation_policy.md; OUT_OF_SCOPE returned policy_sources []) and unused for exchange (exchange_policy.md exists, 0 retrievals across X1–X5); refund guidance is properly grounded.
- CONTEXT-006: NOT REPRODUCED — no stale order contamination; explicit order IDs always overrode correctly (S1→S2→S4). LANG-002: NOT found.

**Observations (no new IDs):** R3's LLM turn took 86.4 s (UX risk for refund completion); the response `intent` label was stale again for unrecognized/odd inputs (S4, S5 — META-001 recurrence).

**Screenshots:** `data/customer_journey_audit/14_refund_intent_switch.png` (conversation 1: refund request → ORD-1034 → reason → policy-grounded terms → "forwarded to staff" → order-ID request), `data/customer_journey_audit/15_exchange_colour_flow.png` (conversation 2: colour-change request → ORD-1011 status dump → black → red → cancel-exchange ask), `data/customer_journey_audit/16_order_switching_failure.png` (conversation 3: ORD-1011 status → refund for ORD-1034 → "Has it been paid?" refused → back to ORD-1011 → colour-change refused).

## Audit Checkpoint — Phase 6B Complete

- **Refund workflow result:** PARTIAL PASS — intent recognized, bare order ID absorbed, reason captured, ORD-1034 evidence used, policy-grounded Thai terms served, no auto-approval; but 86 s LLM latency and the order context is not reusable afterwards (CONTEXT-004).
- **Cancellation override result:** FAIL — "cancel" never yields a cancellation workflow: R4 → OUT_OF_SCOPE/human review, X5 → RETURN_REFUND re-ask (CONV-007, ROUTE-001, POLICY-001).
- **Exchange/colour result:** FAIL — no EXCHANGE intent exists; colour never captured; bare ORD-1011 hijacked to ORDER_STATUS (ROUTE-001, CONTEXT-005, CONV-008, CONV-009, POLICY-001).
- **Order-switching result:** PARTIAL — explicit IDs switch correctly (S1→S2→S4, no stale contamination); pronoun turns fail; workflow order never shared across handlers (CONTEXT-004).
- **Active-order persistence result:** FAIL — order context exists only inside the refund workflow; never a session-level active_order_id (CONTEXT-004/005).
- **Policy evidence result:** PASS for refund (refund_policy.md, 3 chunks, grounding passed); FAIL for cancellation (no policy) and exchange (policy unretrieved) → POLICY-001.
- **Thai-response result:** PASS — all replies Thai; LANG-002 not found.
- **Issues found:** ROUTE-001 (P1), CONTEXT-004 (P2), CONTEXT-005 (P2), CONV-007 (P2), CONV-008 (P2), CONV-009 (P2), POLICY-001 (P2). CONTEXT-006 not reproduced. LANG-002 not found.
- **Cumulative severity counts:** **P0 = 0 · P1 = 2 (CART-003, ROUTE-001) · P2 = 16 (ENV-001, CART-001, CART-002, ORDER-001, TOTAL-001, CONTEXT-001, CONTEXT-002, CONTEXT-003, CONV-006, ORDER-003, CONTEXT-004, CONTEXT-005, CONV-007, CONV-008, CONV-009, POLICY-001) · P3 = 10 (CONV-001, CONV-002, CONV-003, UI-001, UI-002, CHECKOUT-001, CHECKOUT-002, ORDER-002, UI-003, META-001).**
- **Completed phases:** 1–6B.
- **Continuation point:** Phase 7 Continuous Conversation Stress Test.

---

## Phase 7A — Continuous Conversation Stress Test, Turns 1–8 (completed 2026-08-03)

**Audit orders:** ORD-1034 (Bank Transfer / Paid / Processing / Not Shipped / Aura Wireless Headphones ×1 + Linen Everyday Blouse ×1 / ฿1,980) and ORD-1011 (COD / Pending / Processing / Not Shipped / Aura Wireless Headphones ×2). **Mode:** audit only — no order created/cancelled/refunded/exchanged/paid/modified; SQLite untouched; one healthy server on port 8000; one continuous chat session; per-turn evidence written immediately after each turn. Existing screenshots 17_continuous_context_break.png / 18_wrong_order_or_final_state.png are unverified and NOT used as evidence.

| Turn | Message | Session ID | Intent | Handler | Explicit Order | Active Order | Pending Workflow | Missing Slots | Collected Slots | Source | Actual Response | Expected | Result |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | hai | demo-c2092222 | GREETING | general_response | — | — | — | — | — | deterministic (0.0 ms) | Thai: "สวัสดีค่ะ ยินดีต้อนรับสู่ SiamCart กรุณาสอบถามเกี่ยวกับสินค้า คำสั่งซื้อ การชำระเงิน การจัดส่ง การคืนสินค้า หรือการคืนเงินได้เลยค่ะ" | Thai greeting | PASS |
| 2 | I want a refund | demo-c2092222 | RETURN_REFUND | store_policy_evaluator | — | — | RETURN_REFUND (created) | order_id, reason | — | deterministic (0.0 ms) | Thai: "ยินดีช่วยตรวจสอบคำขอคืนสินค้าหรือยกเลิกคำสั่งซื้อค่ะ กรุณาระบุหมายเลขคำสั่งซื้อ และเหตุผลสั้น ๆ ที่ต้องการคืนสินค้า ค่ะ" | create pending refund workflow; ask only for missing order info | PASS |
| 3 | the order I just bought | demo-c2092222 | RETURN_REFUND | store_policy_evaluator | — | — | RETURN_REFUND (kept) | order_id | — | deterministic (0.0 ms) | Thai: "ยินดีช่วยตรวจสอบคำขอคืนสินค้าหรือยกเลิกคำสั่งซื้อค่ะ กรุณาระบุหมายเลขคำสั่งซื้อ ค่ะ" — asked once for the order number; workflow intact | resolve to latest order OR ask once for ID without destroying workflow | PASS |
| 4 | ORD-1034 | demo-c2092222 | RETURN_REFUND | store_policy_evaluator | ORD-1034 | ORD-1034 (workflow) | RETURN_REFUND (kept) | — | reason = "the order I just bought" (mis-collected from T3 text) | LLM w/ fallback (7.9 s), policy: refund_policy.md + return_policy.md (3 chunks) | Thai: "พบคำสั่งซื้อ ORD-1034 แล้ว เข้าใจว่าคุณต้องการคืนสินค้าหรือยกเลิกคำสั่งซื้อ (เหตุผล: the order I just bought)... คำสั่งซื้อนี้ชำระเงินแล้ว สินค้ายังไม่ได้จัดส่ง การยกเลิกคำสั่งซื้ออาจเหมาะสมกว่า... ตามนโยบายของ SiamCart: # Return Policy... ยังไม่มีการอนุมัติคืนเงินหรือยกเลิก — ทีมงานจะตรวจสอบคำขอของคุณค่ะ" | bare ID continues refund workflow (no ORDER_STATUS hijack); ORD-1034 becomes active | PASS |
| 5 | I don't like the headphones | demo-c2092222 | UNKNOWN | clarification_handler | — | — (workflow completed at T4 due to mis-collected reason) | — (no pending workflow) | — | — | deterministic (0.0 ms) | Thai: "กรุณาระบุหมายเลขคำสั่งซื้อของคุณด้วยค่ะ" — repeated order-ID request although ORD-1034 was supplied in T4 | retain ORD-1034; collect refund reason; use order facts + policy; no auto-approve | FAIL |
| 6 | actually I want to change the colour instead | demo-c2092222 | UNKNOWN | clarification_handler | — | — | — (no exchange workflow; refund workflow already closed) | — | — | deterministic (0.0 ms) | Thai: "กรุณาระบุหมายเลขคำสั่งซื้อของคุณด้วยค่ะ" — colour-change intent not recognized; no reuse of ORD-1034 | explicit colour-change intent overrides refund; reuse ORD-1034; ask only for desired colour | FAIL |
| 7 | black | demo-c2092222 | UNKNOWN | clarification_handler | — | — | — | — | — (colour never stored; no exchange workflow) | deterministic (1.0 ms) | Thai: "กรุณาระบุหมายเลขคำสั่งซื้อของคุณด้วยค่ะ" — colour not captured; no availability invented; no approval | retain ORD-1034; store desired colour black separately from refund reason; do not invent availability; do not approve exchange | FAIL |
| 8 | has it been shipped? | demo-c2092222 | UNKNOWN | clarification_handler | — | — | — | — | — | deterministic (0.0 ms) | Thai: "กรุณาระบุหมายเลขคำสั่งซื้อของคุณด้วยค่ะ" — shipment question not answered; order ID requested again | shipment query temporarily overrides workflow; reuse ORD-1034; answer Not Shipped; preserve active order context | FAIL |

### 7A.1 — Run summary

One continuous session **demo-c2092222** (page loaded once, chat never reset). Turns 1–8 executed exactly per script; no repair messages; no extra order IDs. Screenshot captured after turn 8 (verified): `data/customer_journey_audit/17_phase7a_verified_turns_1_8.png`. The pre-existing unverified screenshots 17_continuous_context_break.png and 18_wrong_order_or_final_state.png were NOT used or overwritten.

**Result: 4 PASS (turns 1–4), 4 FAIL (turns 5–8), 0 PARTIAL.**

**Cascade observed (root cause of turns 5–8):** turn 3's referring expression "the order I just bought" was NOT resolved to an order; the workflow kept asking once for the order ID (acceptable), but the slot-filler simultaneously captured the phrase as the **refund reason** (reason = "the order I just bought"). Turn 4's bare ORD-1034 then filled the order slot and the workflow **completed immediately** (both slots "filled"), producing the policy-grounded evaluation with the WRONG reason. Turns 5–8 therefore arrived with no pending workflow and no session-level active order — every one was routed to clarification_handler with the generic "กรุณาระบุหมายเลขคำสั่งซื้อของคุณด้วยค่ะ".

### 7A.2 — Check findings

- **Bare order ID hijacking to ORDER_STATUS:** NOT observed — turn 4's bare ORD-1034 stayed in the RETURN_REFUND workflow (intent RETURN_REFUND, order stored; the routing_reason "Matched ORDER_STATUS intent pattern" is metadata only). Confirms 6B's finding that a pending workflow absorbs bare IDs.
- **Repeated order-ID clarification:** CONFIRMED — turns 5, 6, 7, 8 all served the identical "กรุณาระบุหมายเลขคำสั่งซื้อของคุณด้วยค่ะ" (4 consecutive repeats) although ORD-1034 was supplied at turn 4 → **CONV-010** + additional evidence for CONV-006.
- **Lost active_order_id:** CONFIRMED — ORD-1034 existed only inside the workflow (T3–T4); after the workflow completed there was no session-level active order (T5–T8 all failed) → additional evidence for CONTEXT-004.
- **Refund reason overwritten by desired colour:** NOT observed — no colour was ever stored (T7 UNKNOWN); the reason slot was instead corrupted by the T3 referring phrase → additional evidence for CONV-009 (reason captured with the wrong value).
- **Refund intent failing to switch to exchange:** CONFIRMED — turn 6 "actually I want to change the colour instead" → UNKNOWN/clarification → **CONV-011** + additional evidence for ROUTE-001.
- **English customer-facing output:** NOT observed — every response `response_language: th`.
- **False refund/exchange approval:** NOT observed — no approval, no mutation, no confirmation prompt even needed (T4 explicitly "ยังไม่มีการอนุมัติคืนเงินหรือยกเลิก").
- **Wrong order facts:** NOT observed — the only order facts served (T4) matched ORD-1034 exactly.
- **Stale research metadata:** T4's `routing_reason` ("Matched ORDER_STATUS intent pattern") mismatched the served intent (RETURN_REFUND) — a META-001-class metadata artifact; no distinct META-002 behavior observed.

### 7A.3 — Issues

- **CONV-010 (P2, new)** — Already supplied order information is repeatedly requested: after ORD-1034 was supplied at turn 4, turns 5–8 each re-requested the order number (4 consecutive identical requests in one session).
- **CONV-011 (P2, new)** — Refund → exchange switch fails in a long conversation: turn 6's explicit colour-change intent routed to clarification_handler (no EXCHANGE intent pattern).
- **CONV-012:** NOT REPRODUCED — refund reason and desired colour never overwrote each other (colour was never stored; reason was mis-captured, not overwritten).
- **CONTEXT-007:** NOT REPRODUCED — the turn-8 shipment question did not destroy any active order/workflow (the workflow had already completed at turn 4 due to the mis-collected reason slot).
- **META-002:** NOT REPRODUCED as a distinct defect — the T4 routing_reason/intent mismatch is a META-001 recurrence.

**Evidence added to existing issues:** ROUTE-001 (T6 exchange intent absent), CONV-006 (T5–T8 identical clarification ×4), CONV-009 (T3/T4 reason slot mis-captured "the order I just bought"; T7 colour not captured), CONTEXT-004 (ORD-1034 not reusable after workflow completion), META-001 (T4 routing metadata mismatch).

## Audit Checkpoint — Phase 7A Complete

- **Turns executed:** 1–8, one continuous session.
- **Pass/fail/partial:** PASS 4 (T1–T4) · FAIL 4 (T5–T8) · PARTIAL 0.
- **Final session_id:** demo-c2092222.
- **Final active order:** NONE at session level (ORD-1034 was workflow-scoped through T4 only).
- **Final pending workflow:** NONE (refund workflow completed at T4 with a mis-collected reason; no exchange workflow ever created).
- **Refund → order-ID result:** PASS — T3 asked once without destroying the workflow; T4's bare ID was absorbed (no ORDER_STATUS hijack).
- **Refund → exchange result:** FAIL — T6 colour-change intent unrecognized (CONV-011/ROUTE-001).
- **Shipment follow-up result:** FAIL — T8 not answered; order ID re-requested (CONV-010).
- **Repeated clarification count:** 4 consecutive identical order-ID requests (T5–T8).
- **Wrong-order fact count:** 0.
- **False-action count:** 0 (no approvals, no mutations, no exchange/colour actions).
- **Thai-only result:** PASS — all 8 responses Thai (LANG-002 not found).
- **New issues:** CONV-010 (P2), CONV-011 (P2). CONV-012, CONTEXT-007, META-002 not reproduced.
- **Evidence added to existing issues:** ROUTE-001, CONV-006, CONV-009, CONTEXT-004, META-001.
- **Cumulative severity counts:** **P0 = 0 · P1 = 2 (CART-003, ROUTE-001) · P2 = 18 (ENV-001, CART-001, CART-002, ORDER-001, TOTAL-001, CONTEXT-001, CONTEXT-002, CONTEXT-003, CONV-006, ORDER-003, CONTEXT-004, CONTEXT-005, CONV-007, CONV-008, CONV-009, POLICY-001, CONV-010, CONV-011) · P3 = 10 (CONV-001, CONV-002, CONV-003, UI-001, UI-002, CHECKOUT-001, CHECKOUT-002, ORDER-002, UI-003, META-001).**
- **Continuation point:** Phase 7B, continue the SAME logical scenario with turns 9–15.
