"""S4.7: the item view says where a saved link came from, and "Add the text" only applies to a
link that is waiting for text."""

import uuid

from secondmind.api import Services
from secondmind.auth import resolve_scope
from secondmind.core import Kind
from secondmind.memory import WriterTurn
from tests.unit.memory.helpers import NOW, create, item
from tests.unit.test_api import _services
from tests.unit.test_web_hardening import _login, client_for


async def _memory_item(services: Services, workspace_id: str, user_id: uuid.UUID) -> uuid.UUID:
    scope, _ = await resolve_scope(
        services.identity, user_id=user_id, workspace_id=uuid.UUID(workspace_id)
    )
    op = create(item("Tea is nice", Kind.NOTE))
    writer = services.runner.memory.writer(
        scope,
        WriterTurn(turn_id=uuid.uuid4(), workspace_id=scope.workspace_id, kind="user", now=NOW),
    )
    writer.add(op)
    await writer.commit()
    return op.item_id


async def test_an_ordinary_memory_has_no_link_source_and_takes_no_added_text(
    base_env: dict[str, str],
) -> None:
    services = _services(base_env)
    async with client_for(services) as c:
        workspace_id = await _login(c)
        me = (await c.get("/v1/me")).json()
        item_id = await _memory_item(services, workspace_id, uuid.UUID(me["user"]["id"]))
        detail = await c.get(f"/v1/items/{item_id}")
        assert detail.status_code == 200
        assert detail.json()["source"] is None
        refused = await c.post(f"/v1/items/{item_id}/content", json={"text": "pasted words"})
        assert refused.status_code == 422
        assert "saved link" in refused.json()["error"]["message"]
        empty = await c.post(f"/v1/items/{item_id}/content", json={"text": ""})
        assert empty.status_code == 422
        unknown = await c.post(f"/v1/items/{uuid.uuid4()}/content", json={"text": "x"})
        assert unknown.status_code == 404


async def test_added_text_is_refused_for_someone_elses_item(base_env: dict[str, str]) -> None:
    services = _services(base_env)
    async with client_for(services) as owner:
        workspace_id = await _login(owner)
        me = (await owner.get("/v1/me")).json()
        item_id = await _memory_item(services, workspace_id, uuid.UUID(me["user"]["id"]))
    async with client_for(services) as other:
        await other.post("/v1/auth/dev-login", json={"email": "someone-else@example.test"})
        assert (
            await other.post(f"/v1/items/{item_id}/content", json={"text": "x"})
        ).status_code == 404
        assert (await other.get(f"/v1/items/{item_id}")).status_code == 404
