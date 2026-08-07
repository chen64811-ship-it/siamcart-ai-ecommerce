# SiamCart

LLM-Powered Multi-Agent Customer Support Framework for Thai E-commerce

A research prototype demonstrating an LLM-powered multi-agent framework for
automated customer support in Thai e-commerce. SiamCart combines a working
storefront (products, cart, checkout) with a Thai customer-support assistant
that answers order, payment, shipping, and policy questions, and simulates
transactional workflows (payment, cancellation/refund, shipment) for research
and demonstration purposes.

## Overview

- **Research prototype** — built for an M.Sc. thesis study on multi-agent
  customer support; not a production store.
- **Thai-first** — every customer-facing assistant response is polite,
  natural Thai, regardless of the input language.
- **Multi-agent architecture** — an Intelligent Router dispatches to focused
  agents (see below).
- **Stack** — FastAPI + SQLite + ChromaDB + DeepSeek (optional LLM formatter).
- **Simulated transactions** — payments, refunds, and shipments are explicit
  research simulations. No real money moves, no real courier is connected.

## Key Features

- Product catalog and shopping cart
- Checkout and order creation
- Demo payment (explicit, research-only simulation)
- My Orders (search by order ID / email / phone)
- Product-aware support (deterministic product catalog lookup)
- Transaction tracking (order / payment / shipment status)
- Store policy evaluation (RAG over policy documents)
- Persistent multi-turn chat context (survives refresh and page changes)
- Demo cancellation and simulated refund
- Demo shipment lifecycle (Not Shipped → In Transit → Delivered)
- Tracking number and courier simulation (SC-… numbers, SiamCart Demo Logistics)

## Multi-Agent Architecture

The framework keeps the thesis terminology — three evaluated agents:

1. **Intelligent Router** — deterministic intent classification and routing.
2. **Transaction Tracker** — SQLite-backed order/payment/shipment facts.
3. **Store Policy Evaluator** — RAG retrieval over store policy documents.

In addition, the storefront ships a **Product Catalog Lookup** handler. It is
a **Demo Extension / deterministic handler**, not an evaluated fourth agent.

## Tech Stack

- Python
- FastAPI
- SQLite
- ChromaDB
- DeepSeek (optional LLM formatter)
- Vanilla JavaScript
- HTML/CSS

## Demo Order Lifecycle

```
Created
→ Paid (Demo)
→ Processing
→ In Transit (Demo)
→ Delivered (Demo)
```

Cancellation path (only while Processing + Not Shipped):

```
Processing + Not Shipped
→ Cancelled
→ Refunded (Demo, when already paid)
```

Once a parcel is In Transit or Delivered, direct cancellation is blocked and
the assistant points to return/refund policy guidance instead.

## Local Setup

```bash
python -m venv .venv
```

Windows:

```bat
.venv\Scripts\activate
```

Linux/macOS:

```bash
source .venv/bin/activate
```

Install dependencies and create your environment file:

```bash
pip install -r requirements.txt
copy .env.example .env        # Windows
cp .env.example .env          # Linux/macOS
```

Start the server. Primary:

```bash
python run.py server
```

Fallback (identical application, uvicorn directly):

```bash
python -m uvicorn app.api.server:app --host 0.0.0.0 --port 8000
```

Open http://localhost:8000 — the database, schema, products, and demo seed
data initialize automatically on first startup.

## Environment Variables

All configuration is read from `.env` (see `.env.example` — variable names
only, no secrets). The important ones:

| Variable | Default | Purpose |
| --- | --- | --- |
| `DEEPSEEK_ENABLED` | `false` | Enable the optional LLM formatter |
| `DEEPSEEK_API_KEY` | *(empty)* | DeepSeek API key (never committed) |
| `DEEPSEEK_MODEL` | `deepseek-v4-flash` | LLM model name |
| `DEEPSEEK_BASE_URL` | `https://api.deepseek.com` | LLM endpoint |
| `DEEPSEEK_TIMEOUT_SECONDS` | `8` | LLM timeout |
| `EMBEDDING_MODEL` | sentence-transformers paraphrase-multilingual-MiniLM-L12-v2 | RAG embedding model |

## Tests

Focused suites (temporary SQLite only — the real runtime database is never
touched by tests):

- Demo stabilization focused tests: **21 passed**
- Shipment lifecycle focused tests: **21 passed**
- Relevant regression suites (cancellation, shipment, stabilization, tracker,
  router): **95 passed**

Run a suite:

```bash
.venv\Scripts\python.exe -m pytest tests/test_demo_shipment_lifecycle.py -v
```

> **Note on the thesis evaluation:** the demo extensions (multi-turn context,
> cancellation UX, shipment lifecycle) were added **after** the frozen thesis
> evaluation and were **not** part of the original 120-scenario comparison.
> The original evaluation datasets/results are preserved and have not been
> modified or re-run for these extensions.

## Research Scope

- Simulated payment — no real money is transferred.
- Simulated refund — no real money is transferred.
- Simulated shipment and tracking — no real courier integration, no real
  tracking API, no ETA prediction.
- No production authentication (research prototype only).
- Customer-facing responses are generated in Thai by a deterministic
  SQLite-backed pipeline, with optional LLM formatting when configured.

## Project Layout

```
app/
  agents/        # router, transaction tracker, policy evaluator, orchestrator
  api/           # FastAPI app and storefront routes
  db/            # SQLite schema, migrations, product seed
  models/        # Pydantic request models
  services/      # deterministic product catalog lookup
  static/        # frontend JS/CSS/images
  templates/     # HTML pages
data/
  policies/      # store policy documents (required by the app)
tests/           # focused pytest suites
```
