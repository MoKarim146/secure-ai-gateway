# 0003: Rules-based routing first

**Status:** accepted

## Context

The gateway should send each request to the cheapest model that can handle it. Options range
from fixed rules to a learned classifier or asking an LLM to judge difficulty.

## Decision

Start with transparent rules: score prompt length, code in the latest user message, and
demanding or light task words; map the score to a tier (1–3); filter by hard requirements
(capabilities, context window); rank by estimated cost for this specific request.

Tier is treated as a preference, not a hard limit. If nothing strong enough is configured or
reachable, weaker models are used as a last resort, because a weaker answer beats an error
for most applications.

## Alternatives considered

- **Classifier model:** likely more accurate, but needs labelled data, adds latency and cost
  to every request, and is harder to explain or debug.
- **LLM-as-router:** pays for a model call to save on a model call; adds a failure point.

## Consequences

- Every decision can be explained from the prompt and a short rule table, and unit-tested
  with plain function calls.
- Keyword rules miss demanding requests phrased in plain words, and the ~4 characters per
  token estimate is rough. The evaluation in phase 7 measures how much this costs in quality,
  which tells us whether a classifier is worth it.
- Rules only look at the latest user message for hints, so a fixed system prompt doesn't push
  every request up a tier.
