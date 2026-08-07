"""Configuration Module"""
import os
from pathlib import Path

from dotenv import load_dotenv

# Load .env from project root (so run.py doesn't need to)
_env_path = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(_env_path)

# Debug: write config state for runtime verification
_debug_key = os.environ.get("DEEPSEEK_API_KEY", "")
try:
    _log_dir = Path(__file__).resolve().parent.parent / "logs"
    _log_dir.mkdir(parents=True, exist_ok=True)
    with open(_log_dir / "config_debug.log", "w") as _f:
        _f.write(f"ENV file: {_env_path} (exists={_env_path.exists()})\n")
        _f.write(f"DEEPSEEK_ENABLED: {os.environ.get('DEEPSEEK_ENABLED')}\n")
        _f.write(f"DEEPSEEK_API_KEY set: {bool(_debug_key)}\n")
        _f.write(f"DEEPSEEK_API_KEY len: {len(_debug_key)}\n")
        _f.write(f"DEEPSEEK_API_KEY first 12: {_debug_key[:12] if _debug_key else 'EMPTY'}\n")
        _f.write(f"os.environ DEEPSEEK keys: {[k for k in os.environ if 'DEEPSEEK' in k]}\n")
except Exception:
    pass  # debug logging must not crash config

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

# ChromaDB (Phase 3+)
CHROMA_DB_DIR = str(BASE_DIR / "data" / "chroma")

# Database
DATABASE_URL = os.getenv("DATABASE_URL", f"sqlite:///{BASE_DIR / 'data' / 'orders.db'}")

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
