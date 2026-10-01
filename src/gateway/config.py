"""Runtime settings (environment variables) and the model catalog (a TOML price table)."""

import tomllib
from decimal import Decimal
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

ONE_MILLION = Decimal(1_000_000)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Required, with no default: it holds a password, and secrets only come from the environment.
    database_url: str
    models_file: Path = Path("config/models.toml")
    mock_provider_enabled: bool = True
    # Empty means Ollama is switched off.
    ollama_base_url: str = ""
    provider_timeout_seconds: float = 30.0
    max_request_bytes: int = 1_000_000


class ModelInfo(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    # A Literal turns a typo in the TOML file into a startup error instead of a silently
    # unreachable model.
    provider: Literal["mock", "ollama"]
    tier: int = Field(ge=1, le=3)  # 1 = small and cheap, 2 = mid-range, 3 = premium
    input_price: Decimal = Field(ge=0)  # USD per 1M input tokens
    output_price: Decimal = Field(ge=0)  # USD per 1M output tokens
    context_window: int = Field(gt=0)
    capabilities: frozenset[str] = frozenset()

    def cost(self, prompt_tokens: int, completion_tokens: int) -> Decimal:
        return (
            prompt_tokens * self.input_price + completion_tokens * self.output_price
        ) / ONE_MILLION


def load_catalog(path: Path) -> list[ModelInfo]:
    with path.open("rb") as f:
        return [ModelInfo.model_validate(entry) for entry in tomllib.load(f)["models"]]
