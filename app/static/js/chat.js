/**
 * SiamCart AI Assistant — chat.js
 * Opening/closing the chat, message rendering, suggested prompts,
 * product-context + order-context display, POST /api/chat, loading/typing
 * states, research metadata panel, error handling.
 *
 * Task 8A — shared chat persistence:
 *   - one versioned localStorage object (siamcart.chat.v1) shared by the
 *     Store page and the My Orders page;
 *   - the server-issued session_id is persisted and reused across pages,
 *     refreshes and panel close/reopen;
 *   - the active order context (Ask AI on an order card) is persisted;
 *   - only explicit Reset clears the transcript/session/context.
 *
 * Vanilla JavaScript. No frameworks.
 */
(function () {
    "use strict";

    // Task 5D-7 Objective 6: customer-facing chat text is Thai.
    var WELCOME = "สวัสดีค่ะ ฉันคือผู้ช่วย AI ของ SiamCart สามารถช่วยตรวจสอบสินค้า คำสั่งซื้อ การชำระเงิน การจัดส่ง การคืนสินค้า และนโยบายของร้านได้ค่ะ";
    var SUGGESTIONS = [
        "คำสั่งซื้อ ORD-1001 อยู่ที่ไหน?",
        "ORD-1002 ชำระเงินแล้วหรือยัง?",
        "แสดงหมายเลขพัสดุของ ORD-1003",
        "นโยบายการคืนสินค้าคืออะไร?",
        "การคืนเงินใช้เวลากี่วัน?"
    ];

    // ── Task 8A — shared chat storage contract ─────────────────────────
    // Store and My Orders use the SAME key and schema. Only customer-visible
    // messages + minimal display metadata are stored — never API keys,
    // internal prompts, research traces or hidden metadata.
    var LS_CHAT = "siamcart.chat.v1";
    var MAX_STORED_MESSAGES = 50;

    var el = {
        fab: document.getElementById("chatFab"),
        unread: document.getElementById("chatUnread"),
        panel: document.getElementById("chatPanel"),
        minBtn: document.getElementById("chatMinBtn"),
        closeBtn: document.getElementById("chatCloseBtn"),
        resetBtn: document.getElementById("chatResetBtn"),
        orderContext: document.getElementById("chatOrderContext"),
        productContext: document.getElementById("chatProductContext"),
        messages: document.getElementById("chatMessages"),
        suggestions: document.getElementById("chatSuggestions"),
        input: document.getElementById("chatInput"),
        sendBtn: document.getElementById("sendBtn"),
        rd: {
            intent: document.getElementById("rdIntent"),
            agent: document.getElementById("rdAgent"),
            orderId: document.getElementById("rdOrderId"),
            productId: document.getElementById("rdProductId"),
            latency: document.getElementById("rdLatency"),
            source: document.getElementById("rdSource"),
            dbSource: document.getElementById("rdDbSource"),
            demoExt: document.getElementById("rdDemoExt"),
            policySource: document.getElementById("rdPolicySource"),
            chunks: document.getElementById("rdChunks"),
            confidence: document.getElementById("rdConfidence"),
            responseLang: document.getElementById("rdResponseLang"),
            inputLang: document.getElementById("rdInputLang")
        }
    };

    var state = {
        sessionId: null,
        isOpen: false,
        isSending: false,
        product: null,
        welcomeRendered: false,
        userSent: false,
        // Task 8A — persistent transcript + active order context.
        messages: [],
        activeOrder: null,
        // The order for which the automatic Ask-AI message was already sent
        // (prevents duplicate auto messages on repeated Ask AI clicks).
        autoAskOrder: null
    };

    /* ── Time helpers ─────────────────────────────────────────── */
    function nowTime() {
        var d = new Date();
        return String(d.getHours()).padStart(2, "0") + ":" + String(d.getMinutes()).padStart(2, "0");
    }

    /* ── Task 8A — localStorage persistence ───────────────────── */
    function isValidOrderContext(o) {
        return !!o && typeof o === "object" && typeof o.order_id === "string" && o.order_id !== "";
    }

    function loadChatState() {
        var raw;
        try {
            raw = JSON.parse(localStorage.getItem(LS_CHAT));
        } catch (e) {
            return null; // malformed storage — recover safely
        }
        if (!raw || typeof raw !== "object") return null;
        if (raw.version !== 1) return null;
        if (!Array.isArray(raw.messages)) return null;

        var messages = [];
        raw.messages.forEach(function (m) {
            if (!m || typeof m !== "object") return;
            if ((m.role !== "user" && m.role !== "agent") || typeof m.content !== "string") return;
            messages.push({
                role: m.role,
                content: m.content,
                timestamp: typeof m.timestamp === "string" ? m.timestamp : null
            });
        });
        messages = messages.slice(-MAX_STORED_MESSAGES);

        return {
            session_id: typeof raw.session_id === "string" && raw.session_id ? raw.session_id : null,
            messages: messages,
            active_order: isValidOrderContext(raw.active_order) ? raw.active_order : null,
            updated_at: typeof raw.updated_at === "string" ? raw.updated_at : null
        };
    }

    function saveChatState() {
        var data = {
            version: 1,
            session_id: state.sessionId,
            messages: state.messages.slice(-MAX_STORED_MESSAGES),
            active_order: state.activeOrder,
            active_product: null,
            updated_at: new Date().toISOString()
        };
        try {
            localStorage.setItem(LS_CHAT, JSON.stringify(data));
        } catch (e) {
            /* storage full/unavailable — chat keeps working in memory */
        }
    }

    function clearChatState() {
        try {
            localStorage.removeItem(LS_CHAT);
        } catch (e) {
            /* ignore */
        }
    }

    /* ── Open / close / minimize / reset ──────────────────────── */
    function open() {
        el.panel.hidden = false;
        el.panel.setAttribute("aria-hidden", "false");
        state.isOpen = true;
        el.unread.hidden = true;
        if (!state.welcomeRendered) {
            renderWelcome();
            state.welcomeRendered = true;
        }
        renderSuggestions();
        setTimeout(function () { el.input.focus(); }, 60);
        scrollBottom();
    }

    function minimize() {
        el.panel.hidden = true;
        el.panel.setAttribute("aria-hidden", "true");
        state.isOpen = false;
    }

    function close() {
        minimize();
        el.fab.focus();
    }

    // Task 8A — explicit Reset: clears transcript, session_id, context.
    function resetChat() {
        var oldSession = state.sessionId;
        if (oldSession) {
            fetch("/api/chat/reset", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ session_id: oldSession })
            }).catch(function () { /* backend reset is best-effort */ });
        }
        clearChatState();
        state.sessionId = null;
        state.messages = [];
        state.activeOrder = null;
        state.product = null;
        state.autoAskOrder = null;
        state.userSent = false;
        el.messages.innerHTML = "";
        setProductContext(null);
        setOrderContext(null, { silent: true });
        state.welcomeRendered = false;
        renderWelcome();
        state.welcomeRendered = true;
        saveChatState();
        renderSuggestions();
        el.input.focus();
    }

    /* ── Rendering ───────────────────────────────────────────── */
    function renderWelcome() {
        appendMessage(WELCOME, "agent");
    }

    function renderSuggestions() {
        if (state.userSent && !state.product && !state.activeOrder) {
            el.suggestions.innerHTML = "";
            return;
        }
        var list = SUGGESTIONS.slice();
        el.suggestions.innerHTML = list.map(function (s) {
            return '<button type="button" class="suggest-chip" data-suggest="' + s.replace(/\"/g, "&quot;") + '">' + s + "</button>";
        }).join("");
    }

    function appendMessage(text, role, isError, timestamp) {
        var wrap = document.createElement("div");
        wrap.className = "msg msg-" + role + (isError ? " msg-error" : "");

        var bubble = document.createElement("div");
        bubble.className = "msg-bubble";
        bubble.textContent = text;

        var time = document.createElement("span");
        time.className = "msg-time";
        time.textContent = timestamp || nowTime();

        wrap.appendChild(bubble);
        wrap.appendChild(time);
        el.messages.appendChild(wrap);

        // Task 8A — keep the persisted transcript in sync (cap at 50).
        state.messages.push({
            role: role,
            content: text,
            timestamp: timestamp || new Date().toISOString()
        });
        if (state.messages.length > MAX_STORED_MESSAGES) {
            state.messages = state.messages.slice(-MAX_STORED_MESSAGES);
        }
        saveChatState();

        scrollBottom();
        return wrap;
    }

    function showTyping() {
        var wrap = document.createElement("div");
        wrap.className = "msg msg-agent";
        wrap.id = "typingIndicator";
        wrap.innerHTML =
            '<div class="typing-indicator" role="status" aria-label="กำลังตรวจสอบข้อมูล">' +
            '<span class="typing-dot"></span><span class="typing-dot"></span><span class="typing-dot"></span>' +
            "</div>";
        el.messages.appendChild(wrap);
        scrollBottom();
    }

    function hideTyping() {
        var t = document.getElementById("typingIndicator");
        if (t) t.remove();
    }

    function scrollBottom() {
        el.messages.scrollTop = el.messages.scrollHeight;
    }

    /* ── Product context card ────────────────────────────────── */
    function stockText(product) {
        return product.stock > 0 ? product.stock + " in stock" : "Out of stock";
    }

    function setProductContext(product) {
        state.product = product || null;
        if (!product) {
            el.productContext.hidden = true;
            el.productContext.innerHTML = "";
            return;
        }
        el.productContext.hidden = false;
        el.productContext.innerHTML =
            '<img src="' + product.image + '" alt="' + product.name + '" onerror="this.onerror=null;this.src=window.SiamCart.fallbackImage;">' +
            '<div class="chat-product-context-info">' +
            "<strong>" + product.name + "</strong>" +
            "<span>" + product.id + " · " + formatPrice(product.price) + "</span>" +
            "<span>" + stockText(product) + "</span>" +
            "</div>" +
            '<button type="button" class="chat-product-context-close" id="productCtxClose" aria-label="Remove product context">' +
            '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M6 6l12 12M18 6L6 18" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></svg>' +
            "</button>";
        document.getElementById("productCtxClose").addEventListener("click", function () {
            setProductContext(null);
            el.input.focus();
        });
        renderSuggestions();
    }

    function formatPrice(n) {
        return "\u0E3F" + Number(n || 0).toLocaleString("en-US");
    }

    /* ── Task 8A — order context card ─────────────────────────── */
    function humanizeStatus(s) {
        if (s == null || s === "") return "—";
        return String(s).replace(/_/g, " ").replace(/\b\w/g, function (c) { return c.toUpperCase(); });
    }

    function setOrderContext(order, opts) {
        opts = opts || {};
        if (!order) {
            state.activeOrder = null;
            el.orderContext.hidden = true;
            el.orderContext.innerHTML = "";
            if (!opts.silent) saveChatState();
            renderSuggestions();
            return;
        }
        // Replace the previous context entirely (new order overrides old).
        state.activeOrder = {
            order_id: order.order_id || order.orderId,
            first_product_name: order.first_product_name || order.product_name || "",
            first_product_image: order.first_product_image || order.image_url || "",
            payment_status: order.payment_status || "",
            order_status: order.order_status || "",
            shipment_status: order.shipment_status || ""
        };
        renderOrderContext();
        saveChatState();
        renderSuggestions();
    }

    function renderOrderContext() {
        var o = state.activeOrder;
        if (!o) return;
        var img = o.first_product_image
            ? '<img src="' + o.first_product_image + '" alt="" onerror="this.onerror=null;this.src=window.SiamCart.fallbackImage;">'
            : "";
        el.orderContext.innerHTML =
            img +
            '<div class="chat-order-context-info">' +
            "<strong>" + escHtml(o.order_id) + " · " + escHtml(o.first_product_name || "Order") + "</strong>" +
            "<span>Payment: " + escHtml(humanizeStatus(o.payment_status)) + "</span>" +
            "<span>Order: " + escHtml(humanizeStatus(o.order_status)) + "</span>" +
            "<span>Shipment: " + escHtml(humanizeStatus(o.shipment_status)) + "</span>" +
            '<button type="button" class="chat-order-context-remove" id="orderCtxRemove">Remove order context</button>' +
            "</div>";
        el.orderContext.hidden = false;
        document.getElementById("orderCtxRemove").addEventListener("click", function () {
            removeOrderContext();
            el.input.focus();
        });
    }

    function escHtml(s) {
        return String(s == null ? "" : s)
            .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
            .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
    }

    // Task 8A — Remove order context: clear local + backend, keep transcript
    // and session_id.
    function removeOrderContext() {
        if (state.sessionId) {
            fetch("/api/chat/remove-context", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ session_id: state.sessionId })
            }).catch(function () { /* best-effort */ });
        }
        state.activeOrder = null;
        state.autoAskOrder = null;
        el.orderContext.hidden = true;
        el.orderContext.innerHTML = "";
        saveChatState();
        renderSuggestions();
    }

    // Task 8A — Ask AI on an order card: open the chat, attach the order
    // context, and send one automatic message that carries order_id.
    function openWithOrder(order) {
        if (!state.welcomeRendered) {
            renderWelcome();
            state.welcomeRendered = true;
        }
        open();
        setOrderContext(order);
        var orderId = order.order_id || order.orderId;
        if (orderId && state.autoAskOrder === orderId) {
            // Repeated Ask AI click for the SAME order: just open, no
            // duplicate automatic message.
            return;
        }
        state.autoAskOrder = orderId || null;
        sendText("Where is order " + orderId + "?");
    }

    function openWithProduct(product) {
        if (!state.welcomeRendered) {
            renderWelcome();
            state.welcomeRendered = true;
        }
        setProductContext(product);
        el.input.value = "What can you tell me about this product?";
        open();
        autoResize();
    }

    /* ── Sending ─────────────────────────────────────────────── */
    function sendMessage() {
        var message = el.input.value.trim();
        if (!message || state.isSending) return;
        _doSend(message);
        el.input.value = "";
        autoResize();
    }

    function sendText(text) {
        if (state.isSending) return;
        _doSend(String(text));
    }

    function _doSend(message) {
        state.isSending = true;
        state.userSent = true;
        el.sendBtn.disabled = true;
        el.suggestions.innerHTML = "";

        appendMessage(message, "user");
        showTyping();

        // Task 8A — payload: session_id is reused until Reset; order_id is
        // attached while an order context is active.
        var payload = { message: message, session_id: state.sessionId, order_id: null };
        if (state.activeOrder && state.activeOrder.order_id) {
            payload.order_id = state.activeOrder.order_id;
        }
        if (state.product && state.product.id) {
            payload.product_id = state.product.id;
        }

        fetch("/api/chat", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(payload)
        })
            .then(function (r) {
                if (!r.ok) throw new Error("HTTP " + r.status);
                return r.json();
            })
            .then(function (data) {
                state.sessionId = data.session_id || state.sessionId;
                // Keep the persisted session_id in sync immediately.
                saveChatState();
                hideTyping();
                appendMessage(data.response || "ขออภัย ฉันไม่พบคำตอบสำหรับคำถามนี้ กรุณาลองใหม่อีกครั้งค่ะ", "agent");
                updateResearchDetails(data);
            })
            .catch(function () {
                hideTyping();
                appendMessage(
                    "ขออภัย ขณะนี้ระบบผู้ช่วยไม่สามารถให้บริการได้ กรุณาลองใหม่อีกครั้งค่ะ",
                    "agent",
                    true
                );
            })
            .finally(function () {
                state.isSending = false;
                el.sendBtn.disabled = false;
                if (state.isOpen) el.input.focus();
                else el.unread.hidden = false;
            });
    }

    /* ── Research details panel ──────────────────────────────── */
    function updateResearchDetails(data) {
        function set(key, val) {
            if (el.rd[key]) el.rd[key].textContent = val == null || val === "" ? "—" : String(val);
        }
        set("intent", data.intent);
        set("agent", data.agent);
        set("orderId", data.order_id);
        set("productId", data.product_id);
        set("latency", (data.latency_ms || 0).toFixed(1) + " ms");
        set("source", data.response_source || "deterministic");
        set("dbSource", data.database_source || "—");
        set("demoExt", data.demo_extension === true ? "Yes" : (data.demo_extension === false ? "No" : "—"));
        set("confidence", (data.routing_confidence || 1).toFixed(2));
        // Task 5D-7 Objective 7: Response language: Thai
        set("responseLang", data.response_language === "th" ? "Thai" : (data.response_language || "—"));
        set("inputLang", data.input_language_detected || "—");

        var pe = data.policy_evidence || {};
        var chunks = pe.retrieved_clauses || pe.retrieved_chunks || [];
        var count = pe.retrieved_chunk_count != null ? pe.retrieved_chunk_count : (chunks.length || 0);
        set("chunks", count ? count + " chunk" + (count === 1 ? "" : "s") : "—");

        var src = "";
        if (pe.policy_sources && pe.policy_sources.length) src = pe.policy_sources[0];
        else if (chunks.length && chunks[0].source_filename) src = chunks[0].source_filename;
        set("policySource", src);
    }

    /* ── Composer behavior ───────────────────────────────────── */
    function autoResize() {
        el.input.style.height = "auto";
        el.input.style.height = Math.min(el.input.scrollHeight, 110) + "px";
    }

    el.input.addEventListener("input", autoResize);
    el.input.addEventListener("keydown", function (e) {
        if (e.key === "Enter" && !e.shiftKey) {
            e.preventDefault();
            sendMessage();
        }
    });

    el.sendBtn.addEventListener("click", sendMessage);

    /* ── Suggestion chips (delegation) ───────────────────────── */
    el.suggestions.addEventListener("click", function (e) {
        var chip = e.target.closest("[data-suggest]");
        if (!chip) return;
        sendText(chip.getAttribute("data-suggest"));
    });

    /* ── Openers across the page ─────────────────────────────── */
    el.fab.addEventListener("click", open);
    el.minBtn.addEventListener("click", minimize);
    el.closeBtn.addEventListener("click", close);
    if (el.resetBtn) el.resetBtn.addEventListener("click", resetChat);

    var heroAskBtn = document.getElementById("heroAskAiBtn");
    if (heroAskBtn) heroAskBtn.addEventListener("click", open);

    var footerAsk = document.getElementById("footerAskAi");
    if (footerAsk) footerAsk.addEventListener("click", function (e) { e.preventDefault(); open(); });

    // Footer "Customer Support / Research" links that prefill a question
    document.querySelectorAll("[data-chat-suggest]").forEach(function (a) {
        a.addEventListener("click", function (e) {
            e.preventDefault();
            open();
            sendText(a.getAttribute("data-chat-suggest"));
        });
    });

    /* ── Task 8A — restore persisted state on page load ──────── */
    // Same key/schema on Store AND My Orders: navigating between pages,
    // refreshing, or re-opening the panel restores the exact transcript,
    // session_id and order context. Never auto-cleared on init.
    (function restore() {
        var saved = loadChatState();
        if (!saved || (!saved.messages.length && !saved.session_id && !saved.active_order)) {
            return; // fresh state — welcome renders on first open
        }
        state.sessionId = saved.session_id;
        state.messages = [];
        saved.messages.forEach(function (m) {
            var wrap = document.createElement("div");
            wrap.className = "msg msg-" + m.role;
            var bubble = document.createElement("div");
            bubble.className = "msg-bubble";
            bubble.textContent = m.content;
            var time = document.createElement("span");
            time.className = "msg-time";
            time.textContent = m.timestamp ? formatStoredTime(m.timestamp) : nowTime();
            wrap.appendChild(bubble);
            wrap.appendChild(time);
            el.messages.appendChild(wrap);
            state.messages.push(m);
        });
        // Only restore the welcome flag when a transcript actually exists
        // (no duplicate welcome after refresh/navigation).
        if (state.messages.length) state.welcomeRendered = true;
        if (saved.active_order) {
            state.activeOrder = saved.active_order;
            renderOrderContext();
        }
        if (state.sessionId && state.activeOrder) {
            state.autoAskOrder = state.activeOrder.order_id;
        }
        scrollBottom();
    })();

    function formatStoredTime(iso) {
        var d = new Date(iso);
        if (isNaN(d.getTime())) return nowTime();
        return String(d.getHours()).padStart(2, "0") + ":" + String(d.getMinutes()).padStart(2, "0");
    }

    /* ── Public API ──────────────────────────────────────────── */
    window.Chat = {
        open: open,
        close: close,
        minimize: minimize,
        sendText: sendText,
        openWithProduct: openWithProduct,
        openWithOrder: openWithOrder,
        removeOrderContext: removeOrderContext,
        reset: resetChat,
        isOpen: function () { return state.isOpen; }
    };
})();
