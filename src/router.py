import logging

import httpx

from src.config import settings
from src.models import ModelInfo

logger = logging.getLogger(__name__)

CATEGORIES = ["coding", "reasoning", "writing", "chat", "analysis", "creative"]

CATEGORY_MODEL_MAP: dict[str, str] = {}


def build_category_map(models: list[ModelInfo]) -> dict[str, str]:
    """Map request categories to best discounted model."""
    global CATEGORY_MODEL_MAP

    sorted_models = sorted(
        models,
        key=lambda m: (-m.discount_pct, m.input_price + m.output_price),
    )

    # Single pass: assign each model to its best category
    category_best: dict[str, str | None] = {c: None for c in CATEGORIES}
    for m in sorted_models:
        mid = m.id.lower()
        for cat, keywords in {
            "coding": ["coder", "code", "deepseek", "qwen"],
            "reasoning": ["r1", "o1", "o3", "reason"],
            "writing": ["claude", "gemini", "llama"],
            "chat": ["flash", "turbo", "mini"],
            "analysis": ["pro", "max", "ultra"],
            "creative": ["sonnet", "opus"],
        }.items():
            if any(kw in mid for kw in keywords) and category_best[cat] is None:
                category_best[cat] = m.id

    CATEGORY_MODEL_MAP = {
        cat: category_best[cat] or sorted_models[0].id for cat in CATEGORIES
    }

    logger.info(f"Category map: {CATEGORY_MODEL_MAP}")
    return CATEGORY_MODEL_MAP


CLASSIFY_QUESTIONS = {
    "category": {
        "instructions": "Classify this request into one category.",
        "criteria": {
            "coding": "Code generation, debugging, refactoring, software development",
            "reasoning": "Math, logic, complex problem solving, step-by-step analysis",
            "writing": "Creative writing, editing, summarization, content generation",
            "chat": "Casual conversation, greetings, general questions",
            "analysis": "Data analysis, research, comparison, evaluation",
            "creative": "Art, design, brainstorming, ideation",
        },
        "type": "choice",
    }
}


async def classify_request(
    message: str, last_model: str | None, models: list[ModelInfo]
) -> str | None:
    """Use System One model to classify request and select best model."""
    if not models:
        logger.warning("No models available for routing")
        return None

    build_category_map(models)

    api_base, api_key = _get_router_api()
    if not api_key:
        logger.error("No router API key configured")
        return None

    async with httpx.AsyncClient(timeout=15.0) as client:
        # Normalize base URL: strip trailing /api/v1 for decisions endpoint
        base = api_base.rstrip("/")
        base = base.removesuffix("/api/v1")

        endpoint = f"{base}/api/alpha/decisions"

        resp = await client.post(
            endpoint,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": settings.model,
                "state": {"message": message},
                "questions": CLASSIFY_QUESTIONS,
            },
        )

        if resp.status_code != 200:
            logger.error(f"Router model failed: {resp.status_code} {resp.text}")
            return None

        data = resp.json()
        choice = data["answers"]["category"]["choice"].strip().lower()
        model_id = _category_to_model(choice)
        if model_id:
            logger.info(f"Router selected: {model_id} (category: {choice})")
            return model_id

        logger.warning(f"Router returned unparseable response: {choice}")
        return None


def _get_router_api() -> tuple[str, str]:
    """Get router API base and key based on mode."""
    if settings.mode == "local":
        return settings.omlx_base, ""
    elif settings.mode == "cloud":
        return settings.api_base, settings.api_key
    else:
        if settings.api_key:
            return settings.api_base, settings.api_key
        return settings.omlx_base, ""


def _category_to_model(category: str) -> str | None:
    """Map classified category to best model from inventory."""
    category = category.strip().lower()
    if category in CATEGORY_MODEL_MAP:
        return CATEGORY_MODEL_MAP[category]
    for key, model_id in CATEGORY_MODEL_MAP.items():
        if category in key or key in category:
            return model_id
    return CATEGORY_MODEL_MAP.get("chat")
