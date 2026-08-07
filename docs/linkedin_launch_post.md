Built and deployed an AI-powered e-commerce customer support system with FastAPI and Railway.

Over the past few weeks, I have been developing SiamCart, a research prototype
for Thai e-commerce customer support.

What started as a small multi-agent AI prototype gradually became a complete,
publicly deployed customer journey.

The current system supports:

- Product browsing and shopping cart
- Checkout and order creation
- Simulated payment
- Order, payment, and shipment tracking
- Persistent multi-turn AI conversation context
- Store policy retrieval with RAG
- Simulated order cancellation and refund
- Inventory restoration after cancellation
- Simulated shipment, tracking number, and delivery lifecycle
- Thai customer-facing responses

On the backend, I used FastAPI + SQLite, with an Intelligent Router, Transaction
Tracker, and Store Policy Evaluator handling different customer-support tasks.

For deployment, I containerized the application with Docker and deployed it on
Railway, using a persistent volume so SQLite order data survives service restarts.

One thing I learned from this project is that building an AI application is not
only about connecting an LLM.

A useful system also needs deterministic business logic, reliable state
management, error handling, database consistency, testing, deployment, and a
usable customer experience.

Live Demo:
https://web-production-1ea79.up.railway.app

GitHub:
https://github.com/chen64811-ship-it/siamcart-ai-ecommerce

This project is also part of my master's research into an LLM-powered
multi-agent customer support framework for Thai e-commerce.

#FastAPI #Python #AI #LLM #MultiAgent #RAG #BackendDevelopment #Docker
#Railway #SQLite #Ecommerce #SoftwareEngineering
