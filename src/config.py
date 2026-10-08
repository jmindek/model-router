from pydantic import Field
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # System One model
    model: str = Field(default="upstage/solar-decide", validation_alias="ROUTER_MODEL")
    api_base: str = Field(
        default="https://openrouter.ai/api/v1", validation_alias="ROUTER_API_BASE"
    )
    api_key: str = Field(default="", validation_alias="ROUTER_API_KEY")

    # OpenRouter
    openrouter_api_key: str = Field(default="", validation_alias="OPENROUTER_API_KEY")
    inventory_url: str = Field(
        default="https://openrouter.ai/api/v1", validation_alias="ROUTER_INVENTORY_URL"
    )

    # Routing mode: cloud, local, auto
    mode: str = Field(default="auto", validation_alias="ROUTER_MODE")

    # Local oMLX fallback
    omlx_base: str = Field(
        default="http://127.0.0.1:8000", validation_alias="ROUTER_OMLX_BASE"
    )

    # Discount threshold (0.0-1.0)
    min_discount: float = Field(default=0.50, validation_alias="ROUTER_MIN_DISCOUNT")

    # Fallback
    fallback_mode: str = Field(
        default="static", validation_alias="ROUTER_FALLBACK_MODE"
    )
    static_fallback: str = Field(
        default="openrouter/deepseek/deepseek-v3.1",
        validation_alias="ROUTER_STATIC_FALLBACK",
    )

    # Model handoff when selected model falls off discount list
    model_handoff: str = Field(
        default="next_best", validation_alias="ROUTER_MODEL_HANDOFF"
    )

    # Cache
    cache_file: str = Field(
        default="data/models.json", validation_alias="ROUTER_CACHE_FILE"
    )
    refresh_cron: str = Field(
        default="0 2 * * *", validation_alias="ROUTER_REFRESH_CRON"
    )

    # OpenTelemetry
    otel_endpoint: str = Field(
        default="", validation_alias="OTEL_ENDPOINT"
    )

    model_config = {"env_file": ".env", "extra": "ignore"}


settings = Settings()
