import time

import pytest

from gateway.privacy import Redactor, Span, without_overlaps
from gateway.privacy.regex import RegexDetector, valid_card, valid_iban
from gateway.schemas import ChatCompletionRequest

DETECTOR = RegexDetector()


def found(text: str) -> list[tuple[str, str]]:
    """(kind, value) for each detection, in text order, after overlap removal."""
    spans = without_overlaps(DETECTOR.find(text))
    return [(span.kind, text[span.start : span.end]) for span in spans]


@pytest.mark.parametrize(
    ("text", "kind", "value"),
    [
        ("Mail jane.doe+news@mail.example.nl now", "EMAIL", "jane.doe+news@mail.example.nl"),
        ("Write to b@mail.example.co.uk.", "EMAIL", "b@mail.example.co.uk"),
        ("IBAN NL91 ABNA 0417 1643 00, thanks", "IBAN", "NL91 ABNA 0417 1643 00"),
        ("IBAN: GB29NWBK60161331926819.", "IBAN", "GB29NWBK60161331926819"),
        ("Card 4111 1111 1111 1111 expires soon", "CARD", "4111 1111 1111 1111"),
        ("Amex 3782-822463-10005", "CARD", "3782-822463-10005"),
        ("Call +31 6 12345678 after five", "PHONE", "+31 6 12345678"),
        ("Call +31 (0)20 123 4567", "PHONE", "+31 (0)20 123 4567"),
        ("Bel 06-12345678 of 0612345678", "PHONE", "06-12345678"),
        ("Office: 020 123 4567", "PHONE", "020 123 4567"),
        ("US office (555) 123-4567", "PHONE", "(555) 123-4567"),
        ("Hi, my name is Jane van Dijk", "PERSON", "Jane"),
        ("My name is Anne-Marie O'Brien.", "PERSON", "Anne-Marie O'Brien"),
        ("Ask Dr. Émile Durand about it", "PERSON", "Émile Durand"),
        ("Dear Sam,", "PERSON", "Sam"),
    ],
)
def test_finds_personal_data(text: str, kind: str, value: str) -> None:
    assert (kind, value) in found(text)


@pytest.mark.parametrize(
    "text",
    [
        "Meeting on 2026-10-01 at 09:30, budget 1 250 000 EUR",
        "Order 123456789 shipped",  # long number, but no phone formatting
        "Version 0.12.3, hex 0x1234abcd, ratio 0.123456789",
        "Card 4111 1111 1111 1112",  # Luhn fails
        "Write to support at example dot com",
        "the name of the game",  # cue words without a capitalised name
    ],
)
def test_ignores_lookalikes(text: str) -> None:
    assert found(text) == []


def test_iban_with_a_bad_checksum_is_not_an_iban() -> None:
    # Its digits may still be caught as a phone number, which errs on the safe side.
    assert "IBAN" not in [kind for kind, _ in found("IBAN NL91 ABNA 0417 1643 01")]


def test_two_phone_numbers_in_a_row_stay_separate() -> None:
    assert found("0612345678 0687654321") == [
        ("PHONE", "0612345678"),
        ("PHONE", "0687654321"),
    ]


def test_card_inside_an_iban_is_reported_once_as_iban() -> None:
    assert found("DE89 3704 0044 0532 0130 00") == [("IBAN", "DE89 3704 0044 0532 0130 00")]


def test_checksums() -> None:
    assert valid_iban("BE68 5390 0754 7034")
    assert not valid_iban("BE68 5390 0754 7035")
    assert not valid_iban("NL91 ABNA 0417")  # shorter than any country's IBAN
    assert valid_card("5555 5555 5555 4444")
    assert not valid_card("5555 5555 5555 4445")


HOSTILE = {
    "dotted-words": "a." * 100_000,
    "email-endless-domain": "a@" + "b." * 100_000,
    "phone-endless-digits": "0" + "1" * 200_000 + "x",
    "phone-short-groups": "0 1" * 70_000,
    "phone-endless-groups": "+3" + " 12345678" * 25_000,
    "card-endless-digits": "4" + " 1" * 100_000,
    "iban-endless-groups": "NL91" + " ABCD" * 50_000,
    "person-endless-name": "Dear " + "Abc-" * 50_000,
}


@pytest.mark.parametrize("hostile", HOSTILE.values(), ids=HOSTILE.keys())
def test_detection_stays_fast_on_hostile_input(hostile: str) -> None:
    # A pattern that backtracks quadratically would take minutes on these; linear ones
    # take milliseconds. The bound is loose so slow CI machines don't make it flaky.
    started = time.perf_counter()
    DETECTOR.find(hostile)
    assert time.perf_counter() - started < 2


class FixedDetector:
    """Finds the given words, so redaction tests don't depend on regex details."""

    def __init__(self, **words: str) -> None:
        self._words = words

    def find(self, text: str) -> list[Span]:
        spans = []
        for word, kind in self._words.items():
            start = text.find(word)
            while start != -1:
                spans.append(Span(start, start + len(word), kind))
                start = text.find(word, start + 1)
        return spans


def test_redact_then_restore_round_trips() -> None:
    redactor = Redactor(RegexDetector())
    text = "Mail jane@example.com or call +31 6 12345678."
    redacted = redactor.redact(text)
    assert redacted == "Mail <EMAIL_1> or call <PHONE_1>."
    assert redactor.restore("I will mail <EMAIL_1> today.") == (
        "I will mail jane@example.com today."
    )
    assert redactor.count == 2


def test_same_value_gets_the_same_placeholder_across_messages() -> None:
    request = ChatCompletionRequest.model_validate(
        {
            "messages": [
                {"role": "user", "content": "Jane and Bob met."},
                {"role": "assistant", "content": "What did Jane say?"},
            ]
        }
    )
    redactor = Redactor(FixedDetector(Jane="PERSON", Bob="PERSON"))
    redacted = redactor.redact_request(request)
    assert [m.content for m in redacted.messages] == [
        "<PERSON_1> and <PERSON_2> met.",
        "What did <PERSON_1> say?",
    ]
    # The original request object is untouched.
    assert request.messages[0].content == "Jane and Bob met."


def test_placeholders_the_client_wrote_are_never_reused() -> None:
    request = ChatCompletionRequest.model_validate(
        {"messages": [{"role": "user", "content": "What does <PERSON_1> mean? Ask Jane."}]}
    )
    redactor = Redactor(FixedDetector(Jane="PERSON"))
    redacted = redactor.redact_request(request)
    assert redacted.messages[0].content == "What does <PERSON_1> mean? Ask <PERSON_2>."
    # The client's literal <PERSON_1> comes back as written, not as someone's name.
    assert redactor.restore("<PERSON_1> is a placeholder; <PERSON_2> knows.") == (
        "<PERSON_1> is a placeholder; Jane knows."
    )


def test_unknown_or_mangled_placeholders_are_left_alone() -> None:
    redactor = Redactor(FixedDetector(Jane="PERSON"))
    redactor.redact("Jane")
    assert redactor.restore("<PERSON_9> and <person_1>") == "<PERSON_9> and <person_1>"


def test_overlapping_spans_keep_the_earliest_then_longest() -> None:
    spans = [Span(5, 9, "B"), Span(0, 6, "A"), Span(0, 3, "C"), Span(9, 12, "D")]
    assert without_overlaps(spans) == [Span(0, 6, "A"), Span(9, 12, "D")]
