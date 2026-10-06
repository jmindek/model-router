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
from src.handoff import handle_model_handoff, model_in_inventory
from src.inventory import fetch_discounted_models, load_cache, save_cache
from src.models import ChatCompletionRequest
from src.rate_limiter import rate_limiter
from src.router import classify_request
from src.telemetry import (
    errors_total,
    handoff_events,
    init_telemetry,
    inventory_errors,
    inventory_refreshes,
    model_selections,
    rate_limit_events,
    request_duration,
    tracer,
)

import time

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def _parse_cron(expr: str) -> dict:
    """Parse cron expression to APScheduler kwargs."""
    parts = expr.strip().split()
    return {
        "minute": parts[0],
        "hour": parts[1],
        "day": parts[2],
        "month": parts[3],
        "day_of_week": parts[4],
    }


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_telemetry()
    logger.info("Starting model router...")
    await refresh_inventory()

    scheduler = AsyncIOScheduler()
    scheduler.add_job(
        refresh_inventory,
        "cron",
        **_parse_cron(settings.refresh_cron),
        id="inventory_refresh",
        replace_existing=True,
    )
    scheduler.start()
    app.state.scheduler = scheduler
    yield
    scheduler.shutdown()


async def refresh_inventory():
    """Refresh discount models from OpenRouter."""
    with tracer.start_as_current_span("inventory_refresh"):
        inventory_refreshes.add(1)
        try:
            models = await fetch_discounted_models()
            save_cache(models)
            logger.info(f"Inventory refreshed: {len(models)} models")
        except BaseException:  # noqa: BLE001
            inventory_errors.add(1)
            logger.error("Inventory refresh failed")


async def forward_to_openrouter(
    messages: list, model: str, stream: bool = False
) -> dict | StreamingResponse:
    # Convert ChatMessage objects to dicts for JSON serialization
    msg_list = [m.model_dump() if hasattr(m, "model_dump") else m for m in messages]
    """Forward request to OpenRouter with reactive rate limiting."""
    if settings.use_litellm:
        api_base = settings.litellm_proxy.rstrip("/")
        api_key = settings.litellm_master_key
    else:
        api_base = settings.inventory_url.removesuffix("/v1").rstrip("/")
        api_key = settings.openrouter_api_key

    max_retries = 3
    for attempt in range(max_retries):
        await rate_limiter.wait_if_needed(model)

        # Build headers - only include Authorization if key is present
        headers = {}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        async with httpx.AsyncClient(timeout=120.0) as client:
            try:
                if stream:
                    async with client.stream(
                        "POST",
                        f"{api_base}/v1/chat/completions",
                        headers=headers,
                        json={"model": model, "messages": msg_list, "stream": True},
                    ) as resp:
                        if resp.status_code == 429:
                            rate_limiter.record_429(model)
                            rate_limit_events.add(1, {"model": model})
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
                        headers=headers,
                        json={"model": model, "messages": msg_list, "stream": False},
                    )
                    if resp.status_code == 429:
                        rate_limiter.record_429(model)
                        rate_limit_events.add(1, {"model": model})
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
    start = time.time()
    model = "unknown"
    with tracer.start_as_current_span("chat_completions") as span:
        try:
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

            # Check if selected model is still discounted
            if models and not model_in_inventory(model, models):
                model, error = handle_model_handoff(model, models)
                if error:
                    raise HTTPException(status_code=400, detail=error)
                handoff_events.add(1, {"reason": "model_removed_from_discount"})

            model_selections.add(1, {"model": model})
            span.set_attribute("router.model", model)

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
        except HTTPException:
            raise
        except Exception as e:
            errors_total.add(1, {"type": type(e).__name__})
            raise
        finally:
            duration_ms = (time.time() - start) * 1000
            request_duration.record(duration_ms, {"model": model if 'model' in dir() else "unknown"})


@app.get("/healthz")
async def health():
    return {"status": "ok"}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8080)
