# Architecture & Execution Handoff

## Project Overview

Discount Model Router — routes AI inference requests to OpenRouter models that have active discounts (≥50% by default). Uses a "System One" model (e.g., `upstage/solar-decide`) to classify incoming requests into categories and select the best discounted model.

**Target API**: OpenAI-compatible (`/v1/chat/completions`). Compatible with OpenRouter.

---

## Architecture

```
Client → FastAPI (port 8080) → Router Logic → OpenRouter API
                              │
                              ├── Classify request (System One model)
                              ├── Select discounted model from inventory
                              ├── Forward request (with rate limiting)
                              └── Return response (stream or non-stream)
                              │
                              └── Inventory refresh (cron, daily at 2am UTC)
                                  ├── GET /api/v1/models (all models)
                                  └── GET /api/v1/models/{id}/endpoints (discount info)
```

### Key Insight: Discount Data Source

The `/models` endpoint does NOT include discount info. Discount is only in `/models/{id}/endpoints`. This means ~400 endpoint calls per refresh cycle (one per model). Confirmed via live API calls.

---

## File Inventory

| File | Lines | Purpose |
|------|-------|---------|
| `src/main.py` | 202 | FastAPI app, lifespan, routing endpoint, forward/stream logic, URL target resolution |
| `src/router.py` | 128 | Category classification, model selection, System One integration |
| `src/inventory.py` | 102 | Fetch discounted models, cache save/load, client lifecycle management |
| `src/config.py` | 57 | Pydantic settings, env var mapping |
| `src/models.py` | 24 | Pydantic models: `ChatCompletionRequest`, `ModelInfo`, `ChatMessage` |
| `src/rate_limiter.py` | 49 | Reactive rate limiting (429 handling, backoff tracking) |
| `tests/test_regression_p0.py` | 124 | Regression unit tests for P0 fixes (inventory client context, URL formatting, streaming lifecycle) |
| `tests/test_router.py` | 78 | Router unit tests |
| `tests/test_inventory.py` | 38 | Cache save/load tests |
| `tests/test_models.py` | 24 | Model validation tests |
| `tests/test_rate_limiter.py` | 29 | Rate limiter tests |
| `tests/conftest.py` | 5 | Test path setup |
| `.env.example` | 42 | Environment variable template |
| `Dockerfile` | 19 | Container build |
| `docker-compose.yml` | 15 | Docker compose (router only) |
| `pyproject.toml` | 28 | Project config, dependencies |

---

## Key Components

### 1. Inventory (`src/inventory.py`)
- Fetches all models from OpenRouter `/models` endpoint.
- Iterates through models while keeping `httpx.AsyncClient` context open to fetch `/models/{id}/endpoints` for discount info.
- Accepts optional `client` parameter for easy dependency injection in tests.
- Filters by `min_discount` threshold (default 50%).
- Saves to `data/models.json` cache file.
- Refreshed via cron scheduler (default: daily at 2am UTC).

### 2. Router (`src/router.py`)
- **Categories**: `coding`, `reasoning`, `writing`, `chat`, `analysis`, `creative`
- `build_category_map()`: Maps each category to the best discounted model (sorted by discount %, then price)
- `classify_request()`: Sends request to System One model for classification
- `_category_to_model()`: Maps classified category to model ID
- `CATEGORY_MODEL_MAP`: Global map updated on each classification

### 3. Main App (`src/main.py`)
- FastAPI app with `/v1/chat/completions` endpoint.
- `forward_to_openrouter()`: Proxies requests to OpenRouter with reactive rate limiting. Supports streaming and non-streaming responses.
- Fallback: static model or 503 if no models available.

### 4. Rate Limiter (`src/rate_limiter.py`)
- Reactive: only activates on 429 responses.
- Backoff tracking per-model.
- Clears backoff state on successful request.

### 5. Config (`src/config.py`)
- Pydantic Settings with env var aliases.
- All vars prefixed with `ROUTER_` (except `OPENROUTER_API_KEY`).

---

## Current State

### Tests
- **14 tests, all passing** (including 3 P0 regression tests in `test_regression_p0.py`).
- Coverage: router logic, inventory cache & client lifecycle, model validation, rate limiter, URL target resolution, and streaming connection lifecycle.

### Git History & Status
- **Active Branch**: `feature/router`
- **Recent P0 Fixes**:
  - Fixed closed `httpx.AsyncClient` bug during inventory endpoint fetching (`src/inventory.py`).
  - Fixed streaming generator premature context manager exit (`src/main.py`).
  - Fixed `/v1` URL duplication and API key assignment logic (`src/main.py`).

---

## Known Issues & Backlog

### Resolved (P0)
1. **[FIXED] Closed HTTP Client in Inventory Refresh** — Scoped `httpx.AsyncClient` context around both `/models` and `/endpoints` loop.
2. **[FIXED] Broken Streaming Lifecycle** — `event_generator` now manages connection closing in a `finally` block upon completion.
3. **[FIXED] Base URL `/v1` Duplication & API Key Assignment** — Added `forward_to_openrouter()` helper to strip trailing slashes/v1 duplicates and forward to OpenRouter.

### Pending Issues & Open Questions

1. **Inventory Refresh Bottleneck (400+ API Calls)**
   - **Problem**: Fetching `/models/{id}/endpoints` for every model sequentially.
   - **Recommendation**: Add concurrency limit via `asyncio.Semaphore(10)` to speed up refresh.

2. **`forward_to_openrouter` Stream/Non-Stream Duplication**
   - Stream and non-stream logic can be further consolidated.

3. **`main.py` Non-Streaming Response Wrapping**
   - Non-streaming response is manually wrapped. Returning OpenRouter response dict directly will preserve tool calling, logprobs, and full OpenAI schema.

4. **Category Map Keyword Config**
   - Category keywords in `build_category_map()` are currently hardcoded.

5. **Inventory Health Check**
   - `/healthz` returns static `"ok"` without reporting cache freshness or age.

---

## Active Requirements for Next Iteration

### High Priority
1. **Add concurrency limit to inventory fetch** — Use `asyncio.Semaphore(10)` to accelerate model endpoint fetching.
2. **Fix RateLimiter lock scope** — Prevent `wait_if_needed` lock from blocking concurrent requests for unthrottled models.
3. **Align Dockerfile Python version** — Set Dockerfile base image to Python 3.13.

### Medium Priority
4. **Add inventory freshness check** — Report cache age in `/healthz`.
5. **Preserve full OpenAI schema** — Pass through OpenRouter JSON directly for non-streaming completions.

---

## Run Commands

```bash
# Local development
uv run uvicorn src.main:app --reload --port 8080

# Tests
uv run pytest

# Type checking
uv run pyright

# Docker
docker compose up --build
```
