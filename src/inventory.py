import json
import logging
from pathlib import Path

import httpx

from src.config import settings
from src.models import ModelInfo

logger = logging.getLogger(__name__)


async def fetch_discounted_models() -> list[ModelInfo]:
    """Fetch all models from OpenRouter, filter by discount threshold."""
    async with httpx.AsyncClient(timeout=30.0) as client:
        # Get all models
        resp = await client.get(
            f"{settings.inventory_url}/models",
            headers={"Authorization": f"Bearer {settings.openrouter_api_key}"},
        )
        resp.raise_for_status()
        data = resp.json()

    models = data.get("data", [])
    discounted = []

    for model in models:
        model_id = model.get("id", "")
        if not model_id:
            continue

        # Fetch endpoint details for discount info
        try:
            ep_resp = await client.get(
                f"{settings.inventory_url}/models/{model_id}/endpoints",
                headers={"Authorization": f"Bearer {settings.openrouter_api_key}"},
                timeout=10.0,
            )
            if ep_resp.status_code != 200:
                continue

            ep_data = ep_resp.json()
            endpoints = ep_data.get("data", {}).get("endpoints", [])

            for ep in endpoints:
                pricing = ep.get("pricing", {})
                discount = pricing.get("discount", 0)

                if discount >= settings.min_discount:
                    discounted.append(
                        ModelInfo(
                            id=model_id,
                            discount_pct=discount * 100,
                            provider=ep.get("provider", ""),
                            input_price=pricing.get("prompt", 0),
                            output_price=pricing.get("completion", 0),
                            context_length=ep.get("context_length", 0),
                        )
                    )
                    break  # One entry per model is enough
        except httpx.HTTPError as e:
            logger.warning(f"Failed to fetch endpoints for {model_id}: {e}")
            continue

    logger.info(
        f"Found {len(discounted)} discounted models (>= {settings.min_discount * 100:.0f}%)"
    )
    return discounted


def save_cache(models: list[ModelInfo], path: str | None = None) -> None:
    """Save model cache to JSON file."""
    if path is None:
        path = settings.cache_file

    cache_path = Path(path)
    cache_path.parent.mkdir(parents=True, exist_ok=True)

    data = [m.model_dump() for m in models]
    cache_path.write_text(json.dumps(data, indent=2))
    logger.info(f"Saved {len(models)} models to {path}")


def load_cache(path: str | None = None) -> list[ModelInfo]:
    """Load model cache from JSON file."""
    if path is None:
        path = settings.cache_file

    cache_path = Path(path)
    if not cache_path.exists():
        return []

    try:
        data = json.loads(cache_path.read_text())
        return [ModelInfo(**m) for m in data]
    except (json.JSONDecodeError, ValueError) as e:
        logger.error(f"Failed to load cache: {e}")
        return []
