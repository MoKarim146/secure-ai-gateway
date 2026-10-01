from decimal import Decimal
from pathlib import Path
from typing import Any

from conftest import make_settings
from fastapi.testclient import TestClient

from gateway.main import create_app
from gateway.schemas import ModelUsage


def chat(client: TestClient, content: str, **extra: Any) -> Any:
    return client.post(
        "/v1/chat/completions", json={"messages": [{"role": "user", "content": content}], **extra}
    )


def test_health(client: TestClient) -> None:
    assert client.get("/health").json() == {"status": "ok"}


def test_models_lists_auto_and_enabled_models_only(client: TestClient) -> None:
    ids = [m["id"] for m in client.get("/v1/models").json()["data"]]
    # Ollama is switched off in tests, so its catalog entries must not appear.
    assert ids == ["auto", "mock-small", "mock-medium", "mock-large"]


def test_cloud_providers_switch_on_with_their_api_key(
    migrated_database: str, tmp_path: Path
) -> None:
    models_file = tmp_path / "models.toml"
    models_file.write_text(
        "".join(
            f'[[models]]\nid = "{provider}-model"\nprovider = "{provider}"\ntier = 1\n'
            "input_price = 1\noutput_price = 1\ncontext_window = 1000\n"
            for provider in ("mock", "openai", "anthropic")
        )
    )
    settings = make_settings(
        migrated_database,
        models_file=models_file,
        mock_provider_enabled=False,
        openai_api_key="sk-test",
        anthropic_api_key="sk-ant-test",
    )
    with TestClient(create_app(settings)) as client:
        ids = [m["id"] for m in client.get("/v1/models").json()["data"]]
    assert ids == ["auto", "openai-model", "anthropic-model"]


def test_chat_completion_has_openai_shape(client: TestClient) -> None:
    response = chat(client, "Hi!")
    assert response.status_code == 200
    body = response.json()
    assert body["object"] == "chat.completion"
    assert body["id"].startswith("chatcmpl-")
    assert body["choices"][0]["message"]["role"] == "assistant"
    assert body["usage"]["total_tokens"] == (
        body["usage"]["prompt_tokens"] + body["usage"]["completion_tokens"]
    )


def test_routing_picks_model_by_complexity(client: TestClient) -> None:
    assert chat(client, "Hi!").json()["model"] == "mock-small"
    hard = "Analyze the trade-offs of this design:\n```\nclass Cache:\n    pass\n```"
    assert chat(client, hard).json()["model"] == "mock-medium"
    assert chat(client, "Hi!", model="mock-large").json()["model"] == "mock-large"


def test_usage_records_exact_cost(client: TestClient) -> None:
    # Mock counts: "Hi!" is 1 prompt token; "[mock-small] You said: Hi!" is 6 completion
    # tokens. mock-small costs $0.15 / $0.60 per 1M tokens: (1 * 0.15 + 6 * 0.60) / 1M.
    chat(client, "Hi!")
    chat(client, "Hi!")
    usage = client.get("/v1/usage").json()
    assert usage["requests"] == 2
    assert usage["by_model"] == [
        {
            "model": "mock-small",
            "requests": 2,
            "prompt_tokens": 2,
            "completion_tokens": 12,
            "cost_usd": "0.00000750",
        }
    ]
    assert usage["cost_usd"] == "0.00000750"


def test_personal_data_is_redacted_and_restored(client: TestClient) -> None:
    response = chat(client, "Hi, I am reachable at jane@example.com")
    # The mock echoes what it received; the gateway puts the address back afterwards.
    assert response.json()["choices"][0]["message"]["content"] == (
        "[mock-small] You said: Hi, I am reachable at jane@example.com"
    )
    assert response.headers["X-PII-Redactions"] == "1"
    assert chat(client, "Hi!").headers["X-PII-Redactions"] == "0"


def test_money_is_serialised_without_exponent() -> None:
    # PostgreSQL returns a NUMERIC(12, 8) zero as Decimal("0E-8").
    usage = ModelUsage(
        model="m", requests=1, prompt_tokens=1, completion_tokens=1, cost_usd=Decimal("0E-8")
    )
    assert usage.model_dump(mode="json")["cost_usd"] == "0.00000000"


def test_unknown_model_returns_404_in_error_format(client: TestClient) -> None:
    response = chat(client, "Hi!", model="gpt-nonexistent")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "model_not_found"


def test_validation_errors_do_not_echo_input(client: TestClient) -> None:
    response = client.post(
        "/v1/chat/completions",
        json={"messages": [{"role": "robot", "content": "my email is jane@example.com"}]},
    )
    assert response.status_code == 400
    error = response.json()["error"]
    assert error["type"] == "invalid_request_error"
    assert "messages.0.role" in error["message"]
    assert "jane@example.com" not in response.text


def test_oversized_body_is_rejected(client: TestClient) -> None:
    response = chat(client, "x" * 20_000)  # conftest caps bodies at 10 KB
    assert response.status_code == 413
    assert response.json()["error"]["type"] == "invalid_request_error"


def test_oversized_chunked_body_is_rejected(client: TestClient) -> None:
    # A generator body is sent chunked, without Content-Length, so only counting works.
    chunks = (b"x" * 6_000 for _ in range(2))
    response = client.post("/v1/chat/completions", content=chunks)
    assert response.status_code == 413


def test_streaming_is_rejected_clearly(client: TestClient) -> None:
    response = chat(client, "Hi!", stream=True)
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "stream_not_supported"


def test_unknown_route_uses_error_format(client: TestClient) -> None:
    response = client.get("/v1/nope")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"
