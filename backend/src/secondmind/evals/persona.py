"""The persona goldens (S4.10, S4.11): ten questions asked of a fresh copy of Aditi Rao's memory.

Each case in ``evals/cases/persona/*.yaml`` is a recall-suite case (plan, rerank and extraction
recorded for the fake provider) and runs on its own copy of the template, moved ``shift`` days, so a
case passes on any date: the same file is run on copies moved by 0, 60 and 400 days. A case may also
say what its save must have written (``saved``).
"""

import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from datetime import timedelta
from pathlib import Path
from typing import Any

import yaml

from secondmind.auth import IdentityStore
from secondmind.config import DEFAULT_RESOURCES_DIR
from secondmind.core import WorkspaceScope, new_id
from secondmind.evals.fixture import Seeded
from secondmind.evals.recall import (
    RecallCase,
    RecallRun,
    failures,
    fake_router,
    load_cases,
    run_case,
)
from secondmind.memory import Memory
from secondmind.memory.adapters import Database, sql_memory
from secondmind.persona import PersonaService

CASES_DIR = DEFAULT_RESOURCES_DIR / "evals" / "cases" / "persona"


@dataclass(slots=True)
class PersonaResult:
    case: RecallCase
    shift: int
    failures: list[str] = field(default_factory=list)
    run: RecallRun | None = None

    @property
    def passed(self) -> bool:
        return not self.failures


def persona_cases(directory: Path = CASES_DIR) -> list[RecallCase]:
    return load_cases(directory)


def _saved(case: RecallCase) -> list[dict[str, Any]]:
    raw = yaml.safe_load(case.path.read_text(encoding="utf-8")) or {}
    return list(raw.get("saved", []))


async def seeded_copy(
    db: Database,
    service: PersonaService,
    template_items: Mapping[str, uuid.UUID],
    scope: WorkspaceScope,
) -> Seeded:
    """The copy's items by the seed's own keys, found by what they say (a copy has new ids)."""
    memory = Memory(sql_memory(db))
    template = await service.template()
    if template is None:
        raise LookupError("the persona template hasn't been loaded")
    t_scope = WorkspaceScope(workspace_id=template.id, user_id=template.owner_user_id)
    by_id_t = {i.id: i for i in await memory.reader(t_scope).find_items(limit=2000)}
    copies = await memory.reader(scope).find_items(limit=2000)

    def key_of(item: Any) -> tuple[str, str, str]:
        return (item.kind.value, item.title, item.text)

    counts: dict[tuple[str, str, str], int] = {}
    for item in copies:
        counts[key_of(item)] = counts.get(key_of(item), 0) + 1
    by_natural = {key_of(i): i.id for i in copies if counts[key_of(i)] == 1}
    items: dict[str, uuid.UUID] = {}
    for key, template_id in template_items.items():
        original = by_id_t.get(template_id)
        if original is not None and key_of(original) in by_natural:
            items[key] = by_natural[key_of(original)]
    return Seeded(scope=scope, system_turn=uuid.UUID(int=0), items=items)


async def run_persona_case(
    case: RecallCase,
    *,
    db: Database,
    identity: IdentityStore,
    service: PersonaService,
    template_items: Mapping[str, uuid.UUID],
    shift: int = 0,
) -> PersonaResult:
    """One case on its own copy moved ``shift`` days."""
    user = await identity.create_user(email=f"persona-{new_id()}@example.test")
    copy = await service.make_copy(user.id, moved_days=shift)
    scope = WorkspaceScope(workspace_id=copy.id, user_id=user.id)
    seeded = await seeded_copy(db, service, template_items, scope)
    moved = replace(case, now=case.now + timedelta(days=shift))
    run = await run_case(moved, db=db, seeded=seeded, router=fake_router([moved]))
    result = PersonaResult(case=case, shift=shift, run=run)
    result.failures.extend(failures(run))
    await _check_saved(db, scope, case, run, result)
    return result


async def _check_saved(
    db: Database, scope: WorkspaceScope, case: RecallCase, run: RecallRun, result: PersonaResult
) -> None:
    wanted = _saved(case)
    if not wanted:
        return
    memory = Memory(sql_memory(db))
    rows = await memory.reader(scope).write_log(run.turn.id)
    ids = [r.target_id for r in rows if r.target_type.value == "item"]
    items = await memory.reader(scope).items(list(dict.fromkeys(ids)))
    for want in wanted:
        if not any(
            i.kind.value == want["kind"]
            and want.get("title_contains", "").lower() in i.title.lower()
            for i in items
        ):
            result.failures.append(
                f"the save wrote no {want['kind']} with {want.get('title_contains')!r}"
            )
