# ADR-0021: Sensitivity levels, and refusing secrets rather than vaulting them

- **Status:** accepted
- **Date:** 2026-09-25

## Context

People will tell a memory app their wifi password, their bank PIN and their therapist's name.
The first must never be stored or sent to a model provider. The last is fine to keep, but it
shouldn't sit in the prompt of every turn. Phase 1 has no encrypted vault, no second factor
and no way to prove who is asking, so "store it safely" isn't on offer yet.

## Decision

- **Four levels** on every memory: `normal`; `personal` (ordinary private life, e.g. an
  address); `sensitive` (health, finances, relationships, beliefs: kept, but reaches core
  only after the user confirms, rule `P-SENS-1`); and `secret` (passwords, PINs, one-time
  codes, API keys: **never stored**, rule `P-SECRET-1`, which blocks even a confirmed write).
- **Deterministic pre-check before any model call** (`policy/secrets.py`). It matches a value
  after phrases like "password is", "PIN", "OTP", "one-time code" and "API key", and strings
  shaped like provider keys, JWTs and private-key blocks. On a hit the intent comes from the
  rule, the extract call is skipped, the turn row is stored with the span replaced by
  `[redacted]`, the trace gets the redacted input, and the reply says plainly that it wasn't
  saved and why.
- **Belt and braces:** anything the model later labels `secret` is blocked by the policy.
  Its value is redacted from the stored turn input and the trace after the fact, and the write
  log keeps no content for a blocked op.
- **Tested:** after the turn, the secret string is absent from `turns`, `turn_events`,
  `write_log`, `memory_*`, every provider request and the logs.

## Alternatives considered

- **Encrypt secrets in a vault.** Needs key management, re-authentication and a threat model
  that Phase 1 doesn't have. It can come later behind its own ADR.
- **Rely on the model to spot secrets.** The secret would already have reached a provider by
  the time the model spotted it.

## Consequences

The pre-check has false negatives: a password phrased unusually ("the thing I type at the
router is …"), or a bare high-entropy string with no cue word. The model label catches some
of these, but by then the text has already been sent to the provider for extraction. It also
has false positives on a value after "password is" that looks like a word; common words are
excluded, so "my password is the same as before" passes. Users see refusals in the reply and
the diff (`∅ not written · P-SECRET-1`), so misses are visible and can be reported.
