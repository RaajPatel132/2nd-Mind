"""Upcoming's Snooze and the memory editor through the API: a turn that comes back completed means
something was written. An action that can't be done is refused with the reason (422), and a
snooze to a time that has passed is one of them: the reminder would only expire."""

import uuid
from collections.abc import AsyncIterator
from datetime import timedelta
from typing import Any

import httpx
import pytest

from secondmind.api import Services, create_app
from secondmind.auth import resolve_scope
from secondmind.core import Kind, TriggerOn, TriggerState, WorkspaceScope, new_id, utc_now
from secondmind.memory import NewTrigger, SetTriggerState, TriggerContent, WriterTurn
from tests.unit.memory.helpers import create, item
from tests.unit.test_api import _services, login


@pytest.fixture
async def stack(base_env: dict[str, str]) -> AsyncIterator[tuple[httpx.AsyncClient, Services]]:
    services = _services(base_env)
    app = create_app(services=services)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        yield c, services


async def scope_of(services: Services) -> WorkspaceScope:
    (user_id,) = services.identity.users  # type: ignore[attr-defined]
    workspace = (await services.identity.workspaces_for(user_id))[0]
    return (await resolve_scope(services.identity, user_id=user_id, workspace_id=workspace.id))[0]


async def a_reminder(services: Services) -> tuple[uuid.UUID, uuid.UUID]:
    """A plan with a pending reminder a day ahead: (item id, trigger id)."""
    scope = await scope_of(services)
    now = utc_now()
    op = create(item("Call the plumber", Kind.PLAN, occurred_start=now + timedelta(days=2)))
    trigger = NewTrigger(
        trigger_id=new_id(),
        trigger=TriggerContent(on=TriggerOn.TIME, fires_at=now + timedelta(days=1)),
    )
    op = op.model_copy(update={"triggers": [trigger]})
    writer = services.runner.memory.writer(
        scope, WriterTurn(turn_id=new_id(), workspace_id=scope.workspace_id, kind="user", now=now)
    )
    writer.add(op)
    await writer.commit()
    return op.item_id, trigger.trigger_id


async def snooze(client: httpx.AsyncClient, trigger_id: uuid.UUID, when: str) -> httpx.Response:
    return await client.post(f"/v1/triggers/{trigger_id}/snooze", json={"date_expression": when})


def message_of(response: httpx.Response) -> str:
    body: dict[str, Any] = response.json()
    return str(body["error"]["message"])


async def test_a_snooze_to_a_time_that_has_passed_is_refused_and_the_reminder_stays(
    stack: tuple[httpx.AsyncClient, Services],
) -> None:
    client, services = stack
    await login(client)
    _, trigger_id = await a_reminder(services)
    scope = await scope_of(services)
    before = await services.runner.memory.reader(scope).trigger(trigger_id)
    assert before is not None

    past = (utc_now() - timedelta(days=1)).strftime("%Y-%m-%d %H:%M")
    refused = await snooze(client, trigger_id, past)
    assert refused.status_code == 422, refused.text
    assert "already passed" in message_of(refused)

    after = await services.runner.memory.reader(scope).trigger(trigger_id)
    assert after is not None
    assert (after.fires_at, after.state) == (before.fires_at, before.state)


async def test_a_snooze_that_cannot_be_read_is_refused_not_answered_as_done(
    stack: tuple[httpx.AsyncClient, Services],
) -> None:
    client, services = stack
    await login(client)
    _, trigger_id = await a_reminder(services)
    refused = await snooze(client, trigger_id, "whenever the tide turns")
    assert refused.status_code == 422, refused.text
    assert "couldn't work out the date" in message_of(refused)


async def test_a_snooze_to_a_later_time_moves_the_reminder_and_completes(
    stack: tuple[httpx.AsyncClient, Services],
) -> None:
    client, services = stack
    await login(client)
    _, trigger_id = await a_reminder(services)
    later = (utc_now() + timedelta(days=5)).strftime("%Y-%m-%d 19:30")
    done = await snooze(client, trigger_id, later)
    assert done.status_code == 200, done.text
    assert done.json()["status"] == "completed"
    scope = await scope_of(services)
    moved = await services.runner.memory.reader(scope).trigger(trigger_id)
    assert moved is not None
    assert moved.fires_at is not None
    assert moved.fires_at > utc_now() + timedelta(days=4)


async def test_a_second_snooze_of_a_reminder_that_is_no_longer_pending_is_refused(
    stack: tuple[httpx.AsyncClient, Services],
) -> None:
    client, services = stack
    await login(client)
    _, trigger_id = await a_reminder(services)
    scope = await scope_of(services)
    writer = services.runner.memory.writer(
        scope,
        WriterTurn(turn_id=new_id(), workspace_id=scope.workspace_id, kind="system", now=utc_now()),
    )
    writer.add(SetTriggerState(trigger_id=trigger_id, state=TriggerState.EXPIRED, title="reminder"))
    await writer.commit()
    refused = await snooze(client, trigger_id, (utc_now() + timedelta(days=3)).strftime("%Y-%m-%d"))
    assert refused.status_code == 422, refused.text
    assert "isn't pending any more" in message_of(refused)


async def test_an_edit_that_changes_nothing_is_refused_with_the_reason(
    stack: tuple[httpx.AsyncClient, Services],
) -> None:
    client, services = stack
    await login(client)
    item_id, _ = await a_reminder(services)
    nothing = await client.patch(f"/v1/items/{item_id}", json={})
    assert nothing.status_code == 422, nothing.text
    assert "Nothing needed changing" in message_of(nothing)

    unreadable = await client.patch(
        f"/v1/items/{item_id}", json={"date_expression": "whenever the tide turns"}
    )
    assert unreadable.status_code == 422, unreadable.text
    assert "couldn't work out" in message_of(unreadable)
