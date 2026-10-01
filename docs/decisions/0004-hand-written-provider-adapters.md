# 0004: Hand-written provider adapters

**Status:** accepted

## Context

The gateway talks to several LLM backends (mock, Ollama; OpenAI and Anthropic later).
Libraries such as LiteLLM already wrap dozens of providers behind one call.

## Decision

Define a small `Provider` protocol (`complete(model, request) -> Completion`) and write each
adapter directly with httpx. Each adapter translates the OpenAI-style request into the
provider's native format, and turns every failure (timeout, refused connection, HTTP error,
malformed body) into a single `ProviderError` that triggers fallback.

## Alternatives considered

- **LiteLLM or similar:** broad coverage, but a large dependency between the gateway and
  every provider. A security gateway must know exactly what is sent, what is logged, how
  errors are classified and how long each call may take. Those are the parts a wrapper hides.

## Consequences

- Each adapter is about 50 lines, readable end to end, and tested against `httpx.MockTransport`
  with no network.
- Adding a provider means writing and testing one adapter. That is acceptable for a handful of
  providers; it would not scale to dozens, and the gateway doesn't need dozens.
- Error messages carry only the exception type, never upstream bodies, which can echo
  prompt content.
