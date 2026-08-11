/**
 * SiamCart — My Orders page (orders.js)
 * Order-history listing (GET /api/orders), order-details modal
 * (GET /api/orders/{id}), explicit demo payment
 * (POST /api/orders/{id}/demo-payment), and Ask-AI deep links into the
 * existing chat. Vanilla JavaScript. No frameworks.
 *
 * Task 8A — Ask AI hands the chat a real order context via
 * window.Chat.openWithOrder (order_id + first product name/image +
 * payment/order/shipment status) instead of a bare text prefill, so the
 * assistant keeps the order across follow-up questions.
 */
(function () {
    "use strict";

    /* ═══════════════════════════════════════════════════════════
       1. STATE & CONSTANTS
       ═══════════════════════════════════════════════════════════ */
    var LS_CUSTOMER_EMAIL = "siamcart.customer.email";
    var LS_CUSTOMER_ORDERS = "siamcart.customer.orders.v1";

    var FALLBACK = "data:image/svg+xml;charset=utf-8," + encodeURIComponent(
        '<svg xmlns="http://www.w3.org/2000/svg" width="400" height="400"><rect width="400" height="400" fill="#EEF1F6"/><g fill="none" stroke="#9AA5B5" stroke-width="14" stroke-linecap="round"><path d="M120 240c30-50 80-60 110-20 14 19 26 20 40 0 30-40 80-30 110 20"/><path d="M150 170c0-40 30-60 60-60s60 20 60 60" transform="translate(0,-10)"/></g></svg>');

    // Keep chat.js / inline image handlers working (same fallback contract).
    window.SiamCart = window.SiamCart || {};
    window.SiamCart.fallbackImage = FALLBACK;

    // Demo-payment eligibility mirrors the backend rule set.
    var DEMO_ELIGIBLE = ["bank_transfer", "credit_card", "debit_card", "card"];
    var COD_METHODS = ["cod", "cash_on_delivery"];

    // Demo cancellation mirrors the backend rule set: only orders still
    // processing and not yet shipped may be cancelled.
    function canCancel(order) {
        if (!order) return false;
        var os = String(order.order_status || "").toLowerCase().replace(/[\s-]+/g, "_");
        var ss = String(order.shipment_status || "").toLowerCase().replace(/[\s-]+/g, "_");
        return os === "processing" && ss === "not_shipped";
    }

    // Task 8C — demo shipment mirrors the backend rule set
    // (SHIPPABLE_ORDER_STATUS = {"processing"} in app/db/store.py).
    function canShip(order) {
        if (!order) return false;
        var os = String(order.order_status || "").toLowerCase().replace(/[\s-]+/g, "_");
        return os === "processing";
    }

    // Task 8D — Simulate Shipment is offered for paid + processing +
    // not_shipped orders (the demo lifecycle gate in app/db/store.py).
    function canSimulateShipment(order) {
        if (!order) return false;
        var ps = String(order.payment_status || "").toLowerCase();
        var os = String(order.order_status || "").toLowerCase().replace(/[\s-]+/g, "_");
        var ss = String(order.shipment_status || "").toLowerCase().replace(/[\s-]+/g, "_");
        return ps === "paid" && os === "processing" && ss === "not_shipped";
    }

    // Task 8D — Mark Delivered is offered only while the parcel is in transit.
    function canDeliver(order) {
        if (!order) return false;
        var ss = String(order.shipment_status || "").toLowerCase().replace(/[\s-]+/g, "_");
        return ss === "in_transit";
    }

    var state = {
        loading: false,
        mode: "latest",          // "latest" | "search" | "email"
        orders: [],
        email: null,
        detail: null
    };

    var el = {
        form: document.getElementById("ordersSearchForm"),
        type: document.getElementById("ordersSearchType"),
        input: document.getElementById("ordersSearchInput"),
        searchBtn: document.getElementById("ordersSearchBtn"),
        latestBtn: document.getElementById("ordersLatestBtn"),
        demoNote: document.getElementById("ordersDemoNote"),
        resultsHead: document.getElementById("ordersResultsHead"),
        resultsText: document.getElementById("ordersResultsText"),
        list: document.getElementById("ordersList"),
        modal: document.getElementById("orderModal"),
        modalClose: document.getElementById("odClose"),
        modalBody: document.getElementById("odBody")
    };

    /* ═══════════════════════════════════════════════════════════
       2. HELPERS
       ═══════════════════════════════════════════════════════════ */
    function esc(s) {
        return String(s == null ? "" : s)
            .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
            .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
    }

    function formatTHB(n) {
        var v = Number(n);
        if (!isFinite(v) || v <= 0) return "\u0E3F0";
        return "\u0E3F" + v.toLocaleString("en-US");
    }

    function formatDate(iso) {
        if (!iso) return "—";
        var d = new Date(iso);
        if (isNaN(d.getTime())) return String(iso);
        return d.toLocaleDateString("en-US", { year: "numeric", month: "short", day: "numeric" }) +
            " · " + d.toLocaleTimeString("en-US", { hour: "2-digit", minute: "2-digit" });
    }

    function humanize(s) {
        if (s == null || s === "") return "—";
        return String(s)
            .replace(/_/g, " ")
            .replace(/\b\w/g, function (c) { return c.toUpperCase(); });
    }

    function paymentMethodLabel(method) {
        var m = String(method || "").toLowerCase().replace(/[\s-]+/g, "_");
        if (m === "cod" || m === "cash_on_delivery") return "Cash on Delivery";
        if (m === "bank_transfer") return "Bank Transfer";
        // Task 5D-4 — the storefront's supported card option is labelled
        // "Demo Card Payment" everywhere (no real card is ever collected).
        if (m === "credit_card" || m === "debit_card" || m === "card") return "Demo Card Payment";
        return humanize(method);
    }

    // Task 5D-4 — short payment-method explanation shown on order cards.
    function paymentHint(method, paymentStatus) {
        var m = normalizePaymentMethod(method);
        if (m === "cod" || m === "cash_on_delivery") return "Pay on delivery";
        return paymentStatus === "paid" ? "Paid" : "Payment required";
    }

    var STATUS_EXPLAINER = "Payment shows whether money has been received. Order shows the merchant processing stage. Shipment shows whether the parcel has been dispatched.";

    function normalizePaymentMethod(method) {
        return String(method || "").toLowerCase().replace(/[\s-]+/g, "_");
    }

    function itemsOf(order) {
        var items = order.order_items;
        if (Array.isArray(items) && items.length) return items;
        items = order.items;
        return Array.isArray(items) ? items : [];
    }

    // Task 8A — order context handed to the chat (openWithOrder contract).
    // Accepts both the /api/orders list shape (order_items) and the
    // /api/orders/{id} detail shape (items). The chat only needs the order
    // ID plus the first product + the three statuses for display; SQLite
    // remains the source of truth for every answer.
    function orderContextOf(order) {
        var items = itemsOf(order);
        var first = items[0] || {};
        return {
            order_id: order.order_id,
            first_product_name: first.product_name || first.product_id || "",
            first_product_image: first.image_url || "",
            payment_status: order.payment_status,
            order_status: order.order_status,
            shipment_status: order.shipment_status
        };
    }

    function orderById(orderId) {
        var orders = state.orders || [];
        for (var i = 0; i < orders.length; i++) {
            if (orders[i].order_id === orderId) return orders[i];
        }
        // Fallback: a minimal context with just the ID — the chat still
        // carries it and SQLite remains the source of truth.
        return { order_id: orderId };
    }

    function itemCount(order) {
        return itemsOf(order).reduce(function (n, it) { return n + (Number(it.quantity) || 0); }, 0);
    }

    function showToast(msg, type) {
        var container = document.getElementById("toastContainer");
        if (!container) return;
        var t = document.createElement("div");
        t.className = "toast" + (type === "error" ? " toast-error" : "");
        t.innerHTML = (type === "error"
            ? '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 8v5M12 16.5h.01" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round"/><circle cx="12" cy="12" r="9" fill="none" stroke="currentColor" stroke-width="1.8"/></svg>'
            : '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M9 12l2 2 4-4" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"/><circle cx="12" cy="12" r="9" fill="none" stroke="currentColor" stroke-width="1.8"/></svg>') +
            "<span>" + esc(msg) + "</span>";
        container.appendChild(t);
        setTimeout(function () {
            t.classList.add("leaving");
            setTimeout(function () { t.remove(); }, 350);
        }, 2600);
    }

    /* ═══════════════════════════════════════════════════════════
       3. BADGES
       ═══════════════════════════════════════════════════════════ */
    function badgeClass(kind, value) {
        var v = String(value || "").toLowerCase().replace(/[\s-]+/g, "_");
        if (kind === "payment") {
            if (v === "paid") return "badge-paid";
            if (v === "pending" || v === "unpaid") return "badge-pending";
            if (v === "failed") return "badge-failed";
            if (v === "refunded") return "badge-refunded";
            return "badge-muted";
        }
        if (kind === "order") {
            if (v === "processing" || v === "pending") return "badge-pending";
            if (v === "confirmed") return "badge-confirmed";
            if (v === "shipped") return "badge-shipped";
            if (v === "delivered") return "badge-delivered";
            if (v === "cancelled") return "badge-failed";
            return "badge-muted";
        }
        // shipment
        if (v === "not_shipped") return "badge-muted";
        if (v === "shipped") return "badge-shipped";
        if (v === "in_transit" || v === "pending_pickup") return "badge-in-transit";
        if (v === "delivered") return "badge-delivered";
        return "badge-muted";
    }

    function badgeHTML(kind, value, label) {
        var cls = badgeClass(kind, value);
        var text = label || humanize(value);
        if (text === "—") return "";
        return '<span class="order-badge ' + cls + '">' + esc(text) + "</span>";
    }

    /* ═══════════════════════════════════════════════════════════
       4. FETCH + RENDER LIST
       ═══════════════════════════════════════════════════════════ */
    function showLoading() {
        state.loading = true;
        el.list.innerHTML =
            '<div class="orders-loading" role="status" aria-live="polite">' +
            '<span class="loading-spinner" aria-hidden="true"></span>' +
            "<p>Loading your orders…</p></div>";
    }

    function showEmpty(message) {
        el.list.innerHTML =
            '<div class="orders-empty">' +
            '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M3 7l9-4 9 4v10l-9 4-9-4z" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round"/><path d="M3 7l9 4 9-4M12 11v10" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round"/></svg>' +
            "<p><strong>No orders found.</strong></p>" +
            "<p>" + esc(message || "Try a different email, phone or order ID.") + "</p>" +
            '<button type="button" class="btn btn-secondary" id="ordersEmptyLatest">Show latest demo orders</button>' +
            "</div>";
        var b = document.getElementById("ordersEmptyLatest");
        if (b) b.addEventListener("click", loadLatest);
    }

    function showInvalid(message) {
        el.list.innerHTML =
            '<div class="orders-invalid" role="alert">' +
            '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 8v5M12 16.5h.01" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round"/><circle cx="12" cy="12" r="9" fill="none" stroke="currentColor" stroke-width="1.8"/></svg>' +
            "<p><strong>Invalid search.</strong></p>" +
            "<p>" + esc(message) + "</p></div>";
    }

    function showError() {
        el.list.innerHTML =
            '<div class="orders-error" role="alert">' +
            '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 8v5M12 16.5h.01" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round"/><circle cx="12" cy="12" r="9" fill="none" stroke="currentColor" stroke-width="1.8"/></svg>' +
            "<p><strong>Orders are unavailable right now.</strong></p>" +
            "<p>The order service could not be reached. Please try again.</p>" +
            '<button type="button" class="btn btn-secondary" id="ordersRetryBtn">Retry</button>' +
            "</div>";
        var retry = document.getElementById("ordersRetryBtn");
        if (retry) retry.addEventListener("click", function () {
            if (state.mode === "latest") loadLatest();
            else if (state.mode === "email" && state.email) searchByEmail(state.email);
            else runSearch();
        });
    }

    function setResultsText(orders, mode) {
        el.resultsHead.hidden = false;
        var n = orders.length;
        var authUser = window.SiamCartAuth && window.SiamCartAuth.currentUser();
        if (authUser) {
            el.resultsText.innerHTML = "<strong>" + n + "</strong> order" + (n === 1 ? "" : "s") + " owned by <strong>" + esc(authUser.display_name) + "</strong>";
        } else if (mode === "latest") {
            el.resultsText.innerHTML = "<strong>" + n + "</strong> latest demo order" + (n === 1 ? "" : "s");
        } else if (mode === "email" && state.email) {
            el.resultsText.innerHTML = "<strong>" + n + "</strong> order" + (n === 1 ? "" : "s") + " for <strong>" + esc(state.email) + "</strong>";
        } else {
            el.resultsText.innerHTML = "<strong>" + n + "</strong> order" + (n === 1 ? "" : "s") + " found";
        }
    }

    function fetchOrders(params, mode) {
        if (state.loading) return; // prevent duplicate searches
        if (window.SiamCartAuth && window.SiamCartAuth.isRequired() && !window.SiamCartAuth.isAuthenticated()) {
            showAuthenticationRequired();
            window.SiamCartAuth.openLogin("Sign in or use Demo Login to view My Orders.");
            return;
        }
        state.mode = mode;
        showLoading();

        var qs = [];
        Object.keys(params).forEach(function (k) {
            if (params[k] != null && params[k] !== "") qs.push(encodeURIComponent(k) + "=" + encodeURIComponent(params[k]));
        });
        var url = "/api/orders" + (qs.length ? "?" + qs.join("&") : "");

        fetch(url)
            .then(function (r) {
                if (!r.ok) throw new Error("HTTP " + r.status);
                return r.json();
            })
            .then(function (data) {
                state.loading = false;
                var orders = (data && Array.isArray(data.orders)) ? data.orders : [];
                state.orders = orders;
                var authenticated = window.SiamCartAuth && window.SiamCartAuth.isAuthenticated();
                el.demoNote.hidden = mode !== "latest" || !authenticated;
                if (!orders.length) {
                    el.resultsHead.hidden = true;
                    if (mode === "latest" && authenticated) showEmpty("Your account has no orders yet — create one to get started.");
                    else if (mode === "latest") showEmpty("The demo database has no orders yet — create one to get started.");
                    else showEmpty("We could not find any orders matching that search.");
                    return;
                }
                setResultsText(orders, mode);
                renderCards(orders, mode);
            })
            .catch(function () {
                state.loading = false;
                el.resultsHead.hidden = true;
                showError();
            });
    }

    function loadLatest() {
        state.email = null;
        el.type.value = "order_id";
        el.input.value = "";
        fetchOrders({ limit: 20 }, "latest");
    }

    function searchByEmail(email) {
        state.email = email;
        fetchOrders({ email: email }, "email");
    }

    function runSearch() {
        if (state.loading) return;
        var type = el.type.value;
        var value = el.input.value.trim();
        if (!value) {
            showInvalid("Enter an order ID, email or phone number to search.");
            return;
        }
        if (type === "email" && !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(value)) {
            showInvalid("That does not look like a valid email address (e.g. name@example.com).");
            return;
        }
        if (type === "order_id" && !/^ord[\s-]?\d+$/i.test(value)) {
            showInvalid("Order IDs look like ORD-1010 — try again with the full ID.");
            return;
        }
        var params = {};
        params[type] = value;
        if (type === "email") state.email = value;
        else state.email = null;
        fetchOrders(params, "search");
    }

    /* ═══════════════════════════════════════════════════════════
       5. RENDER ORDER CARDS
       ═══════════════════════════════════════════════════════════ */
    function cardHTML(order, demoMode) {
        var items = itemsOf(order);
        var first = items[0] || {};
        var names = items.map(function (it) {
            return "<span class='order-item-line'>" +
                "<span class='order-item-name'>" + esc(it.product_name || it.product_id || "Item") + "</span>" +
                "<span class='order-item-qty'>× " + (Number(it.quantity) || 0) + "</span>" +
                "</span>";
        }).join("");
        var img = first.image_url
            ? '<img src="' + esc(first.image_url) + '" alt="" loading="lazy" onerror="this.onerror=null;this.src=window.SiamCart.fallbackImage;">'
            : '<img src="' + FALLBACK + '" alt="" aria-hidden="true">';

        var demoTag = demoMode
            ? '<span class="order-demo-tag">Demo order</span>'
            : "";

        // Task 5D-4 — payment method is shown prominently with a short
        // explanation; the three statuses are labelled separately.
        var methodLine = esc(paymentMethodLabel(order.payment_method)) + " · " + esc(paymentHint(order.payment_method, order.payment_status));
        var itemCountText = itemCount(order);

        return '<article class="order-card" data-order-id="' + esc(order.order_id) + '">' +
            '<div class="order-card-head">' +
            '<div class="order-card-id"><strong>' + esc(order.order_id) + "</strong>" + demoTag +
            '<span class="order-card-date">' + formatDate(order.created_at) + "</span></div>" +
            "</div>" +
            '<div class="order-card-body">' +
            '<div class="order-card-thumb">' + img + "</div>" +
            '<div class="order-card-info">' +
            '<span class="order-card-customer">' + esc(order.customer_name || "—") + "</span>" +
            '<div class="order-card-items">' + (names || "<span class='order-item-line'>No items recorded</span>") + "</div>" +
            '<span class="order-card-meta">' + methodLine + " · " + itemCountText + " item" + (itemCountText === 1 ? "" : "s") + "</span>" +
            "</div>" +
            '<div class="order-card-total"><span class="order-card-total-label">Total</span><strong>' + formatTHB(order.total_amount) + "</strong></div>" +
            "</div>" +
            '<div class="order-card-status">' +
            '<div class="order-status-line"><span class="order-status-label">Payment</span>' + badgeHTML("payment", order.payment_status) + "</div>" +
            '<div class="order-status-line"><span class="order-status-label">Order</span>' + badgeHTML("order", order.order_status) + "</div>" +
            '<div class="order-status-line"><span class="order-status-label">Shipment status</span>' + badgeHTML("shipment", order.shipment_status) + "</div>" +
            // Task 8D — compact tracking line when a tracking number exists.
            (order.tracking_number
                ? '<div class="order-status-line order-tracking-line"><span class="order-status-label">Tracking</span><strong class="order-tracking-value">' + esc(order.tracking_number) + "</strong></div>"
                : "") +
            '<p class="status-explainer">' + STATUS_EXPLAINER + "</p>" +
            "</div>" +
            '<div class="order-card-actions">' +
            '<button type="button" class="btn btn-secondary" data-order-view="' + esc(order.order_id) + '">View Details</button>' +
            '<button type="button" class="btn btn-ghost" data-order-ask="' + esc(order.order_id) + '">' +
            '<svg class="icon" viewBox="0 0 24 24" aria-hidden="true"><path d="M12 3a8 8 0 00-8 8c0 1.8.6 3.5 1.6 4.9L4 20l4.2-1.4A8 8 0 1012 3z" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round"/></svg>' +
            "Ask AI</button>" +
            "</div>" +
            "</article>";
    }

    function renderCards(orders, mode) {
        var demoMode = mode === "latest";
        el.list.innerHTML = orders.map(function (o) { return cardHTML(o, demoMode); }).join("");
    }

    /* ═══════════════════════════════════════════════════════════
       6. ORDER DETAILS MODAL
       ═══════════════════════════════════════════════════════════ */
    function openDetails(orderId) {
        fetch("/api/orders/" + encodeURIComponent(orderId))
            .then(function (r) {
                if (!r.ok) throw new Error("HTTP " + r.status);
                return r.json();
            })
            .then(function (order) {
                state.detail = order;
                renderDetails(order);
                el.modal.hidden = false;
                document.body.classList.add("modal-open");
                el.modalClose.focus();
            })
            .catch(function () {
                showToast("Could not load order " + orderId + " details", "error");
            });
    }

    function closeDetails() {
        el.modal.hidden = true;
        document.body.classList.remove("modal-open");
        state.detail = null;
    }

    function canDemoPay(order) {
        if (!order || order.payment_status !== "pending") return false;
        var m = normalizePaymentMethod(order.payment_method);
        if (COD_METHODS.indexOf(m) !== -1) return false;
        return DEMO_ELIGIBLE.indexOf(m) !== -1;
    }

    function shippingFor(total) {
        var t = Number(total) || 0;
        return t >= 2000 ? 0 : 49;
    }

    // Task 5D-4 — short payment-method explanation inside Order Details.
    function paymentNoteHTML(order) {
        var m = normalizePaymentMethod(order.payment_method);
        if (m === "cod" || m === "cash_on_delivery") {
            return '<p class="od-pay-note">Pay on delivery — payment is collected when the order is delivered.</p>';
        }
        if (order.payment_status === "refunded") {
            return '<p class="od-pay-note">Simulated refund completed. No real money was transferred.</p>';
        }
        if (order.payment_status === "paid") {
            return '<p class="od-pay-note">Payment received — this order has been paid.</p>';
        }
        return '<p class="od-pay-note">Payment required — use Confirm Demo Payment to simulate payment for the research prototype.</p>';
    }

    function renderDetails(order) {
        var items = itemsOf(order);
        var ship = shippingFor(order.total_amount);
        var sub = Math.max(0, (Number(order.total_amount) || 0) - ship);

        var itemRows = items.length ? items.map(function (it) {
            var img = it.image_url
                ? '<img src="' + esc(it.image_url) + '" alt="" loading="lazy" onerror="this.onerror=null;this.src=window.SiamCart.fallbackImage;">'
                : '<img src="' + FALLBACK + '" alt="" aria-hidden="true">';
            return '<div class="od-item">' +
                '<div class="od-item-img">' + img + "</div>" +
                '<div class="od-item-info"><strong>' + esc(it.product_name || it.product_id || "Item") + "</strong>" +
                '<span>' + esc(it.product_id || "") + "</span></div>" +
                '<div class="od-item-qty">' + (Number(it.quantity) || 0) + " × " + formatTHB(it.unit_price) + "</div>" +
                '<div class="od-item-total">' + formatTHB(it.line_total != null ? it.line_total : (Number(it.unit_price) || 0) * (Number(it.quantity) || 0)) + "</div>" +
                "</div>";
        }).join("") : '<p class="od-no-items">No items recorded for this order.</p>';

        var paidAt = order.paid_at
            ? '<div class="od-row"><span>Paid at</span><strong>' + esc(formatDate(order.paid_at)) + "</strong></div>"
            : "";

        // Demo cancellation — cancellation facts shown once cancelled.
        var cancelledAtRow = order.cancelled_at
            ? '<div class="od-row"><span>Cancelled at</span><strong>' + esc(formatDate(order.cancelled_at)) + "</strong></div>"
            : "";
        var cancelReasonRow = order.cancellation_reason
            ? '<div class="od-row"><span>Cancellation reason</span><strong>' + esc(order.cancellation_reason) + "</strong></div>"
            : "";

        var demoPayBtn = canDemoPay(order)
            ? '<button type="button" class="btn btn-primary" id="odDemoPayBtn">Confirm Demo Payment</button>'
            : "";

        // Demo cancellation — red-outline Cancel Order button for
        // processing + not_shipped orders only.
        var cancelBtn = canCancel(order)
            ? '<button type="button" class="btn btn-danger-outline" id="odCancelBtn">Cancel Order</button>'
            : "";

        // Task 8D — Simulate Shipment for paid + processing + not_shipped.
        var shipBtn = canSimulateShipment(order)
            ? '<button type="button" class="btn btn-primary" id="odShipBtn">Simulate Shipment</button>'
            : "";

        // Task 8D — Mark Delivered only while the parcel is in transit.
        var deliverBtn = canDeliver(order)
            ? '<button type="button" class="btn btn-primary" id="odDeliverBtn">Mark Delivered</button>'
            : "";

        // Inline confirmation section — opens when Cancel Order is clicked.
        var cancelPanel = canCancel(order)
            ? '<div class="od-cancel-panel" id="odCancelPanel" hidden>' +
            "<h4>Cancel this order?</h4>" +
            "<p>Order <strong>" + esc(order.order_id) + "</strong> has not been shipped yet, so it can still be cancelled.</p>" +
            (order.payment_status === "paid"
                ? '<p class="od-cancel-refund-note">This order was paid — cancelling triggers a simulated refund. No real money is transferred.</p>'
                : '<p class="od-cancel-refund-note">No payment has been collected — no refund is required.</p>') +
            '<label class="visually-hidden" for="odCancelReason">Cancellation reason</label>' +
            '<input type="text" id="odCancelReason" class="od-cancel-reason" placeholder="Cancellation reason (optional)" value="Customer changed their mind">' +
            '<div class="od-cancel-actions">' +
            '<button type="button" class="btn btn-ghost" id="odKeepBtn">Keep Order</button>' +
            '<button type="button" class="btn btn-danger" id="odConfirmCancelBtn">Confirm Cancellation</button>' +
            "</div></div>"
            : "";

        // Outcome note shown after a successful cancellation.
        var cancelOutcomeNote = "";
        if (order.order_status === "cancelled") {
            cancelOutcomeNote = order.payment_status === "refunded"
                ? '<p class="od-refund-note">Simulated refund completed. No real money was transferred.</p>'
                : '<p class="od-refund-note">Order cancelled — no payment was collected.</p>';
        }

        el.modalBody.innerHTML =
            '<div class="od-hero">' +
            '<div><span class="od-hero-label">Order</span><h3>' + esc(order.order_id) + "</h3>" +
            "<p>" + esc(formatDate(order.created_at)) + "</p></div>" +
            "</div>" +

            '<div class="od-grid">' +
            '<div class="od-section">' +
            "<h4>Customer</h4>" +
            '<div class="od-row"><span>Name</span><strong>' + esc(order.customer_name || "—") + "</strong></div>" +
            '<div class="od-row"><span>Email</span><strong>' + esc(order.customer_email || "—") + "</strong></div>" +
            '<div class="od-row"><span>Phone</span><strong>' + esc(order.customer_phone || "—") + "</strong></div>" +
            "</div>" +
            '<div class="od-section">' +
            "<h4>Delivery</h4>" +
            '<div class="od-row"><span>Address</span><strong>' + esc([
                order.shipping_address, order.district, order.province, order.postal_code
            ].filter(Boolean).join(", ") || "—") + "</strong></div>" +
            "</div>" +
            "</div>" +

            // Task 5D-4 — three clearly separated status sections.
            '<div class="od-grid od-grid-3">' +
            '<div class="od-section">' +
            "<h4>Payment</h4>" +
            '<div class="od-row"><span>Method</span><strong>' + esc(paymentMethodLabel(order.payment_method)) + "</strong></div>" +
            '<div class="od-row"><span>Status</span><strong>' + badgeHTML("payment", order.payment_status) + "</strong></div>" +
            paidAt +
            paymentNoteHTML(order) +
            "</div>" +
            '<div class="od-section">' +
            "<h4>Order</h4>" +
            '<div class="od-row"><span>Status</span><strong>' + badgeHTML("order", order.order_status) + "</strong></div>" +
            cancelledAtRow +
            cancelReasonRow +
            "</div>" +
            '<div class="od-section">' +
            "<h4>Shipment</h4>" +
            '<div class="od-row"><span>Shipment status</span><strong>' + badgeHTML("shipment", order.shipment_status) + "</strong></div>" +
            '<div class="od-row"><span>Courier</span><strong>' + esc(order.shipping_provider || "—") + "</strong></div>" +
            '<div class="od-row"><span>Tracking Number</span><strong>' + esc(order.tracking_number || "—") + "</strong></div>" +
            // Task 8D — lifecycle timestamps (shown when they exist).
            (order.shipped_at
                ? '<div class="od-row"><span>Shipped at</span><strong>' + esc(formatDate(order.shipped_at)) + "</strong></div>"
                : "") +
            (order.delivered_at
                ? '<div class="od-row"><span>Delivered at</span><strong>' + esc(formatDate(order.delivered_at)) + "</strong></div>"
                : "") +
            // Task 8C — copy button only when a tracking number exists.
            (order.tracking_number
                ? '<div class="od-ship-actions"><button type="button" class="btn btn-ghost" id="odCopyTrackingBtn">Copy Tracking Number</button></div>'
                : "") +
            // Task 8D — research-prototype note whenever the parcel shipped.
            (order.shipment_status === "in_transit" || order.shipment_status === "delivered"
                ? '<p class="od-ship-note">Demo shipment only. No real courier service is connected.</p>'
                : "") +
            // Task 8C — simulated shipping notification facts + demo notice.
            (order.notification_sent
                ? '<div class="od-row"><span>Notification Sent (Simulation)</span><strong>Sent' + (order.notification_sent_at ? " " + esc(formatDate(order.notification_sent_at)) : "") + "</strong></div>"
                : "") +
            (order.notification_sent
                ? '<p class="od-ship-note">Simulated shipping notification — no real SMS or email was sent.</p>'
                : "") +
            "</div>" +
            "</div>" +

            '<p class="status-explainer od-status-explainer">' + STATUS_EXPLAINER + "</p>" +

            cancelOutcomeNote +

            '<div class="od-section od-items-section">' +
            "<h4>Items</h4>" +
            '<div class="od-items">' + itemRows + "</div>" +
            '<div class="od-totals">' +
            '<div class="od-row"><span>Subtotal</span><strong>' + formatTHB(sub) + "</strong></div>" +
            '<div class="od-row"><span>Shipping</span><strong>' + (ship === 0 ? "Free" : formatTHB(ship)) + "</strong></div>" +
            '<div class="od-row od-grand"><span>Total</span><strong>' + formatTHB(order.total_amount) + "</strong></div>" +
            "</div>" +
            "</div>" +

            '<div class="od-actions">' +
            demoPayBtn +
            cancelBtn +
            cancelPanel +
            shipBtn +
            deliverBtn +
            '<button type="button" class="btn btn-secondary" id="odAskAi">' +
            '<svg class="icon" viewBox="0 0 24 24" aria-hidden="true"><path d="M12 3a8 8 0 00-8 8c0 1.8.6 3.5 1.6 4.9L4 20l4.2-1.4A8 8 0 1012 3z" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round"/></svg>' +
            "Ask AI About This Order</button>" +
            '<a href="/" class="btn btn-ghost">Continue Shopping</a>' +
            '<a href="/orders/new" class="btn btn-ghost">Create Another Order</a>' +
            "</div>" +

            '<p class="od-research-note" id="odResearchNote" hidden>Payment is simulated for the research prototype — no real money is charged.</p>';

        var payBtn = document.getElementById("odDemoPayBtn");
        if (payBtn) payBtn.addEventListener("click", function () { confirmDemoPayment(order); });
        document.getElementById("odAskAi").addEventListener("click", function () {
            askAiAboutOrder(order);
        });

        // Demo cancellation wiring — only present for cancellable orders.
        var cancelBtnEl = document.getElementById("odCancelBtn");
        if (cancelBtnEl) cancelBtnEl.addEventListener("click", function () {
            var panel = document.getElementById("odCancelPanel");
            if (panel) panel.hidden = false;
            var reason = document.getElementById("odCancelReason");
            if (reason) reason.focus();
        });
        var keepBtn = document.getElementById("odKeepBtn");
        if (keepBtn) keepBtn.addEventListener("click", function () {
            var panel = document.getElementById("odCancelPanel");
            if (panel) panel.hidden = true;
        });
        var confirmCancelBtn = document.getElementById("odConfirmCancelBtn");
        if (confirmCancelBtn) {
            confirmCancelBtn.addEventListener("click", function () { confirmCancellation(order); });
        }

        // Task 8D — Simulate Shipment: explicit confirm, then the same
        // deterministic backend service as the AI flow (demo-shipment).
        var shipBtnEl = document.getElementById("odShipBtn");
        if (shipBtnEl) {
            shipBtnEl.addEventListener("click", function () { simulateShipment(order); });
        }

        // Task 8D — Mark Delivered (demo-delivery endpoint).
        var deliverBtnEl = document.getElementById("odDeliverBtn");
        if (deliverBtnEl) {
            deliverBtnEl.addEventListener("click", function () { markDelivered(order); });
        }

        // Task 8C — copy the tracking number to the clipboard (requirement 6).
        var copyBtnEl = document.getElementById("odCopyTrackingBtn");
        if (copyBtnEl) {
            copyBtnEl.addEventListener("click", function () {
                copyTrackingNumber(order);
            });
        }
    }

    // Demo cancellation — explicit two-step confirmation, same as the
    // backend's confirm=true contract. Disables the buttons while the
    // request is pending and re-renders from fresh SQLite facts on success.
    function confirmCancellation(order) {
        var btn = document.getElementById("odConfirmCancelBtn");
        var cancelBtn = document.getElementById("odCancelBtn");
        var reasonInput = document.getElementById("odCancelReason");
        var reason = (reasonInput && reasonInput.value.trim())
            ? reasonInput.value.trim()
            : "Customer changed their mind";
        if (btn) { btn.disabled = true; btn.classList.add("is-loading"); }
        if (cancelBtn) cancelBtn.disabled = true;
        fetch("/api/orders/" + encodeURIComponent(order.order_id) + "/cancel-demo", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ confirm: true, reason: reason })
        })
            .then(function (r) {
                return r.json().then(function (body) {
                    if (!r.ok) throw { apiError: true, body: body, status: r.status };
                    return body;
                });
            })
            .then(function (updated) {
                // Re-read the full order (the cancel response is a compact
                // status result) and refresh the modal + list from SQLite.
                return fetch("/api/orders/" + encodeURIComponent(updated.order_id))
                    .then(function (r2) {
                        if (!r2.ok) throw new Error("HTTP " + r2.status);
                        return r2.json();
                    })
                    .then(function (full) { return full; });
            })
            .then(function (full) {
                state.detail = full;
                renderDetails(full);
                showToast("Simulated refund completed. No real money was transferred.");
                refreshListAfterPayment(full.order_id);
            })
            .catch(function (err) {
                var msg = (err && err.apiError && err.body && err.body.detail)
                    ? err.body.detail
                    : "Cancellation failed — please try again.";
                showToast(msg, "error");
                if (btn) { btn.disabled = false; btn.classList.remove("is-loading"); }
                if (cancelBtn) cancelBtn.disabled = false;
            });
    }

    // Task 8D — deterministic demo shipment, same explicit two-step
    // contract as demo payment/cancellation. Disables the button while the
    // request is pending and re-renders from fresh SQLite facts on success.
    function simulateShipment(order) {
        var ok = window.confirm(
            "This simulates the shipment start for the research prototype. " +
            "No real courier service is connected."
        );
        if (!ok) return;
        var btn = document.getElementById("odShipBtn");
        if (btn) { btn.disabled = true; btn.classList.add("is-loading"); }
        fetch("/api/orders/" + encodeURIComponent(order.order_id) + "/demo-shipment", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ confirm_demo_shipment: true })
        })
            .then(function (r) {
                return r.json().then(function (body) {
                    if (!r.ok) throw { apiError: true, body: body, status: r.status };
                    return body;
                });
            })
            .then(function (updated) {
                // Re-read the full order (the ship response is a compact
                // status result) and refresh the modal + list from SQLite.
                return fetch("/api/orders/" + encodeURIComponent(updated.order_id))
                    .then(function (r2) {
                        if (!r2.ok) throw new Error("HTTP " + r2.status);
                        return r2.json();
                    });
            })
            .then(function (full) {
                state.detail = full;
                renderDetails(full);
                showToast(
                    "Shipment simulated — " + full.order_id + " is now In Transit (tracking " +
                    (full.tracking_number || "") + ")"
                );
                refreshListAfterPayment(full.order_id);
            })
            .catch(function (err) {
                var msg = (err && err.apiError && err.body && err.body.detail)
                    ? err.body.detail
                    : "Simulating shipment failed — please try again.";
                showToast(msg, "error");
                if (btn) { btn.disabled = false; btn.classList.remove("is-loading"); }
            });
    }

    // Task 8D — mark the in-transit demo parcel delivered.
    function markDelivered(order) {
        var ok = window.confirm(
            "This marks the demo parcel as delivered for the research prototype."
        );
        if (!ok) return;
        var btn = document.getElementById("odDeliverBtn");
        if (btn) { btn.disabled = true; btn.classList.add("is-loading"); }
        fetch("/api/orders/" + encodeURIComponent(order.order_id) + "/demo-delivery", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ confirm_demo_delivery: true })
        })
            .then(function (r) {
                return r.json().then(function (body) {
                    if (!r.ok) throw { apiError: true, body: body, status: r.status };
                    return body;
                });
            })
            .then(function (updated) {
                return fetch("/api/orders/" + encodeURIComponent(updated.order_id))
                    .then(function (r2) {
                        if (!r2.ok) throw new Error("HTTP " + r2.status);
                        return r2.json();
                    });
            })
            .then(function (full) {
                state.detail = full;
                renderDetails(full);
                showToast(
                    "Delivered — " + full.order_id + " has reached the destination (demo)"
                );
                refreshListAfterPayment(full.order_id);
            })
            .catch(function (err) {
                var msg = (err && err.apiError && err.body && err.body.detail)
                    ? err.body.detail
                    : "Marking delivered failed — please try again.";
                showToast(msg, "error");
                if (btn) { btn.disabled = false; btn.classList.remove("is-loading"); }
            });
    }

    // Task 8C — copy the demo tracking number to the clipboard.
    function copyTrackingNumber(order) {
        var tracking = String((order && order.tracking_number) || "");
        if (!tracking) {
            showToast("No tracking number to copy yet.", "error");
            return;
        }
        var done = function () {
            showToast("Tracking number copied to clipboard");
        };
        var fail = function () {
            // Fallback for browsers without the async Clipboard API.
            try {
                var ta = document.createElement("textarea");
                ta.value = tracking;
                ta.setAttribute("readonly", "");
                ta.style.position = "absolute";
                ta.style.left = "-9999px";
                document.body.appendChild(ta);
                ta.select();
                var ok = document.execCommand("copy");
                document.body.removeChild(ta);
                if (ok) { done(); return; }
            } catch (e) { /* fall through */ }
            showToast("Copy failed — tracking: " + tracking, "error");
        };
        if (navigator.clipboard && navigator.clipboard.writeText) {
            navigator.clipboard.writeText(tracking).then(done, fail);
        } else {
            fail();
        }
    }

    function confirmDemoPayment(order) {
        var ok = window.confirm(
            "This simulates payment for the research prototype. No real money will be charged."
        );
        if (!ok) return;
        fetch("/api/orders/" + encodeURIComponent(order.order_id) + "/demo-payment", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ confirm_demo_payment: true })
        })
            .then(function (r) {
                return r.json().then(function (body) {
                    if (!r.ok) throw { apiError: true, body: body, status: r.status };
                    return body;
                });
            })
            .then(function (updated) {
                state.detail = updated;
                renderDetails(updated);
                showToast("Demo payment confirmed — order " + updated.order_id + " is now Paid");
                refreshListAfterPayment(updated.order_id);
            })
            .catch(function (err) {
                var msg = (err && err.apiError && err.body && err.body.detail)
                    ? err.body.detail
                    : "Demo payment failed — please try again.";
                showToast(msg, "error");
            });
    }

    function refreshListAfterPayment(orderId) {
        // Re-run the current mode so the list badges stay in sync with SQLite.
        if (state.mode === "latest") fetchOrders({ limit: 20 }, "latest");
        else if (state.mode === "email" && state.email) fetchOrders({ email: state.email }, "email");
        else runSearch();
    }

    // Task 8A — Ask AI with real order context. openWithOrder attaches the
    // order context card and sends ONE automatic message; repeated Ask AI
    // clicks for the SAME order never send duplicates (chat.js guards it).
    function askAiAboutOrder(order) {
        closeDetails();
        if (!order || typeof order !== "object") return;
        if (window.Chat && window.Chat.openWithOrder) {
            window.Chat.openWithOrder(orderContextOf(order));
        } else if (window.Chat && window.Chat.open) {
            // Fallback for a stale cached chat.js (pre-Task-8A): text
            // prefill only, matching the old behaviour.
            window.Chat.open();
            if (window.Chat.sendText) window.Chat.sendText("Where is order " + order.order_id + "?");
        }
    }

    /* ═══════════════════════════════════════════════════════════
       7. LOCAL STORAGE (shared keys with store.js / create_order.js)
       ═══════════════════════════════════════════════════════════ */
    function savedEmail() {
        try { return localStorage.getItem(LS_CUSTOMER_EMAIL) || null; } catch (e) { return null; }
    }

    function savedOrderIds() {
        try {
            var arr = JSON.parse(localStorage.getItem(LS_CUSTOMER_ORDERS)) || [];
            return Array.isArray(arr) ? arr : [];
        } catch (e) { return []; }
    }

    /* ═══════════════════════════════════════════════════════════
       8. INIT
       ═══════════════════════════════════════════════════════════ */
    function init() {
        var params = new URLSearchParams(window.location.search);
        var orderIdParam = (params.get("order_id") || "").trim();
        var emailParam = (params.get("email") || "").trim();

        // With JWT auth, ownership comes from the token. Never select a
        // customer by a saved email from another browser session.
        if (window.SiamCartAuth && window.SiamCartAuth.isAuthenticated()) {
            el.latestBtn.textContent = "Refresh My Orders";
            var user = window.SiamCartAuth.currentUser();
            if (user) {
                var note = el.demoNote.querySelector("p");
                if (note) note.innerHTML = "<strong>Private account view.</strong> Only orders owned by " + esc(user.display_name) + " are returned by the API.";
            }
            if (orderIdParam) {
                el.type.value = "order_id";
                el.input.value = orderIdParam;
                runSearch();
            } else {
                loadLatest();
            }
            return;
        }

        // 1) email in the page query string (View My Orders navigation)
        if (emailParam) {
            el.type.value = "email";
            el.input.value = emailParam;
            searchByEmail(emailParam);
            return;
        }
        // 2) most recently saved customer email
        var email = savedEmail();
        if (email) {
            el.type.value = "email";
            el.input.value = email;
            searchByEmail(email);
            return;
        }
        // 3) latest synthetic demo orders
        loadLatest();
    }

    /* ═══════════════════════════════════════════════════════════
       9. EVENT WIRING
       ═══════════════════════════════════════════════════════════ */
    el.form.addEventListener("submit", function (e) {
        e.preventDefault();
        runSearch();
    });

    el.latestBtn.addEventListener("click", loadLatest);

    el.list.addEventListener("click", function (e) {
        var view = e.target.closest("[data-order-view]");
        var ask = e.target.closest("[data-order-ask]");
        if (view) openDetails(view.getAttribute("data-order-view"));
        else if (ask) askAiAboutOrder(orderById(ask.getAttribute("data-order-ask")));
    });

    el.modalClose.addEventListener("click", closeDetails);
    el.modal.addEventListener("click", function (e) {
        if (e.target === el.modal) closeDetails();
    });

    var ordersAskAi = document.getElementById("ordersAskAiBtn");
    if (ordersAskAi) ordersAskAi.addEventListener("click", function () {
        if (window.Chat && window.Chat.open) window.Chat.open();
    });

    var ordersFooterAsk = document.getElementById("ordersFooterAskAi");
    if (ordersFooterAsk) ordersFooterAsk.addEventListener("click", function (e) {
        e.preventDefault();
        if (window.Chat && window.Chat.open) window.Chat.open();
    });

    document.addEventListener("keydown", function (e) {
        if (e.key === "Escape" && !el.modal.hidden) closeDetails();
    });

    function showAuthenticationRequired() {
        state.loading = false;
        el.demoNote.hidden = true;
        el.resultsHead.hidden = true;
        el.list.innerHTML =
            '<div class="orders-empty" role="status">' +
            '<h2>Sign in to view your orders</h2>' +
            '<p>Order history is private and linked to the current JWT account.</p>' +
            '<button type="button" class="btn btn-primary" id="ordersLoginBtn">Open Login</button>' +
            "</div>";
        var button = document.getElementById("ordersLoginBtn");
        if (button) button.addEventListener("click", function () {
            window.SiamCartAuth.openLogin("Sign in or use Demo Login to view My Orders.");
        });
    }

    if (window.SiamCartAuth) {
        window.SiamCartAuth.ready().then(function () {
            if (window.SiamCartAuth.isRequired() && !window.SiamCartAuth.isAuthenticated()) {
                showAuthenticationRequired();
            } else {
                init();
            }
        });
    } else {
        init();
    }
})();
