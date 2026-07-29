# Cloud-friendly image for UTI (free APIs + TradingAgents + Kronos-mini CPU)
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

# Core API + TradingAgents
RUN pip install --no-cache-dir -r /app/requirements.txt \
    && pip install --no-cache-dir -e /app/packages/tradingagents \
    && pip install --no-cache-dir \
        'transformers>=4.40,<5' safetensors einops yfinance worldmonitor-sdk \
        requests huggingface_hub pandas numpy tqdm

# PyTorch CPU (required for real Kronos-mini inference)
RUN pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu

# Prefetch Kronos-mini weights into the image so first scan is not blocked
ENV HF_HOME=/app/.cache/huggingface
ENV TRANSFORMERS_CACHE=/app/.cache/huggingface
RUN python - <<'PY'
from huggingface_hub import snapshot_download
for repo in ("NeoQuasar/Kronos-Tokenizer-2k", "NeoQuasar/Kronos-mini"):
    print("downloading", repo)
    snapshot_download(repo_id=repo)
print("kronos weights ready")
PY

ENV PYTHONPATH=/app/service/server:/app/packages/kronos
# auto = pick first free LLM with a key (gemini → cerebras → groq → openrouter → hf → ollama)
ENV UTI_LLM_PROVIDER=auto
ENV UTI_QUICK_MODEL=llama-3.1-8b-instant
ENV UTI_DEEP_MODEL=llama-3.1-8b-instant
ENV UTI_SCANNER_ENABLED=true
ENV UTI_SCAN_SYMBOLS=XAUUSD,XAGUSD,NAS100,US30,SPX500,USOIL,EURUSD
ENV UTI_SCAN_TIMEFRAME=30
ENV UTI_SCAN_INTERVAL_SECONDS=900
ENV UTI_SIGNAL_MIN_QUALITY=60
ENV UTI_SIGNAL_MIN_CONFIDENCE=65
ENV UTI_PAPER_ONLY=true
ENV TRADINGAGENTS_ENABLED=true
ENV TRADINGAGENTS_FULL_GRAPH=true
ENV UTI_TA_ANALYSTS=market,news
ENV UTI_MAX_DEBATE_ROUNDS=1
ENV UTI_TA_CACHE_SECONDS=1200
ENV UTI_TA_PREFER_COMPACT=true
ENV KRONOS_ENABLED=true
ENV KRONOS_MODEL=NeoQuasar/Kronos-mini
ENV KRONOS_TOKENIZER=NeoQuasar/Kronos-Tokenizer-2k
ENV KRONOS_DEVICE=cpu
ENV MIROFISH_ENABLED=true
ENV DB_PATH=/tmp/clawtrader.db
ENV DATABASE_URL=
ENV API_STDERR_LOG=true

EXPOSE 8000

CMD ["python", "-m", "uvicorn", "main:app", "--app-dir", "service/server", "--host", "0.0.0.0", "--port", "8000"]
