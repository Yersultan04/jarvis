"""Model presets — pickable per-RAG configurations.

Each preset bundles models for every role (chat, vision, rerank) and the
embedding backend. When a RAG is created with a preset, a snapshot of the
resolved values is saved into `rag.settings.models` so changing a preset
definition later doesn't retroactively break existing RAGs.

The embedding part (provider + model + dim) is locked at create-time:
changing it later requires re-indexing because Qdrant collections are
created with a fixed vector dimension.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass

from app.config import get_settings


@dataclass(frozen=True)
class Preset:
    id: str
    label: str
    description: str
    llm_model: str
    llm_rerank_model: str
    llm_vision_model: str
    embed_provider: str               # "voyage" | "openai"
    embed_model: str                  # voyage model name OR openai-compat model name
    embed_dim: int
    # Per-RAG LLM endpoint (chat + rerank). None → use OpenRouter (env defaults).
    # When set, points at a direct OpenAI-compatible host (Cerebras, Groq, Ollama, …).
    llm_base_url: str | None = None
    llm_api_key: str | None = None    # None → fall back to LLM_API_KEY env var
    # Only used when embed_provider == "openai". None → fall back to global env.
    embed_base_url: str | None = None
    embed_api_key: str | None = None
    # OpenRouter sub-provider pinning (e.g. ["Cerebras"]); applied as
    # extra_body={"provider": {"order": [...], "allow_fallbacks": true}} on chat calls.
    # Ignored when llm_base_url points at a non-OpenRouter host.
    llm_provider_order: tuple[str, ...] | None = None


# ----- Registry -----
# Add new presets here. Keys are stable IDs persisted into rag.settings.
PRESETS: dict[str, Preset] = {
    "cloud": Preset(
        id="cloud",
        label="Cloud (GPT-5.4)",
        description="Managed cloud stack: OpenAI GPT-5.4 + Voyage voyage-3. "
                    "Топ-качество, OpenRouter + Voyage cloud.",
        llm_model="openai/gpt-5.4",
        llm_rerank_model="openai/gpt-4o-mini",
        llm_vision_model="openai/gpt-4o",
        embed_provider="voyage",
        embed_model="voyage-3",
        embed_dim=1024,
    ),
    "oss": Preset(
        id="oss",
        label="OSS (gpt-oss + Qwen3-VL + bge-m3)",
        description="Open-weights end-to-end: gpt-oss-120b (chat/rerank, OpenRouter) + "
                    "qwen3-vl-30b-a3b (vision) + bge-m3 (embed via DeepInfra).",
        llm_model="openai/gpt-oss-120b",
        llm_rerank_model="openai/gpt-oss-120b",
        llm_vision_model="qwen/qwen3-vl-30b-a3b-instruct",
        embed_provider="openai",
        embed_model="BAAI/bge-m3",
        embed_dim=1024,
        embed_base_url="https://api.deepinfra.com/v1/openai",
        embed_api_key=None,
    ),
    "oss-qwen3": Preset(
        id="oss-qwen3",
        label="OSS-MAX (gpt-oss + Qwen3-VL + Qwen3-Embedding-8B)",
        description="Тот же OSS, но эмбеддер — Qwen3-Embedding-8B (4096d, лидер MTEB). "
                    "Лучше качество retrieval, в 4× жирнее вектора, чуть выше латентность.",
        llm_model="openai/gpt-oss-120b",
        llm_rerank_model="openai/gpt-oss-120b",
        llm_vision_model="qwen/qwen3-vl-30b-a3b-instruct",
        embed_provider="openai",
        embed_model="Qwen/Qwen3-Embedding-8B",
        embed_dim=4096,
        embed_base_url="https://api.deepinfra.com/v1/openai",
        embed_api_key=None,
    ),
    "fast": Preset(
        id="fast",
        label="Fast (cheap & quick)",
        description="GPT-4o-mini + voyage-3-lite. Самая дешёвая + быстрая, "
                    "качество ниже двух других.",
        llm_model="openai/gpt-4o-mini",
        llm_rerank_model="openai/gpt-4o-mini",
        llm_vision_model="openai/gpt-4o-mini",
        embed_provider="voyage",
        embed_model="voyage-3-lite",
        embed_dim=512,
    ),
}


def list_presets() -> list[dict]:
    """For the API to surface to the UI."""
    return [
        {
            "id": p.id,
            "label": p.label,
            "description": p.description,
            "llm_model": p.llm_model,
            "llm_rerank_model": p.llm_rerank_model,
            "llm_vision_model": p.llm_vision_model,
            "embed_provider": p.embed_provider,
            "embed_model": p.embed_model,
            "embed_dim": p.embed_dim,
        }
        for p in PRESETS.values()
    ]


def get_preset(preset_id: str | None) -> Preset | None:
    if not preset_id:
        return None
    return PRESETS.get(preset_id)


def snapshot_from_preset(preset_id: str) -> dict:
    """Return a JSON-safe dict to store in rag.settings.models."""
    p = PRESETS[preset_id]
    return {"preset": preset_id, **asdict(p)}


def snapshot_from_env() -> dict:
    """Fallback snapshot when the caller didn't specify a preset.

    Captures whatever the global env is configured to right now so the RAG
    remains consistent even if env is changed later.
    """
    s = get_settings()
    return {
        "preset": "env",
        "id": "env",
        "label": "Custom (env)",
        "description": "Inherited from server env at create time.",
        "llm_model": s.llm_model,
        "llm_rerank_model": s.llm_rerank_model,
        "llm_vision_model": s.llm_vision_model,
        "embed_provider": s.embed_provider if s.embed_provider == "openai" else "voyage",
        "embed_model": s.embed_model_name if s.embed_provider == "openai" and s.embed_model_name
                       else s.voyage_embed_model,
        "embed_dim": s.embed_dim if s.embed_provider == "openai" and s.embed_dim
                     else s.voyage_embed_dim,
    }


def resolve_models_for_rag(rag) -> dict:
    """Reads rag.settings.models or falls back to current env. Used at run-time
    by pipeline / loop / OCR so a RAG always sees the models it was created with.
    """
    s = get_settings()
    snap = (rag.settings or {}).get("models") if rag is not None else None
    if not snap:
        return {
            "llm_model": s.llm_model,
            "llm_rerank_model": s.llm_rerank_model,
            "llm_vision_model": s.llm_vision_model,
            "llm_base_url": s.llm_api_base_url,
            "llm_api_key": s.llm_api_key,
            "embed_provider": s.embed_provider if s.embed_provider == "openai" else "voyage",
            "embed_model": s.embed_model_name if s.embed_provider == "openai" and s.embed_model_name
                           else s.voyage_embed_model,
            "embed_dim": s.embed_dim if s.embed_provider == "openai" and s.embed_dim
                         else s.voyage_embed_dim,
            "embed_base_url": s.embed_api_base_url,
            "embed_api_key": s.embed_api_key,
            "llm_provider_order": None,
        }
    return {
        "llm_model": snap.get("llm_model", s.llm_model),
        "llm_rerank_model": snap.get("llm_rerank_model", s.llm_rerank_model),
        "llm_vision_model": snap.get("llm_vision_model", s.llm_vision_model),
        "llm_base_url": snap.get("llm_base_url") or s.llm_api_base_url,
        # If snapshot specifies a base_url but no key, look up the *snapshot's* key
        # first, then fall back to LLM_API_KEY env. (Don't fall back to openrouter
        # key — that's a different provider altogether.)
        "llm_api_key": snap.get("llm_api_key") or s.llm_api_key,
        "embed_provider": snap.get("embed_provider", "voyage"),
        "embed_model": snap.get("embed_model", s.voyage_embed_model),
        "embed_dim": snap.get("embed_dim", s.voyage_embed_dim),
        "embed_base_url": snap.get("embed_base_url") or s.embed_api_base_url,
        "embed_api_key": snap.get("embed_api_key") or s.embed_api_key,
        "llm_provider_order": snap.get("llm_provider_order"),
    }
