"""Configuration Module"""
import os
from pathlib import Path

from dotenv import load_dotenv

# Load .env from project root (so run.py doesn't need to)
_env_path = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(_env_path)

BASE_DIR = Path(__file__).resolve().parent.parent

# ── DeepSeek LLM Generator (Phase 4A) ─────────────────────────────────
# Reads from .env or environment variables
DEEPSEEK_ENABLED = os.getenv("DEEPSEEK_ENABLED", "false").lower() == "true"
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
DEEPSEEK_MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-v4-flash")
DEEPSEEK_TIMEOUT_SECONDS = int(os.getenv("DEEPSEEK_TIMEOUT_SECONDS", "8"))

# LLM Client config (used when DEEPSEEK_ENABLED=True and key is non-empty)
LLM_CONFIG = {
    "api_key": DEEPSEEK_API_KEY,
    "base_url": os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
    "model": DEEPSEEK_MODEL,
    "temperature": 0.1,
    "max_tokens": 512,
    "timeout": DEEPSEEK_TIMEOUT_SECONDS,
}

# Embedding model for RAG (Phase 3+)
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2")

# ChromaDB (Phase 3+). Overridable for deployment (persistent volume path).
CHROMA_DB_DIR = os.getenv("CHROMA_DIR", str(BASE_DIR / "data" / "chroma"))

# Runtime SQLite database. Overridable for deployment (persistent volume).
STORE_DB_PATH = os.getenv("STORE_DB_PATH", str(BASE_DIR / "data" / "orders.db"))

# Database
DATABASE_URL = os.getenv(
    "DATABASE_URL", f"sqlite:///{Path(STORE_DB_PATH).as_posix()}"
)

# Authentication is optional for the public research demo and mandatory when
# explicitly enabled in production.
AUTH_REQUIRED = os.getenv("AUTH_REQUIRED", "false").lower() == "true"
JWT_SECRET_KEY = os.getenv("JWT_SECRET_KEY", "change-me-in-production")
JWT_ALGORITHM = "HS256"
JWT_ACCESS_TOKEN_MINUTES = int(os.getenv("JWT_ACCESS_TOKEN_MINUTES", "30"))

# Comma-separated browser origins. Empty means same-origin only.
CORS_ORIGINS = [
    origin.strip()
    for origin in os.getenv("CORS_ORIGINS", "").split(",")
    if origin.strip()
]

# Session
SESSION_EXPIRE_MINUTES = 30

# Paths
DATA_DIR = BASE_DIR / "data"
POLICIES_DIR = DATA_DIR / "policies"
SCENARIOS_DIR = DATA_DIR / "scenarios"
LOGS_DIR = BASE_DIR / "logs"
STATIC_DIR = BASE_DIR / "app" / "static"

# Simulation
INTENT_CATEGORIES = [
    "order_status",
    "return_refund",
    "shipping_delay",
    "store_policy",
    "clarification",
    "out_of_scope",
]
