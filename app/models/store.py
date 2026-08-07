"""Request models for the SiamCart store APIs (Task 5C)."""

from typing import List, Optional

from pydantic import BaseModel, Field


class OrderItemRequest(BaseModel):
    """One product line requested from the browser.

    Only product_id and quantity are accepted. Prices are never accepted
    from the client — they are always read from SQLite server-side.
    """

    product_id: str = Field(..., min_length=1)
    quantity: int = Field(..., ge=1)


class OrderCreateRequest(BaseModel):
    """POST /api/orders body.

    No card fields exist on purpose: this is a research demo and no card
    numbers are ever accepted or stored.
    """

    customer_name: str = Field(..., min_length=1)
    customer_email: str = Field(..., min_length=3)
    customer_phone: str = Field(..., min_length=1)
    shipping_address: str = Field(..., min_length=1)
    district: str = ""
    province: str = ""
    postal_code: str = ""
    payment_method: str = "cash_on_delivery"
    items: List[OrderItemRequest] = Field(..., min_length=1)


class ProductQuery(BaseModel):
    search: Optional[str] = None
    category: Optional[str] = None
    sort: Optional[str] = None


class DemoPaymentRequest(BaseModel):
    """POST /api/orders/{order_id}/demo-payment body (Task 5C-3).

    Research simulation only — the only field accepted is an explicit
    confirmation boolean. No card fields exist on purpose: card numbers
    are never accepted or stored by this prototype.
    """

    confirm_demo_payment: bool = True


class CancelDemoRequest(BaseModel):
    """POST /api/orders/{order_id}/cancel-demo body (demo cancellation).

    Research simulation only — cancellation never moves real money. The
    explicit confirm flag must be true to execute; without it the endpoint
    only reports confirmation_required and never touches the database.
    """

    confirm: bool = False
    reason: str = "Customer changed their mind"


class ShipDemoRequest(BaseModel):
    """POST /api/orders/{order_id}/ship-demo body (Task 8C).

    Research simulation only — no real SMS/email/WhatsApp/LINE message is
    ever sent. The explicit confirm flag must be true to execute the demo
    shipment (generates a tracking number + records the simulated
    notification). Same guard convention as DemoPaymentRequest.
    """

    confirm_demo_shipment: bool = True


class DemoShipmentRequest(BaseModel):
    """POST /api/orders/{order_id}/demo-shipment body (Task 8D).

    Research simulation only — no real courier service is connected. The
    explicit confirm flag must be true to execute the simulated shipment
    (sets shipment_status='in_transit' and generates a persisted SC- demo
    tracking number).
    """

    confirm_demo_shipment: bool = True


class DemoDeliveryRequest(BaseModel):
    """POST /api/orders/{order_id}/demo-delivery body (Task 8D).

    Research simulation only. The explicit confirm flag must be true to
    mark the in-transit demo parcel delivered.
    """

    confirm_demo_delivery: bool = True
