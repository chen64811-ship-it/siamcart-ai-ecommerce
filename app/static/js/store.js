/**
 * SiamCart Storefront — store.js
 * Product data, rendering, search, filters, sorting, product modal,
 * cart state (localStorage), cart drawer, checkout UI, demo-order
 * confirmation, product-to-chat context, toast notifications.
 * Vanilla JavaScript. No frameworks.
 */
(function () {
    "use strict";

    /* ═══════════════════════════════════════════════════════════
       1. PRODUCT DATA
       ═══════════════════════════════════════════════════════════ */
    var PRODUCTS = []; // populated from GET /api/products (Task 5C)
var PRODUCTS_LOADED = false;

    var CATEGORIES = [
        { key: "All Products", label: "All Products", icon: "grid" },
        { key: "Fashion", label: "Fashion", icon: "shirt" },
        { key: "Electronics", label: "Electronics", icon: "chip" },
        { key: "Footwear", label: "Footwear", icon: "shoe" },
        { key: "Bags & Accessories", label: "Bags & Accessories", icon: "bag" }
    ];

    /* ═══════════════════════════════════════════════════════════
       2. STATE & HELPERS
       ═══════════════════════════════════════════════════════════ */
    var LS_CART = "siamcart.cart.v1";
    var LS_WISH = "siamcart.wishlist.v1";
    var LS_VIEW = "siamcart.view";
    // Task 5C-3 — remember the customer's last email + created order IDs so
    // the My Orders page can pre-fill. SQLite stays the source of truth.
    var LS_CUSTOMER_EMAIL = "siamcart.customer.email";
    var LS_CUSTOMER_ORDERS = "siamcart.customer.orders.v1";

    var state = {
        query: "",
        category: "All Products",
        sort: "featured",
        view: localStorage.getItem(LS_VIEW) || "grid",
        cart: loadCart(),
        wishlist: loadWishlist(),
        pmQty: 1,
        pmProduct: null,
        productsLoaded: false,
        buyNow: null, // {id, qty} when checkout is for a single Buy Now product
        lastOrder: null,
        submittingOrder: false
    };

    function loadCart() {
        try {
            var raw = JSON.parse(localStorage.getItem(LS_CART)) || {};
            var clean = {};
            Object.keys(raw).forEach(function (id) {
                var p = getProduct(id);
                var qty = parseInt(raw[id], 10);
                if (p && qty > 0) clean[id] = Math.min(qty, p.stock);
            });
            return clean;
        } catch (e) { return {}; }
    }
    function saveCart() { localStorage.setItem(LS_CART, JSON.stringify(state.cart)); }
    function loadWishlist() {
        try { return JSON.parse(localStorage.getItem(LS_WISH)) || []; } catch (e) { return []; }
    }
    function saveWishlist() { localStorage.setItem(LS_WISH, JSON.stringify(state.wishlist)); }

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

    function getProduct(id) {
        for (var i = 0; i < PRODUCTS.length; i++) if (PRODUCTS[i].id === id) return PRODUCTS[i];
        return null;
    }

    /* ═══════════════════════════════════════════════════════════
       2b. PRODUCT API (Task 5C) — live SQLite products
       ═══════════════════════════════════════════════════════════ */
    function fetchProducts() {
        el.grid.innerHTML =
            '<div class="store-loading" role="status" aria-live="polite">' +
            '<span class="loading-spinner" aria-hidden="true"></span>' +
            "<p>Loading products…</p></div>";
        el.resultCount.innerHTML = "<strong>—</strong> products";

        fetch("/api/products")
            .then(function (r) {
                if (!r.ok) throw new Error("HTTP " + r.status);
                return r.json();
            })
            .then(function (list) {
                if (!Array.isArray(list)) throw new Error("Unexpected API response");
                applyProducts(list);
            })
            .catch(function (err) {
                showProductsError(err);
            });
    }

    function applyProducts(list) {
        PRODUCTS.length = 0;
        list.forEach(function (raw) {
            PRODUCTS.push({
                id: raw.product_id,
                name: raw.name,
                shortDescription: raw.short_description || "",
                fullDescription: raw.full_description || "",
                category: raw.category,
                price: Number(raw.price) || 0,
                originalPrice: raw.original_price == null ? null : Number(raw.original_price),
                rating: Number(raw.rating) || 0,
                reviewCount: Number(raw.review_count) || 0,
                stock: Number(raw.stock_quantity) || 0,
                badge: raw.badge || null,
                image: raw.image_url,
                thumbnails: Array.isArray(raw.thumbnail_urls) && raw.thumbnail_urls.length
                    ? raw.thumbnail_urls
                    : [raw.image_url],
                features: Array.isArray(raw.features) ? raw.features : [],
                newInDays: raw.created_at
                    ? Math.max(0, Math.floor((Date.now() - new Date(raw.created_at).getTime()) / 86400000))
                    : 999
            });
        });
        state.productsLoaded = true;
        PRODUCTS_LOADED = true;

        // Reconcile cart quantities with current (live) stock
        var changed = false;
        Object.keys(state.cart).forEach(function (id) {
            var p = getProduct(id);
            if (!p) {
                delete state.cart[id];
                changed = true;
            } else if (state.cart[id] > p.stock) {
                state.cart[id] = p.stock;
                changed = true;
            }
        });
        if (changed) saveCart();

        renderCategories();
        renderProducts();
        updateCartBadge();
        renderCartDrawer();
        maybeRunChatPrefill();
    }

    function showProductsError(err) {
        state.productsLoaded = false;
        el.grid.innerHTML =
            '<div class="api-error" role="alert">' +
            '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 8v5M12 16.5h.01" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round"/><circle cx="12" cy="12" r="9" fill="none" stroke="currentColor" stroke-width="1.8"/></svg>' +
            "<p><strong>Products are unavailable right now.</strong></p>" +
            "<p>The product catalog could not be loaded from the server. Please try again.</p>" +
            '<button type="button" class="btn btn-secondary" id="retryProductsBtn">Try again</button>' +
            "</div>";
        el.resultCount.innerHTML = "";
        el.activeCategory.textContent = "All Products";
        var retry = document.getElementById("retryProductsBtn");
        if (retry) retry.addEventListener("click", fetchProducts);
        // Do NOT fall back to stale hard-coded product data — the visible
        // error state above is the only message.
    }

    function maybeRunChatPrefill() {
        // Support /?chat=<question> (used by the Create Demo Order page)
        var params = new URLSearchParams(window.location.search);
        var question = params.get("chat");
        if (!question) return;
        // Bare order IDs (e.g. /?chat=ORD-1006) become the same friendly
        // question the checkout confirmation uses.
        if (/^ord-\d+$/i.test(question.trim())) {
            question = "Where is order " + question.trim().toUpperCase() + "?";
        }
        setTimeout(function () {
            if (window.Chat && window.Chat.open) {
                window.Chat.open();
                window.Chat.sendText(question);
            }
        }, 400);
    }

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

    function starsHTML(rating) {
        var filled = Math.round(rating || 0);
        var html = "";
        for (var i = 1; i <= 5; i++) {
            html += i <= filled
                ? '<svg viewBox="0 0 24 24" aria-hidden="true"><path fill="currentColor" d="M12 2l2.9 6.3 6.9.7-5.2 4.6 1.5 6.8L12 17.2 5.9 20.4l1.5-6.8L2.2 9l6.9-.7z"/></svg>'
                : '<svg class="star-empty" viewBox="0 0 24 24" aria-hidden="true"><path fill="currentColor" d="M12 2l2.9 6.3 6.9.7-5.2 4.6 1.5 6.8L12 17.2 5.9 20.4l1.5-6.8L2.2 9l6.9-.7z"/></svg>';
        }
        return '<span class="stars" role="img" aria-label="Rated ' + rating + ' out of 5">' + html + "</span>";
    }

    function badgeHTML(p) {
        if (!p.badge) return "";
        var cls = "product-badge";
        if (p.badge === "New") cls += " badge-new";
        else if (p.badge === "Limited") cls += " badge-limited";
        else cls += " badge-bestseller";
        return '<span class="' + cls + '">' + esc(p.badge) + "</span>";
    }

    function stockLabel(p) {
        if (p.stock <= 0) return '<span class="pm-stock out"><i class="status-dot"></i>Out of stock</span>';
        if (p.stock <= 10) return '<span class="pm-stock low"><i class="status-dot"></i>Only ' + p.stock + ' left in stock</span>';
        return '<span class="pm-stock in-stock"><i class="status-dot"></i>In stock — ships within 24h</span>';
    }

    var ICONS = {
        grid: '<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="4" y="4" width="7" height="7" rx="1.5" fill="none" stroke="currentColor" stroke-width="1.8"/><rect x="13" y="4" width="7" height="7" rx="1.5" fill="none" stroke="currentColor" stroke-width="1.8"/><rect x="4" y="13" width="7" height="7" rx="1.5" fill="none" stroke="currentColor" stroke-width="1.8"/><rect x="13" y="13" width="7" height="7" rx="1.5" fill="none" stroke="currentColor" stroke-width="1.8"/></svg>',
        shirt: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M8 3l4 3 4-3 4 4-3 2v12H7V9L4 7z" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round"/></svg>',
        chip: '<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="6" y="6" width="12" height="12" rx="2" fill="none" stroke="currentColor" stroke-width="1.8"/><path d="M9 1v4M15 1v4M9 19v4M15 19v4M1 9h4M1 15h4M19 9h4M19 15h4" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/></svg>',
        shoe: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M2 16c3-3 6-4 10-4 3 0 6 1 8 3 1 .8.7 3-1 3H4c-1.5 0-2.5-1-2-2z" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round"/><path d="M12 12V9M9 9V6M15 9V6" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/></svg>',
        bag: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M5 8h14l-1 12H6z" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round"/><path d="M9 10V6a3 3 0 016 0v4" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/></svg>'
    };

    var CHECK_ICON = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M9 12l2 2 4-4" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"/></svg>';
    var CART_ICON = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M3 4h2.5l2 12h11l2-8H7" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/><circle cx="9.5" cy="20" r="1.6" fill="currentColor"/><circle cx="17" cy="20" r="1.6" fill="currentColor"/></svg>';

    /* ═══════════════════════════════════════════════════════════
       3. DOM REFERENCES
       ═══════════════════════════════════════════════════════════ */
    var el = {
        grid: document.getElementById("productGrid"),
        categoryGrid: document.getElementById("categoryGrid"),
        searchInput: document.getElementById("searchInput"),
        searchForm: document.getElementById("searchForm"),
        sortSelect: document.getElementById("sortSelect"),
        viewGrid: document.getElementById("viewGrid"),
        viewList: document.getElementById("viewList"),
        resultCount: document.getElementById("resultCount"),
        activeCategory: document.getElementById("activeCategory"),
        emptyState: document.getElementById("emptyState"),
        clearFilters: document.getElementById("clearFiltersBtn"),
        cartToggle: document.getElementById("cartToggle"),
        cartBadge: document.getElementById("cartBadge"),
        cartBackdrop: document.getElementById("cartBackdrop"),
        cartDrawer: document.getElementById("cartDrawer"),
        cartDrawerCount: document.getElementById("cartDrawerCount"),
        cartItems: document.getElementById("cartItems"),
        cartFooter: document.getElementById("cartFooter"),
        cartSubtotal: document.getElementById("cartSubtotal"),
        cartShipping: document.getElementById("cartShipping"),
        cartGrandTotal: document.getElementById("cartGrandTotal"),
        checkoutBtn: document.getElementById("checkoutBtn"),
        continueShopping: document.getElementById("continueShoppingBtn"),
        cartClose: document.getElementById("cartClose"),
        productModal: document.getElementById("productModal"),
        pmClose: document.getElementById("pmClose"),
        pmBody: document.getElementById("pmBody"),
        checkoutModal: document.getElementById("checkoutModal"),
        checkoutView: document.getElementById("checkoutView"),
        confirmationView: document.getElementById("confirmationView"),
        checkoutForm: document.getElementById("checkoutForm"),
        coClose: document.getElementById("coClose"),
        coBackToCart: document.getElementById("coBackToCart"),
        coSummaryItems: document.getElementById("coSummaryItems"),
        coSubtotal: document.getElementById("coSubtotal"),
        coShipping: document.getElementById("coShipping"),
        coTotal: document.getElementById("coTotal"),
        coPlaceOrder: document.getElementById("coPlaceOrder"),
        coSubmitError: document.getElementById("coSubmitError"),
        confirmRef: document.getElementById("confirmRef"),
        confirmDetails: document.getElementById("confirmDetails"),
        confirmOrderItems: document.getElementById("confirmOrderItems"),
        confirmAskAi: document.getElementById("confirmAskAiBtn"),
        confirmView: document.getElementById("confirmViewBtn"),
        confirmViewOrders: document.getElementById("confirmViewOrders"),
        confirmDone: document.getElementById("confirmDoneBtn"),
        toastContainer: document.getElementById("toastContainer"),
        accountBtn: document.getElementById("accountBtn"),
        ordersBtn: document.getElementById("ordersBtn"),
        heroDealBtn: document.getElementById("heroDealBtn"),
        menuBtn: document.getElementById("menuBtn"),
        mobileNav: document.getElementById("mobileNav")
    };

    /* ═══════════════════════════════════════════════════════════
       4. RENDERING — categories
       ═══════════════════════════════════════════════════════════ */
    function renderCategories() {
        var html = CATEGORIES.map(function (c) {
            var count = c.key === "All Products"
                ? PRODUCTS.length
                : PRODUCTS.filter(function (p) { return p.category === c.key; }).length;
            var active = state.category === c.key ? " active" : "";
            return '<button type="button" class="category-tile' + active + '" data-category="' + esc(c.key) + '" aria-pressed="' + (active ? "true" : "false") + '">' +
                '<span class="category-icon">' + ICONS[c.icon] + "</span>" +
                '<span class="category-name">' + esc(c.label) + "</span>" +
                '<span class="category-count">' + count + " items</span>" +
                "</button>";
        }).join("");
        el.categoryGrid.innerHTML = html;
    }

    /* ═══════════════════════════════════════════════════════════
       5. RENDERING — product grid / list
       ═══════════════════════════════════════════════════════════ */
    function filteredProducts() {
        var q = state.query.trim().toLowerCase();
        var list = PRODUCTS.filter(function (p) {
            var okCat = state.category === "All Products" || p.category === state.category;
            if (state.category === "New") okCat = p.badge === "New";
            if (!okCat) return false;
            if (!q) return true;
            var hay = (p.name + " " + p.category + " " + p.shortDescription + " " + p.fullDescription).toLowerCase();
            return hay.indexOf(q) !== -1;
        });

        var sort = state.sort;
        list.sort(function (a, b) {
            if (sort === "price-asc") return a.price - b.price;
            if (sort === "price-desc") return b.price - a.price;
            if (sort === "newest") return (a.newInDays || 999) - (b.newInDays || 999);
            // featured: bestsellers first, then rating desc
            var ab = a.badge === "Bestseller" ? 0 : 1;
            var bb = b.badge === "Bestseller" ? 0 : 1;
            if (ab !== bb) return ab - bb;
            return b.rating - a.rating;
        });
        return list;
    }

    function cardHTML(p) {
        var wished = state.wishlist.indexOf(p.id) !== -1;
        var original = p.originalPrice
            ? '<span class="product-original">' + formatTHB(p.originalPrice) + "</span>"
            : "";
        return '<article class="product-card" data-id="' + p.id + '">' +
            '<div class="product-media">' +
            badgeHTML(p) +
            '<button type="button" class="wishlist-btn' + (wished ? " wished" : "") + '" data-action="wish" data-id="' + p.id + '" aria-label="' + (wished ? "Remove from wishlist" : "Add to wishlist") + '" aria-pressed="' + (wished ? "true" : "false") + '">' +
            '<svg viewBox="0 0 24 24" aria-hidden="true"><path fill="none" stroke="currentColor" stroke-width="1.8" d="M12 20s-7-4.5-9-9c-1.6-3.8 1-7.5 4.8-7.5 2 0 3.4 1.1 4.2 2.4.8-1.3 2.2-2.4 4.2-2.4 3.8 0 6.4 3.7 4.8 7.5-2 4.5-9 9-9 9z"/></svg>' +
            "</button>" +
            '<img src="' + p.image + '" alt="' + esc(p.name) + '" loading="lazy" decoding="async" onerror="this.onerror=null;this.src=window.SiamCart.fallbackImage;">' +
            '<button type="button" class="quick-view-btn" data-action="quickview" data-id="' + p.id + '">Quick View</button>' +
            "</div>" +
            '<div class="product-info">' +
            '<span class="product-cat">' + esc(p.category) + "</span>" +
            '<h3 class="product-name" data-action="quickview" data-id="' + p.id + '" tabindex="0" role="button">' + esc(p.name) + "</h3>" +
            '<div class="product-rating">' + starsHTML(p.rating) + "<span>" + p.rating + " (" + p.reviewCount + " reviews)</span></div>" +
            '<div class="product-price-row"><span class="product-price">' + formatTHB(p.price) + "</span>" + original + "</div>" +
            '<div class="product-actions">' +
            '<button type="button" class="add-cart-btn" data-action="addcart" data-id="' + p.id + '">' + CART_ICON + "<span>Add to Cart</span></button>" +
            "</div>" +
            "</div>" +
            "</article>";
    }

    function renderProducts() {
        // While products are still loading (or failed), the grid owns its
        // loading/error message — do not overwrite it with an empty render.
        if (!state.productsLoaded) {
            el.grid.classList.toggle("list-view", state.view === "list");
            el.viewGrid.setAttribute("aria-pressed", state.view === "grid" ? "true" : "false");
            el.viewList.setAttribute("aria-pressed", state.view === "list" ? "true" : "false");
            el.viewGrid.classList.toggle("active", state.view === "grid");
            el.viewList.classList.toggle("active", state.view === "list");
            return;
        }
        var list = filteredProducts();

        if (!list.length) {
            el.grid.innerHTML = "";
            el.emptyState.hidden = false;
        } else {
            el.emptyState.hidden = true;
            var html = list.map(cardHTML).join("");
            el.grid.innerHTML = html;
        }
        el.grid.classList.toggle("list-view", state.view === "list");

        var total = PRODUCTS.length;
        el.resultCount.innerHTML = list.length === total
            ? "<strong>" + total + "</strong> products"
            : "<strong>" + list.length + "</strong> of " + total + " products";
        el.activeCategory.textContent = state.category === "New" ? "New Arrivals" : state.category;

        el.viewGrid.setAttribute("aria-pressed", state.view === "grid" ? "true" : "false");
        el.viewList.setAttribute("aria-pressed", state.view === "list" ? "true" : "false");
        el.viewGrid.classList.toggle("active", state.view === "grid");
        el.viewList.classList.toggle("active", state.view === "list");
    }

    /* ═══════════════════════════════════════════════════════════
       6. PRODUCT MODAL
       ═══════════════════════════════════════════════════════════ */
    function openProductModal(id) {
        var p = getProduct(id);
        if (!p) return;
        state.pmProduct = p;
        state.pmQty = 1;

        var original = p.originalPrice ? '<span class="pm-original">' + formatTHB(p.originalPrice) + "</span>" : "";
        var discount = p.originalPrice
            ? '<span class="pm-discount">Save ' + formatTHB(p.originalPrice - p.price) + "</span>"
            : "";
        var badge = p.badge ? '<span class="product-badge ' + (p.badge === "New" ? "badge-new" : p.badge === "Limited" ? "badge-limited" : "badge-bestseller") + '">' + esc(p.badge) + "</span>" : "";

        var features = p.features.map(function (f) {
            return "<li>" + CHECK_ICON + "<span>" + esc(f) + "</span></li>";
        }).join("");

        var thumbs = (p.thumbnails || [p.image]).map(function (t, i) {
            return '<button type="button" class="pm-thumb' + (i === 0 ? " active" : "") + '" data-thumb="' + i + '" aria-label="Product image ' + (i + 1) + '">' +
                '<img src="' + t + '" alt="" loading="lazy" onerror="this.onerror=null;this.src=window.SiamCart.fallbackImage;">' +
                "</button>";
        }).join("");

        el.pmBody.innerHTML =
            '<div class="pm-gallery">' +
            '<img class="pm-main-img" id="pmMainImg" src="' + p.image + '" alt="' + esc(p.name) + '" onerror="this.onerror=null;this.src=window.SiamCart.fallbackImage;">' +
            '<div class="pm-thumbs">' + thumbs + "</div>" +
            "</div>" +
            '<div class="pm-info">' +
            '<span class="pm-cat">' + esc(p.category) + " · " + p.id + "</span>" +
            '<h2 class="pm-title" id="pmTitle">' + esc(p.name) + "</h2>" +
            '<div class="pm-rating">' + starsHTML(p.rating) + "<span>" + p.rating + " · " + p.reviewCount + " reviews</span></div>" +
            '<div class="pm-price-row"><span class="pm-price">' + formatTHB(p.price) + "</span>" + original + discount + "</div>" +
            '<p class="pm-desc">' + esc(p.fullDescription) + "</p>" +
            '<p class="pm-features-title">Key features</p>' +
            '<ul class="pm-features">' + features + "</ul>" +
            stockLabel(p) +
            '<div class="pm-qty-row">' +
            '<div class="qty-stepper">' +
            '<button type="button" id="pmQtyMinus" aria-label="Decrease quantity">−</button>' +
            '<span class="qty-value" id="pmQtyValue">1</span>' +
            '<button type="button" id="pmQtyPlus" aria-label="Increase quantity">+</button>' +
            "</div>" +
            '<span class="pm-stock in-stock" style="color:var(--muted);font-size:12.5px;">Max ' + p.stock + " per order</span>" +
            "</div>" +
            '<div class="pm-buttons">' +
            '<button type="button" class="btn btn-primary" id="pmAddCart">' + CART_ICON + " Add to Cart</button>" +
            '<button type="button" class="btn btn-secondary" id="pmBuyNow">Buy Now</button>' +
            "</div>" +
            '<button type="button" class="pm-ai-btn" id="pmAskAi">' +
            '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 3a8 8 0 00-8 8c0 1.8.6 3.5 1.6 4.9L4 20l4.2-1.4A8 8 0 1012 3z" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round"/></svg>' +
            "Ask AI About This Product</button>" +
            '<div class="pm-service">' +
            '<div class="pm-service-row">' + CHECK_ICON + "<span>Free delivery in Bangkok on orders over ฿2,000 · Nationwide 2–5 business days</span></div>" +
            '<div class="pm-service-row">' + CHECK_ICON + "<span>30-day easy return guidance — ask the AI assistant how it works</span></div>" +
            '<div class="pm-service-row">' + CHECK_ICON + "<span>Secure demo checkout · Cash on delivery, bank transfer, card (demo)</span></div>" +
            "</div>" +
            "</div>";

        el.pmBody.querySelector("#pmQtyMinus").addEventListener("click", function () { pmChangeQty(-1); });
        el.pmBody.querySelector("#pmQtyPlus").addEventListener("click", function () { pmChangeQty(1); });
        el.pmBody.querySelector("#pmAddCart").addEventListener("click", function () {
            addToCart(p.id, state.pmQty);
            showToast("Added " + state.pmQty + " × " + p.name + " to cart");
        });
        el.pmBody.querySelector("#pmBuyNow").addEventListener("click", function () {
            // Buy Now: checkout this single product at the modal quantity.
            // The saved cart is NOT modified (no addToCart, no overwrite).
            state.buyNow = { id: p.id, qty: state.pmQty };
            closeProductModal();
            openCheckout();
        });
        el.pmBody.querySelector("#pmAskAi").addEventListener("click", function () {
            closeProductModal();
            if (window.Chat && window.Chat.openWithProduct) {
                window.Chat.openWithProduct(p);
            }
        });
        el.pmBody.querySelectorAll(".pm-thumb").forEach(function (btn) {
            btn.addEventListener("click", function () {
                var i = parseInt(btn.getAttribute("data-thumb"), 10);
                var src = (p.thumbnails || [p.image])[i];
                el.pmBody.querySelector("#pmMainImg").src = src;
                el.pmBody.querySelectorAll(".pm-thumb").forEach(function (t) { t.classList.remove("active"); });
                btn.classList.add("active");
            });
        });

        el.productModal.hidden = false;
        document.body.classList.add("modal-open");
        rememberFocus();
        el.pmClose.focus();
    }

    function closeProductModal() {
        el.productModal.hidden = true;
        document.body.classList.remove("modal-open");
        restoreFocus();
    }

    function pmChangeQty(delta) {
        var p = state.pmProduct;
        if (!p) return;
        state.pmQty = Math.max(1, Math.min(p.stock, state.pmQty + delta));
        var v = el.pmBody.querySelector("#pmQtyValue");
        if (v) v.textContent = state.pmQty;
    }

    /* ═══════════════════════════════════════════════════════════
       7. CART
       ═══════════════════════════════════════════════════════════ */
    function cartCount() {
        return Object.keys(state.cart).reduce(function (n, id) { return n + state.cart[id]; }, 0);
    }
    function cartSubtotal() {
        return Object.keys(state.cart).reduce(function (sum, id) {
            var p = getProduct(id);
            return p ? sum + p.price * state.cart[id] : sum;
        }, 0);
    }
    function shippingFor(subtotal) { return subtotal >= 2000 || subtotal === 0 ? 0 : 49; }

    function addToCart(id, qty) {
        var p = getProduct(id);
        if (!p) return;
        qty = qty || 1;
        var cur = state.cart[id] || 0;
        state.cart[id] = Math.min(p.stock, cur + qty);
        saveCart();
        updateCartBadge();
        renderCartDrawer();
    }

    function setCartQty(id, qty) {
        var p = getProduct(id);
        if (!p) return;
        qty = Math.max(1, Math.min(p.stock, qty));
        state.cart[id] = qty;
        saveCart();
        updateCartBadge();
        renderCartDrawer();
        if (el.checkoutModal && !el.checkoutModal.hidden) renderCheckoutSummary();
    }

    function removeFromCart(id) {
        delete state.cart[id];
        saveCart();
        updateCartBadge();
        renderCartDrawer();
        if (el.checkoutModal && !el.checkoutModal.hidden) renderCheckoutSummary();
    }

    function clearCart() {
        state.cart = {};
        saveCart();
        updateCartBadge();
        renderCartDrawer();
    }

    function updateCartBadge() {
        var n = cartCount();
        el.cartBadge.hidden = n === 0;
        el.cartBadge.textContent = n > 99 ? "99+" : String(n);
    }

    function renderCartDrawer() {
        var ids = Object.keys(state.cart);
        el.cartDrawerCount.textContent = ids.length ? "(" + cartCount() + " items)" : "";
        el.cartFooter.hidden = ids.length === 0;

        if (!ids.length) {
            el.cartItems.innerHTML =
                '<div class="cart-empty">' +
                '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M3 4h2.5l2 12h11l2-8H7" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/><circle cx="9.5" cy="20" r="1.6" fill="currentColor"/><circle cx="17" cy="20" r="1.6" fill="currentColor"/></svg>' +
                "<p>Your cart is empty</p><span>Browse the collection and add something you love.</span>" +
                "</div>";
            el.cartSubtotal.textContent = formatTHB(0);
            el.cartShipping.textContent = "—";
            el.cartGrandTotal.textContent = formatTHB(0);
            return;
        }

        var html = ids.map(function (id) {
            var p = getProduct(id);
            var qty = state.cart[id];
            return '<div class="cart-item" data-id="' + id + '">' +
                '<img src="' + p.image + '" alt="' + esc(p.name) + '" loading="lazy" onerror="this.onerror=null;this.src=window.SiamCart.fallbackImage;">' +
                '<div class="cart-item-info">' +
                '<span class="cart-item-name" title="' + esc(p.name) + '">' + esc(p.name) + "</span>" +
                '<span class="cart-item-meta">' + esc(p.category) + " · " + p.id + "</span>" +
                '<span class="cart-item-price">' + formatTHB(p.price) + " × " + qty + "</span>" +
                '<div class="cart-item-controls">' +
                '<div class="cart-item-qty">' +
                '<button type="button" data-cart="dec" data-id="' + id + '" aria-label="Decrease quantity of ' + esc(p.name) + '"' + (qty <= 1 ? " disabled" : "") + ">−</button>" +
                '<span class="qty-value">' + qty + "</span>" +
                '<button type="button" data-cart="inc" data-id="' + id + '" aria-label="Increase quantity of ' + esc(p.name) + '"' + (qty >= p.stock ? " disabled" : "") + ">+</button>" +
                "</div>" +
                '<button type="button" class="cart-remove" data-cart="remove" data-id="' + id + '">' +
                '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 7h16M9 7V5h6v2M7 7l1 13h8l1-13" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></svg> Remove</button>' +
                "</div>" +
                "</div>" +
                "</div>";
        }).join("");

        el.cartItems.innerHTML = html;

        var sub = cartSubtotal();
        var ship = shippingFor(sub);
        el.cartSubtotal.textContent = formatTHB(sub);
        el.cartShipping.textContent = ship === 0 ? "Free" : formatTHB(ship);
        el.cartGrandTotal.textContent = formatTHB(sub + ship);
    }

    /* ═══════════════════════════════════════════════════════════
       8. CART DRAWER OPEN/CLOSE
       ═══════════════════════════════════════════════════════════ */
    function openCartDrawer() {
        renderCartDrawer();
        el.cartDrawer.hidden = false;
        el.cartBackdrop.hidden = false;
        document.body.classList.add("modal-open");
        rememberFocus();
        var closeBtn = document.getElementById("cartClose");
        if (closeBtn) closeBtn.focus();
    }
    function closeCartDrawer() {
        el.cartDrawer.hidden = true;
        el.cartBackdrop.hidden = true;
        document.body.classList.remove("modal-open");
        restoreFocus();
    }

    /* ═══════════════════════════════════════════════════════════
       9. CHECKOUT (frontend demo)
       ═══════════════════════════════════════════════════════════ */
    function openCheckout() {
        // Buy Now allows checkout even when the saved cart is empty.
        if (!state.buyNow && !cartCount()) {
            showToast("Your cart is empty — add a product first", "error");
            return;
        }
        resetCheckoutForm();
        renderCheckoutSummary();
        el.checkoutView.hidden = false;
        el.confirmationView.hidden = true;
        el.checkoutModal.hidden = false;
        document.body.classList.add("modal-open");
        rememberFocus();
        document.getElementById("coName").focus();
    }

    function closeCheckout() {
        el.checkoutModal.hidden = true;
        document.body.classList.remove("modal-open");
        // Cancelling a Buy Now flow returns to the previous state; the saved
        // cart is untouched (Buy Now never modified it).
        state.buyNow = null;
        restoreFocus();
    }

    function resetCheckoutForm() {
        el.checkoutForm.reset();
        el.checkoutForm.querySelectorAll(".co-error").forEach(function (e) { e.textContent = ""; });
        el.checkoutForm.querySelectorAll("input").forEach(function (i) { i.classList.remove("invalid"); });
        hideCheckoutSubmitError();
        updateCheckoutButton();
    }

    function hideCheckoutSubmitError() {
        el.coSubmitError.hidden = true;
        el.coSubmitError.textContent = "";
    }

    function showCheckoutSubmitError(msg) {
        el.coSubmitError.textContent = msg;
        el.coSubmitError.hidden = false;
    }

    function renderCheckoutSummary() {
        var html = "";
        var sub = 0;
        var keptNote = "";
        if (state.buyNow) {
            // Buy Now: the summary contains ONLY the Buy Now product.
            var p = getProduct(state.buyNow.id);
            if (p) {
                var qty = state.buyNow.qty;
                sub = p.price * qty;
                html = '<div class="co-summary-item">' +
                    '<img src="' + p.image + '" alt="" onerror="this.onerror=null;this.src=window.SiamCart.fallbackImage;">' +
                    '<div class="co-summary-item-info"><strong>' + esc(p.name) + "</strong><span>" + qty + " × " + formatTHB(p.price) + "</span></div>" +
                    '<span class="co-summary-item-price">' + formatTHB(sub) + "</span>" +
                    "</div>";
            }
            var others = cartCount();
            if (others > 0) {
                keptNote = '<p class="co-buy-now-note">' + others + " more item" +
                    (others === 1 ? "" : "s") + " kept in your cart</p>";
            }
        } else {
            var ids = Object.keys(state.cart);
            html = ids.map(function (id) {
                var p = getProduct(id);
                var qty = state.cart[id];
                return '<div class="co-summary-item">' +
                    '<img src="' + p.image + '" alt="" onerror="this.onerror=null;this.src=window.SiamCart.fallbackImage;">' +
                    '<div class="co-summary-item-info"><strong>' + esc(p.name) + "</strong><span>" + qty + " × " + formatTHB(p.price) + "</span></div>" +
                    '<span class="co-summary-item-price">' + formatTHB(p.price * qty) + "</span>" +
                    "</div>";
            }).join("");
            sub = cartSubtotal();
        }
        el.coSummaryItems.innerHTML = html + keptNote;
        var ship = shippingFor(sub);
        el.coSubtotal.textContent = formatTHB(sub);
        el.coShipping.textContent = ship === 0 ? "Free" : formatTHB(ship);
        el.coTotal.textContent = formatTHB(sub + ship);
    }

    function selectedPayment() {
        var checked = el.checkoutForm.querySelector('input[name="payment"]:checked');
        return checked ? checked.value : "";
    }

    // Task 5D-4 — no payment method is preselected. The submit button is
    // disabled until the customer actively chooses one, and its label
    // reflects the chosen method.
    function updateCheckoutButton() {
        var checked = el.checkoutForm.querySelector('input[name="payment"]:checked');
        var btn = el.coPlaceOrder;
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
            btn.textContent = "Place Demo Order";
        }
    }

    function validateCheckout() {
        var ok = true;
        function mark(id, msg) {
            var input = document.getElementById(id);
            var err = el.checkoutForm.querySelector('.co-error[data-for="' + id + '"]');
            input.classList.toggle("invalid", !!msg);
            if (err) err.textContent = msg || "";
            if (msg) ok = false;
        }
        var name = document.getElementById("coName").value.trim();
        var email = document.getElementById("coEmail").value.trim();
        var phone = document.getElementById("coPhone").value.trim();
        var address = document.getElementById("coAddress").value.trim();
        var district = document.getElementById("coDistrict").value.trim();
        var province = document.getElementById("coProvince").value.trim();
        var postal = document.getElementById("coPostal").value.trim();

        mark("coName", name ? "" : "Please enter your full name.");
        mark("coEmail", /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email) ? "" : "Please enter a valid email address.");
        mark("coPhone", /^[0-9+\-\s]{9,}$/.test(phone) ? "" : "Please enter a valid phone number.");
        mark("coAddress", address ? "" : "Please enter your delivery address.");
        mark("coDistrict", district ? "" : "Please enter your district.");
        mark("coProvince", province ? "" : "Please enter your province.");
        mark("coPostal", /^\d{5}$/.test(postal) ? "" : "Postal code must be 5 digits.");

        // Task 5D-4 — a payment method must be actively selected.
        var payChecked = el.checkoutForm.querySelector('input[name="payment"]:checked');
        var payErr = el.checkoutForm.querySelector('.co-error[data-for="payment"]');
        if (!payChecked) {
            if (payErr) payErr.textContent = "Please select a payment method.";
            ok = false;
        } else if (payErr) {
            payErr.textContent = "";
        }
        return ok;
    }

    function placeDemoOrder() {
        // Prevent double submission while a request is in flight.
        if (state.submittingOrder) return;
        if (!validateCheckout()) {
            showToast("Please fix the highlighted fields", "error");
            return;
        }
        // Task 5D-4 — hard guard: never submit without an explicit payment method.
        if (!selectedPayment()) {
            var payErr = el.checkoutForm.querySelector('.co-error[data-for="payment"]');
            if (payErr) payErr.textContent = "Please select a payment method.";
            showToast("Please select a payment method", "error");
            return;
        }
        hideCheckoutSubmitError();

        var payload = {
            customer_name: document.getElementById("coName").value.trim(),
            customer_email: document.getElementById("coEmail").value.trim(),
            customer_phone: document.getElementById("coPhone").value.trim(),
            shipping_address: document.getElementById("coAddress").value.trim(),
            district: document.getElementById("coDistrict").value.trim(),
            province: document.getElementById("coProvince").value.trim(),
            postal_code: document.getElementById("coPostal").value.trim(),
            payment_method: selectedPayment(),
            items: checkoutItems()
        };

        setOrderSubmitting(true);

        fetch("/api/orders", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(payload)
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
                setOrderSubmitting(false);
                if (err && err.apiError) {
                    showCheckoutSubmitError(apiErrorMessage(err.body, err.status));
                    showToast("Could not create the order — please check the details", "error");
                } else {
                    showCheckoutSubmitError("Network error — the order could not be created. Please try again.");
                    showToast("Network error — order not created", "error");
                }
            });
    }

    // Items for the POST body: the Buy Now product only, or the saved cart.
    function checkoutItems() {
        if (state.buyNow) {
            return [{ product_id: state.buyNow.id, quantity: state.buyNow.qty }];
        }
        return Object.keys(state.cart).map(function (id) {
            return { product_id: id, quantity: state.cart[id] };
        });
    }

    function setOrderSubmitting(on) {
        state.submittingOrder = on;
        el.coPlaceOrder.disabled = on;
        el.coPlaceOrder.classList.toggle("is-loading", on);
        if (on) {
            el.coPlaceOrder.textContent = "Creating Order...";
        } else {
            el.coPlaceOrder.classList.remove("is-loading");
            updateCheckoutButton();
        }
    }

    // Backend errors come back as either a `detail` string (business rules,
    // e.g. stock limits) or a FastAPI 422 array ({loc, msg, type}).
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

    function onOrderCreated(order) {
        state.lastOrder = order;
        var orderId = (order && (order.order_id || order.id)) || "";
        setOrderSubmitting(false);

        // Task 5C-3 — remember email + order ID so My Orders can pre-fill.
        var emailInput = document.getElementById("coEmail");
        rememberOrder(emailInput ? emailInput.value.trim() : "", orderId);

        // Clear the cart ONLY on success. Buy Now never touched the saved
        // cart, so it stays fully preserved.
        if (!state.buyNow) clearCart();
        state.buyNow = null;

        renderConfirmation(order, orderId);
        el.checkoutView.hidden = true;
        el.confirmationView.hidden = false;
        showToast("Order created successfully — " + orderId);
        var f = el.confirmDone;
        if (f && f.focus) f.focus();
    }

    function lastOrderId() {
        return state.lastOrder ? (state.lastOrder.order_id || state.lastOrder.id || "") : "";
    }

    function renderConfirmation(order, orderId) {
        el.confirmRef.textContent = orderId;

        // Task 5D-4 — never claim success/payment when only the order was
        // created. COD stays pending; bank/card orders wait for the explicit
        // Confirm Demo Payment action.
        var cod = isCodMethod(order.payment_method);
        var paid = order.payment_status === "paid";
        var title = document.getElementById("confirmTitle");
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
                '<button type="button" class="btn btn-primary" id="confirmDemoPayBtn">Confirm Demo Payment</button>' +
                '<p class="co-pay-note">This simulates payment for the research prototype. No real money will be charged.</p>' +
                "</div>";
        }
        el.confirmDetails.innerHTML = html;

        var items = Array.isArray(order.order_items) ? order.order_items : [];
        if (items.length) {
            el.confirmOrderItems.classList.add("confirm-order-items");
            el.confirmOrderItems.innerHTML = items.map(function (it) {
                var name = it.product_name || it.name || it.product_id || "";
                var qty = it.quantity || 0;
                var unit = it.unit_price != null ? it.unit_price : (it.price != null ? it.price : 0);
                var line = it.line_total != null ? it.line_total : unit * qty;
                return '<div class="co-summary-item">' +
                    '<div class="co-summary-item-info"><strong>' + esc(name) + "</strong><span>" + qty + " × " + formatTHB(unit) + "</span></div>" +
                    '<span class="co-summary-item-price">' + formatTHB(line) + "</span>" +
                    "</div>";
            }).join("");
            el.confirmOrderItems.hidden = false;
        } else {
            el.confirmOrderItems.hidden = true;
            el.confirmOrderItems.innerHTML = "";
        }

        var payBtn = document.getElementById("confirmDemoPayBtn");
        if (payBtn) payBtn.addEventListener("click", function () { confirmDemoPayment(order); });
    }

    // Task 5D-4 — demo-payment is called ONLY after the customer explicitly
    // clicks Confirm Demo Payment on the order-confirmation screen. It is
    // never called automatically after order creation.
    function confirmDemoPayment(order) {
        var orderId = (order && (order.order_id || order.id)) || "";
        if (!orderId) return;
        var btn = document.getElementById("confirmDemoPayBtn");
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
                renderConfirmation(updated, orderId);
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

    /* ═══════════════════════════════════════════════════════════
       10. TOASTS
       ═══════════════════════════════════════════════════════════ */
    function showToast(msg, type) {
        var t = document.createElement("div");
        t.className = "toast" + (type === "error" ? " toast-error" : "");
        t.innerHTML = (type === "error"
            ? '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 8v5M12 16.5h.01" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round"/><circle cx="12" cy="12" r="9" fill="none" stroke="currentColor" stroke-width="1.8"/></svg>'
            : '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M9 12l2 2 4-4" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"/><circle cx="12" cy="12" r="9" fill="none" stroke="currentColor" stroke-width="1.8"/></svg>') +
            "<span>" + esc(msg) + "</span>";
        el.toastContainer.appendChild(t);
        setTimeout(function () {
            t.classList.add("leaving");
            setTimeout(function () { t.remove(); }, 350);
        }, 2600);
    }

    /* ═══════════════════════════════════════════════════════════
       11. FOCUS MANAGEMENT
       ═══════════════════════════════════════════════════════════ */
    var lastFocused = null;

    function rememberFocus() { lastFocused = document.activeElement; }
    function restoreFocus() { if (lastFocused && lastFocused.focus) lastFocused.focus(); }

    function trapFocus(container, e) {
        if (e.key !== "Tab") return;
        var focusables = container.querySelectorAll('button, a[href], input, select, textarea, [tabindex]:not([tabindex="-1"])');
        if (!focusables.length) return;
        var first = focusables[0];
        var last = focusables[focusables.length - 1];
        if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
        else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
    }

    /* ═══════════════════════════════════════════════════════════
       12. EVENT WIRING
       ═══════════════════════════════════════════════════════════ */
    // Search
    var searchTimer = null;
    el.searchInput.addEventListener("input", function () {
        clearTimeout(searchTimer);
        var v = this.value;
        searchTimer = setTimeout(function () {
            state.query = v;
            renderProducts();
        }, 180);
    });
    el.searchForm.addEventListener("submit", function (e) {
        e.preventDefault();
        state.query = el.searchInput.value;
        renderProducts();
    });

    // Sort
    el.sortSelect.addEventListener("change", function () {
        state.sort = this.value;
        renderProducts();
    });

    // View toggle
    el.viewGrid.addEventListener("click", function () { state.view = "grid"; localStorage.setItem(LS_VIEW, "grid"); renderProducts(); });
    el.viewList.addEventListener("click", function () { state.view = "list"; localStorage.setItem(LS_VIEW, "list"); renderProducts(); });

    // Category tiles (delegation)
    el.categoryGrid.addEventListener("click", function (e) {
        var tile = e.target.closest("[data-category]");
        if (!tile) return;
        setCategory(tile.getAttribute("data-category"));
    });

    function setCategory(cat) {
        state.category = cat;
        renderCategories();
        renderProducts();
        document.getElementById("products").scrollIntoView({ behavior: "smooth", block: "start" });
    }

    // Nav links (desktop + footer) — data-nav
    document.querySelectorAll("[data-nav]").forEach(function (a) {
        a.addEventListener("click", function (e) {
            e.preventDefault();
            setCategory(a.getAttribute("data-nav"));
        });
    });

    // Mobile menu
    el.menuBtn.addEventListener("click", function () {
        var open = el.mobileNav.hidden;
        el.mobileNav.hidden = !open;
        el.menuBtn.setAttribute("aria-expanded", open ? "true" : "false");
        if (open) {
            el.mobileNav.innerHTML = document.querySelectorAll(".main-nav a").length
                ? Array.prototype.map.call(document.querySelectorAll(".main-nav a"), function (a) {
                    return '<a href="' + a.getAttribute("href") + '" data-nav="' + a.getAttribute("data-nav") + '">' + a.textContent + "</a>";
                }).join("")
                : "";
        }
    });

    // Product grid delegation
    el.grid.addEventListener("click", function (e) {
        var btn = e.target.closest("[data-action]");
        if (!btn) return;
        var id = btn.getAttribute("data-id");
        var action = btn.getAttribute("data-action");
        if (action === "quickview") openProductModal(id);
        else if (action === "addcart") {
            addToCart(id, 1);
            var p = getProduct(id);
            showToast("Added to cart: " + p.name);
            var b = btn;
            b.classList.add("added");
            b.querySelector("span").textContent = "Added ✓";
            setTimeout(function () { b.classList.remove("added"); b.querySelector("span").textContent = "Add to Cart"; }, 1200);
        } else if (action === "wish") {
            toggleWish(id, btn);
        }
    });

    // Wishlist
    function toggleWish(id, btn) {
        var i = state.wishlist.indexOf(id);
        if (i === -1) {
            state.wishlist.push(id);
            showToast("Added to wishlist");
        } else {
            state.wishlist.splice(i, 1);
            showToast("Removed from wishlist");
        }
        saveWishlist();
        if (btn) {
            btn.classList.toggle("wished", i === -1);
            btn.setAttribute("aria-pressed", i === -1 ? "true" : "false");
        }
    }

    // Cart drawer
    el.cartToggle.addEventListener("click", openCartDrawer);
    el.cartClose.addEventListener("click", closeCartDrawer);
    el.cartBackdrop.addEventListener("click", closeCartDrawer);
    el.continueShopping.addEventListener("click", closeCartDrawer);

    el.cartItems.addEventListener("click", function (e) {
        var btn = e.target.closest("[data-cart]");
        if (!btn) return;
        var id = btn.getAttribute("data-id");
        var act = btn.getAttribute("data-cart");
        if (act === "inc") setCartQty(id, state.cart[id] + 1);
        else if (act === "dec") setCartQty(id, state.cart[id] - 1);
        else if (act === "remove") removeFromCart(id);
    });

    // Checkout
    el.checkoutBtn.addEventListener("click", function () { closeCartDrawer(); openCheckout(); });
    el.coClose.addEventListener("click", closeCheckout);
    el.coBackToCart.addEventListener("click", function () {
        // In Buy Now mode there is no cart drawer to return to — just close.
        var wasBuyNow = !!state.buyNow;
        closeCheckout();
        if (!wasBuyNow) openCartDrawer();
    });
    el.confirmDone.addEventListener("click", function () {
        closeCheckout();
        resetCheckoutForm();
        document.getElementById("products").scrollIntoView({ behavior: "smooth", block: "start" });
    });
    el.confirmAskAi.addEventListener("click", function () {
        var orderId = lastOrderId();
        if (!orderId) return;
        closeCheckout();
        if (window.Chat && window.Chat.open) {
            setTimeout(function () {
                window.Chat.open();
                if (window.Chat.sendText) window.Chat.sendText("Where is order " + orderId + "?");
            }, 150);
        }
    });
    el.confirmViewOrders.addEventListener("click", function () {
        var orderId = lastOrderId();
        if (!orderId) return;
        var emailInput = document.getElementById("coEmail");
        var email = emailInput ? emailInput.value.trim() : "";
        closeCheckout();
        window.location.href = "/orders?email=" + encodeURIComponent(email);
    });
    el.confirmView.addEventListener("click", function () {
        var orderId = lastOrderId();
        if (!orderId) return;
        // Re-fetch the persisted order from the API, then render the full
        // details (customer, totals, statuses, items) in the confirmation view.
        fetch("/api/orders/" + encodeURIComponent(orderId))
            .then(function (r) { return r.ok ? r.json() : null; })
            .then(function (order) {
                if (order) state.lastOrder = order;
                renderConfirmation(state.lastOrder, orderId);
                el.checkoutView.hidden = true;
                el.confirmationView.hidden = false;
                if (el.confirmOrderItems) {
                    el.confirmOrderItems.scrollIntoView({ behavior: "smooth", block: "nearest" });
                }
            })
            .catch(function () {
                renderConfirmation(state.lastOrder, orderId);
            });
    });
    el.checkoutForm.addEventListener("submit", function (e) { e.preventDefault(); placeDemoOrder(); });
    el.checkoutForm.addEventListener("change", function (e) {
        if (e.target && e.target.name === "payment") {
            updateCheckoutButton();
            var payErr = el.checkoutForm.querySelector('.co-error[data-for="payment"]');
            if (payErr) payErr.textContent = "";
        }
    });

    // Product modal close
    el.pmClose.addEventListener("click", closeProductModal);
    el.productModal.addEventListener("click", function (e) {
        if (e.target === el.productModal) closeProductModal();
    });

    // Checkout modal backdrop close
    el.checkoutModal.addEventListener("click", function (e) {
        if (e.target === el.checkoutModal) closeCheckout();
    });

    // Escape closes overlays (in priority order)
    document.addEventListener("keydown", function (e) {
        if (e.key !== "Escape") return;
        if (!el.checkoutModal.hidden) closeCheckout();
        else if (!el.productModal.hidden) closeProductModal();
        else if (!el.cartDrawer.hidden) closeCartDrawer();
    });

    // Focus traps
    document.addEventListener("keydown", function (e) {
        if (!el.productModal.hidden) trapFocus(el.productModal, e);
        else if (!el.checkoutModal.hidden) trapFocus(el.checkoutModal, e);
        else if (!el.cartDrawer.hidden) trapFocus(el.cartDrawer, e);
    });

    // Decorative header buttons
    el.accountBtn.addEventListener("click", function () {
        showToast("Accounts are part of a later phase — this is a research prototype");
    });
    el.ordersBtn.addEventListener("click", function () {
        window.location.href = "/orders";
    });

    // Hero deal card → open the deal product
    el.heroDealBtn.addEventListener("click", function () { openProductModal("PROD-004"); });

    // Clear filters
    el.clearFilters.addEventListener("click", function () {
        state.query = "";
        state.category = "All Products";
        el.searchInput.value = "";
        renderCategories();
        renderProducts();
    });

    /* ═══════════════════════════════════════════════════════════
       13. PUBLIC API (used by chat.js & inline handlers)
       ═══════════════════════════════════════════════════════════ */
    window.SiamCart = {
        fallbackImage: "data:image/svg+xml;charset=utf-8," + encodeURIComponent(
            '<svg xmlns="http://www.w3.org/2000/svg" width="400" height="400"><rect width="400" height="400" fill="#EEF1F6"/><g fill="none" stroke="#9AA5B5" stroke-width="14" stroke-linecap="round"><path d="M120 240c30-50 80-60 110-20 14 19 26 20 40 0 30-40 80-30 110 20"/><path d="M150 170c0-40 30-60 60-60s60 20 60 60" transform="translate(0,-10)"/></g></svg>'),
        getProduct: getProduct,
        addToCart: addToCart,
        openProductModal: openProductModal,
        openChatWithProduct: function (p) {
            if (window.Chat && window.Chat.openWithProduct) window.Chat.openWithProduct(p);
            else if (window.Chat && window.Chat.open) window.Chat.open();
        }
    };

    /* ═══════════════════════════════════════════════════════════
       14. INIT
       ═══════════════════════════════════════════════════════════ */
    fetchProducts();
    renderCategories();
    renderProducts();
    updateCartBadge();
    renderCartDrawer();
})();
