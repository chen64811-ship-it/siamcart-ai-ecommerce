# SiamCart — FastAPI production container (research prototype)
FROM python:3.11-slim

WORKDIR /app

# Install dependencies first (better layer caching)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Application source, templates, static assets, and policy documents
COPY app ./app
COPY run.py .
COPY pytest.ini .

# Store policy documents are read at runtime by the policy evaluator.
# The runtime SQLite DB and Chroma index are intentionally NOT copied —
# they are created/initialized on persistent storage at startup.
COPY data/policies ./data/policies

# Runtime configuration comes from environment variables only.
# Never copy .env into the image.
ENV DEEPSEEK_ENABLED=false \
    DEEPSEEK_API_KEY="" \
    DEEPSEEK_MODEL=deepseek-v4-flash \
    DEEPSEEK_BASE_URL=https://api.deepseek.com \
    DEEPSEEK_TIMEOUT_SECONDS=8 \
    DATABASE_URL=sqlite:////data/orders.db

# Persistent storage is provided by the platform (Railway volume at /data).

EXPOSE 8000

# Listen on $PORT when provided by the platform (Railway/Render/Fly),
# defaulting to 8000 locally.
CMD ["sh", "-c", "python -m uvicorn app.api.server:app --host 0.0.0.0 --port ${PORT:-8000}"]
