FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Optimized for 2 vCPUs and 4GB RAM:
# - 1 worker (single process to reduce memory overhead)
# - 4 threads per worker (utilize 2 vCPUs with threading)
# - Limit concurrency to prevent memory exhaustion
# - Timeout settings to prevent hanging connections
CMD uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000} --workers 1 --limit-concurrency 50 --timeout-keep-alive 5 --timeout-graceful-shutdown 30
