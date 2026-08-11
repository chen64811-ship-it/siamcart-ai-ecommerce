/**
 * SiamCart — Create Demo Order page (create_order.js)
 * Product picking, selected-items summary and real order creation via
 * POST /api/orders. Products always come from the live API — this page
 * contains NO hard-coded product array.
 * Vanilla JavaScript. No frameworks.
 */
(function () {
    "use strict";

    /* ═══════════════════════════════════════════════════════════
       1. STATE
       ═══════════════════════════════════════════════════════════ */
    var CATEGORY_ALL = "All Products";

    // Task 5C-3 — shared localStorage keys with store.js / orders.js.
    var LS_CUSTOMER_EMAIL = "siamcart.customer.email";
    var LS_CUSTOMER_ORDERS = "siamcart.customer.orders.v1";

    var state = {
        products: [],      // camelCase-mapped API products
        loaded: false,
        query: "",
        category: CATEGORY_ALL,
        pickQty: {},       // product_id -> quantity in the stepper
        selected: {},      // product_id -> quantity chosen for the order
        submitting: false,
        lastOrder: null
    };

    var FALLBACK = "data:image/svg+xml;charset=utf-8," + encodeURIComponent(
        '<svg xmlns="http://www.w3.org/2000/svg" width="400" height="400"><rect width="400" height="400" fill="#EEF1F6"/><g fill="none" stroke="#9AA5B5" stroke-width="14" stroke-linecap="round"><path d="M120 240c30-50 80-60 110-20 14 19 26 20 40 0 30-40 80-30 110 20"/><path d="M150 170c0-40 30-60 60-60s60 20 60 60" transform="translate(0,-10)"/></g></svg>');

    /* ═══════════════════════════════════════════════════════════
       2. DOM REFERENCES
       ═══════════════════════════════════════════════════════════ */
    var el = {
        search: document.getElementById("coSearch"),
        category: document.getElementById("coCategory"),
        list: document.getElementById("coProducts"),
        selectedList: document.getElementById("coSelectedList"),
        selectedCount: document.getElementById("coSelectedCount"),
        subtotal: document.getElementById("coSubtotal"),
        shipping: document.getElementById("coShipping"),
        total: document.getElementById("coTotal"),
        builder: document.getElementById("coBuilder"),
        success: document.getElementById("coSuccess"),
        form: document.getElementById("createOrderForm"),
        submitError: document.getElementById("coSubmitError"),
        createBtn: document.getElementById("coCreateBtn"),
        successRef: document.getElementById("coSuccessRef"),
        successDetails: document.getElementById("coSuccessDetails"),
        successItems: document.getElementById("coSuccessItems"),
        successAskAi: document.getElementById("coSuccessAskAi"),
        successView: document.getElementById("coSuccessView"),
        successViewOrders: document.getElementById("coSuccessViewOrders"),
        successAgain: document.getElementById("coSuccessAgain")
    };

    /* ═══════════════════════════════════════════════════════════
       3. HELPERS
       ═══════════════════════════════════════════════════════════ */
    function esc(s) {
        return String(s == null ? "" : s)
            .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
            .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
    }

    function formatTHB(n) {
        return "\u0E3F" + Number(n || 0).toLocaleString("en-US");
    }

    /* ── Task 5D-4 — payment-method helpers ─────────────────────── */
    function normalizePaymentMethod(m) {
        return String(m == null ? "" : m).toLowerCase().replace(/[\s-]+/g, "_");
    }
    function isCodMethod(m) {
        var n = normalizePaymentMethod(m);
        return n === "cod" || n === "cash_on_delivery";
    }
    function isDemoPayableMethod(m) {
        var n = normalizePaymentMethod(m);
        return n === "bank_transfer" || n === "credit_card" || n === "debit_card" || n === "card";
    }
    function paymentMethodDisplay(m) {
        var n = normalizePaymentMethod(m);
        if (n === "cod" || n === "cash_on_delivery") return "Cash on Delivery";
        if (n === "bank_transfer") return "Bank Transfer";
        if (n === "card" || n === "credit_card" || n === "debit_card") return "Demo Card Payment";
        return humanizeStatus(m);
    }
    function humanizeStatus(s) {
        if (s == null || s === "") return "—";
        return String(s).replace(/_/g, " ").replace(/\b\w/g, function (c) { return c.toUpperCase(); });
    }
    function formatDateTime(iso) {
        if (!iso) return "—";
        var d = new Date(iso);
        if (isNaN(d.getTime())) return String(iso);
        return d.toLocaleString("en-US", { year: "numeric", month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
    }
    var STATUS_EXPLAINER = "Payment shows whether money has been received. Order shows the merchant processing stage. Shipment shows whether the parcel has been dispatched.";
    function statusSectionsHTML(order) {
        return '<div class="confirm-status-block">' +
            '<div class="confirm-detail-row"><span>Payment</span><strong>' + humanizeStatus(order.payment_status) + "</strong></div>" +
            (order.paid_at ? '<div class="confirm-detail-row"><span>Paid at</span><strong>' + esc(formatDateTime(order.paid_at)) + "</strong></div>" : "") +
            '<div class="confirm-detail-row"><span>Order</span><strong>' + humanizeStatus(order.order_status) + "</strong></div>" +
            '<div class="confirm-detail-row"><span>Shipment status</span><strong>' + humanizeStatus(order.shipment_status) + "</strong></div>" +
            '<p class="status-explainer">' + STATUS_EXPLAINER + "</p>" +
            "</div>";
    }

    function getProduct(id) {
        for (var i = 0; i < state.products.length; i++) {
            if (state.products[i].id === id) return state.products[i];
        }
        return null;
    }

    function shippingFor(subtotal) { return subtotal >= 2000 || subtotal === 0 ? 0 : 49; }

    function totalQty() {
        return Object.keys(state.selected).reduce(function (n, id) { return n + state.selected[id]; }, 0);
    }

    // Task 5C-3 — remember the submitted email + created order ID (deduped).
    function rememberOrder(email, orderId) {
        if (!email || !orderId) return;
        try { localStorage.setItem(LS_CUSTOMER_EMAIL, email); } catch (e) { /* ignore */ }
        try {
            var arr = JSON.parse(localStorage.getItem(LS_CUSTOMER_ORDERS)) || [];
            if (!Array.isArray(arr)) arr = [];
            if (arr.indexOf(orderId) === -1) {
                arr.push(orderId);
                localStorage.setItem(LS_CUSTOMER_ORDERS, JSON.stringify(arr));
            }
        } catch (e) { /* ignore */ }
    }

    function selectedSubtotal() {
        return Object.keys(state.selected).reduce(function (sum, id) {
            var p = getProduct(id);
            return p ? sum + p.price * state.selected[id] : sum;
        }, 0);
    }

    function showToast(msg, type) {
        var container = document.getElementById("toastContainer") || (function () {
            var c = document.createElement("div");
            c.className = "toast-container";
            c.id = "toastContainer";
            document.body.appendChild(c);
            return c;
        })();
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
       4. PRODUCT LOADING (live API only)
       ═══════════════════════════════════════════════════════════ */
    function fetchProducts() {
        el.list.innerHTML =
            '<div class="store-loading" role="status" aria-live="polite">' +
            '<span class="loading-spinner" aria-hidden="true"></span>' +
            "<p>Loading products…</p></div>";
        fetch("/api/products")
            .then(function (r) {
                if (!r.ok) throw new Error("HTTP " + r.status);
                return r.json();
            })
            .then(function (list) {
                if (!Array.isArray(list)) throw new Error("Unexpected API response");
                applyProducts(list);
            })
            .catch(function () {
                showProductsError();
            });
    }

    function applyProducts(list) {
        state.products = list.map(function (raw) {
            return {
                id: raw.product_id,
                name: raw.name,
                category: raw.category,
                price: Number(raw.price) || 0,
                stock: Number(raw.stock_quantity) || 0,
                image: raw.image_url
            };
        });
        state.loaded = true;
        renderCategoryOptions();
        renderProductList();
        renderSelected();
    }

    function renderCategoryOptions() {
        var cats = [CATEGORY_ALL];
        state.products.forEach(function (p) {
            if (p.category && cats.indexOf(p.category) === -1) cats.push(p.category);
        });
        el.category.innerHTML = cats.map(function (c) {
            return '<option value="' + esc(c) + '">' + esc(c) + "</option>";
        }).join("");
        el.category.value = state.category;
    }

    function showProductsError() {
        state.loaded = false;
        el.list.innerHTML =
            '<div class="api-error" role="alert">' +
            '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 8v5M12 16.5h.01" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round"/><circle cx="12" cy="12" r="9" fill="none" stroke="currentColor" stroke-width="1.8"/></svg>' +
            "<p><strong>Products are unavailable right now.</strong></p>" +
            "<p>The product catalog could not be loaded from the server. Please try again.</p>" +
            '<button type="button" class="btn btn-secondary" id="retryProductsBtn">Try again</button>' +
            "</div>";
        var retry = document.getElementById("retryProductsBtn");
        if (retry) retry.addEventListener("click", fetchProducts);
    }

    /* ═══════════════════════════════════════════════════════════
       5. PRODUCT LIST
       ═══════════════════════════════════════════════════════════ */
    function filteredProducts() {
        var q = state.query.trim().toLowerCase();
        return state.products.filter(function (p) {
            var okCat = state.category === CATEGORY_ALL || p.category === state.category;
            if (!okCat) return false;
            if (!q) return true;
            return (p.name + " " + p.category).toLowerCase().indexOf(q) !== -1;
        });
    }

    function stockHTML(p) {
        if (p.stock <= 0) return '<span class="opr-stock out">Out of stock</span>';
        if (p.stock <= 10) return '<span class="opr-stock low">Only ' + p.stock + " left</span>";
        return '<span class="opr-stock in">In stock · ' + p.stock + " available</span>";
    }

    function renderProductList() {
        if (!state.loaded) return;
        var list = filteredProducts();
        if (!list.length) {
            el.list.innerHTML = '<div class="empty-state"><p>No products match your search.</p></div>';
            return;
        }
        el.list.innerHTML = list.map(function (p) {
            var unavailable = p.stock <= 0;
            var qty = Math.max(1, Math.min(p.stock, state.pickQty[p.id] || 1));
            state.pickQty[p.id] = qty;
            return '<div class="order-product-row' + (unavailable ? " is-unavailable" : "") + '" data-id="' + p.id + '">' +
                '<img src="' + p.image + '" alt="' + esc(p.name) + '" loading="lazy">' +
                '<div class="opr-info">' +
                "<strong>" + esc(p.name) + "</strong>" +
                '<span class="opr-meta">' + esc(p.category) + " · " + p.id + "</span>" +
                '<span class="opr-price">' + formatTHB(p.price) + "</span>" +
                stockHTML(p) +
                "</div>" +
                '<div class="opr-actions">' +
                '<div class="qty-stepper">' +
                '<button type="button" class="opr-minus" data-id="' + p.id + '" aria-label="Decrease quantity of ' + esc(p.name) + '"' + (unavailable ? " disabled" : "") + ">−</button>" +
                '<span class="qty-value">' + qty + "</span>" +
                '<button type="button" class="opr-plus" data-id="' + p.id + '" aria-label="Increase quantity of ' + esc(p.name) + '"' + (unavailable ? " disabled" : "") + ">+</button>" +
                "</div>" +
                '<button type="button" class="btn btn-primary opr-add" data-id="' + p.id + '"' + (unavailable ? " disabled" : "") + ">Add</button>" +
                "</div>" +
                "</div>";
        }).join("");
    }

    /* ═══════════════════════════════════════════════════════════
       6. SELECTED ITEMS SUMMARY
       ═══════════════════════════════════════════════════════════ */
    function renderSelected() {
        var ids = Object.keys(state.selected);
        el.selectedCount.textContent = ids.length ? "(" + totalQty() + " items)" : "";
        if (!ids.length) {
            el.selectedList.innerHTML =
                '<div class="selected-empty">No items selected yet — pick a product on the left and press Add.</div>';
            el.subtotal.textContent = formatTHB(0);
            el.shipping.textContent = "—";
            el.total.textContent = formatTHB(0);
            return;
        }
        el.selectedList.innerHTML = ids.map(function (id) {
            var p = getProduct(id);
            var qty = state.selected[id];
            return '<div class="selected-item" data-id="' + id + '">' +
                '<img src="' + p.image + '" alt="" loading="lazy">' +
                '<div class="selected-item-info">' +
                "<strong>" + esc(p.name) + "</strong>" +
                "<span>" + qty + " × " + formatTHB(p.price) + "</span>" +
                "</div>" +
                '<span class="selected-item-price">' + formatTHB(p.price * qty) + "</span>" +
                '<button type="button" class="selected-item-remove" data-remove="' + id + '" aria-label="Remove ' + esc(p.name) + ' from the order">Remove</button>' +
                "</div>";
        }).join("");
        var sub = selectedSubtotal();
        var ship = shippingFor(sub);
        el.subtotal.textContent = formatTHB(sub);
        el.shipping.textContent = ship === 0 ? "Free" : formatTHB(ship);
        el.total.textContent = formatTHB(sub + ship);
    }

    function addSelected(id) {
        var p = getProduct(id);
        if (!p || p.stock <= 0) return;
        var want = state.pickQty[id] || 1;
        var current = state.selected[id] || 0;
        var room = p.stock - current;
        if (room <= 0) {
            showToast("Only " + p.stock + " of this item can be ordered", "error");
            return;
        }
        var add = Math.min(want, room);
        state.selected[id] = current + add;
        if (add < want) {
            state.pickQty[id] = add;
            showToast("Only " + add + " more in stock — added " + add);
        } else {
            showToast("Added " + add + " × " + p.name + " to the order");
        }
        renderProductList();
        renderSelected();
    }

    /* ═══════════════════════════════════════════════════════════
       7. FORM VALIDATION
       ═══════════════════════════════════════════════════════════ */
    function validateForm() {
        var ok = true;
        function mark(id, msg) {
            var input = document.getElementById(id);
            var err = el.form.querySelector('.co-error[data-for="' + id + '"]');
            input.classList.toggle("invalid", !!msg);
            if (err) err.textContent = msg || "";
            if (msg) ok = false;
        }
        mark("coName", document.getElementById("coName").value.trim() ? "" : "Please enter your full name.");
        mark("coEmail", /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(document.getElementById("coEmail").value.trim()) ? "" : "Please enter a valid email address.");
        mark("coPhone", /^[0-9+\-\s]{9,}$/.test(document.getElementById("coPhone").value.trim()) ? "" : "Please enter a valid phone number.");
        mark("coAddress", document.getElementById("coAddress").value.trim() ? "" : "Please enter your delivery address.");
        mark("coDistrict", document.getElementById("coDistrict").value.trim() ? "" : "Please enter your district.");
        mark("coProvince", document.getElementById("coProvince").value.trim() ? "" : "Please enter your province.");
        mark("coPostal", /^\d{5}$/.test(document.getElementById("coPostal").value.trim()) ? "" : "Postal code must be 5 digits.");

        // Task 5D-4 — a payment method must be actively selected.
        var payChecked = el.form.querySelector('input[name="payment"]:checked');
        var payErr = el.form.querySelector('.co-error[data-for="payment"]');
        if (!payChecked) {
            if (payErr) payErr.textContent = "Please select a payment method.";
            ok = false;
        } else if (payErr) {
            payErr.textContent = "";
        }
        return ok;
    }

    function selectedPayment() {
        var checked = el.form.querySelector('input[name="payment"]:checked');
        return checked ? checked.value : "";
    }

    // Task 5D-4 — no payment method is preselected. The submit button is
    // disabled until the customer actively chooses one, and its label
    // reflects the chosen method.
    function updateCreateButton() {
        var checked = el.form.querySelector('input[name="payment"]:checked');
        var btn = el.createBtn;
        if (!checked) {
            btn.disabled = true;
            btn.textContent = "Select a Payment Method";
            return;
        }
        btn.disabled = false;
        if (isCodMethod(checked.value)) {
            btn.textContent = "Place Cash on Delivery Order";
        } else if (isDemoPayableMethod(checked.value)) {
            btn.textContent = "Place Order & Continue to Demo Payment";
        } else {
            btn.textContent = "Create Demo Order";
        }
    }

    /* ═══════════════════════════════════════════════════════════
       8. ORDER SUBMISSION (POST /api/orders)
       ═══════════════════════════════════════════════════════════ */
    function buildPayload() {
        return {
            customer_name: document.getElementById("coName").value.trim(),
            customer_email: document.getElementById("coEmail").value.trim(),
            customer_phone: document.getElementById("coPhone").value.trim(),
            shipping_address: document.getElementById("coAddress").value.trim(),
            district: document.getElementById("coDistrict").value.trim(),
            province: document.getElementById("coProvince").value.trim(),
            postal_code: document.getElementById("coPostal").value.trim(),
            payment_method: selectedPayment(),
            items: Object.keys(state.selected).map(function (id) {
                return { product_id: id, quantity: state.selected[id] };
            })
        };
    }

    function apiErrorMessage(body, status) {
        if (body && typeof body.detail === "string") return body.detail;
        if (body && Array.isArray(body.detail)) {
            return body.detail.map(function (d) {
                var field = Array.isArray(d.loc) ? d.loc[d.loc.length - 1] : d.loc;
                return field ? field + ": " + d.msg : d.msg;
            }).join("; ");
        }
        return "Could not create the order (HTTP " + status + "). Please try again.";
    }

    function setSubmitting(on) {
        state.submitting = on;
        el.createBtn.disabled = on;
        el.createBtn.classList.toggle("is-loading", on);
        if (on) {
            el.createBtn.textContent = "Creating Order...";
        } else {
            el.createBtn.classList.remove("is-loading");
            updateCreateButton();
        }
    }

    function hideSubmitError() { el.submitError.hidden = true; el.submitError.textContent = ""; }
    function showSubmitError(msg) { el.submitError.textContent = msg; el.submitError.hidden = false; }

    function onOrderCreated(order) {
        state.lastOrder = order;
        var orderId = (order && (order.order_id || order.id)) || "";
        // Task 5C-3 — remember email + order ID so My Orders can pre-fill.
        var emailInput = document.getElementById("coEmail");
        rememberOrder(emailInput ? emailInput.value.trim() : "", orderId);
        // Clear selected items only after success.
        state.selected = {};
        setSubmitting(false);
        renderSuccess(order, orderId);
        el.builder.hidden = true;
        el.success.hidden = false;
        showToast("Order created successfully — " + orderId);
        var again = el.successAgain;
        if (again && again.focus) again.focus();
    }

    function renderSuccess(order, orderId) {
        el.successRef.textContent = orderId;

        // Task 5D-4 — COD stays pending; bank/card orders wait for the
        // explicit Confirm Demo Payment action. Never claim payment success
        // when only the order was created.
        var cod = isCodMethod(order.payment_method);
        var paid = order.payment_status === "paid";
        var title = document.getElementById("coSuccessTitle");
        if (title) title.textContent = paid ? "Demo payment confirmed" : "Order created successfully";

        var html =
            '<div class="confirm-detail-row"><span>Payment method</span><strong>' + esc(paymentMethodDisplay(order.payment_method)) + "</strong></div>" +
            statusSectionsHTML(order);
        if (cod) {
            html += '<p class="co-pay-note">No payment has been collected. You will pay when the order is delivered.</p>';
        } else if (paid) {
            html += '<p class="co-pay-note">Payment received for this research prototype.</p>';
        } else {
            html += '<div class="confirm-pay-actions">' +
                '<button type="button" class="btn btn-primary" id="coDemoPayBtn">Confirm Demo Payment</button>' +
                '<p class="co-pay-note">This simulates payment for the research prototype. No real money will be charged.</p>' +
                "</div>";
        }
        el.successDetails.innerHTML = html;

        var items = Array.isArray(order.order_items) ? order.order_items : [];
        if (items.length) {
            el.successItems.hidden = false;
            el.successItems.innerHTML = items.map(function (it) {
                var name = it.product_name || it.name || it.product_id || "";
                var qty = it.quantity || 0;
                var unit = it.unit_price != null ? it.unit_price : (it.price != null ? it.price : 0);
                var line = it.line_total != null ? it.line_total : unit * qty;
                return '<div class="co-summary-item">' +
                    '<div class="co-summary-item-info"><strong>' + esc(name) + "</strong><span>" + qty + " × " + formatTHB(unit) + "</span></div>" +
                    '<span class="co-summary-item-price">' + formatTHB(line) + "</span>" +
                    "</div>";
            }).join("");
        } else {
            el.successItems.hidden = true;
            el.successItems.innerHTML = "";
        }

        var payBtn = document.getElementById("coDemoPayBtn");
        if (payBtn) payBtn.addEventListener("click", function () { confirmDemoPayment(order); });
    }

    // Task 5D-4 — demo-payment is called ONLY after the customer explicitly
    // clicks Confirm Demo Payment on the order-confirmation screen. It is
    // never called automatically after order creation.
    function confirmDemoPayment(order) {
        var orderId = (order && (order.order_id || order.id)) || "";
        if (!orderId) return;
        var btn = document.getElementById("coDemoPayBtn");
        if (btn) { btn.disabled = true; btn.textContent = "Confirming..."; }
        fetch("/api/orders/" + encodeURIComponent(orderId) + "/demo-payment", {
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
                state.lastOrder = updated;
                renderSuccess(updated, orderId);
                showToast("Demo payment confirmed — " + orderId + " is now Paid");
            })
            .catch(function (err) {
                var msg = (err && err.apiError && err.body && err.body.detail)
                    ? err.body.detail
                    : "Demo payment failed — please try again.";
                if (btn) { btn.disabled = false; btn.textContent = "Confirm Demo Payment"; }
                showToast(msg, "error");
            });
    }

    function resetAll() {
        state.selected = {};
        state.pickQty = {};
        state.lastOrder = null;
        el.form.reset();
        el.form.querySelectorAll(".co-error").forEach(function (e) { e.textContent = ""; });
        el.form.querySelectorAll("input").forEach(function (i) { i.classList.remove("invalid"); });
        hideSubmitError();
        setSubmitting(false);
        renderSelected();
        renderProductList();
        el.success.hidden = true;
        el.builder.hidden = false;
        if (window.SiamCartAuth) window.SiamCartAuth.applyUserToForms();
    }

    /* ═══════════════════════════════════════════════════════════
       9. EVENT WIRING
       ═══════════════════════════════════════════════════════════ */
    el.search.addEventListener("input", function () {
        state.query = this.value;
        renderProductList();
    });

    el.category.addEventListener("change", function () {
        state.category = this.value;
        renderProductList();
    });

    // Product list: quantity steppers + Add (delegation).
    el.list.addEventListener("click", function (e) {
        var btn = e.target.closest("[data-id]");
        if (!btn) return;
        var id = btn.getAttribute("data-id");
        var p = getProduct(id);
        if (!p) return;
        if (btn.classList.contains("opr-plus")) {
            if (state.pickQty[id] < p.stock) { state.pickQty[id] += 1; renderProductList(); }
        } else if (btn.classList.contains("opr-minus")) {
            if (state.pickQty[id] > 1) { state.pickQty[id] -= 1; renderProductList(); }
        } else if (btn.classList.contains("opr-add")) {
            addSelected(id);
        }
    });

    // Product images: broken-image fallback (capture phase — error events
    // do not bubble, but they do traverse the capture path).
    el.list.addEventListener("error", function (e) {
        if (e.target && e.target.tagName === "IMG") {
            e.target.onerror = null;
            e.target.src = FALLBACK;
        }
    }, true);

    // Selected items: remove (delegation).
    el.selectedList.addEventListener("click", function (e) {
        var btn = e.target.closest("[data-remove]");
        if (!btn) return;
        delete state.selected[btn.getAttribute("data-remove")];
        renderSelected();
    });

    el.form.addEventListener("change", function (e) {
        if (e.target && e.target.name === "payment") {
            updateCreateButton();
            var payErr = el.form.querySelector('.co-error[data-for="payment"]');
            if (payErr) payErr.textContent = "";
        }
    });

    el.form.addEventListener("submit", function (e) {
        e.preventDefault();
        if (state.submitting) return;
        if (window.SiamCartAuth && window.SiamCartAuth.isRequired() && !window.SiamCartAuth.isAuthenticated()) {
            window.SiamCartAuth.openLogin("Sign in or use Demo Login to create an order.");
            return;
        }
        if (!Object.keys(state.selected).length) {
            showToast("Select at least one product first", "error");
            return;
        }
        if (!validateForm()) {
            showToast("Please fix the highlighted fields", "error");
            return;
        }
        if (!selectedPayment()) {
            showToast("Please select a payment method", "error");
            return;
        }
        hideSubmitError();
        setSubmitting(true);

        fetch("/api/orders", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(buildPayload())
        })
            .then(function (resp) {
                if (resp.status === 201) return resp.json();
                return resp.json().then(function (body) {
                    throw { apiError: true, body: body, status: resp.status };
                });
            })
            .then(function (order) {
                onOrderCreated(order);
            })
            .catch(function (err) {
                setSubmitting(false);
                if (err && err.apiError) {
                    showSubmitError(apiErrorMessage(err.body, err.status));
                    showToast("Could not create the order — please check the details", "error");
                } else {
                    showSubmitError("Network error — the order could not be created. Please try again.");
                    showToast("Network error — order not created", "error");
                }
            });
    });

    // Success actions
    el.successAskAi.addEventListener("click", function () {
        var orderId = state.lastOrder && (state.lastOrder.order_id || state.lastOrder.id);
        if (!orderId) return;
        window.location.href = "/?chat=" + encodeURIComponent(orderId);
    });

    el.successView.addEventListener("click", function () {
        var orderId = state.lastOrder && (state.lastOrder.order_id || state.lastOrder.id);
        if (!orderId) return;
        window.location.href = "/orders?order_id=" + encodeURIComponent(orderId);
    });

    el.successViewOrders.addEventListener("click", function () {
        window.location.href = "/orders";
    });

    el.successAgain.addEventListener("click", function () {
        resetAll();
        window.scrollTo({ top: 0, behavior: "smooth" });
    });

    /* ═══════════════════════════════════════════════════════════
       10. INIT
       ═══════════════════════════════════════════════════════════ */
    fetchProducts();
    renderProductList();
    renderSelected();
    if (window.SiamCartAuth) {
        window.SiamCartAuth.ready().then(window.SiamCartAuth.applyUserToForms);
    }
})();
