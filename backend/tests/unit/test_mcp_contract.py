"""The MCP server's client (integrations/mcp) against the real API, in-process: it signs in with
the access code as the site does, saves, reads, lists what is ahead and undoes. The package has its
own tests against a stand-in site; this holds the stand-in to the real shapes."""

import sys
from pathlib import Path

import httpx
import pytest

from secondmind.api import Services, create_app
from tests.unit.test_api import _services
from tests.unit.test_web_hardening import CODE, coded

SRC = Path(__file__).parents[3] / "integrations" / "mcp" / "src"
sys.path.insert(0, str(SRC))

from secondmind_mcp.client import Config, SecondMind, SecondMindError  # noqa: E402


def talking_to(services: Services, code: str = CODE) -> SecondMind:
    config = Config(url="https://web.test", email="ada@example.test", access_code=code)
    return SecondMind(config, transport=httpx.ASGITransport(app=create_app(services=services)))


async def test_save_read_upcoming_and_undo_work_against_the_real_api(
    base_env: dict[str, str],
) -> None:
    # Fake providers and open dev sign-in (the access code is asked for only in production, which
    # needs live models): the shapes of every response are the same.
    api = talking_to(_services(base_env))

    saved = await api.turn("I keep my passport in the blue drawer")
    assert saved.text
    added = [c for c in saved.changes if c.op == "added"]
    assert [c.title for c in added] == ["I keep my passport in the blue drawer"]
    assert added[0].item_id

    detail = await api.item(added[0].item_id)
    assert detail["item"]["title"] == "I keep my passport in the blue drawer"

    asked = await api.turn("Where do I keep my passport?")
    assert asked.turn_id != saved.turn_id
    assert asked.text

    ahead = await api.upcoming(14)
    assert {"timezone", "days", "undated"} <= set(ahead)

    assert [c.op for c in await api.changes_of(saved.turn_id)] == ["added"]
    undone = await api.undo(saved.turn_id)
    assert undone.text.startswith("Undone")
    await api.aclose()


async def test_a_wrong_access_code_is_refused_with_the_sites_sentence(
    base_env: dict[str, str],
) -> None:
    api = talking_to(coded(base_env), code="not-the-code")
    with pytest.raises(SecondMindError, match="access code"):
        await api.upcoming(7)
    await api.aclose()
