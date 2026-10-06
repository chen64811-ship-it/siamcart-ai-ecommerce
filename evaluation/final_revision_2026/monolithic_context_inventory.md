# Monolithic Context Inventory

This is the exact controlled context supplied to the monolithic baseline.

- Order source: `T:\thai-ecommerce-agent\app\db\orders.py`
- Order source SHA-256: `3a9fd61245ebc2ddfbb1a0424c22bb5ac28d713084723b707a9cae8c0c1f2aae`
- Synthetic order count: 5
- Canonical order-context SHA-256: `3c903fb8be1fee5f04e1b181ec66b26e99177792ebc7afd9149b62f6913675d0`
- Policy source files:
  - `T:\thai-ecommerce-agent\data\policies\refund_policy.md` — SHA-256 `435f96561488dd046052d37272cd228abf89796bda3a583eaface1e6aa4d60b3`, 1994 characters
  - `T:\thai-ecommerce-agent\data\policies\return_policy.md` — SHA-256 `71e73220fb133c0c51555e1490f2cd5bf3d7eae674c18fd6fcc0be990e41885b`, 2066 characters
  - `T:\thai-ecommerce-agent\data\policies\exchange_policy.md` — SHA-256 `7af9760bd8759c37c177d04192ba9d6e3881819217110765da8e1b4ff59d3f3b`, 2172 characters
  - `T:\thai-ecommerce-agent\data\policies\shipping_policy.md` — SHA-256 `1b73b0e979f0049d3e0bdb4250693a1fe08308ab491e20816f9d8ba3ec50cf62`, 1898 characters
  - `T:\thai-ecommerce-agent\data\policies\payment_policy.md` — SHA-256 `1f1fc5b0804fd8212f1eb8dda7df10fcf718152e649ed9b55e21121bdbcabb3f`, 1868 characters

- Exact system-prompt SHA-256: `8e7e536fc5c0589617e0a4876d98fb2a63158407daaa3f897f270706389d6e0c`
- Baseline design: one unified DeepSeek JSON pipeline; no Router, Transaction Tracker, Store Policy Evaluator, direct SQLite lookup, or ChromaDB retrieval
