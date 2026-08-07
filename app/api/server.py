"""
FastAPI Application — Phase 1A.

Endpoints:
  GET  /health      — health check
  GET  /            — Thai e-commerce customer support page (Jinja2)
  POST /api/chat    — single-agent Transaction Tracker chat
"""

import os
import json
import time
import uuid
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app.agents import process_message as orchestrator_process
from app.agents import set_db_path
from app.agents.orchestrator import (
    detect_order_intent,
    get_active_order_for_session,
    remove_order_context,
    reset_session,
)
from app.agents.router import route_message
from app.api.store_routes import router as store_router
from app.db import store as store_db
from app.db.orders import database_available
from app.config import DATA_DIR, LOGS_DIR, STATIC_DIR, DEEPSEEK_ENABLED
from app.services.product_catalog import classify_product_question, product_catalog_lookup

# ── Application ──────────────────────────────────────────────────────

app = FastAPI(
    title="Thai E-Commerce Customer Support — Phase 1A",
    description="Single-agent Transaction Tracker with Thai e-commerce web UI",
    version="1.0.0",
)

# Ensure directories exist
os.makedirs(STATIC_DIR, exist_ok=True)
os.makedirs(str(LOGS_DIR), exist_ok=True)
os.makedirs(str(DATA_DIR), exist_ok=True)

# Mount static files
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

# Storefront APIs (products + real order creation) — Task 5C
app.include_router(store_router)

# Jinja2 templates — using inline HTML to avoid version-specific LRU bugs
templates_dir = Path(__file__).resolve().parent.parent / "templates"

# ── Models ───────────────────────────────────────────────────────────

class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1)
    session_id: str | None = None
    # Task 5D-1: optional product context. When supplied, the current product
    # is loaded fresh from SQLite — product details from the browser are never
    # trusted. Existing clients sending only message/session_id stay compatible.
    product_id: str | None = None
    # Task 8A: optional explicit order context (Ask AI on an order card).
    # Only the order ID is accepted; payment/order/shipment values shown on
    # the frontend order-context card are display context only — SQLite is
    # the source of truth. Backward compatible (may be null).
    order_id: str | None = None


class Evidence(BaseModel):
    order_id: str | None = None
    order_status: str | None = None
    payment_status: str | None = None
    shipment_status: str | None = None
    tracking_number: str | None = None
    shipping_provider: str | None = None
    product_name: str | None = None
    estimated_delivery_date: str | None = None


class ChatResponse(BaseModel):
    session_id: str
    intent: str
    agent: str
    order_id: str | None = None
    response: str
    evidence: dict
    policy_evidence: dict = {}
    requires_clarification: bool = False
    simulated_human_review: bool = False
    latency_ms: float = 0.0
    response_source: str = "deterministic"
    llm_enabled: bool = False
    llm_fallback_used: bool = False
    llm_latency_ms: float = 0.0
    llm_error_type: str | None = None
    routing_confidence: float = 1.0
    routing_reason: str = ""
    grounding_validation_passed: bool | None = None
    # Task 5D-1: Product Catalog Lookup research metadata (demo extension).
    product_id: str | None = None
    database_source: str | None = None
    demo_extension: bool | None = None
    # Task 5D-5: multi-source refund-flow research metadata.
    handler: str | None = None
    refund_reason: str | None = None
    order_evidence_source: str | None = None
    policy_source: str | None = None
    retrieved_chunks: int | None = None
    llm_used: bool | None = None
    fallback_reason: str | None = None
    # Task 5D-7 Objective 7: fixed-Thai response metadata.
    response_language: str = "th"
    input_language_detected: str | None = None
    # Task 8A — session active order + honest-action metadata.
    # active_order_id is the reusable order context established by an
    # explicit ID, the Ask AI order card, or follow-up resolution.
    active_order_id: str | None = None
    # Honest-action flags: never True unless a real persisted action exists.
    action_executed: bool = False
    escalation_created: bool = False
    cancellation_created: bool = False


class SessionActionRequest(BaseModel):
    """Body for the Task 8A session-context endpoints."""

    session_id: str | None = None


# ── Routing constants (Task 5D-1) ──────────────────────────────────────
# Intents that keep their existing routes even when a product context is
# active. Product Catalog Lookup is a storefront demo extension — it never
# replaces the evaluated Transaction Tracker or Store Policy Evaluator.
_TXN_INTENTS = {"ORDER_STATUS", "PAYMENT_STATUS", "SHIPMENT_STATUS", "TRACKING_NUMBER", "ETA", "COURIER"}
_POLICY_INTENTS = {"RETURN_REFUND", "STORE_POLICY"}


# ── Experiment Logger ────────────────────────────────────────────────

DB_PATH = str(DATA_DIR / "orders.db")
EXPERIMENT_LOG = str(LOGS_DIR / "experiment.jsonl")


def write_experiment_log(
    session_id: str,
    user_message: str,
    result: dict,
):
    """Write one JSON line to logs/experiment.jsonl. Failure is silent."""
    try:
        record = {
            "timestamp": datetime.now().isoformat(),
            "session_id": session_id,
            "user_message": user_message,
            "intent": result.get("intent", ""),
            "selected_agent": result.get("agent", ""),
            "order_id": result.get("order_id"),
            "evidence": result.get("evidence", {}),
            "response": result.get("response", ""),
            "latency_ms": result.get("latency_ms", 0),
            "response_source": result.get("response_source", "deterministic"),
            "llm_enabled": result.get("llm_enabled", False),
            "llm_fallback_used": result.get("llm_fallback_used", False),
            "llm_latency_ms": result.get("llm_latency_ms", 0),
            "llm_error_type": result.get("llm_error_type"),
            "routing_confidence": result.get("routing_confidence", 1.0),
            "routing_reason": result.get("routing_reason", ""),
            "success": result.get("intent") != "ORDER_NOT_FOUND",
            "requires_clarification": result.get("requires_clarification", False),
            "simulated_human_review": result.get("simulated_human_review", False),
            "grounding_validation_passed": result.get("grounding_validation_passed"),
            "policy_sources": result.get("policy_evidence", {}).get("policy_sources", []),
            "retrieved_chunk_count": result.get("policy_evidence", {}).get("retrieved_chunk_count", 0),
            "top_similarity_score": result.get("policy_evidence", {}).get("top_similarity_score", 0.0),
            "retrieval_success": result.get("policy_evidence", {}).get("retrieval_success", False),
            "response_language": result.get("response_language", "th"),
            "input_language_detected": result.get("input_language_detected"),
        }
        with open(EXPERIMENT_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception:
        pass  # Logging failure must not crash the endpoint


# ── Endpoints ────────────────────────────────────────────────────────

@app.on_event("startup")
async def startup():
    """Ensure the orders database exists on startup."""
    from app.db.orders import init_database
    init_database(DB_PATH)
    # Storefront schema (products, order_items, orders migration) — Task 5C
    store_db.STORE_DB_PATH = DB_PATH
    store_db.init_store_database(DB_PATH)
    # Tell orchestrator about the database path
    set_db_path(DB_PATH)
    # Write a blank log header
    with open(EXPERIMENT_LOG, "a", encoding="utf-8") as f:
        pass  # just ensure file exists


@app.get("/health")
async def health():
    """Health check endpoint."""
    db_ok = database_available(DB_PATH)
    return {
        "status": "ok" if db_ok else "degraded",
        "database": "available" if db_ok else "unavailable",
        "active_agent": "transaction_tracker",
    }


@app.get("/", response_class=HTMLResponse)
async def index():
    """Serve the Thai e-commerce customer support page."""
    path = templates_dir / "index.html"
    return HTMLResponse(content=path.read_text(encoding="utf-8"))


@app.get("/orders/new", response_class=HTMLResponse)
async def create_order_page():
    """Serve the public demo order-creation page (Task 5C)."""
    path = templates_dir / "create_order.html"
    return HTMLResponse(content=path.read_text(encoding="utf-8"))


@app.get("/orders", response_class=HTMLResponse)
async def orders_page():
    """Serve the customer-facing My Orders page (Task 5C-3)."""
    path = templates_dir / "orders.html"
    return HTMLResponse(content=path.read_text(encoding="utf-8"))


@app.post("/api/chat", response_model=ChatResponse)
async def chat(request: ChatRequest):
    """Process a chat message through the Intelligent Router and agents.

    Task 5D-1: an optional product_id activates the deterministic Product
    Catalog Lookup demo extension for factual product questions. Order and
    policy questions keep their existing routes even with product context.
    """
    session_id = request.session_id or f"demo-{uuid.uuid4().hex[:8]}"
    message = request.message.strip()

    if not message:
        raise HTTPException(status_code=400, detail="Message cannot be empty")

    # ── Optional product context (Task 5D-1) ─────────────────────
    # Never trust product details sent by the browser — only product_id is
    # accepted, and the current product is always read fresh from SQLite.
    product = None
    if request.product_id:
        product = store_db.get_product(store_db.STORE_DB_PATH, request.product_id)
        if product is None:
            raise HTTPException(
                status_code=404,
                detail=f"Product {request.product_id} not found",
            )

    route = route_message(message)
    intent = route["intent"]

    # Product Catalog Lookup — deterministic storefront demo extension.
    # It is NOT one of the three evaluated agents. With a product context
    # active (Objective 3/4):
    #   - order questions containing an ORD ID → Transaction Tracker;
    #   - policy questions → Store Policy Evaluator (safe product context);
    #   - real greetings / out-of-scope requests → existing behavior;
    #   - anything else (including product factual questions the existing
    #     router mislabels as GREETING due to substring keywords) → Product
    #     Catalog Lookup.
    if product is not None:
        has_order_id = route.get("extracted_order_id") is not None
        # Task 8A — an order-total / purchased-items question is order work,
        # never product-catalog work, even when a product context is active.
        demo_order_intent = detect_order_intent(message)
        session_has_order_context = bool(
            request.order_id or get_active_order_for_session(session_id)
        )
        if (
            (intent in _TXN_INTENTS and has_order_id)
            or (demo_order_intent and (has_order_id or session_has_order_context))
        ):
            result = orchestrator_process(
                message, session_id=session_id, order_id=request.order_id
            )
        elif intent in _POLICY_INTENTS:
            result = orchestrator_process(
                message, session_id=session_id, order_id=request.order_id
            )
            # Safe product context for policy questions: only the verified
            # product name, ID and category. Catalogue facts are never treated
            # as policy evidence; retrieval/policy architecture is unchanged.
            result.setdefault("evidence", {})["product_context"] = {
                "product_id": product["product_id"],
                "name": product["name"],
                "category": product["category"],
            }
            result["product_id"] = product["product_id"]
        elif (
            intent in ("OUT_OF_SCOPE", "GREETING")
            and classify_product_question(message) is None
        ):
            result = orchestrator_process(
                message, session_id=session_id, order_id=request.order_id
            )
        else:
            result = product_catalog_lookup(product, message, session_id)
    else:
        result = orchestrator_process(
            message, session_id=session_id, order_id=request.order_id
        )

    # Write experiment log
    write_experiment_log(session_id, message, result)

    return ChatResponse(
        session_id=result["session_id"],
        intent=result["intent"],
        agent=result["agent"],
        order_id=result.get("order_id"),
        response=result["response"],
        evidence=result.get("evidence", {}),
        policy_evidence=result.get("policy_evidence", {}),
        requires_clarification=result.get("requires_clarification", False),
        simulated_human_review=result.get("simulated_human_review", False),
        latency_ms=result.get("latency_ms", 0),
        response_source=result.get("response_source", "deterministic"),
        llm_enabled=result.get("llm_enabled", False),
        llm_fallback_used=result.get("llm_fallback_used", False),
        llm_latency_ms=result.get("llm_latency_ms", 0.0),
        llm_error_type=result.get("llm_error_type"),
        routing_confidence=result.get("routing_confidence", 1.0),
        routing_reason=result.get("routing_reason", ""),
        grounding_validation_passed=result.get("grounding_validation_passed"),
        product_id=result.get("product_id"),
        database_source=result.get("database_source"),
        demo_extension=result.get("demo_extension"),
        handler=result.get("handler"),
        refund_reason=result.get("refund_reason"),
        order_evidence_source=result.get("order_evidence_source"),
        policy_source=result.get("policy_source"),
        retrieved_chunks=result.get("retrieved_chunks"),
        llm_used=result.get("llm_used"),
        fallback_reason=result.get("fallback_reason"),
        response_language=result.get("response_language", "th"),
        input_language_detected=result.get("input_language_detected"),
        active_order_id=result.get("active_order_id"),
        action_executed=result.get("action_executed", False),
        escalation_created=result.get("escalation_created", False),
        cancellation_created=result.get("cancellation_created", False),
    )


@app.post("/api/chat/remove-context")
async def chat_remove_context(request: SessionActionRequest):
    """Task 8A — explicit Remove order context.

    Clears the session active_order_id only. The transcript and session_id
    are preserved; pending workflow state is untouched.
    """
    session_id = request.session_id or f"demo-{uuid.uuid4().hex[:8]}"
    remove_order_context(session_id)
    return {
        "ok": True,
        "session_id": session_id,
        "active_order_id": get_active_order_for_session(session_id),
    }


@app.post("/api/chat/reset")
async def chat_reset(request: SessionActionRequest):
    """Task 8A — explicit Reset chat.

    Clears pending workflow state and the active order context for the
    given session. The frontend also clears its local transcript and
    session_id, so the next message starts a brand-new session.
    """
    session_id = request.session_id or f"demo-{uuid.uuid4().hex[:8]}"
    reset_session(session_id)
    return {
        "ok": True,
        "session_id": session_id,
        "active_order_id": get_active_order_for_session(session_id),
    }
