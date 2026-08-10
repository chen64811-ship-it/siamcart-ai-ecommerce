"""Storefront API routes — products and real order creation (Task 5C).

Endpoints:
  GET  /api/products                — JSON array of active products (search/category/sort)
  GET  /api/products/{id}           — one complete active product (404 when unknown)
  POST /api/orders                  — create a real order in SQLite (201)
  GET  /api/orders                  — order summaries, newest first (Task 5C-3)
  GET  /api/orders/{order_id}       — order + items (404 when unknown)
  POST /api/orders/{order_id}/demo-payment — research-only paid simulation (Task 5C-3)
  POST /api/orders/{order_id}/cancel-demo  — demo cancellation (simulated refund)
  POST /api/orders/{order_id}/ship-demo    — demo shipment + simulated notification (Task 8C)
  POST /api/orders/{order_id}/demo-shipment — demo shipment lifecycle: Not Shipped -> In Transit (Task 8D)
  POST /api/orders/{order_id}/demo-delivery — demo shipment lifecycle: In Transit -> Delivered (Task 8D)

Performance: these routes use SQLite only. They never import or initialize
ChromaDB, SentenceTransformer, or DeepSeek. Latency is reported in the
X-Elapsed-Ms response header.
"""

import time
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response

from app.db import store as store_db
from app.models.store import (
    CancelDemoRequest,
    DemoDeliveryRequest,
    DemoPaymentRequest,
    DemoShipmentRequest,
    OrderCreateRequest,
    ShipDemoRequest,
)
from app.security import get_current_user

router = APIRouter()


def _with_elapsed(response: Response, start: float) -> None:
    response.headers["X-Elapsed-Ms"] = f"{round((time.perf_counter() - start) * 1000, 2)}"


def _ensure_order_access(order_id: str, user: dict | None) -> None:
    if user is not None and not store_db.user_owns_order(
        store_db.STORE_DB_PATH, order_id, user["user_id"]
    ):
        # Do not reveal whether another customer's order exists.
        raise HTTPException(status_code=404, detail=f"Order {order_id.strip()} not found")


# ── Products ───────────────────────────────────────────────────────────


@router.get("/api/products")
async def list_products(
    response: Response,
    search: Optional[str] = Query(default=None),
    category: Optional[str] = Query(default=None),
    sort: Optional[str] = Query(default=None),
):
    """Return active products as a JSON array with live stock."""
    start = time.perf_counter()
    sort = sort or "featured"
    if sort not in ("featured", "price_asc", "price_desc", "newest"):
        raise HTTPException(status_code=400, detail="Unsupported sort value")

    products = store_db.list_products(
        store_db.STORE_DB_PATH,
        search=(search or "").strip() or None,
        category=(category or "").strip() or None,
        sort=sort,
    )
    _with_elapsed(response, start)
    return products


@router.get("/api/products/{product_id}")
async def get_product(product_id: str, response: Response):
    """Return one complete active product; 404 for unknown/inactive."""
    start = time.perf_counter()
    product = store_db.get_product(store_db.STORE_DB_PATH, product_id.strip().upper())
    if product is None:
        raise HTTPException(status_code=404, detail=f"Product {product_id} not found")
    _with_elapsed(response, start)
    return product


# ── Orders ─────────────────────────────────────────────────────────────


@router.post("/api/orders", status_code=201)
async def create_order(
    request: OrderCreateRequest,
    response: Response,
    current_user: dict | None = Depends(get_current_user),
):
    """Create a real order: one SQLite transaction, server-side pricing."""
    start = time.perf_counter()

    # Combine duplicate product_ids clearly
    combined: dict = {}
    for item in request.items:
        pid = item.product_id.strip().upper()
        combined[pid] = combined.get(pid, 0) + item.quantity

    items = [{"product_id": pid, "quantity": qty} for pid, qty in combined.items()]

    customer = {
        "customer_name": request.customer_name.strip(),
        "customer_email": request.customer_email.strip(),
        "customer_phone": request.customer_phone.strip(),
        "shipping_address": request.shipping_address.strip(),
        "district": request.district.strip(),
        "province": request.province.strip(),
        "postal_code": request.postal_code.strip(),
        "payment_method": request.payment_method.strip(),
        "user_id": current_user["user_id"] if current_user else None,
    }

    try:
        store_db.validate_customer_payload(customer, items)
        order = store_db.create_order(store_db.STORE_DB_PATH, customer, items)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    _with_elapsed(response, start)
    return order


@router.get("/api/orders")
async def list_orders(
    response: Response,
    email: Optional[str] = Query(default=None),
    phone: Optional[str] = Query(default=None),
    order_id: Optional[str] = Query(default=None),
    limit: Optional[int] = Query(default=None),
    current_user: dict | None = Depends(get_current_user),
):
    """Return order summaries (newest first) with items and product images.

    At least one search parameter is normally expected; for this research
    prototype, calling with no filter returns the latest synthetic demo
    orders (limit defaults to 20, maximum 50).
    """
    start = time.perf_counter()
    orders = store_db.list_orders(
        store_db.STORE_DB_PATH,
        email=email,
        phone=phone,
        order_id=order_id,
        limit=limit if limit is not None else 20,
        user_id=current_user["user_id"] if current_user else None,
    )
    _with_elapsed(response, start)
    return {"orders": orders, "count": len(orders)}


@router.post("/api/orders/{order_id}/demo-payment")
async def demo_payment(
    order_id: str,
    request: DemoPaymentRequest,
    response: Response,
    current_user: dict | None = Depends(get_current_user),
):
    """Explicitly labelled research-only payment simulation.

    Never accepts card numbers (the body model has no card fields). Only an
    existing, pending, eligible order (bank transfer / card) is updated;
    Cash on Delivery always stays pending.
    """
    start = time.perf_counter()
    _ensure_order_access(order_id, current_user)
    if not request.confirm_demo_payment:
        raise HTTPException(
            status_code=400, detail="confirm_demo_payment must be true"
        )
    try:
        order = store_db.mark_order_paid(store_db.STORE_DB_PATH, order_id)
    except ValueError as exc:
        message = str(exc)
        if message == "ORDER_NOT_FOUND":
            raise HTTPException(
                status_code=404, detail=f"Order {order_id.strip()} not found"
            )
        if message == "CASH_ON_DELIVERY":
            raise HTTPException(
                status_code=409,
                detail="Cash on Delivery remains pending until delivery.",
            )
        if message == "METHOD_NOT_ELIGIBLE":
            raise HTTPException(
                status_code=409,
                detail="This payment method does not support demo payment.",
            )
        if message == "NOT_PENDING":
            raise HTTPException(
                status_code=409,
                detail="Order payment is not pending — it cannot be marked paid again.",
            )
        raise HTTPException(status_code=400, detail=message)
    _with_elapsed(response, start)
    return order


@router.get("/api/orders/{order_id}")
async def get_order(
    order_id: str,
    response: Response,
    current_user: dict | None = Depends(get_current_user),
):
    """Return order details + items; 404 when the order does not exist."""
    start = time.perf_counter()
    _ensure_order_access(order_id, current_user)
    order = store_db.get_order_with_items(store_db.STORE_DB_PATH, order_id)
    if order is None:
        raise HTTPException(
            status_code=404, detail=f"Order {order_id.strip()} not found"
        )
    _with_elapsed(response, start)
    return order


@router.post("/api/orders/{order_id}/cancel-demo")
async def cancel_demo(
    order_id: str,
    request: CancelDemoRequest,
    response: Response,
    current_user: dict | None = Depends(get_current_user),
):
    """Deterministic demo cancellation — simulated refund, no real money.

    Without confirm=true the endpoint returns confirmation_required=true
    and never touches the database. With confirm=true it calls the same
    deterministic service used by the AI cancellation flow.
    """
    start = time.perf_counter()
    _ensure_order_access(order_id, current_user)
    if not request.confirm:
        _with_elapsed(response, start)
        return {
            "order_id": store_db.normalize_order_id(order_id),
            "confirmation_required": True,
            "action_executed": False,
        }
    try:
        result = store_db.cancel_demo_order(
            store_db.STORE_DB_PATH, order_id, request.reason
        )
    except ValueError as exc:
        message = str(exc)
        if message == "INVALID_ORDER_ID":
            raise HTTPException(
                status_code=400, detail=f"Invalid order ID: {order_id.strip()}"
            )
        if message == "ORDER_NOT_FOUND":
            raise HTTPException(
                status_code=404, detail=f"Order {order_id.strip()} not found"
            )
        if message == "NOT_CANCELLABLE":
            raise HTTPException(
                status_code=409,
                detail=(
                    "This order cannot be cancelled — only processing orders "
                    "that have not been shipped are cancellable."
                ),
            )
        raise HTTPException(status_code=400, detail=message)
    _with_elapsed(response, start)
    return result


@router.post("/api/orders/{order_id}/ship-demo")
async def ship_demo(
    order_id: str,
    request: ShipDemoRequest,
    response: Response,
    current_user: dict | None = Depends(get_current_user),
):
    """Explicitly labelled research-only shipment simulation (Task 8C).

    Moves a processing order to shipped, generates a realistic demo
    tracking number (once — existing numbers are preserved) and records a
    simulated shipping notification. No real SMS, email, WhatsApp or LINE
    message is ever sent; the endpoint only writes to the demo SQLite DB.
    """
    start = time.perf_counter()
    _ensure_order_access(order_id, current_user)
    if not request.confirm_demo_shipment:
        raise HTTPException(
            status_code=400, detail="confirm_demo_shipment must be true"
        )
    try:
        result = store_db.mark_order_shipped(store_db.STORE_DB_PATH, order_id)
    except ValueError as exc:
        message = str(exc)
        if message == "INVALID_ORDER_ID":
            raise HTTPException(
                status_code=400, detail=f"Invalid order ID: {order_id.strip()}"
            )
        if message == "ORDER_NOT_FOUND":
            raise HTTPException(
                status_code=404, detail=f"Order {order_id.strip()} not found"
            )
        if message == "NOT_SHIPPABLE":
            raise HTTPException(
                status_code=409,
                detail=(
                    "This order cannot be shipped — only processing orders "
                    "that are not yet shipped can be shipped."
                ),
            )
        raise HTTPException(status_code=400, detail=message)
    _with_elapsed(response, start)
    return result


@router.post("/api/orders/{order_id}/demo-shipment")
async def demo_shipment(
    order_id: str,
    request: DemoShipmentRequest,
    response: Response,
    current_user: dict | None = Depends(get_current_user),
):
    """Task 8D — simulate shipment: Not Shipped -> In Transit.

    Research simulation only — no real courier service is connected. Sets
    shipment_status='in_transit', order_status='shipped', persists the
    demo provider and a SC- tracking number (generated once, reused on
    every later query). Idempotent; payment_status never changes.
    """
    start = time.perf_counter()
    _ensure_order_access(order_id, current_user)
    if not request.confirm_demo_shipment:
        raise HTTPException(
            status_code=400, detail="confirm_demo_shipment must be true"
        )
    try:
        result = store_db.simulate_shipment(store_db.STORE_DB_PATH, order_id)
    except ValueError as exc:
        message = str(exc)
        if message == "INVALID_ORDER_ID":
            raise HTTPException(
                status_code=400, detail=f"Invalid order ID: {order_id.strip()}"
            )
        if message == "ORDER_NOT_FOUND":
            raise HTTPException(
                status_code=404, detail=f"Order {order_id.strip()} not found"
            )
        if message == "NOT_SHIPPABLE":
            raise HTTPException(
                status_code=409,
                detail=(
                    "This order cannot be shipped — only processing orders "
                    "that have not been shipped and are not cancelled can "
                    "be shipped."
                ),
            )
        raise HTTPException(status_code=400, detail=message)
    _with_elapsed(response, start)
    return result


@router.post("/api/orders/{order_id}/demo-delivery")
async def demo_delivery(
    order_id: str,
    request: DemoDeliveryRequest,
    response: Response,
    current_user: dict | None = Depends(get_current_user),
):
    """Task 8D — mark delivered: In Transit -> Delivered.

    Research simulation only. Sets shipment_status='delivered',
    order_status='delivered' and records delivered_at. Idempotent; a
    not-shipped parcel cannot jump straight to delivered.
    """
    start = time.perf_counter()
    _ensure_order_access(order_id, current_user)
    if not request.confirm_demo_delivery:
        raise HTTPException(
            status_code=400, detail="confirm_demo_delivery must be true"
        )
    try:
        result = store_db.mark_demo_delivered(store_db.STORE_DB_PATH, order_id)
    except ValueError as exc:
        message = str(exc)
        if message == "INVALID_ORDER_ID":
            raise HTTPException(
                status_code=400, detail=f"Invalid order ID: {order_id.strip()}"
            )
        if message == "ORDER_NOT_FOUND":
            raise HTTPException(
                status_code=404, detail=f"Order {order_id.strip()} not found"
            )
        if message == "NOT_DELIVERABLE":
            raise HTTPException(
                status_code=409,
                detail=(
                    "This order cannot be marked delivered — only parcels "
                    "that are in transit can be marked delivered."
                ),
            )
        raise HTTPException(status_code=400, detail=message)
    _with_elapsed(response, start)
    return result
