# Cloud-friendly image for UTI (use Groq — Ollama is too heavy for free tiers)
FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential curl git \
    && rm -rf /var/lib/apt/lists/*

COPY service/requirements.txt /app/requirements.txt
COPY packages/tradingagents /app/packages/tradingagents
COPY packages/kronos /app/packages/kronos
COPY service /app/service
COPY .env.example /app/.env.example

RUN pip install --no-cache-dir -r /app/requirements.txt \
    && pip install --no-cache-dir -e /app/packages/tradingagents \
    && pip install --no-cache-dir 'transformers>=4.40,<5' safetensors einops yfinance worldmonitor-sdk requests

ENV PYTHONPATH=/app/service/server
ENV UTI_LLM_PROVIDER=groq
ENV UTI_SCANNER_ENABLED=true
ENV UTI_SCAN_SYMBOLS=XAUUSD,XAGUSD,NAS100,USOIL,EURUSD
ENV UTI_SCAN_TIMEFRAME=30
ENV DB_PATH=/tmp/clawtrader.db
ENV DATABASE_URL=
ENV API_STDERR_LOG=true

EXPOSE 8000

CMD ["python", "-m", "uvicorn", "main:app", "--app-dir", "service/server", "--host", "0.0.0.0", "--port", "8000"]
