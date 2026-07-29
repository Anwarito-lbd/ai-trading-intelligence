"""Shared OpenAI-compatible LLM endpoint resolver (Groq, Ollama, OpenAI, …).

Used by the trading brain and MiroFish swarm so local models (Ollama) work
the same way as hosted providers.
"""

from __future__ import annotations

import logging
import os
from typing import Any

import requests

logger = logging.getLogger(__name__)


def resolve_llm_provider() -> str:
    return (
        os.getenv("UTI_LLM_PROVIDER")
        or os.getenv("TRADINGAGENTS_LLM_PROVIDER")
        or "heuristic"
    ).strip().lower()


def resolve_quick_model(provider: str | None = None) -> str:
    provider = (provider or resolve_llm_provider()).lower()
    explicit = os.getenv("UTI_QUICK_MODEL", "").strip()
    if explicit:
        return explicit
    if provider == "ollama":
        return os.getenv("OLLAMA_MODEL", "llama3.2:1b")
    if provider == "groq":
        return "llama-3.1-8b-instant"
    return "gpt-4o-mini"


def resolve_deep_model(provider: str | None = None) -> str:
    provider = (provider or resolve_llm_provider()).lower()
    explicit = os.getenv("UTI_DEEP_MODEL", "").strip()
    if explicit:
        return explicit
    if provider == "ollama":
        return os.getenv("OLLAMA_DEEP_MODEL", os.getenv("OLLAMA_MODEL", "llama3.2:1b"))
    if provider == "groq":
        return "llama-3.3-70b-versatile"
    return "gpt-4o"


def llm_endpoint(provider: str | None = None) -> tuple[str, str, str] | None:
    """Return (chat_completions_url, api_key_or_empty, model) or None."""
    provider = (provider or resolve_llm_provider()).lower()
    model = resolve_quick_model(provider)

    if provider == "ollama":
        base = os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434/v1").rstrip("/")
        # Accept either .../v1 or bare host:port
        if not base.endswith("/v1"):
            base = base.rstrip("/") + "/v1"
        key = os.getenv("OLLAMA_API_KEY", "ollama").strip() or "ollama"
        return (f"{base}/chat/completions", key, model)

    if provider == "groq":
        key = os.getenv("GROQ_API_KEY", "").strip()
        if not key:
            return None
        return ("https://api.groq.com/openai/v1/chat/completions", key, model)

    if provider == "openrouter":
        key = os.getenv("OPENROUTER_API_KEY", "").strip()
        if not key:
            return None
        return ("https://openrouter.ai/api/v1/chat/completions", key, model)

    if provider in {"openai", "openai_compatible"}:
        key = os.getenv("OPENAI_API_KEY", os.getenv("OPENAI_COMPATIBLE_API_KEY", "")).strip()
        base = os.getenv("UTI_LLM_BASE_URL", os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")).rstrip("/")
        if not key and provider == "openai":
            return None
        return (f"{base}/chat/completions", key or "EMPTY", model)

    return None


def chat_completion(
    *,
    messages: list[dict[str, str]],
    provider: str | None = None,
    model: str | None = None,
    temperature: float = 0.2,
    max_tokens: int = 200,
    timeout: float | None = None,
) -> str | None:
    """Call chat completions; return assistant content or None on failure."""
    endpoint = llm_endpoint(provider)
    if endpoint is None:
        return None
    url, api_key, default_model = endpoint
    use_model = model or default_model
    headers: dict[str, str] = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    timeout = timeout if timeout is not None else float(os.getenv("UTI_LLM_TIMEOUT_SECONDS", "60"))
    try:
        resp = requests.post(
            url,
            headers=headers,
            json={
                "model": use_model,
                "temperature": temperature,
                "max_tokens": max_tokens,
                "messages": messages,
            },
            timeout=timeout,
        )
        if resp.status_code >= 400:
            logger.warning("LLM HTTP %s via %s: %s", resp.status_code, provider or resolve_llm_provider(), resp.text[:300])
            return None
        return (
            resp.json()
            .get("choices", [{}])[0]
            .get("message", {})
            .get("content", "")
        )
    except Exception as exc:
        logger.warning("LLM call failed (%s): %s", provider or resolve_llm_provider(), exc)
        return None


def ollama_health() -> dict[str, Any]:
    base = os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434/v1").rstrip("/")
    host = base.replace("/v1", "").rstrip("/")
    try:
        resp = requests.get(f"{host}/api/tags", timeout=3)
        if resp.status_code >= 400:
            return {"ok": False, "base": host, "status_code": resp.status_code}
        models = [m.get("name") for m in (resp.json().get("models") or []) if isinstance(m, dict)]
        return {"ok": True, "base": host, "models": models}
    except Exception as exc:
        return {"ok": False, "base": host, "reason": str(exc)}
