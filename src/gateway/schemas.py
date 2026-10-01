"""Request and response bodies. Chat shapes follow the OpenAI API so existing clients work as-is."""

from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, Field, PlainSerializer

# A JSON string in plain notation: exact, never a rounded float, and never "0E-8", which
# is how Python prints a zero read from a NUMERIC column.
Money = Annotated[Decimal, PlainSerializer(lambda value: format(value, "f"), return_type=str)]


class ChatMessage(BaseModel):
    role: Literal["system", "user", "assistant"]
    # Text content only for now; OpenAI's list-of-parts form (images) comes with vision support.
    content: str


class ResponseFormat(BaseModel):
    type: Literal["text", "json_object"] = "text"


class ChatCompletionRequest(BaseModel):
    # "auto" lets the gateway choose; any other value must be a model id from /v1/models.
    model: str = "auto"
    messages: list[ChatMessage] = Field(min_length=1)
    max_tokens: int | None = Field(default=None, gt=0)
    temperature: float | None = Field(default=None, ge=0, le=2)
    response_format: ResponseFormat | None = None
    stream: bool = False

    @property
    def wants_json(self) -> bool:
        return self.response_format is not None and self.response_format.type == "json_object"


class Choice(BaseModel):
    index: int = 0
    message: ChatMessage
    finish_reason: str


class TokenUsage(BaseModel):
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int


class ChatCompletionResponse(BaseModel):
    id: str
    object: Literal["chat.completion"] = "chat.completion"
    created: int
    model: str
    choices: list[Choice]
    usage: TokenUsage


class ModelCard(BaseModel):
    id: str
    object: Literal["model"] = "model"
    created: int = 0
    owned_by: str


class ModelList(BaseModel):
    object: Literal["list"] = "list"
    data: list[ModelCard]


class ModelUsage(BaseModel):
    model: str
    requests: int
    prompt_tokens: int
    completion_tokens: int
    cost_usd: Money


class UsageSummary(BaseModel):
    requests: int
    prompt_tokens: int
    completion_tokens: int
    cost_usd: Money
    by_model: list[ModelUsage]
