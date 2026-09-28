"""Intent accuracy (S2.4, R.3): the labelled messages in ``evals/cases/intent/messages.yaml``,
each sent to the intent step alone, exactly as the turn graph sends it (the rendered intent
prompt, the message, no history). On the fake provider the offline heuristic answers; live, the
routed intent model does."""

import re
import time
from dataclasses import dataclass
from pathlib import Path

import yaml

from secondmind.agent import IntentPromptVars
from secondmind.config import DEFAULT_RESOURCES_DIR, Step
from secondmind.evals.harness import Pass
from secondmind.evals.runs import CaseRecord
from secondmind.ingestion import IntentOutput, offline_responders
from secondmind.providers import ChatMessage, FakeScript, ProviderUnavailableError

CASES_FILE = DEFAULT_RESOURCES_DIR / "evals" / "cases" / "intent" / "messages.yaml"
NOW = "2026-09-23T10:00"
TIMEZONE = "Asia/Kolkata"


@dataclass(frozen=True, slots=True)
class IntentCase:
    id: str
    message: str
    intent: str

    @property
    def tags(self) -> list[str]:
        return [self.intent]


def load_cases(path: Path = CASES_FILE) -> list[IntentCase]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or []
    cases = []
    for n, entry in enumerate(raw, start=1):
        message = str(entry["message"])
        slug = "-".join(re.findall(r"[a-z0-9]+", message.lower())[:4])
        cases.append(IntentCase(id=f"{n:02d}-{slug}", message=message, intent=str(entry["intent"])))
    return cases


async def run_intent_case(case: IntentCase, run: Pass) -> CaseRecord:
    router = run.router(FakeScript(responders=offline_responders()))
    route = router.route(Step.INTENT)
    if route.prompt is None:
        raise RuntimeError("the intent step has no prompt configured")
    system = run.harness.prompts.render(
        route.prompt, IntentPromptVars(now=NOW, timezone=TIMEZONE)
    ).text
    mark = run.log.mark()
    started = time.perf_counter()
    got: str | None = None
    failures: list[str] = []
    try:
        result = await router.structured(
            Step.INTENT,
            IntentOutput,
            system=system,
            messages=[ChatMessage.user(case.message)],
            prompt=route.prompt,
        )
        got = result.value.intent
    except ProviderUnavailableError as exc:
        failures.append(f"provider unavailable: {exc.detail}")
    elapsed = round((time.perf_counter() - started) * 1000)
    if got is not None and got != case.intent:
        failures.append(f"{got} ≠ {case.intent}")
    return run.record(
        mark=mark,
        case_id=case.id,
        title=case.message,
        tags=case.tags,
        passed=not failures,
        failures=failures,
        scores={"expected": case.intent, "got": got},
        latency_ms=elapsed,
    )
