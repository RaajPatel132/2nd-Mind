"""Making the persona template and a copy of it per visitor (S4.10, S4.11, ADR-0038).

The **template** is the seed loaded once into a workspace of its own kind, owned by a system user
and never entered. A **copy** is a new workspace with every row of the template, new ids, and every
time moved forward by whole days, so what was upcoming when the seed was written is upcoming today.
Copying calls no model: the template's embeddings are copied, and the few keys that say a date are
re-rendered in code and keep their vector.
"""

import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from secondmind.agent import TurnStore
from secondmind.auth import IdentityStore, Workspace, WorkspaceKind
from secondmind.core import Clock, WorkspaceScope
from secondmind.memory import Embedder, Memory
from secondmind.persona.loader import PersonaSeed, seed_hash
from secondmind.persona.seed import Seeded, SourceSink, seed_workspace
from secondmind.persona.timing import days_to_move
from secondmind.retrieval import ConversationStore


class PersonaStore(Protocol):
    """The two things the database does for the persona that aren't a workspace's own rows."""

    async def copy_template(
        self,
        *,
        template_id: uuid.UUID,
        workspace_id: uuid.UUID,
        user_id: uuid.UUID,
        moved_days: int,
    ) -> None:
        """Copy every row of the template into the (empty) copy, moved forward by ``moved_days``."""
        ...

    async def mark_template(
        self, workspace_id: uuid.UUID, *, seed_id: str, version: int, seed_hash: str
    ) -> None: ...


@dataclass(frozen=True, slots=True)
class PersonaService:
    identity: IdentityStore
    store: PersonaStore
    memory: Memory
    seed_id: str
    timezone: str
    anchor: datetime
    clock: Clock

    @property
    def moved_days(self) -> int:
        """How far a copy made now is moved: whole days, by the calendar in the persona's zone."""
        return max(0, days_to_move(self.anchor, self.clock(), self.timezone))

    async def template(self) -> Workspace | None:
        return await self.identity.template_for(self.seed_id)

    async def copy_for(self, user_id: uuid.UUID) -> Workspace:
        """The caller's persona copy: the one they have, or a new one."""
        for workspace in await self.identity.workspaces_for(user_id):
            if workspace.kind is WorkspaceKind.PERSONA_COPY:
                return workspace
        return await self.make_copy(user_id)

    async def reset_for(self, user_id: uuid.UUID) -> Workspace:
        """Replace the caller's persona copy with a fresh one."""
        for workspace in await self.identity.workspaces_for(user_id):
            if workspace.kind is WorkspaceKind.PERSONA_COPY:
                await self.identity.delete_workspace(workspace.id)
        return await self.make_copy(user_id)

    async def make_copy(
        self,
        user_id: uuid.UUID,
        *,
        kind: WorkspaceKind = WorkspaceKind.PERSONA_COPY,
        moved_days: int | None = None,
    ) -> Workspace:
        """A new copy of the template for ``user_id``, moved ``moved_days`` (default: to today)."""
        template = await self.template()
        if template is None:
            raise LookupError("the persona template hasn't been loaded (make seed-persona)")
        workspace = await self.identity.create_workspace(
            owner_user_id=user_id, kind=kind, timezone=template.timezone
        )
        days = self.moved_days if moved_days is None else moved_days
        try:
            await self.store.copy_template(
                template_id=template.id,
                workspace_id=workspace.id,
                user_id=user_id,
                moved_days=days,
            )
            await self.rebase_keys(
                WorkspaceScope(workspace_id=workspace.id, user_id=user_id), template.timezone
            )
        except Exception:
            await self.identity.delete_workspace(workspace.id)  # never leave half a copy behind
            raise
        return await self.identity.get_workspace(workspace.id) or workspace

    async def rebase_keys(self, scope: WorkspaceScope, timezone: str) -> int:
        """Re-render the keys that name a date. Each keeps the template's vector (no model call)."""
        items = await self.memory.reader(scope).find_items(limit=2000)
        indexer = self.memory.keys(scope, timezone=timezone, embed=None, model="")
        return await indexer.rerender_in_place([i.id for i in items])


@dataclass(frozen=True, slots=True)
class TemplateDeps:
    """What loading the seed into a template needs, built by the composition root."""

    identity: IdentityStore
    store: PersonaStore
    memory: Memory
    turns: Callable[[WorkspaceScope], TurnStore]
    conversation: Callable[[WorkspaceScope], ConversationStore]
    sources: Callable[[WorkspaceScope], SourceSink]
    embed: Embedder | None
    embedding_model: str


async def seed_template(
    deps: TemplateDeps, seed: PersonaSeed, *, hash_of: str | None = None, force: bool = False
) -> tuple[Workspace, Seeded | None]:
    """Load ``seed`` into its template workspace, unless the template already holds this version of
    the file (the seed file's hash). A changed file replaces the template; copies already made stay
    as they are (S4.10). Returns the template, and what was written (None when nothing was)."""
    digest = hash_of or seed_hash()
    existing = await deps.identity.template_for(seed.id)
    if existing is not None:
        if existing.seed_hash == digest and not force:
            return existing, None
        await deps.identity.delete_workspace(existing.id)
    # The system user that owns it has no email and no session: nobody can sign in as it.
    user = await deps.identity.create_user(email=None)
    workspace = await deps.identity.create_workspace(
        owner_user_id=user.id, kind=WorkspaceKind.TEMPLATE, timezone=seed.timezone
    )
    scope = WorkspaceScope(workspace_id=workspace.id, user_id=user.id)
    seeded = await seed_workspace(
        seed,
        memory=deps.memory,
        turns=deps.turns(scope),
        scope=scope,
        timezone=seed.timezone,
        now=seed.anchor,
        embed=deps.embed,
        embedding_model=deps.embedding_model,
        conversation=deps.conversation(scope),
        sources=deps.sources(scope),
        label=f"the {seed.id} persona",
    )
    await deps.store.mark_template(
        workspace.id, seed_id=seed.id, version=seed.version, seed_hash=digest
    )
    return await deps.identity.get_workspace(workspace.id) or workspace, seeded
