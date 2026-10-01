# secure-ai-gateway

A secure, cost-aware AI gateway that sits between applications and LLM providers. It routes
each request to the cheapest model that can handle it, removes personal data before anything
leaves the system, and gives AI agents controlled access to tools over MCP.

Applications talk to it exactly like the OpenAI API, so existing SDKs work by changing the
base URL.

> **Status: phase 1 of 8.** Working today: the OpenAI-compatible API, mock and Ollama
> providers with fallback, cost-aware routing, and a per-request usage ledger in PostgreSQL.
> Coming next: personal-data redaction, scoped API keys with rate limits and budgets, MCP,
> observability, OpenAI and Anthropic adapters, and Azure deployment. See [Roadmap](#roadmap).

## Architecture

```mermaid
flowchart LR
    app["Client app<br/>(OpenAI SDK, curl)"] -->|"POST /v1/chat/completions"| api["FastAPI routes"]
    api --> router["Router<br/>complexity score → tier<br/>→ cheapest suitable model"]
    router -.reads.-> catalog[("config/models.toml<br/>price table")]
    api --> runner["Completion runner<br/>tries candidates in order"]
    runner --> mock["Mock adapter"]
    runner --> ollama["Ollama adapter"] --> ollamasrv["Ollama server"]
    api --> ledger[("PostgreSQL<br/>usage_records")]
```

More detail in [docs/architecture.md](docs/architecture.md).

## Quickstart

Requires Docker with Compose.

```bash
docker compose up --build
```

The gateway listens on `http://localhost:8000`, with interactive API docs at
`http://localhost:8000/docs`. Migrations run automatically on start. Out of the box it serves
the offline `mock-*` models, so no API key or network is needed.

To also route to models running in [Ollama](https://ollama.com) on your machine, uncomment
`OLLAMA_BASE_URL` in `docker-compose.yml` and pull the models listed in `config/models.toml`:

```bash
ollama pull gemma4:e4b && ollama pull gemma4:12b
```

## API examples

**Let the gateway choose** (`"model": "auto"`, also the default). A simple question goes to
the cheapest tier:

```bash
curl -s localhost:8000/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{"model": "auto", "messages": [{"role": "user", "content": "What is the capital of France?"}]}'
```

```json
{
  "id": "chatcmpl-5b499966a48a460b9fed9284eb4dfed9",
  "object": "chat.completion",
  "created": 1790853613,
  "model": "mock-small",
  "choices": [
    {
      "index": 0,
      "message": {"role": "assistant", "content": "[mock-small] You said: What is the capital of France?"},
      "finish_reason": "stop"
    }
  ],
  "usage": {"prompt_tokens": 7, "completion_tokens": 13, "total_tokens": 20}
}
```

An analysis request with code scores higher and goes to `mock-medium`:

```bash
curl -s localhost:8000/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{"messages": [{"role": "user", "content": "Analyze the trade-offs of this cache design:\n```python\nclass LRUCache:\n    pass\n```"}]}'
```

**Pick a model yourself** (routing is skipped):

```bash
curl -s localhost:8000/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{"model": "mock-large", "messages": [{"role": "user", "content": "Hello"}]}'
```

**JSON mode** (only models with the `json` capability are considered):

```bash
curl -s localhost:8000/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{"response_format": {"type": "json_object"}, "messages": [{"role": "user", "content": "List three colours"}]}'
```

**List models, check health, see spend:**

```bash
curl -s localhost:8000/v1/models
curl -s localhost:8000/health
curl -s localhost:8000/v1/usage
```

```json
{
  "requests": 2,
  "prompt_tokens": 27,
  "completion_tokens": 39,
  "cost_usd": "0.00013285",
  "by_model": [
    {"model": "mock-medium", "requests": 1, "prompt_tokens": 20, "completion_tokens": 26, "cost_usd": "0.00012400"},
    {"model": "mock-small", "requests": 1, "prompt_tokens": 7, "completion_tokens": 13, "cost_usd": "0.00000885"}
  ]
}
```

**Using the OpenAI Python SDK:**

```python
from openai import OpenAI

client = OpenAI(base_url="http://localhost:8000/v1", api_key="unused-until-phase-3")
reply = client.chat.completions.create(model="auto", messages=[{"role": "user", "content": "Hi!"}])
print(reply.model, reply.choices[0].message.content)
```

**Errors** always have the same shape, matching OpenAI's:

```json
{"error": {"message": "Model 'gpt-9' does not exist", "type": "invalid_request_error", "code": "model_not_found"}}
```

| Status | When |
|---|---|
| 400 | Invalid body, `stream: true` (not supported yet), or no model can serve the request |
| 404 | Unknown model or route |
| 413 | Body larger than `MAX_REQUEST_BYTES` |
| 502 | Every candidate provider failed |

## How routing works

The router scores each request using simple rules (in `src/gateway/router.py`):

| Signal | Score |
|---|---|
| Prompt longer than ~1,000 tokens | +1 |
| Prompt longer than ~4,000 tokens | +1 more |
| Code in the latest user message | +1 |
| Demanding task words (analyze, debug, step by step, trade-offs, …) | +2 |
| Light task words (translate, summarize, classify, …) | −1 |

A score of 0–1 needs tier 1, 2–3 needs tier 2, and 4 or more needs tier 3. Then:

1. **Hard filters:** the model must support every required capability (for example `json`
   for `response_format: json_object`) and its context window must fit the prompt plus the
   reply.
2. **Ranking:** models at or above the required tier come first, cheapest first by estimated
   cost for this request. Weaker models follow, strongest first, as a last resort.
3. **Fallback:** candidates are tried in that order. If a provider times out or errors, its
   other models are skipped and the next provider is tried.

Each completed request is written to `usage_records` with model, provider, tokens, cost and
latency. Cost is stored as `NUMERIC`, never as a float.

## Configuration

Settings come from environment variables (or a local `.env`; see `.env.example`).

| Variable | Default | Purpose |
|---|---|---|
| `DATABASE_URL` | *(required)* | PostgreSQL URL, e.g. `postgresql+asyncpg://user:pass@host:5432/db` |
| `MOCK_PROVIDER_ENABLED` | `true` | Serve the offline `mock-*` models |
| `OLLAMA_BASE_URL` | *(empty: off)* | Ollama server, e.g. `http://localhost:11434` |
| `PROVIDER_TIMEOUT_SECONDS` | `30` | Time allowed per provider call before falling back |
| `MAX_REQUEST_BYTES` | `1000000` | Larger request bodies are rejected with 413 |
| `MODELS_FILE` | `config/models.toml` | Model catalog and price table |

Models, tiers, prices (USD per 1M tokens), context windows and capabilities live in
[`config/models.toml`](config/models.toml). A model is only offered when its provider is
switched on.

## Development

Requires [uv](https://docs.astral.sh/uv/) and Docker.

```bash
uv sync                      # install dependencies (Python 3.12 is fetched if needed)
docker compose up -d db      # PostgreSQL for the app and the test suite
cp .env.example .env
uv run alembic upgrade head
uv run uvicorn --factory gateway.main:create_app --reload

uv run pytest                # tests + coverage (uses the gateway_test database)
uv run ruff check . && uv run ruff format --check .
uv run mypy                  # strict mode
```

Tests mock every external provider, so they never need an API key or network access.
CI (GitHub Actions) runs lint, type checks, tests against a PostgreSQL service container, and
a Docker build on every push and pull request.

## Design decisions

- **OpenAI-compatible API**: existing clients work unchanged. [ADR 0001](docs/decisions/0001-openai-compatible-api.md)
- **PostgreSQL for the usage ledger**: transactions, exact `NUMERIC` money, SQL aggregation. [ADR 0002](docs/decisions/0002-postgresql-for-the-usage-ledger.md)
- **Rules-based routing first**: explainable, testable, free to run. [ADR 0003](docs/decisions/0003-rules-based-routing-first.md)
- **Hand-written provider adapters**, no LiteLLM-style wrapper: full control over timeouts,
  errors and what gets logged. [ADR 0004](docs/decisions/0004-hand-written-provider-adapters.md)

## Roadmap

1. ~~Skeleton, REST API, mock and Ollama adapters, router, cost tracking, PostgreSQL, Compose, CI~~
2. Personal data removal and restore
3. Scoped API keys, rate limiting, audit log, budgets
4. MCP server, and MCP client with a tool allowlist
5. Structured logs, Prometheus metrics, Grafana dashboard
6. Full CI/CD and security scanning
7. Evaluation: routed vs always-premium cost and quality
8. Azure deployment with Terraform and OIDC
