"""Built-in detector: regular expressions, plus checksums where the format has one.

Deliberately simple: no dependencies and fast, but it only finds names in obvious patterns
("my name is …", "Dr. …", "Dear …"). The Presidio detector finds names with an NLP model.

Every pattern is written to run in linear time: request bodies can be up to a megabyte, and
a pattern that backtracks badly would let one request stall the gateway.
"""

import re
from collections.abc import Callable

from gateway.privacy import Span

# Lengths are capped (64 for the local part, 63 per domain label, as email standards do),
# and the lookbehind lets a match start only at the beginning of an address-like run.
EMAIL = re.compile(
    r"(?<![\w.%+-])[\w.%+-]{1,64}@[\w-]{1,63}(?:\.[\w-]{1,63})*\.[A-Za-z]{2,24}(?![\w-])"
)

# Two letters, two check digits, then groups of four; checked with mod-97 afterwards.
IBAN = re.compile(r"\b[A-Z]{2}[0-9]{2}(?: ?[A-Z0-9]{4}){2,7}(?: ?[A-Z0-9]{1,3})?\b")

# 13-19 digits, optionally grouped by spaces or hyphens, starting like a card issuer
# (2-6 covers Mastercard, Visa, Amex, Discover, JCB, UnionPay); checked with Luhn afterwards.
CARD = re.compile(r"(?<![0-9])[2-6](?:[ -]?[0-9]){12,18}(?![0-9])")

# International (+31 6 12345678, 0031...), national with a trunk 0 (020-123 4567,
# 0612345678, +31 (0)20 ...) and North American ((555) 123-4567, 555-123-4567) formats.
# Groups are separated by a single space or hyphen, so two numbers in a row stay apart.
PHONE = re.compile(
    r"(?<![\w+])(?:"
    r"(?:\+|0)[0-9]+(?:[ -]?\(0\)[0-9]*)?(?:[ -][0-9]{1,8})*"
    r"|\([0-9]{3}\)[ -]?[0-9]{3}[ -][0-9]{4}"
    r"|[0-9]{3}-[0-9]{3}-[0-9]{4}"
    r")(?!\w)"
)

# A capitalised word, allowing hyphens and apostrophes inside (Anne-Marie, O'Brien).
_NAME_WORD = r"[A-ZÀ-Þ][^\W\d_]*(?:['’-][^\W\d_]+)*"
# The cue word stays in the text; only the name itself (group "value") is replaced.
PERSON = re.compile(
    r"\b(?:(?:Mr|Mrs|Ms|Miss|Dr|Prof)\.?|(?i:my name is|name:)|Dear) +"
    rf"(?P<value>{_NAME_WORD}(?: {_NAME_WORD}){{0,2}})"
)


def valid_iban(candidate: str) -> bool:
    iban = candidate.replace(" ", "")
    if not 15 <= len(iban) <= 34:
        return False
    # Move the first four characters to the end and read letters as numbers (A=10 … Z=35):
    # a real IBAN then leaves remainder 1 when divided by 97.
    rearranged = iban[4:] + iban[:4]
    return int("".join(str(int(char, 36)) for char in rearranged)) % 97 == 1


def valid_card(candidate: str) -> bool:
    digits = [int(char) for char in candidate if char.isdigit()]
    # Luhn: double every second digit from the right (subtracting 9 when that exceeds 9),
    # and the total must be a multiple of 10. Catches typos and most random digit runs.
    total = 0
    for position, digit in enumerate(reversed(digits)):
        if position % 2 == 1:
            digit = digit * 2 - 9 if digit > 4 else digit * 2
        total += digit
    return total % 10 == 0


def valid_phone(candidate: str) -> bool:
    # At most 15 digits (the international maximum). Fewer than 9 is more likely a date,
    # an amount or a code than a phone number.
    return 9 <= sum(char.isdigit() for char in candidate) <= 15


def any_match(candidate: str) -> bool:
    return True


RULES: list[tuple[str, re.Pattern[str], Callable[[str], bool]]] = [
    ("EMAIL", EMAIL, any_match),
    ("IBAN", IBAN, valid_iban),
    ("CARD", CARD, valid_card),
    ("PHONE", PHONE, valid_phone),
    ("PERSON", PERSON, any_match),
]


class RegexDetector:
    def find(self, text: str) -> list[Span]:
        spans: list[Span] = []
        for kind, pattern, is_valid in RULES:
            for match in pattern.finditer(text):
                group = "value" if "value" in pattern.groupindex else 0
                if is_valid(match.group(group)):
                    spans.append(Span(match.start(group), match.end(group), kind))
        return spans
