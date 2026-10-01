"""Optional detector backed by Microsoft Presidio, which finds names with an NLP model.

Needs the presidio extra: `uv sync --extra presidio` (it includes a ~400 MB spaCy model).
Slower than the regex detector but much better at names, which have no fixed format.
"""

from collections.abc import Sequence
from typing import Protocol

from gateway.privacy import Span

# Presidio entity types and the placeholder kind each becomes. Other types are not requested.
KINDS = {
    "EMAIL_ADDRESS": "EMAIL",
    "PHONE_NUMBER": "PHONE",
    "IBAN_CODE": "IBAN",
    "CREDIT_CARD": "CARD",
    "PERSON": "PERSON",
}


class Finding(Protocol):
    @property
    def entity_type(self) -> str: ...
    @property
    def start(self) -> int: ...
    @property
    def end(self) -> int: ...


class Analyzer(Protocol):
    """The part of presidio_analyzer.AnalyzerEngine this detector uses."""

    def analyze(self, text: str, language: str, entities: list[str]) -> Sequence[Finding]: ...


class PresidioDetector:
    def __init__(self, analyzer: Analyzer | None = None, language: str = "en") -> None:
        # Tests pass a fake analyzer; the real one loads the NLP model, so it is only
        # imported when this detector is actually chosen.
        if analyzer is None:
            try:
                from presidio_analyzer import AnalyzerEngine
            except ImportError as exc:
                raise RuntimeError(
                    "PII_DETECTOR=presidio needs the presidio extra: uv sync --extra presidio"
                ) from exc
            analyzer = AnalyzerEngine()
        self._analyzer = analyzer
        self._language = language

    def find(self, text: str) -> list[Span]:
        findings = self._analyzer.analyze(text=text, language=self._language, entities=list(KINDS))
        return [Span(f.start, f.end, KINDS[f.entity_type]) for f in findings]
