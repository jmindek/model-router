# Discount Model Router

Routes AI requests only to discounted OpenRouter models. Auto-switches to the next best discounted model when the current one falls off the discount list.

## Quick Start

```bash
cp .env.example .env
# Edit .env with your API keys

docker compose up --build
```

## Configuration

| Env Var | Default | Description |
|---------|---------|-------------|
| `OPENROUTER_API_KEY` | | OpenRouter API key for forwarding requests |
| `ROUTER_MODEL` | `upstage/solar-decide` | Model used for routing/classification decisions |
| `ROUTER_API_KEY` | | API key for the routing model |
| `ROUTER_API_BASE` | `https://openrouter.ai/api/v1` | API base URL for the routing model |
| `ROUTER_INVENTORY_URL` | `https://openrouter.ai/api/v1` | OpenRouter API URL for fetching discounted models |
| `ROUTER_MODE` | `auto` | Routing mode: cloud, local, auto |
| `ROUTER_OMLX_BASE` | `http://127.0.0.1:8000` | Local oMLX fallback base URL |
| `ROUTER_MIN_DISCOUNT` | `0.50` | Minimum discount threshold (0.0–1.0) |
| `ROUTER_FALLBACK_MODE` | `static` | `static` = auto-fallback to `ROUTER_STATIC_FALLBACK`; `halt` = return 503 |
| `ROUTER_STATIC_FALLBACK` | `openrouter/deepseek/deepseek-v3.1` | Fallback model when routing fails |
| `ROUTER_MODEL_HANDOFF` | `next_best` | Behavior when selected model falls off discount list: `stop`, `next_best`, `keep_current` |
| `ROUTER_CACHE_FILE` | `data/models.json` | Path to cached discounted models |
| `ROUTER_REFRESH_CRON` | `0 2 * * *` | Cron schedule for inventory refresh (default: 2am UTC) |
| `OTEL_ENDPOINT` | | OpenTelemetry gRPC endpoint (e.g. `http://localhost:4317`). Empty = console exporter |

## Model Handoff

When the selected model falls off the discount list, the router handles it based on `ROUTER_MODEL_HANDOFF`:

- **`stop`** (default: `next_best`) — Returns 400 with a message telling the user the model is no longer discounted and they need to pick a new one.
- **`next_best`** — Automatically switches to the best available discounted model.
- **`keep_current`** — Continues using the model even though it's no longer discounted.

## Telemetry

The router emits OpenTelemetry metrics and traces. Set `OTEL_ENDPOINT` to your collector (Langfuse, Honeycomb, Datadog, etc.) to enable OTLP export. Without it, metrics and traces are printed to console.

### Metrics

| Name | Type | Description |
|------|------|-------------|
| `router.request.duration` | Histogram (ms) | Request latency |
| `router.model.selections` | Counter | Model selections, labeled by model ID |
| `router.rate_limits` | Counter | Rate limit (429) events, labeled by model |
| `router.inventory.refreshes` | Counter | Inventory refresh attempts |
| `router.inventory.errors` | Counter | Inventory refresh failures |
| `router.handoffs` | Counter | Model handoff events, labeled by reason |
| `router.errors.total` | Counter | Total errors, labeled by type |

### Traces

- `chat_completions` — Main request flow, labeled with selected model
- `inventory_refresh` — Inventory refresh job

## Architecture

```
Client → /v1/chat/completions → classify_request() → select discounted model
                                              ↓
                                    model_in_inventory()?
                                      ↙          ↘
                                   yes            no
                                    ↓              ↓
                              forward()      handle_model_handoff()
                                    ↓              ↓
                              OpenRouter     next_best / stop / keep
```

- **Inventory**: Fetches discounted models from OpenRouter `/models` and `/models/{id}/endpoints` endpoints concurrently (semaphore 10). Cached to `data/models.json`. Refreshed on cron schedule.
- **Router**: Uses a classification model (`ROUTER_MODEL`) to understand the request intent, then selects the best discounted model.
- **Rate Limiter**: Reactive — tracks 429 responses and backs off per-model.
- **Forwarding**: Proxies to OpenRouter with streaming support and retry logic.

## Development

```bash
uv sync
uv run pytest -v
uv run pyright src/
```
