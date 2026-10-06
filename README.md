# SiamCart

**LLM-Powered Multi-Agent Customer Support Framework for Thai E-commerce**

![Python 3.11](https://img.shields.io/badge/Python-3.11-3776AB?logo=python&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-009688?logo=fastapi&logoColor=white)
![Docker](https://img.shields.io/badge/Docker-2496ED?logo=docker&logoColor=white)
![Railway](https://img.shields.io/badge/Railway-0B0D0E?logo=railway&logoColor=white)
![SQLite](https://img.shields.io/badge/SQLite-003B57?logo=sqlite&logoColor=white)
![PostgreSQL](https://img.shields.io/badge/PostgreSQL-4169E1?logo=postgresql&logoColor=white)
[![CI](https://github.com/chen64811-ship-it/siamcart-ai-ecommerce/actions/workflows/ci.yml/badge.svg)](https://github.com/chen64811-ship-it/siamcart-ai-ecommerce/actions/workflows/ci.yml)

**Local Demo:** follow the setup instructions below, then open http://localhost:8000
**GitHub:** https://github.com/chen64811-ship-it/siamcart-ai-ecommerce

SiamCart is a deployed research prototype for Thai e-commerce customer support.
It combines deterministic transaction processing, policy retrieval, persistent
multi-turn context, JWT-based order ownership, and an optional LLM layer inside a FastAPI-based multi-agent
framework.

## Live Demo

![SiamCart Demo](docs/assets/siamcart_demo.gif)

Try it locally: http://localhost:8000

## Architecture

![SiamCart Architecture](docs/assets/siamcart_architecture.png)

The evaluated framework (M.Sc. thesis study) consists of three agents:

- **Intelligent Router** — deterministic intent classification and routing.
- **Transaction Tracker** — SQLite/PostgreSQL-backed order/payment/shipment facts.
- **Store Policy Evaluator** — RAG retrieval over store policy documents.

Additional components:

- **Product Catalog Lookup** — a demo extension with a deterministic handler,
  not a fourth evaluated agent.
- **Optional DeepSeek** — optional response-generation/fallback layer on top of
  the deterministic pipeline.

Deployment:

- Docker containerization
- Railway hosting with a persistent `/data` volume
- SQLite for local/demo use; PostgreSQL plus Alembic migrations for production
- ChromaDB index stored on persistent storage

## Demo Customer Journey

Normal path:

```
Browse Products
→ Add to Cart
→ Checkout
→ Demo Payment
→ My Orders
→ Ask AI
→ Simulate Shipment
→ Tracking
→ Mark Delivered
```

Cancellation path:

```
Processing + Not Shipped
→ Cancel Order
→ Simulated Refund
→ Stock Restored
```

No real payment or courier transaction occurs.

## What This Project Demonstrates

- FastAPI API design
- deterministic business logic
- SQLite transactions and persistent state
- multi-turn conversational context
- multi-agent routing
- RAG-based store policy retrieval
- simulated payment/refund/shipment workflows
- Docker containerization
- Railway deployment with persistent volume
- automated focused and regression testing
- JWT registration/login and customer-scoped order access
- structured JSON logging with request IDs and latency
- GitHub Actions compile, migration, test, JavaScript, and Docker checks

## Reliability

- Demo stabilization focused tests: 21 passed
- Shipment lifecycle focused tests: 21 passed
- Relevant regression suites: 95 passed
- Public Railway smoke journey passed
- SQLite state persisted after Railway service restart
- Browser console: 0 critical JavaScript errors during final public smoke

> The demo extensions were added after the frozen thesis evaluation and were
> not part of the original 120-scenario comparison.

## Research Scope

- Research prototype — built for an M.Sc. thesis study on multi-agent customer
  support; not a production store.
- Simulated payment — no real money is transferred.
- Simulated refund — no real money is transferred.
- Simulated shipment and tracking — no real courier integration, no real
  tracking API, no ETA prediction.
- Authentication is disabled on the public research demo by default; production
  deployments can enforce JWT authentication with `AUTH_REQUIRED=true`.
- Optional DeepSeek layer — deterministic pipeline works without it; the LLM
  is an optional response-generation/fallback layer.
- Customer-facing responses are generated in Thai by a deterministic
  SQLite-backed pipeline, with optional LLM formatting when configured.

## Tech Stack

- Python
- FastAPI
- SQLite
- PostgreSQL
- Alembic / SQLAlchemy migration metadata
- JWT / Argon2 password hashing
- ChromaDB
- DeepSeek (optional LLM formatter)
- Vanilla JavaScript
- HTML/CSS

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

Create and apply schema migrations:

```bash
alembic revision --autogenerate -m "describe schema change"
alembic upgrade head
```

Local development defaults to SQLite. In production, set `DATABASE_URL` to a
PostgreSQL connection string. The Docker startup command applies migrations
before starting Uvicorn.

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
| `DATABASE_URL` | `sqlite:///data/orders.db` | SQLite locally or PostgreSQL in production |
| `AUTH_REQUIRED` | `false` | Require JWT for order and chat ownership checks |
| `DEMO_LOGIN_ENABLED` | `false` | Enable the explicit one-click portfolio demo account |
| `JWT_SECRET_KEY` | development placeholder | JWT signing secret; required when auth is enforced |
| `JWT_ACCESS_TOKEN_MINUTES` | `30` | Access-token lifetime |
| `CORS_ORIGINS` | *(empty)* | Comma-separated allowed browser origins |

## Authentication

The lightweight auth API provides `POST /api/auth/register`, `POST
/api/auth/login`, and `GET /api/auth/me`. The browser UI includes Login and
Register forms, stores the short-lived access token in `sessionStorage`, and
automatically adds it to same-origin API requests as:

```http
Authorization: Bearer <access_token>
```

Authenticated orders store `orders.user_id`. List, detail, payment,
cancellation, shipment, delivery, and order-aware chat operations verify that
the order belongs to the current user. Cross-customer lookups return 404 so the
API does not disclose whether another customer's order exists.

Public portfolio deployments may explicitly set `DEMO_LOGIN_ENABLED=true` to
show a one-click Demo Login. `POST /api/auth/demo` then issues a token for a
shared, clearly labelled demo account without publishing a reusable password.
The endpoint returns 404 everywhere else.

## Observability

Every request receives an `X-Request-ID` response header. Logs are emitted as
JSON with `request_id`, method, path, latency, and status; chat completion logs
also include intent and order ID. Validation errors, expected HTTP failures,
and unexpected exceptions use centralized handlers.

## Tests

The default command runs the maintained production-facing regression suite
defined in `pytest.ini`. Tests force external LLM calls off and use temporary
SQLite databases, so the real runtime database and API quota are never touched:

```bash
.venv\Scripts\python.exe -m pytest -q
```

Earlier thesis-phase test modules remain available for targeted historical
audits, but they are not mixed into the Backend V2 release gate because several
encode superseded UI, routing, and embedding assumptions.

## Project Layout

```
app/
  agents/        # router, transaction tracker, policy evaluator, orchestrator
  api/           # FastAPI app and storefront routes
  db/            # SQLite/PostgreSQL repositories, migration metadata, seed
  models/        # Pydantic request models
  services/      # deterministic product catalog lookup
  static/        # frontend JS/CSS/images
  templates/     # HTML pages
data/
  policies/      # store policy documents (required by the app)
tests/           # focused pytest suites
alembic/         # versioned SQLite/PostgreSQL schema migrations
.github/workflows/ci.yml  # compile, migration, test, JS, Docker CI
```

## Security Considerations

- Passwords are hashed with Argon2; plaintext passwords are never stored.
- JWT secrets come from environment variables, and enforced auth rejects the
  development placeholder at startup.
- Browser access tokens use tab-scoped `sessionStorage`; a 401 response clears
  the token and prompts for authentication again.
- Order ownership is enforced server-side rather than trusting email, phone,
  session IDs, or browser-provided order data.
- SQL statements remain parameterized across PostgreSQL and SQLite.
- CORS is same-origin by default and can be allow-listed with `CORS_ORIGINS`.
- Responses include `nosniff`, clickjacking, and referrer-policy headers.
- Request logs avoid authentication tokens and password fields.
