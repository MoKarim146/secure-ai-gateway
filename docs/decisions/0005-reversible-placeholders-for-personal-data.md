# 0005: Reversible placeholders for personal data

**Status:** accepted

## Context

Prompts often contain personal data: customer emails, phone numbers, IBANs, card numbers,
names. Sending them to an external LLM provider moves that data outside the organisation,
which privacy rules (GDPR) and customer contracts often forbid or restrict. The gateway sees
every request, so it is the natural place to stop that, but answers still have to make sense
to the user who asked.

## Decision

Before a provider call, replace each detected value with a typed placeholder such as
`<EMAIL_1>` or `<PERSON_2>`, and swap the original values back into the reply.

- One `Redactor` per request holds the mapping in memory. It is never logged or stored.
- The same value gets the same placeholder across all messages, so the model can still tell
  that two mentions are one person.
- Detection sits behind a `Detector` protocol (`find(text) -> list[Span]`). The built-in
  detector uses regular expressions with checksums (mod-97 for IBANs, Luhn for cards). The
  optional Presidio detector finds names with an NLP model.
- Redaction is on by default for every provider. `PII_TRUSTED_PROVIDERS` exempts providers
  that may see the original, such as a local Ollama server.

## Alternatives considered

- **Mask without restoring (`[REDACTED]`):** simpler, but the user gets placeholders back
  where their own data should be, and the model can't tell different values apart.
- **Block requests that contain personal data:** safest, but makes the gateway unusable for
  the many tasks that involve a name or an email.
- **Presidio only:** much better at names, but a ~400 MB model and noticeably more CPU per
  request. Kept optional so the default install stays small and fast.
- **Format-preserving fake values** (a realistic fake email instead of `<EMAIL_1>`): reads
  more naturally for the model, but a fake value can collide with real text and makes
  restoring ambiguous.
- **Rely on provider data agreements:** a contract doesn't stop data from leaving; it only
  governs what happens afterwards. Trusted providers cover the cases where that is enough.

## Consequences

- Providers never see detected values, and clients get complete answers.
- Detection is best-effort. The regex detector only finds names after a cue ("my name is",
  "Dr."), and unusual formats can slip through. The evaluation in phase 7 should measure how
  often.
- Restoring relies on the model repeating placeholders exactly. A rewritten placeholder
  (`EMAIL_1` instead of `<EMAIL_1>`) stays in the reply instead of the value.
- Placeholders change the prompt slightly, so a model may answer a little differently than it
  would with the real values.
- Every regex is written to run in linear time, because request bodies can be up to a
  megabyte and a backtracking pattern would let one request stall the gateway.
