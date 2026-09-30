"""The recall fixture (S3.1): ``evals/fixtures/recall.yaml`` as a workspace.

The loader is ``secondmind.persona.seed_workspace`` (moved there in S4.10 so the persona can use
it too): it writes the fixture's entities, items, links and relations through ``MemoryWriter`` as
**one system turn** (no extract step, so it's deterministic), renders their keys exactly as
ingestion does (same indexer, same embedder), and stores the past turns and indexes what was said.
Tests load it into a fresh workspace per module; ``make seed-dev`` loads it into the dev workspace;
the E2E suite loads it through a dev-only endpoint.
"""

from datetime import datetime
from pathlib import Path
from typing import Any

import yaml
from pydantic import Field

from secondmind.config import DEFAULT_RESOURCES_DIR
from secondmind.persona import (
    EntitySpec,
    ItemSpec,
    LinkSpec,
    OccurredSpec,
    RelationSpec,
    RoleSpec,
    Seeded,
    TimeSpec,
    TriggerSpec,
    TurnSpec,
    WorkspaceSpec,
    local_instant,
)
from secondmind.persona import seed_workspace as seed

# Relative to the resources directory (RESOURCES_DIR): an installed package is not next to it.
FIXTURE_FILE = Path("evals") / "fixtures" / "recall.yaml"
FIXTURE_PATH = DEFAULT_RESOURCES_DIR / FIXTURE_FILE
SYSTEM_TEXT = "Load the recall fixture"


class RecallFixture(WorkspaceSpec):
    now: str
    timezone: str
    other: WorkspaceSpec = Field(default_factory=WorkspaceSpec)

    @property
    def instant(self) -> datetime:
        return local_instant(self.now, self.timezone)


def load_fixture(path: Path = FIXTURE_PATH) -> RecallFixture:
    return RecallFixture.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))


async def seed_workspace(spec: WorkspaceSpec, **kwargs: Any) -> Seeded:
    """Write ``spec`` into an empty workspace as the recall fixture (``persona.seed_workspace``)."""
    return await seed(spec, label="the recall fixture", **kwargs)


__all__ = [
    "FIXTURE_FILE",
    "FIXTURE_PATH",
    "SYSTEM_TEXT",
    "EntitySpec",
    "ItemSpec",
    "LinkSpec",
    "OccurredSpec",
    "RecallFixture",
    "RelationSpec",
    "RoleSpec",
    "Seeded",
    "TimeSpec",
    "TriggerSpec",
    "TurnSpec",
    "WorkspaceSpec",
    "load_fixture",
    "local_instant",
    "seed_workspace",
]
