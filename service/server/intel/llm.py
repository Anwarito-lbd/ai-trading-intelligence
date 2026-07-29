"""Shared OpenAI-compatible LLM endpoint resolver with free-tier failover.

Providers (prefer free / freemium):
  groq | gemini | openrouter | cerebras | huggingface | ollama | openai | openai_compatible

`UTI_LLM_PROVIDER=auto` (or unset with keys present) picks the first working free endpoint.
"""

from __future__ import annotations

import logging
import os
from typing import Any

import requests

logger = logging.getLogger(__name__)

# Order matters: higher TPM / friendlier for agent prompts first when auto.
_FREE_PROVIDER_ORDER = (
    "gemini",      # ~1M TPM free — best for agent desks
    "cerebras",    # high TPM free
    "groq",        # fast but low TPM (6k) — use compact prompts
    "openrouter",  # free model router
    "huggingface", # HF inference routers
    "ollama",      # local
)


def resolve_llm_provider() -> str:
    raw = (
        os.getenv("UTI_LLM_PROVIDER")
        or os.getenv("TRADINGAGENTS_LLM_PROVIDER")
        or "auto"
    ).strip().lower()
    if raw in {"", "auto", "free"}:
        return pick_free_provider() or "heuristic"
    return raw


def pick_free_provider() -> str | None:
    """Return first free provider that has credentials / local runtime."""
    for provider in _FREE_PROVIDER_ORDER:
        if llm_endpoint(provider) is not None:
            return provider
    return None


def _is_ollama_tag(model: str) -> bool:
    """Ollama tags look like llama3.2:1b — never send those to hosted APIs."""
    if not model or "/" in model:
        return False
    return ":" in model


def resolve_quick_model(provider: str | None = None) -> str:
    provider = (provider or resolve_llm_provider()).lower()
    explicit = os.getenv("UTI_QUICK_MODEL", "").strip()
    if provider == "ollama":
        return os.getenv("OLLAMA_MODEL", explicit or "llama3.2:1b")
    if provider == "groq":
        return os.getenv(
            "UTI_GROQ_QUICK_MODEL",
            explicit if explicit and not _is_ollama_tag(explicit) else "llama-3.1-8b-instant",
        )
    if provider == "gemini":
        return os.getenv("UTI_GEMINI_QUICK_MODEL", "gemini-2.5-flash")
    if provider == "cerebras":
        return os.getenv("UTI_CEREBRAS_QUICK_MODEL", "llama-3.3-70b")
    if provider == "openrouter":
        return os.getenv("UTI_OPENROUTER_QUICK_MODEL", "openrouter/free")
    if provider == "huggingface":
        return os.getenv(
            "UTI_HF_QUICK_MODEL",
            "meta-llama/Meta-Llama-3.1-8B-Instruct",
        )
    if explicit and not _is_ollama_tag(explicit):
        return explicit
    return "gpt-4o-mini"


def resolve_deep_model(provider: str | None = None) -> str:
    provider = (provider or resolve_llm_provider()).lower()
    explicit = os.getenv("UTI_DEEP_MODEL", "").strip()
    if provider == "ollama":
        return os.getenv("OLLAMA_DEEP_MODEL", os.getenv("OLLAMA_MODEL", explicit or "llama3.2:1b"))
    if provider == "groq":
        # Keep deep==quick on free Groq to avoid huge TPM burn
        return os.getenv(
            "UTI_GROQ_DEEP_MODEL",
            os.getenv("UTI_GROQ_QUICK_MODEL", "llama-3.1-8b-instant"),
        )
    if provider == "gemini":
        return os.getenv("UTI_GEMINI_DEEP_MODEL", "gemini-2.5-flash")
    if provider == "cerebras":
        return os.getenv("UTI_CEREBRAS_DEEP_MODEL", "llama-3.3-70b")
    if provider == "openrouter":
        return os.getenv("UTI_OPENROUTER_DEEP_MODEL", "openrouter/free")
    if provider == "huggingface":
        return os.getenv(
            "UTI_HF_DEEP_MODEL",
            "meta-llama/Meta-Llama-3.1-8B-Instruct",
        )
    if explicit and not _is_ollama_tag(explicit):
        return explicit
    return "gpt-4o"


def llm_endpoint(provider: str | None = None) -> tuple[str, str, str] | None:
    """Return (chat_completions_url, api_key_or_empty, model) or None."""
    provider = (provider or resolve_llm_provider()).lower()
    if provider in {"auto", "free", "heuristic"}:
        return None
    model = resolve_quick_model(provider)

    if provider == "ollama":
        base = os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434/v1").rstrip("/")
        if not base.endswith("/v1"):
            base = base.rstrip("/") + "/v1"
        key = os.getenv("OLLAMA_API_KEY", "ollama").strip() or "ollama"
        return (f"{base}/chat/completions", key, model)

    if provider == "groq":
        key = os.getenv("GROQ_API_KEY", "").strip()
        if not key:
            return None
        return ("https://api.groq.com/openai/v1/chat/completions", key, model)

    if provider == "gemini":
        key = (
            os.getenv("GEMINI_API_KEY", "").strip()
            or os.getenv("GOOGLE_API_KEY", "").strip()
        )
        if not key:
            return None
        # OpenAI-compatible Gemini endpoint (free tier via AI Studio key)
        return (
            "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions",
            key,
            model,
        )

    if provider == "cerebras":
        key = os.getenv("CEREBRAS_API_KEY", "").strip()
        if not key:
            return None
        return ("https://api.cerebras.ai/v1/chat/completions", key, model)

    if provider == "openrouter":
        key = os.getenv("OPENROUTER_API_KEY", "").strip()
        if not key:
            return None
        return ("https://openrouter.ai/api/v1/chat/completions", key, model)

    if provider == "huggingface":
        key = (
            os.getenv("HF_TOKEN", "").strip()
            or os.getenv("HUGGINGFACE_HUB_TOKEN", "").strip()
            or os.getenv("HUGGINGFACE_API_KEY", "").strip()
        )
        if not key:
            return None
        # HF router OpenAI-compatible
        base = os.getenv(
            "UTI_HF_BASE_URL",
            "https://router.huggingface.co/v1",
        ).rstrip("/")
        return (f"{base}/chat/completions", key, model)

    if provider in {"openai", "openai_compatible"}:
        key = os.getenv("OPENAI_API_KEY", os.getenv("OPENAI_COMPATIBLE_API_KEY", "")).strip()
        base = os.getenv(
            "UTI_LLM_BASE_URL",
            os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1"),
        ).rstrip("/")
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
    failover: bool | None = None,
) -> str | None:
    """Call chat completions; optionally failover across free providers."""
    pinned = (provider or os.getenv("UTI_LLM_PROVIDER") or "auto").strip().lower()
    do_failover = failover if failover is not None else pinned in {"", "auto", "free"}
    providers: list[str] = []
    if pinned not in {"", "auto", "free", "heuristic"}:
        providers = [pinned]
        if do_failover:
            providers += [p for p in _FREE_PROVIDER_ORDER if p != pinned]
    else:
        providers = list(_FREE_PROVIDER_ORDER)

    timeout = timeout if timeout is not None else float(os.getenv("UTI_LLM_TIMEOUT_SECONDS", "60"))
    for prov in providers:
        endpoint = llm_endpoint(prov)
        if endpoint is None:
            continue
        url, api_key, default_model = endpoint
        use_model = model or default_model
        # Don't force Groq/OpenAI model names onto Gemini etc.
        if model and prov != pinned and pinned not in {"", "auto", "free"}:
            use_model = default_model
        if pinned in {"", "auto", "free"}:
            use_model = default_model
        headers: dict[str, str] = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        if prov == "openrouter":
            headers["HTTP-Referer"] = os.getenv("UTI_PUBLIC_URL", "https://uti-trading-intel.onrender.com")
            headers["X-Title"] = "UTI Trading Intelligence"
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
                logger.warning(
                    "LLM HTTP %s via %s: %s",
                    resp.status_code,
                    prov,
                    resp.text[:300],
                )
                continue
            content = (
                resp.json()
                .get("choices", [{}])[0]
                .get("message", {})
                .get("content", "")
            )
            if content:
                return content
        except Exception as exc:
            logger.warning("LLM call failed (%s): %s", prov, exc)
            continue
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


def provider_status() -> dict[str, Any]:
    """Health snapshot of free LLM providers for /api/uti/providers."""
    out: dict[str, Any] = {"active": resolve_llm_provider(), "candidates": {}}
    for prov in _FREE_PROVIDER_ORDER + ("openai",):
        ep = llm_endpoint(prov)
        out["candidates"][prov] = {
            "configured": ep is not None,
            "model": resolve_quick_model(prov) if ep else None,
        }
    return out
