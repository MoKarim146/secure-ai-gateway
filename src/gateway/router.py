"""Rules-based routing: score how demanding a request is, then rank models cheapest-first."""

import re
from decimal import Decimal

from gateway.config import ModelInfo
from gateway.providers import estimate_tokens
from gateway.schemas import ChatCompletionRequest

# Assumed reply length when the client sets no max_tokens. Only used for estimates.
DEFAULT_COMPLETION_TOKENS = 512
LONG_PROMPT_TOKENS = 1_000
VERY_LONG_PROMPT_TOKENS = 4_000

# Deliberately simple: keyword and regex heuristics. Cheap, explainable and testable, but they miss
# hard requests phrased plainly. Upgrade path: a small classifier model scoring the prompt.
_CODE = re.compile(r"```|^\s*(?:def|class|import|function|const|public|#include)\s|[;{]\s*$", re.M)
_HARD_TASK = re.compile(
    r"\b(?:analy[sz]e|prove|step[- ]by[- ]step|architecture|debug|refactor|optimi[sz]e"
    r"|trade-?offs?|compare|explain why|design an?)\b",
    re.IGNORECASE,
)
_EASY_TASK = re.compile(
    r"\b(?:translate|summari[sz]e|classify|extract|rephrase|spell-?check|yes or no)\b",
    re.IGNORECASE,
)


def score_complexity(request: ChatCompletionRequest) -> int:
    """0 means trivial. Each signal of a harder request adds to the score."""
    # Length counts everything the model must read. Code and task hints only look at the
    # latest user message, so a fixed system prompt doesn't push every request up a tier.
    prompt_tokens = estimate_tokens(_prompt_text(request))
    ask = _last_user_message(request)
    score = 0
    if prompt_tokens > LONG_PROMPT_TOKENS:
        score += 1
    if prompt_tokens > VERY_LONG_PROMPT_TOKENS:
        score += 1
    if _CODE.search(ask):
        score += 1
    if _HARD_TASK.search(ask):
        score += 2
    if _EASY_TASK.search(ask):
        score -= 1
    return max(score, 0)


def required_tier(score: int) -> int:
    if score <= 1:
        return 1
    if score <= 3:
        return 2
    return 3


def required_capabilities(request: ChatCompletionRequest) -> frozenset[str]:
    return frozenset({"json"}) if request.wants_json else frozenset()


def select_candidates(request: ChatCompletionRequest, catalog: list[ModelInfo]) -> list[ModelInfo]:
    """Models to try, in order: the first is the pick, the rest are fallbacks."""
    if request.model != "auto":
        return [m for m in catalog if m.id == request.model]

    prompt_tokens = estimate_tokens(_prompt_text(request))
    completion_tokens = request.max_tokens or DEFAULT_COMPLETION_TOKENS
    needs = required_capabilities(request)
    tier = required_tier(score_complexity(request))

    def cost(m: ModelInfo) -> Decimal:
        return m.cost(prompt_tokens, completion_tokens)

    # Capabilities and context size are hard limits: a model without them cannot answer.
    able = [
        m
        for m in catalog
        if needs <= m.capabilities and m.context_window >= prompt_tokens + completion_tokens
    ]
    # Tier is a quality preference, not a hard limit. Strong-enough models go first, cheapest
    # first. Weaker ones follow, strongest first, so the gateway degrades instead of refusing
    # when nothing strong enough is configured or reachable.
    strong = sorted((m for m in able if m.tier >= tier), key=lambda m: (cost(m), m.tier))
    weaker = sorted((m for m in able if m.tier < tier), key=lambda m: (-m.tier, cost(m)))
    return strong + weaker


def _prompt_text(request: ChatCompletionRequest) -> str:
    return "\n".join(m.content for m in request.messages)


def _last_user_message(request: ChatCompletionRequest) -> str:
    return next((m.content for m in reversed(request.messages) if m.role == "user"), "")
