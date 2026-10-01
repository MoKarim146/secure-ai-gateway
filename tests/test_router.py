from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from gateway.config import ModelInfo, load_catalog
from gateway.router import required_tier, score_complexity, select_candidates
from gateway.schemas import ChatCompletionRequest


def model(id: str, tier: int, price: str, **extra: Any) -> ModelInfo:
    return ModelInfo.model_validate(
        {
            "id": id,
            "provider": "mock",
            "tier": tier,
            "input_price": price,
            "output_price": price,
            "context_window": 100_000,
            **extra,
        }
    )


CATALOG = [
    model("large", 3, "10"),
    model("small", 1, "0.1"),
    model("medium", 2, "1", capabilities=["json"]),
]


def ask(content: str, **extra: Any) -> ChatCompletionRequest:
    return ChatCompletionRequest.model_validate(
        {"messages": [{"role": "user", "content": content}], **extra}
    )


def ids(models: list[ModelInfo]) -> list[str]:
    return [m.id for m in models]


@pytest.mark.parametrize(
    ("content", "tier"),
    [
        ("What is the capital of France?", 1),
        ("Translate 'good morning' into Dutch.", 1),
        ("Analyze the trade-offs between these two caching strategies.", 2),
        ("Debug this:\n```python\ndef f(x):\n    return x[0]\n```", 2),
        ("Refactor this step by step:\n```\n" + "x = compute(y);\n" * 400 + "```", 3),
    ],
)
def test_complexity_maps_to_tier(content: str, tier: int) -> None:
    assert required_tier(score_complexity(ask(content))) == tier


def test_hints_in_system_prompt_do_not_raise_the_score() -> None:
    request = ChatCompletionRequest.model_validate(
        {
            "messages": [
                {"role": "system", "content": "You analyze and compare architecture options."},
                {"role": "user", "content": "Hi, who are you?"},
            ]
        }
    )
    assert score_complexity(request) == 0


def test_simple_request_prefers_cheapest_then_falls_back_upwards() -> None:
    assert ids(select_candidates(ask("Hello!"), CATALOG)) == ["small", "medium", "large"]


def test_hard_request_skips_models_below_its_tier_then_degrades() -> None:
    hard = ask("Analyze this design step by step:\n```\nclass Cache:\n    pass\n```")
    assert ids(select_candidates(hard, CATALOG)) == ["medium", "large", "small"]


def test_required_capability_is_a_hard_filter() -> None:
    request = ask("Hello!", response_format={"type": "json_object"})
    assert ids(select_candidates(request, CATALOG)) == ["medium"]


def test_context_window_is_a_hard_filter() -> None:
    catalog = [model("tiny-context", 1, "0", context_window=1_000), model("big", 1, "1")]
    assert ids(select_candidates(ask("x" * 8_000), catalog)) == ["big"]


def test_explicit_model_bypasses_routing() -> None:
    assert ids(select_candidates(ask("Hello!", model="large"), CATALOG)) == ["large"]
    assert select_candidates(ask("Hello!", model="nope"), CATALOG) == []


def test_cost_uses_prices_per_million_tokens() -> None:
    m = model("m", 1, "0", input_price="2.50", output_price="10")
    assert m.cost(1_000, 500) == Decimal("0.0075")


def test_shipped_catalog_loads() -> None:
    catalog = load_catalog(Path("config/models.toml"))
    assert {"mock-small", "mock-medium", "mock-large"} <= set(ids(catalog))
