from __future__ import annotations

from openai import AsyncOpenAI

from app.config import get_settings


def _role_env(role: str) -> tuple[str | None, str | None]:
    """Per-role env overrides (chat/vision/rerank). None when not set."""
    s = get_settings()
    role_attrs = {
        "chat": (s.llm_api_base_url, s.llm_api_key),
        "vision": (s.vision_api_base_url, s.vision_api_key),
        "rerank": (s.rerank_api_base_url, s.rerank_api_key),
    }
    return role_attrs.get(role, (None, None))


def _resolve(role: str) -> tuple[str, str]:
    """Resolve (base_url, api_key) for a role, with OpenRouter as the final fallback.

    Important: a role-specific override is honoured ONLY when BOTH base_url and
    api_key are set together. If only one is set, treat it as misconfigured and
    fall back to OpenRouter — otherwise we'd pair a Cerebras key with the
    OpenRouter URL (or vice-versa) and get a confusing 401.

    Per-RAG (snapshot) base/key overrides go through `chat(base_url=..., api_key=...)`
    directly, not through this resolver.
    """
    s = get_settings()
    base, key = _role_env(role)
    if not (base and key):
        base, key = s.openrouter_base_url, s.openrouter_api_key
    if not key:
        raise RuntimeError(
            f"No API key configured for role={role!r}. Set OPENROUTER_API_KEY "
            f"or BOTH a role-specific *_API_BASE_URL and *_API_KEY override."
        )
    return base, key


# Cache OpenAI-compatible clients by (base_url, api_key) so we don't spin up a
# new httpx pool for every chat call. Different RAGs hitting different
# providers (Cerebras / Groq / OpenRouter / …) get their own client.
_client_cache: dict[tuple[str, str], AsyncOpenAI] = {}


def _get_client(base_url: str, api_key: str) -> AsyncOpenAI:
    key = (base_url, api_key)
    c = _client_cache.get(key)
    if c is None:
        # 90s per request — slow providers (or reasoning models with huge max_tokens)
        # would otherwise hang for 10 min (SDK default).
        c = AsyncOpenAI(api_key=api_key, base_url=base_url, timeout=90.0, max_retries=1)
        _client_cache[key] = c
    return c


def _client_for(role: str) -> AsyncOpenAI:
    base, key = _resolve(role)
    return _get_client(base, key)


def get_llm_client() -> AsyncOpenAI:
    return _client_for("chat")


def get_vision_client() -> AsyncOpenAI:
    return _client_for("vision")


def get_rerank_client() -> AsyncOpenAI:
    return _client_for("rerank")


async def chat(
    messages: list[dict],
    *,
    model: str | None = None,
    temperature: float = 0.1,
    response_format: dict | None = None,
    max_tokens: int | None = None,
    role: str = "chat",
    base_url: str | None = None,
    api_key: str | None = None,
    provider_order: list[str] | tuple[str, ...] | None = None,
) -> str:
    """Send a chat completion request.

    Per-RAG override: pass `base_url` (+ optional `api_key`) to route this
    specific call through a different OpenAI-compatible host (Cerebras, Groq,
    Ollama, …). Without it, falls back to the role-based env resolver.
    """
    s = get_settings()
    if role == "rerank":
        default_model = s.llm_rerank_model
    elif role == "vision":
        default_model = s.llm_vision_model
    else:
        default_model = s.llm_model

    if base_url:
        # Per-RAG endpoint override. api_key may come from the snapshot OR fall
        # back to LLM_API_KEY env. Note: we deliberately do NOT mix with
        # OpenRouter's key — those belong to different providers.
        key = api_key or s.llm_api_key
        if not key:
            raise RuntimeError(
                f"Per-RAG llm_base_url={base_url!r} requires an api_key in the "
                "snapshot OR LLM_API_KEY env."
            )
        client = _get_client(base_url, key)
    else:
        client = _client_for(role)

    kwargs: dict = {
        "model": model or default_model,
        "messages": messages,
        "temperature": temperature,
    }
    if response_format is not None:
        kwargs["response_format"] = response_format
    if max_tokens is not None:
        kwargs["max_tokens"] = max_tokens
    # OpenRouter sub-provider pinning. Harmless on direct hosts (they ignore it).
    if provider_order:
        kwargs["extra_body"] = {
            "provider": {"order": list(provider_order), "allow_fallbacks": True}
        }
    resp = await client.chat.completions.create(**kwargs)
    return resp.choices[0].message.content or ""
