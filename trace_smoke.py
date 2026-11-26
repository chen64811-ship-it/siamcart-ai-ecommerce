"""Arize tracing smoke test for the SiamCart multi-agent framework.

Runs two customer messages through the full multi-agent pipeline and reports
the resulting traces to Arize (project "siamcart").

Usage (Windows CMD):
  set ARIZE_SPACE_ID=U3BhY2U6NTk5NjI6T3I2NA==
  set ARIZE_API_KEY=ak-xxxx-xxxx
  .venv\\Scripts\\python.exe trace_smoke.py
"""

import os
import sys

project_root = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, project_root)

from dotenv import load_dotenv
load_dotenv(os.path.join(project_root, ".env"))

# 1) Initialize the order database (seed ORD-1001..1005)
from app.config import STORE_DB_PATH
from app.db.migrations import upgrade_database
from app.db.orders import init_database

print("Initializing database...")
upgrade_database()
init_database(STORE_DB_PATH)
print(f"DB ready at {STORE_DB_PATH}")

# 2) Initialize Arize tracing (no-op without credentials)
from app.arize_tracing import init_tracing, is_enabled
init_tracing()
print("Tracing enabled:", is_enabled())

# 3) Run messages through the multi-agent pipeline
from app.agents.orchestrator import process_message

messages = [
    "Where is my order ORD-1001?",
    "What is your return policy?",
]

for msg in messages:
    print(f"\n=== 提问: {msg} ===")
    result = process_message(msg)
    print(f"  intent          : {result.get('intent')}")
    print(f"  agent           : {result.get('agent')}")
    print(f"  response_source : {result.get('response_source')}")
    print(f"  latency_ms      : {result.get('latency_ms')}")
    print(f"  response        : {str(result.get('response', ''))[:120]}")

# 4) Flush spans so they are sent to Arize before exit
if is_enabled():
    from opentelemetry.trace import get_tracer_provider
    get_tracer_provider().force_flush()
    print("\nDone. Check the 'siamcart' project in Arize for the traces.")
else:
    print("\nDone (tracing disabled — no spans sent).")
