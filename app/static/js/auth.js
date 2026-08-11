/**
 * SiamCart browser authentication.
 *
 * Owns the short-lived JWT session, injects Bearer authentication into every
 * same-origin /api request, provides login/register/demo-account UI, and
 * clears stale credentials on 401 responses.
 */
(function () {
    "use strict";

    var STORAGE_KEY = "siamcart.auth.session.v1";
    var nativeFetch = window.fetch.bind(window);
    var authConfig = { auth_required: false, demo_login_enabled: false };
    var session = readSession();
    var readyResolve;
    var readyPromise = new Promise(function (resolve) { readyResolve = resolve; });
    var modal;
    var statusButton;

    function readSession() {
        try {
            var parsed = JSON.parse(sessionStorage.getItem(STORAGE_KEY));
            if (!parsed || !parsed.access_token || !parsed.user) return null;
            if (parsed.expires_at && parsed.expires_at <= Date.now()) {
                sessionStorage.removeItem(STORAGE_KEY);
                return null;
            }
            return parsed;
        } catch (e) {
            return null;
        }
    }

    function clearCustomerContext() {
        try {
            localStorage.removeItem("siamcart.chat.v1");
            localStorage.removeItem("siamcart.customer.email");
            localStorage.removeItem("siamcart.customer.orders.v1");
        } catch (e) { /* storage may be unavailable */ }
    }

    function saveSession(data) {
        if (session && session.user && data.user && session.user.user_id !== data.user.user_id) {
            clearCustomerContext();
        }
        session = {
            access_token: data.access_token,
            user: data.user,
            expires_at: Date.now() + (Number(data.expires_in) || 0) * 1000
        };
        try { sessionStorage.setItem(STORAGE_KEY, JSON.stringify(session)); } catch (e) { /* ignore */ }
        try { localStorage.setItem("siamcart.customer.email", data.user.email); } catch (e) { /* ignore */ }
    }

    function clearSession(clearContext) {
        session = null;
        try { sessionStorage.removeItem(STORAGE_KEY); } catch (e) { /* ignore */ }
        if (clearContext !== false) clearCustomerContext();
        updateUi();
    }

    function apiUrl(input) {
        try {
            var raw = typeof input === "string" ? input : input.url;
            return new URL(raw, window.location.href);
        } catch (e) {
            return null;
        }
    }

    function isHandledAuthPath(path) {
        return path === "/api/auth/config" || path === "/api/auth/login" ||
            path === "/api/auth/register" || path === "/api/auth/demo";
    }

    function fetchWithAuth(input, init) {
        var url = apiUrl(input);
        var sameOriginApi = url && url.origin === window.location.origin && url.pathname.indexOf("/api/") === 0;
        var options = Object.assign({}, init || {});

        if (sameOriginApi && session && session.access_token) {
            var headers = new Headers((input instanceof Request && input.headers) || undefined);
            new Headers(options.headers || undefined).forEach(function (value, key) {
                headers.set(key, value);
            });
            headers.set("Authorization", "Bearer " + session.access_token);
            options.headers = headers;
        }

        return nativeFetch(input, options).then(function (response) {
            if (sameOriginApi && response.status === 401 && !isHandledAuthPath(url.pathname)) {
                var hadSession = !!session;
                clearSession();
                openAuth(hadSession ? "Your session expired. Please sign in again." : "Sign in to continue.", "login");
                document.dispatchEvent(new CustomEvent("siamcart:unauthorized"));
            }
            return response;
        });
    }

    // Install before page-specific scripts execute so Chat, My Orders and
    // Checkout all share exactly the same authorization behavior.
    window.fetch = fetchWithAuth;

    function ensureUi() {
        if (modal) return;

        var host = document.querySelector(".site-header .header-actions");
        if (host) {
            statusButton = document.createElement("button");
            statusButton.type = "button";
            statusButton.className = "auth-status-button";
            statusButton.id = "authStatusBtn";
            statusButton.addEventListener("click", function () { openAuth(); });
            host.insertBefore(statusButton, host.firstChild);
        }

        modal = document.createElement("div");
        modal.className = "modal-backdrop auth-backdrop";
        modal.id = "authModal";
        modal.setAttribute("role", "dialog");
        modal.setAttribute("aria-modal", "true");
        modal.setAttribute("aria-labelledby", "authTitle");
        modal.hidden = true;
        modal.innerHTML =
            '<div class="modal auth-modal">' +
                '<button class="modal-close" id="authClose" type="button" aria-label="Close sign in">' +
                    '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M6 6l12 12M18 6L6 18" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></svg>' +
                "</button>" +
                '<div class="auth-brand"><span class="auth-brand-mark">S</span><div><strong>SiamCart Account</strong><span>Secure access to your orders and AI support</span></div></div>' +
                '<p class="auth-message" id="authMessage" hidden></p>' +
                '<section id="authGuestView">' +
                    '<h2 id="authTitle">Welcome to SiamCart</h2>' +
                    '<p class="auth-lead">Sign in so orders, checkout and chat stay connected to your account.</p>' +
                    '<button class="btn btn-accent auth-demo-button" id="authDemoBtn" type="button" hidden>Continue with Demo Login</button>' +
                    '<div class="auth-divider" id="authDivider" hidden><span>or use your own account</span></div>' +
                    '<div class="auth-tabs" role="tablist" aria-label="Authentication options">' +
                        '<button type="button" role="tab" id="authLoginTab" aria-selected="true">Login</button>' +
                        '<button type="button" role="tab" id="authRegisterTab" aria-selected="false">Register</button>' +
                    "</div>" +
                    '<form class="auth-form" id="authLoginForm" novalidate>' +
                        '<label>Email<input id="authLoginEmail" type="email" autocomplete="email" required></label>' +
                        '<label>Password<input id="authLoginPassword" type="password" autocomplete="current-password" required></label>' +
                        '<button class="btn btn-primary btn-block" type="submit">Login</button>' +
                    "</form>" +
                    '<form class="auth-form" id="authRegisterForm" novalidate hidden>' +
                        '<label>Display name<input id="authRegisterName" type="text" autocomplete="name" maxlength="120" required></label>' +
                        '<label>Email<input id="authRegisterEmail" type="email" autocomplete="email" required></label>' +
                        '<label>Password<input id="authRegisterPassword" type="password" autocomplete="new-password" minlength="8" maxlength="128" required></label>' +
                        '<p class="auth-field-hint">Use at least 8 characters.</p>' +
                        '<button class="btn btn-primary btn-block" type="submit">Create Account</button>' +
                    "</form>" +
                    '<p class="auth-error" id="authError" role="alert" hidden></p>' +
                    '<p class="auth-security-note">Your access token is kept in this browser tab and is never written into the page URL.</p>' +
                "</section>" +
                '<section class="auth-account" id="authAccountView" hidden>' +
                    '<div class="auth-avatar" id="authAvatar">S</div>' +
                    '<h2 id="authAccountName">SiamCart Shopper</h2>' +
                    '<p id="authAccountEmail"></p>' +
                    '<div class="auth-account-actions">' +
                        '<a class="btn btn-primary" href="/orders">View My Orders</a>' +
                        '<button class="btn btn-secondary" id="authLogoutBtn" type="button">Log out</button>' +
                    "</div>" +
                "</section>" +
            "</div>";
        document.body.appendChild(modal);

        document.getElementById("authClose").addEventListener("click", closeAuth);
        modal.addEventListener("click", function (event) {
            if (event.target === modal) closeAuth();
        });
        document.getElementById("authLoginTab").addEventListener("click", function () { showTab("login"); });
        document.getElementById("authRegisterTab").addEventListener("click", function () { showTab("register"); });
        document.getElementById("authLoginForm").addEventListener("submit", submitLogin);
        document.getElementById("authRegisterForm").addEventListener("submit", submitRegister);
        document.getElementById("authDemoBtn").addEventListener("click", submitDemoLogin);
        document.getElementById("authLogoutBtn").addEventListener("click", function () {
            clearSession();
            closeAuth();
            window.location.href = "/";
        });
        document.addEventListener("keydown", function (event) {
            if (event.key === "Escape" && !modal.hidden) closeAuth();
        });

        var existingAccountButton = document.getElementById("accountBtn");
        if (existingAccountButton) {
            existingAccountButton.addEventListener("click", function () { openAuth(); });
        }
        updateUi();
    }

    function showTab(tab) {
        var login = tab !== "register";
        document.getElementById("authLoginForm").hidden = !login;
        document.getElementById("authRegisterForm").hidden = login;
        document.getElementById("authLoginTab").setAttribute("aria-selected", String(login));
        document.getElementById("authRegisterTab").setAttribute("aria-selected", String(!login));
        setError("");
        setTimeout(function () {
            var target = document.getElementById(login ? "authLoginEmail" : "authRegisterName");
            if (target) target.focus();
        }, 20);
    }

    function openAuth(message, tab) {
        ensureUi();
        var messageNode = document.getElementById("authMessage");
        messageNode.textContent = message || "";
        messageNode.hidden = !message;
        document.getElementById("authGuestView").hidden = !!session && !tab;
        document.getElementById("authAccountView").hidden = !session || !!tab;
        if (!session || tab) showTab(tab || "login");
        modal.hidden = false;
        document.body.classList.add("modal-open");
        setTimeout(function () {
            var focusTarget = session && !tab
                ? document.getElementById("authLogoutBtn")
                : document.getElementById("authDemoBtn").hidden
                    ? document.getElementById("authLoginEmail")
                    : document.getElementById("authDemoBtn");
            if (focusTarget) focusTarget.focus();
        }, 30);
    }

    function closeAuth() {
        if (!modal) return;
        modal.hidden = true;
        document.body.classList.remove("modal-open");
    }

    function setError(message) {
        var node = document.getElementById("authError");
        node.textContent = message || "";
        node.hidden = !message;
    }

    function setBusy(form, busy, label) {
        var button = form.querySelector('button[type="submit"]');
        if (!button.dataset.label) button.dataset.label = button.textContent;
        button.disabled = busy;
        button.textContent = busy ? label : button.dataset.label;
    }

    function errorDetail(body, fallback) {
        if (body && typeof body.detail === "string") return body.detail;
        if (body && Array.isArray(body.detail)) {
            return body.detail.map(function (item) { return item.msg; }).join("; ");
        }
        return fallback;
    }

    function postAuth(path, payload) {
        return nativeFetch(path, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: payload == null ? undefined : JSON.stringify(payload)
        }).then(function (response) {
            return response.json().catch(function () { return {}; }).then(function (body) {
                if (!response.ok) throw new Error(errorDetail(body, "Authentication failed."));
                return body;
            });
        });
    }

    function completeAuthentication(data) {
        saveSession(data);
        updateUi();
        applyUserToForms();
        document.dispatchEvent(new CustomEvent("siamcart:authenticated", { detail: { user: session.user } }));
        closeAuth();
        window.location.reload();
    }

    function submitLogin(event) {
        event.preventDefault();
        var form = event.currentTarget;
        var email = document.getElementById("authLoginEmail").value.trim();
        var password = document.getElementById("authLoginPassword").value;
        if (!email || !password) { setError("Enter both email and password."); return; }
        setError("");
        setBusy(form, true, "Signing in...");
        postAuth("/api/auth/login", { email: email, password: password })
            .then(completeAuthentication)
            .catch(function (error) { setError(error.message); })
            .finally(function () { setBusy(form, false); });
    }

    function submitRegister(event) {
        event.preventDefault();
        var form = event.currentTarget;
        var payload = {
            display_name: document.getElementById("authRegisterName").value.trim(),
            email: document.getElementById("authRegisterEmail").value.trim(),
            password: document.getElementById("authRegisterPassword").value
        };
        if (!payload.display_name || !payload.email || payload.password.length < 8) {
            setError("Enter a name, valid email and password of at least 8 characters.");
            return;
        }
        setError("");
        setBusy(form, true, "Creating account...");
        postAuth("/api/auth/register", payload)
            .then(completeAuthentication)
            .catch(function (error) { setError(error.message); })
            .finally(function () { setBusy(form, false); });
    }

    function submitDemoLogin() {
        var button = document.getElementById("authDemoBtn");
        setError("");
        button.disabled = true;
        button.textContent = "Opening demo account...";
        postAuth("/api/auth/demo")
            .then(completeAuthentication)
            .catch(function (error) {
                setError(error.message);
                button.disabled = false;
                button.textContent = "Continue with Demo Login";
            });
    }

    function updateUi() {
        if (!modal) return;
        var user = session && session.user;
        document.body.classList.toggle("auth-authenticated", !!user);
        document.body.classList.toggle("auth-required", !!authConfig.auth_required);
        if (statusButton) {
            statusButton.textContent = user ? user.display_name : "Sign in";
            statusButton.classList.toggle("is-authenticated", !!user);
            statusButton.setAttribute("aria-label", user ? "Account for " + user.display_name : "Login or register");
        }
        var demoButton = document.getElementById("authDemoBtn");
        var divider = document.getElementById("authDivider");
        demoButton.hidden = !authConfig.demo_login_enabled;
        divider.hidden = !authConfig.demo_login_enabled;
        if (user) {
            document.getElementById("authAccountName").textContent = user.display_name;
            document.getElementById("authAccountEmail").textContent = user.email;
            document.getElementById("authAvatar").textContent = (user.display_name || "S").charAt(0).toUpperCase();
        }
    }

    function applyUserToForms() {
        var user = session && session.user;
        var nameInput = document.getElementById("coName");
        var emailInput = document.getElementById("coEmail");
        if (!user) {
            if (emailInput && emailInput.dataset.authOwned === "true") {
                emailInput.readOnly = false;
                delete emailInput.dataset.authOwned;
            }
            return;
        }
        if (nameInput && !nameInput.value) nameInput.value = user.display_name || "";
        if (emailInput) {
            emailInput.value = user.email || "";
            emailInput.readOnly = true;
            emailInput.dataset.authOwned = "true";
            emailInput.title = "Orders are linked to your signed-in account";
        }
    }

    function bootstrap() {
        ensureUi();
        nativeFetch("/api/auth/config")
            .then(function (response) { return response.ok ? response.json() : authConfig; })
            .then(function (config) {
                authConfig = config || authConfig;
                updateUi();
                if (!session) return null;
                return fetchWithAuth("/api/auth/me").then(function (response) {
                    if (!response.ok) return null;
                    return response.json();
                }).then(function (user) {
                    if (user && session) {
                        session.user = user;
                        try { sessionStorage.setItem(STORAGE_KEY, JSON.stringify(session)); } catch (e) { /* ignore */ }
                        updateUi();
                        applyUserToForms();
                    }
                });
            })
            .catch(function () { /* protected requests still handle 401 */ })
            .finally(function () {
                readyResolve();
                document.dispatchEvent(new CustomEvent("siamcart:auth-ready", {
                    detail: { authenticated: !!session, required: !!authConfig.auth_required }
                }));
                if (authConfig.auth_required && !session) {
                    openAuth("Use Demo Login for instant access, or sign in with your own account.", "login");
                }
            });
    }

    window.SiamCartAuth = {
        ready: function () { return readyPromise; },
        isAuthenticated: function () { return !!session; },
        isRequired: function () { return !!authConfig.auth_required; },
        currentUser: function () { return session ? session.user : null; },
        open: openAuth,
        openLogin: function (message) { openAuth(message || "Sign in to continue.", "login"); },
        logout: function () { clearSession(); window.location.href = "/"; },
        applyUserToForms: applyUserToForms
    };

    bootstrap();
})();
