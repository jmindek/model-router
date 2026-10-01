import asyncio
import logging
import time
import uuid
from contextlib import asynccontextmanager

import httpx
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse

from src.config import settings
from src.inventory import fetch_discounted_models, load_cache, save_cache
from src.models import ChatCompletionRequest
from src.rate_limiter import rate_limiter
from src.router import classify_request

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting model router...")
    await refresh_inventory()

    scheduler = AsyncIOScheduler()
    cron_parts = settings.refresh_cron.split()
    cron_kwargs = {}
    for i, part in enumerate(cron_parts):
        key = ["minute", "hour", "day", "month", "day_of_week"][i]
        if part != "*":
            cron_kwargs[key] = int(part)
    scheduler.add_job(
        refresh_inventory,
        "cron",
        **cron_kwargs,
        id="inventory_refresh",
        replace_existing=True,
    )
    scheduler.start()
    app.state.scheduler = scheduler
    yield
    scheduler.shutdown()


async def refresh_inventory():
    """Refresh discount models from OpenRouter."""
    try:
        models = await fetch_discounted_models()
        save_cache(models)
        logger.info(f"Inventory refreshed: {len(models)} models")
    except BaseException:  # noqa: BLE001
        logger.error("Inventory refresh failed")


async def forward_to_openrouter(
    messages: list, model: str, stream: bool = False
) -> dict | StreamingResponse:
    """Forward request to OpenRouter with reactive rate limiting."""
    api_base = (
        settings.litellm_proxy if settings.use_litellm else settings.inventory_url
    )
    api_key = (
        settings.litellm_proxy if settings.use_litellm else settings.openrouter_api_key
    )

    if settings.use_litellm:
        api_base = settings.litellm_proxy
        api_key = ""

    max_retries = 3
    for attempt in range(max_retries):
        await rate_limiter.wait_if_needed(model)

        async with httpx.AsyncClient(timeout=120.0) as client:
            try:
                if stream:
                    async with client.stream(
                        "POST",
                        f"{api_base}/v1/chat/completions",
                        headers={"Authorization": f"Bearer {api_key}"},
                        json={"model": model, "messages": messages, "stream": True},
                    ) as resp:
                        if resp.status_code == 429:
                            rate_limiter.record_429(model)
                            if attempt < max_retries - 1:
                                await asyncio.sleep(2**attempt)
                                continue
                            raise HTTPException(status_code=429, detail="Rate limited")
                        resp.raise_for_status()
                        rate_limiter.clear(model)

                        async def event_generator(resp_inner=resp):
                            async for line in resp_inner.aiter_lines():
                                if line.startswith("data:"):
                                    yield f"{line}\n"

                        return StreamingResponse(
                            event_generator(), media_type="text/event-stream"
                        )
                else:
                    resp = await client.post(
                        f"{api_base}/v1/chat/completions",
                        headers={"Authorization": f"Bearer {api_key}"},
                        json={"model": model, "messages": messages, "stream": False},
                    )
                    if resp.status_code == 429:
                        rate_limiter.record_429(model)
                        if attempt < max_retries - 1:
                            await asyncio.sleep(2**attempt)
                            continue
                        raise HTTPException(status_code=429, detail="Rate limited")
                    resp.raise_for_status()
                    rate_limiter.clear(model)
                    return resp.json()

            except httpx.HTTPStatusError:
                raise
            except httpx.HTTPError:
                if attempt < max_retries - 1:
                    await asyncio.sleep(2**attempt)
                    continue
                raise

    raise HTTPException(status_code=503, detail="Max retries exceeded")


app = FastAPI(title="Discount Model Router", lifespan=lifespan)


@app.post("/v1/chat/completions")
async def chat_completions(request: ChatCompletionRequest):
    """Main routing endpoint."""
    messages = request.messages
    last_msg = messages[-1] if messages else None
    content = last_msg.content if last_msg else ""
    last_message = content if isinstance(content, str) else ""

    models = load_cache()
    if not models:
        if settings.fallback_mode == "halt":
            raise HTTPException(status_code=503, detail="No models available")
        model = settings.static_fallback
    else:
        model = await classify_request(last_message, None, models)
        if not model:
            if settings.fallback_mode == "halt":
                raise HTTPException(status_code=503, detail="Routing failed")
            model = settings.static_fallback

    result = await forward_to_openrouter(messages, model, request.stream)
    if isinstance(result, StreamingResponse):
        return result

    return {
        "id": f"chatcmpl-{uuid.uuid4().hex[:12]}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": result["choices"][0]["message"]["content"],
                },
                "finish_reason": result["choices"][0].get("finish_reason"),
            }
        ],
        "usage": result.get("usage", {}),
    }


@app.get("/healthz")
async def health():
    return {"status": "ok"}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8080)
