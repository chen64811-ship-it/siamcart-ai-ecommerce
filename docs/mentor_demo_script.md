# Mentor Demo Script — SiamCart Multi-Agent Customer Support

**Project:** An LLM-Powered Multi-Agent Framework for Automated Customer Support in Thai E-commerce
**Target duration:** 5–7 minutes
**Presenter notes:** brief Chinese notes (中文备注) under each section.

---

## 1. Introduction — 30 seconds

Good morning. This is a research prototype of an LLM-powered multi-agent framework for
automated customer support in Thai e-commerce. The system combines a realistic storefront
with an AI assistant that answers product questions, tracks orders, explains store policy,
and simulates payments — all backed by a real SQLite database.

**中文备注：** 开场定位：这是硕士论文研究原型——"LLM 驱动的泰国电商多智能体客服框架"。
强调"真实店面 + 多智能体 AI 客服 + SQLite 真实数据"三件事，30 秒内讲完。

---

## 2. Realistic storefront — 45 seconds

This is SiamCart, the storefront. It is built with FastAPI serving a vanilla JavaScript
front end. There are 12 products across fashion, electronics, footwear, and bags —
each with real product images, ratings, prices, and live stock levels read directly
from SQLite. You can search, filter by category, and sort products. Nothing here is
hard-coded in the page; every product comes from the database.

**中文备注：** 展示首页：12 个商品、分类筛选、搜索、排序。强调"数据全部来自 SQLite，
页面零硬编码"。可以点开 1–2 个商品展示图片和价格。

---

## 3. Product Catalog Lookup — 60 seconds

Let me open a product — CloudStep Casual Sneakers — and click *Ask AI About This Product*.

Notice the context card at the top of the chat: it shows the product name, the ID
PROD-008, the current SQLite price, and the current stock. This card is loaded fresh
from the database, not from the browser.

I will ask four questions:

1. **Is this product in stock?** → "Yes — CloudStep Casual Sneakers (PROD-008) is in stock, with 47 units available right now."
2. **How much does it cost?** → "CloudStep Casual Sneakers (PROD-008) is currently ฿990."
3. **What are its main features?** → a deterministic list from the product record.
4. **What sizes are available?** → the assistant honestly says the catalogue does not provide size information — it does not invent an answer.

If we open the *Research details* panel, we see: Handler = product_catalog_lookup,
Database source = SQLite, Demo extension = Yes. The answer took a few milliseconds.
Important: this is a **demo extension**, not a fourth evaluated agent — the completed
120-scenario comparison evaluated the three-agent framework only.

**中文备注：** 这是本演示的核心亮点之一。强调四点：(1) 上下文卡片实时读 SQLite；
(2) 答案确定性、毫秒级；(3) 不知道就说不知道（尺寸不编造）；(4) Product Catalog
Lookup 是演示扩展，不属于论文评估的三智能体框架。

---

## 4. Cart and checkout — 60 seconds

Let me add the sneakers to the cart. The cart drawer updates immediately. Clicking
*Proceed to Checkout* opens the checkout form with customer information, delivery
address, and payment method — Cash on Delivery, Bank Transfer, or Card.

Note the order summary: subtotal, shipping, and total are computed server-side.
Prices are never accepted from the browser; they are read from SQLite on the server.
When the order is placed, a real row is written to the orders table in one SQLite
transaction, and the server returns an order reference like ORD-XXXX.

**中文备注：** 演示加购 → 购物车 → 结账表单。强调"价格由服务器端从 SQLite 读取，
浏览器不可篡改"、"下单即真实写入 SQLite（单事务）"。"Card 支付在本原型中禁用"
可顺带一提，说明没有真实支付。

---

## 5. My Orders and order details — 60 seconds

Here is *My Orders*. I can search by email — for example mentor.demo@example.com.
Every order card shows the product image, product name, quantity, total, and three
status badges: payment, order, and shipment.

ORD-1010 — CloudStep Casual Sneakers, quantity 1, total 990 THB — shows
**Pending / Processing / Not Shipped**. Opening *View Details* shows the order items,
customer information, delivery address, and the payment/shipment badges.
Because this is a Cash on Delivery order, the *Confirm Demo Payment* button is not
offered — cash on delivery remains pending until delivery.

**中文备注：** 演示 My Orders 列表和订单详情。展示三个状态徽章（支付/订单/配送）。
强调 COD 订单不提供"模拟支付"按钮（符合业务逻辑）。数据来自 SQLite。

---

## 6. Transaction Tracker — 60 seconds

Now the AI side of order tracking. Back on the storefront, I open the assistant and ask:

**Where is order ORD-1010?**

The response: order status processing, payment pending, shipment not shipped.
The *Research details* panel shows Handler = transaction_tracker, Intent = ORDER_STATUS,
Order ID = ORD-1010. This is deterministic: the Transaction Tracker reads the order
from SQLite and answers with exact evidence — no language model is involved, so the
answer is always correct and instant. If I ask while a product is selected, the
Transaction Tracker still takes over, because order questions always win over product
context.

**中文备注：** 演示"订单查询覆盖商品上下文"：即使当前选中商品，问订单问题仍由
Transaction Tracker 处理。强调确定性 + SQLite 证据 + 无 LLM 延迟。

---

## 7. Demo Payment — 45 seconds

Payments here are a research simulation — no real money and no real payment provider.
For a bank-transfer order, the assistant and the UI both reflect the payment status.
Let me show a bank-transfer demo order: clicking *Confirm Demo Payment* marks it paid.
The badge changes from Pending to Paid, and a *Paid at* timestamp appears.
Order and shipment statuses stay unchanged. Asking the assistant "Has this order been
paid?" now reports paid — the Transaction Tracker reads the updated SQLite row
immediately.

**中文备注：** 演示 bank_transfer 订单的"Confirm Demo Payment"：Pending → Paid，
出现 paid_at 时间戳；订单/配送状态不变；AI 立即反映最新状态。强调"模拟支付"——
不接入任何真实支付/物流服务。

---

## 8. Architecture and research scope — 45 seconds

Let me summarize the architecture. The browser storefront talks to the FastAPI API.
The API has two sides:

- **Demo-only storefront extensions**: Product Catalog Lookup, cart, checkout, and
  order pages — they read and write SQLite directly. These are not part of the
  evaluated framework.
- **The evaluated three-agent framework**: the Intelligent Router dispatches to the
  Transaction Tracker (deterministic SQLite evidence for orders and payments), the
  Store Policy Evaluator (ChromaDB retrieval over policy documents), and a general
  response path. DeepSeek is used only as an optional LLM layer for natural-language
  generation and policy reasoning.

Product and order facts use deterministic SQLite lookup because it improves
correctness — the answer is exactly what the database says — and latency, which drops
from seconds to milliseconds. That is why DeepSeek is not required for factual product
and order queries.

**中文备注：** 架构三句话：(1) 店面 + 产品查询 = 演示扩展；(2) 三智能体框架
（Router → Transaction Tracker / Policy Evaluator / General）= 论文评估对象；
(3) 确定性 SQLite 路径：更准、更快，事实类问题不需要 LLM。给出架构图一页。

---

## 9. Closing — 20 seconds

To conclude: this prototype shows a complete customer-support loop — browse products,
ask product-aware questions, place orders, track them, and simulate payments — with an
evaluated multi-agent framework at the core and deterministic SQLite as the source of
truth. Thank you — I am happy to take questions.

**中文备注：** 收尾一句话总结 + 致谢。准备好回答"为什么不用 LLM 答产品/订单问题"：
正确性与延迟。

---

## Quick reference — exact demo questions

- Is this product in stock?
- How much does it cost?
- What are its main features?
- What sizes are available?
- Where is order ORD-1010?
- Has ORD-1010 been paid?

## Key talking points checklist

- FastAPI + Vanilla JS + SQLite architecture.
- Evaluated three-agent framework: Intelligent Router → Transaction Tracker / Store Policy Evaluator / General Response.
- Product Catalog Lookup is a demo extension, not a fourth evaluated agent.
- Transaction Tracker uses deterministic SQLite evidence.
- Store Policy Evaluator handles policy questions.
- Product facts use deterministic SQLite lookup.
- No real payment or logistics service is used — payments and orders are research simulations.
- Deterministic lookup improves correctness and latency.
- DeepSeek is not required for factual product and order queries.
