# ADR-0022: Time resolution: python-dateutil plus our own grammar, never the model

- **Status:** accepted
- **Date:** 2026-09-25

## Context

FR-3.2: dates are resolved deterministically. Models do date arithmetic badly: they misjudge
the weekday of a date, roll "next May" into the wrong year, and invent a day for "in March".
General-purpose parsers (dateparser, parsedatetime, duckling-style services) do better on
arithmetic but disagree with people on exactly the phrases that matter. "Next Friday" on a
Wednesday, "Sunday" said on a Sunday, "the 31st" in a 30-day month, "since March" and "every
other Sunday" all come out differently from what people mean.

## Decision

- Extraction returns time expressions **verbatim**, each tagged with its **clock**
  (`occurred`, `valid`, `due`, `trigger`) and, where it matters, a direction. The model writes
  `[[t1]]` placeholders in the memory text, and code fills in the resolved dates.
- `ingestion/time.py` has a small **grammar of our own** for the phrases people use (relative
  weekdays, "the 3rd of next month", month names with past/future sense, "in N days", parts of
  the day, ISO dates, validity phrases, routines). It uses **python-dateutil** for calendar
  arithmetic (`relativedelta`) and for RRULE parsing and validation (`rrulestr`). Every result
  carries value, end, an honest precision (`month` for "next May"), an RRULE for routines, and
  the name of the rule that fired.
- **"Now" is explicit:** a UTC instant plus the workspace timezone, captured once at turn
  start. Nothing in ingestion calls `datetime.now()`.
- An unresolvable or ambiguous expression takes the most likely reading, is marked `assumed`
  with the alternative, and is said in the acknowledgement. A date whose stated weekday
  disagrees ("Thursday 2 October 2026" is a Friday) is flagged, not silently trusted.
- Reminders are time triggers at `occurred − lead`. A whole-day event fires at 09:00 local
  time; the default lead comes from `workspaces.default_lead_minutes` (1 day).

## Alternatives considered

- **dateparser / parsedatetime alone.** Needs so many overrides for our cases that the
  overrides become the grammar anyway, and on top of that the library brings locale magic
  we'd have to pin.
- **Let the model resolve, then validate.** Validation needs the same arithmetic, and the
  model's errors (wrong year, invented day) look plausible.

## Consequences

We maintain a grammar, covered by 103 unit tests: DST in America/New_York, a timezone where
the UTC date differs, RRULE routines, past episodes and validity phrases. A phrase outside the
grammar falls back to an assumed reading, which the Decision panel shows. Adding a language
means adding its phrases here.
