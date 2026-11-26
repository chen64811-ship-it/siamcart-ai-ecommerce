"""Arize monitor webhook receiver.

Receives Arize AX monitor alerts (``monitor.triggered`` / ``monitor.cleared``
/ ``monitor.no_data``) so the SiamCart backend can react to production
incidents, e.g. page on-call, open a ticket, or record the event.

Endpoint:
  POST /api/webhooks/arize

The payload schema is loose (Arize may add fields over time), so we log the
full body and extract the fields we care about. Configure this URL in Arize:
  Alert Integrations -> Webhook -> HTTPS URL -> <this endpoint>
  Subscribe to events: monitor.triggered / monitor.cleared / monitor.no_data

Optional hardening: set ARIZE_WEBHOOK_SECRET and send it as the
``Authorization: Bearer <secret>`` header (Arize supports an optional
authorization header) to reject unauthenticated calls.
"""

import logging
import os
from typing import Any, Dict

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

logger = logging.getLogger("siamcart.arize_webhook")

router = APIRouter()


def _extract(payload: Dict[str, Any], *keys: str, default: str = "unknown") -> str:
    """Return the first present, non-empty scalar among ``keys``."""
    for key in keys:
        value = payload.get(key)
        if isinstance(value, (str, int, float, bool)) and value not in ("", None):
            return str(value)
        if isinstance(value, dict) and value.get("name"):
            return str(value["name"])
    return default


@router.post("/api/webhooks/arize")
async def receive_arize_webhook(request: Request):
    # Optional auth: reject when a secret is configured but the header is wrong.
    secret = os.getenv("ARIZE_WEBHOOK_SECRET", "").strip()
    if secret:
        auth = request.headers.get("authorization", "")
        if auth != f"Bearer {secret}":
            return JSONResponse(status_code=401, content={"received": False})

    try:
        payload = await request.json()
    except Exception:
        return JSONResponse(status_code=400, content={"received": False})

    event = _extract(payload, "event", "event_name", "type")
    monitor_name = _extract(payload, "monitor_name", "monitor")
    status = _extract(payload, "status")
    previous_status = _extract(payload, "previous_status", default="n/a")

    logger.info(
        "arize_webhook_received",
        extra={
            "event": event,
            "monitor_name": monitor_name,
            "status": status,
            "previous_status": previous_status,
            "payload": payload,
        },
    )

    return {
        "received": True,
        "event": event,
        "monitor_name": monitor_name,
        "status": status,
    }
