"""Personal data removal before provider calls, and restoration in the reply.

A Detector finds personal data in text. A Redactor swaps each value for a placeholder such as
<EMAIL_1> before the request leaves the gateway, and puts the real values back into the
provider's reply. The mapping lives in memory for one request only and is never logged.
"""

import re
from collections import Counter
from dataclasses import dataclass
from typing import Protocol

from gateway.schemas import ChatCompletionRequest

# Placeholders look like <EMAIL_1>. Kinds must be upper-case letters to match this pattern.
PLACEHOLDER = re.compile(r"<[A-Z]+_\d+>")


@dataclass(frozen=True)
class Span:
    """Personal data at text[start:end]. kind names the placeholder, e.g. "EMAIL"."""

    start: int
    end: int
    kind: str


class Detector(Protocol):
    def find(self, text: str) -> list[Span]: ...


@dataclass(frozen=True)
class PrivacyPolicy:
    # None switches redaction off.
    detector: Detector | None
    # Providers allowed to see personal data unredacted, e.g. a local Ollama server.
    trusted_providers: frozenset[str] = frozenset()


class Redactor:
    """Placeholders for one request. Never share one between requests: it holds the values."""

    def __init__(self, detector: Detector) -> None:
        self._detector = detector
        self._placeholders: dict[str, str] = {}  # value -> placeholder
        self._originals: dict[str, str] = {}  # placeholder -> value
        self._numbers: Counter[str] = Counter()  # last number used per kind
        self._taken: set[str] = set()

    @property
    def count(self) -> int:
        """How many distinct values were replaced."""
        return len(self._originals)

    def redact_request(self, request: ChatCompletionRequest) -> ChatCompletionRequest:
        # Placeholder-like text the client wrote itself is never reused, so restoring can't
        # turn it into someone's data.
        for message in request.messages:
            self._taken.update(PLACEHOLDER.findall(message.content))
        messages = [
            message.model_copy(update={"content": self.redact(message.content)})
            for message in request.messages
        ]
        return request.model_copy(update={"messages": messages})

    def redact(self, text: str) -> str:
        parts: list[str] = []
        position = 0
        for span in without_overlaps(self._detector.find(text)):
            parts.append(text[position : span.start])
            parts.append(self._placeholder_for(span.kind, text[span.start : span.end]))
            position = span.end
        parts.append(text[position:])
        return "".join(parts)

    def restore(self, text: str) -> str:
        # One pass over the reply, so a restored value is never itself scanned again.
        return PLACEHOLDER.sub(lambda m: self._originals.get(m.group(), m.group()), text)

    def _placeholder_for(self, kind: str, value: str) -> str:
        # The same value gets the same placeholder in every message, so the model can tell
        # that two mentions refer to the same person.
        if value not in self._placeholders:
            placeholder = self._new_placeholder(kind)
            self._placeholders[value] = placeholder
            self._originals[placeholder] = value
        return self._placeholders[value]

    def _new_placeholder(self, kind: str) -> str:
        while True:
            self._numbers[kind] += 1
            placeholder = f"<{kind}_{self._numbers[kind]}>"
            if placeholder not in self._taken:
                return placeholder


def without_overlaps(spans: list[Span]) -> list[Span]:
    """Keeps the earliest span (the longest, if several start together) and drops any span
    that overlaps one already kept. Detectors can report the same digits as, say, both a
    phone number and an IBAN."""
    kept: list[Span] = []
    for span in sorted(spans, key=lambda s: (s.start, -s.end)):
        if not kept or span.start >= kept[-1].end:
            kept.append(span)
    return kept
