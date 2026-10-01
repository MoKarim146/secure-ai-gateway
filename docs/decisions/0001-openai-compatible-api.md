# 0001: OpenAI-compatible API

**Status:** accepted

## Context

Applications already use the OpenAI SDKs and the many tools built on them. A gateway with its
own API would force every client to change code before getting any benefit.

## Decision

Expose `POST /v1/chat/completions` and `GET /v1/models` with OpenAI's request, response and
error shapes. Gateway-specific behaviour hides behind standard fields: `"model": "auto"` asks
the gateway to choose, and `response_format` doubles as a routing capability.

## Consequences

- Adopting the gateway means changing a base URL.
- Fields we don't support yet are ignored, except `stream: true`, which fails clearly with
  400 rather than silently returning a non-streamed reply.
- We follow OpenAI's schema as it evolves. Adapters translate to each provider's native
  format, so other providers' shapes never leak to clients.
