# Mentor Architecture Summary — SiamCart Multi-Agent Customer Support

Task 5D-2 — compact architecture reference for the mentor demonstration.

## Compact text diagram

```
Browser Storefront (vanilla JS)  [DEMO-ONLY EXTENSION]
    |
    |  GET/POST /api/*   (JSON)
    v
FastAPI API (app/api/server.py + store_routes.py)
    |
    +-- Product Catalog Lookup ---------------------> SQLite products   [DETERMINISTIC · DEMO-ONLY EXTENSION]
    |        (product_catalog_lookup, /api/products)
    |
    +-- Intelligent Router  (router.route_message)                      [EVALUATED FRAMEWORK]
            |
            +-- Transaction Tracker ----------------> SQLite orders     [DETERMINISTIC · SQLite evidence]
            |        (order status / payment / shipment / tracking)
            |
            +-- Store Policy Evaluator -------------> ChromaDB + policy documents   [RAG · optional LLM]
            |
            +-- General Response -------------------> optional LLM (DeepSeek)       [OPTIONAL LLM PATH]
```

## Component labels

| Component | Category | Data source | Notes |
|---|---|---|---|
| Browser Storefront (cart, checkout, My Orders, order pages) | Demo-only storefront extension | SQLite (orders/products) | Not part of the evaluated framework |
| Product Catalog Lookup | Demo extension | SQLite products | Deterministic; **not a fourth evaluated agent** — the completed 120-scenario comparison evaluated the three-agent framework only |
| Intelligent Router | Evaluated framework | — | Dispatch layer of the three-agent framework |
| Transaction Tracker | Evaluated framework | SQLite orders | Deterministic evidence: order_id, statuses, tracking_number |
| Store Policy Evaluator | Evaluated framework | ChromaDB + policy documents | Handles policy questions (returns, refunds, shipping rules) |
| General Response | Evaluated framework | Optional LLM | Natural-language generation; used when deterministic paths do not apply |
| DeepSeek (LLM) | Optional LLM path | — | Only used when DEEPSEEK_ENABLED=true; **not required** for factual product/order queries |
| Payments & orders | Simulated | SQLite | Research simulation only — no real payment or logistics service |

## Deterministic paths (always available, no LLM)

- Product facts: Product Catalog Lookup → SQLite `products` table.
- Order/payment/shipment facts: Transaction Tracker → SQLite `orders` + `order_items` tables.

Both paths answer in milliseconds with exact database values; correctness and latency
are the reasons deterministic lookup is preferred for factual queries.

## Simulated payment

- `POST /api/orders/{id}/demo-payment` is an explicitly labelled research-only simulation.
- No card numbers are ever accepted or stored.
- Cash on Delivery orders can never be marked paid in the UI or API
  ("Cash on Delivery remains pending until delivery.").
- Bank-transfer/card demo orders: pending → paid, `paid_at` recorded; order and
  shipment statuses remain unchanged.

## Source of truth

- SQLite (`data/orders.db`) is the single source of truth for products and orders.
- The browser never supplies prices; all pricing is read server-side from SQLite.
