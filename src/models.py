from pydantic import BaseModel


class ChatMessage(BaseModel):
    role: str
    content: str | list


class ChatCompletionRequest(BaseModel):
    model: str = "router"
    messages: list[ChatMessage]
    temperature: float | None = None
    max_tokens: int | None = None
    stream: bool = False


class ModelInfo(BaseModel):
    id: str
    discount_pct: float
    provider: str
    input_price: float
    output_price: float
    context_length: int
