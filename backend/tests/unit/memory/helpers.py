"""Small builders for memory tests (all data synthetic)."""

import uuid
from datetime import UTC, datetime

from secondmind.core import (
    EntityKind,
    EntityRole,
    Kind,
    Sensitivity,
    TurnEvent,
    WorkspaceScope,
    initial_state,
    new_id,
)
from secondmind.memory import (
    CreateItem,
    EntityContent,
    EntityLink,
    ItemContent,
    Memory,
    MemorySettings,
    UpsertEntity,
    WriterTurn,
)
from secondmind.memory.adapters import InMemoryMemory

NOW = datetime(2026, 9, 23, 4, 30, tzinfo=UTC)  # 10:00 Asia/Kolkata


def scope() -> WorkspaceScope:
    return WorkspaceScope(workspace_id=uuid.uuid4(), user_id=uuid.uuid4())


def memory(**settings: object) -> tuple[Memory, InMemoryMemory]:
    db = InMemoryMemory()
    return Memory(db.store, MemorySettings(**settings)), db  # type: ignore[arg-type]


def turn(ws: WorkspaceScope, kind: str = "user", now: datetime = NOW) -> WriterTurn:
    return WriterTurn(turn_id=new_id(), workspace_id=ws.workspace_id, kind=kind, now=now)


def item(
    text: str,
    kind: Kind = Kind.NOTE,
    *,
    title: str | None = None,
    state: str | None = None,
    **fields: object,
) -> ItemContent:
    return ItemContent(
        kind=kind,
        state=state or initial_state(kind),
        text=text,
        title=title or text,
        mentioned_at=NOW,
        **fields,  # type: ignore[arg-type]
    )


def create(content: ItemContent, *entities: tuple[uuid.UUID, EntityRole]) -> CreateItem:
    return CreateItem(
        item_id=new_id(),
        item=content,
        title=content.title,
        entities=[EntityLink(row_id=new_id(), entity_id=e, role=r) for e, r in entities],
    )


def person(name: str, *labels: str, key: bool = False) -> UpsertEntity:
    return UpsertEntity(
        entity_id=new_id(),
        entity=EntityContent(kind=EntityKind.PERSON, name=name, labels=list(labels), is_key=key),
        create=True,
        title=name,
    )


def sensitive(content: ItemContent) -> ItemContent:
    return content.model_copy(update={"sensitivity": Sensitivity.SENSITIVE})


class Events:
    def __init__(self) -> None:
        self.events: list[TurnEvent] = []

    async def __call__(self, event: TurnEvent) -> None:
        self.events.append(event)

    def of(self, kind: str) -> list[TurnEvent]:
        return [e for e in self.events if e.type == kind]
