import asyncio
import json
import logging
from pathlib import Path

import httpx

from src.config import settings
from src.models import ModelInfo
from src.scraper import scrape_discounted_models

logger = logging.getLogger(__name__)

ENDPOINT_CONCURRENCY = 10


async def fetch_discounted_models(client: httpx.AsyncClient | None = None) -> list[ModelInfo]:
    """Fetch all models from OpenRouter, filter by discount threshold.

    When called without a custom client, uses Playwright to scrape models with discount
    badges from /models?order=discount-high-to-low and enriches them with a single /models API call.
    Falls back to cached inventory or per-endpoint queries if scraping fails.
    """
    base_url = settings.inventory_url.rstrip("/")
    headers = {"Authorization": f"Bearer {settings.openrouter_api_key}"}

    # If a custom client is supplied (e.g. in unit tests), use the endpoint-query method
    if client is not None:
        return await _fetch_via_endpoints(client, base_url, headers)

    # 1. Try Playwright scraper + single /models API metadata call
    try:
        scraped_discounts = await scrape_discounted_models()
        if scraped_discounts:
            async with httpx.AsyncClient(timeout=30.0) as c:
                resp = await c.get(f"{base_url}/models", headers=headers)
                resp.raise_for_status()
                data = resp.json()

            models_by_id = {m["id"]: m for m in data.get("data", []) if m.get("id")}
            min_disc_pct = settings.min_discount * 100

            discounted: list[ModelInfo] = []
            for mid, disc_pct in scraped_discounts.items():
                if disc_pct < min_disc_pct:
                    continue

                m_meta = models_by_id.get(mid, {})
                pricing = m_meta.get("pricing", {})
                top_prov = m_meta.get("top_provider", {})

                try:
                    in_price = float(pricing.get("prompt", 0) or 0)
                except (ValueError, TypeError):
                    in_price = 0.0

                try:
                    out_price = float(pricing.get("completion", 0) or 0)
                except (ValueError, TypeError):
                    out_price = 0.0

                discounted.append(
                    ModelInfo(
                        id=mid,
                        discount_pct=disc_pct,
                        provider=top_prov.get("name", "") if isinstance(top_prov, dict) else "",
                        input_price=in_price,
                        output_price=out_price,
                        context_length=int(m_meta.get("context_length", 0) or 0),
                    )
                )

            # Sort by discount desc, then price asc
            discounted.sort(key=lambda m: (-m.discount_pct, m.input_price + m.output_price))

            if discounted:
                save_cache(discounted)
                logger.info(
                    f"Found and cached {len(discounted)} discounted models (>= {min_disc_pct:.0f}%)"
                )
                return discounted

    except Exception as e:
        logger.warning(f"Playwright scraper failed: {e}. Falling back to cache.")

    # 2. Fallback to cached models if available
    cached = load_cache()
    if cached:
        logger.info(f"Loaded {len(cached)} models from cache.")
        return cached

    # 3. Final fallback: endpoint queries
    async with httpx.AsyncClient(timeout=30.0) as c:
        return await _fetch_via_endpoints(c, base_url, headers)


async def _fetch_via_endpoints(
    c: httpx.AsyncClient, base_url: str, headers: dict
) -> list[ModelInfo]:
    """Fetch models and query individual endpoints concurrently."""
    resp = await c.get(f"{base_url}/models", headers=headers)
    resp.raise_for_status()
    data = resp.json()

    models = data.get("data", [])
    model_ids = [m["id"] for m in models if m.get("id")]

    decision_model = settings.model
    if decision_model and decision_model not in model_ids:
        model_ids.append(decision_model)

    semaphore = asyncio.Semaphore(ENDPOINT_CONCURRENCY)
    tasks = [fetch_endpoint(c, base_url, headers, mid, semaphore) for mid in model_ids]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    discounted = []
    for r in results:
        if isinstance(r, ModelInfo):
            discounted.append(r)

    logger.info(
        f"Found {len(discounted)} discounted models (>= {settings.min_discount * 100:.0f}%)"
    )
    return discounted


async def fetch_endpoint(
    c: httpx.AsyncClient,
    base_url: str,
    headers: dict,
    model_id: str,
    semaphore: asyncio.Semaphore,
) -> ModelInfo | None:
    """Fetch endpoints for one model, gated by semaphore."""
    async with semaphore:
        try:
            ep_resp = await c.get(
                f"{base_url}/models/{model_id}/endpoints",
                headers=headers,
                timeout=10.0,
            )
            if ep_resp.status_code != 200:
                return None

            ep_data = ep_resp.json()
            endpoints = ep_data.get("data", {}).get("endpoints", [])

            if not endpoints:
                return None

            ep = endpoints[0]
            pricing = ep.get("pricing", {})
            discount = pricing.get("discount", 0)

            if discount >= settings.min_discount:
                return ModelInfo(
                    id=model_id,
                    discount_pct=discount * 100,
                    provider=ep.get("provider", ""),
                    input_price=float(pricing.get("prompt", 0) or 0),
                    output_price=float(pricing.get("completion", 0) or 0),
                    context_length=int(ep.get("context_length", 0) or 0),
                )
        except httpx.HTTPError as e:
            logger.warning(f"Failed to fetch endpoints for {model_id}: {e}")
    return None


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
