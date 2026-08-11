"""Static browser-auth integration contracts for every public page."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_every_page_loads_shared_auth_before_page_scripts():
    pages = {
        "index.html": "store.js",
        "orders.html": "orders.js",
        "create_order.html": "create_order.js",
    }
    for template_name, page_script in pages.items():
        html = (ROOT / "app" / "templates" / template_name).read_text(encoding="utf-8")
        assert "/static/js/auth.js" in html
        assert html.index("/static/js/auth.js") < html.index("/static/js/" + page_script)


def test_auth_client_owns_token_injection_and_401_cleanup():
    source = (ROOT / "app" / "static" / "js" / "auth.js").read_text(encoding="utf-8")
    assert "sessionStorage.setItem" in source
    assert 'headers.set("Authorization", "Bearer " + session.access_token)' in source
    assert "response.status === 401" in source
    assert "clearSession();" in source
    assert "/api/auth/demo" in source
    assert "Continue with Demo Login" in source

    styles = (ROOT / "app" / "static" / "css" / "styles.css").read_text(encoding="utf-8")
    assert ".auth-form[hidden]" in styles


def test_protected_flows_use_shared_auth_guard():
    for relative in ("store.js", "orders.js", "create_order.js", "chat.js"):
        source = (ROOT / "app" / "static" / "js" / relative).read_text(encoding="utf-8")
        assert "window.SiamCartAuth" in source
