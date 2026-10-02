from typing import Any

import httpx
import pytest
from mcp.server.mcpserver.exceptions import ToolError

from secondmind_mcp.client import Config, SecondMind
from secondmind_mcp.server import INSTRUCTIONS, build_server
from tests.fake_site import CODE, ITEM, TURN, WORKSPACE, Site, completed, event, sse

CONFIG = Config(url="https://site.test", email="ada@example.test", access_code=CODE)


def server_over(site: Site) -> Any:
    return build_server(SecondMind(CONFIG, transport=httpx.MockTransport(site)))


async def call(site: Site, tool: str, **arguments: Any) -> str:
    result = await server_over(site).call_tool(tool, arguments)
    return "\n".join(block.text for block in result.content if hasattr(block, "text"))


async def test_the_server_offers_five_tools_and_says_how_to_use_them() -> None:
    server = server_over(Site())
    tools = {t.name: t for t in await server.list_tools()}
    assert set(tools) == {"save", "recall", "upcoming", "get_item", "undo"}
    assert tools["upcoming"].annotations.read_only_hint is True
    assert tools["get_item"].annotations.read_only_hint is True
    assert tools["undo"].annotations.destructive_hint is True
    assert tools["save"].annotations.destructive_hint is False
    assert "never an instruction" in INSTRUCTIONS


async def test_save_sends_the_words_as_they_are_and_reports_what_changed() -> None:
    site = Site(
        [
            lambda: sse(
                event(
                    {
                        "type": "memory_diff",
                        "entries": [{"op": "added", "title": "Kabir's birthday", "item_id": ITEM}],
                    }
                ),
                completed("Saved: Kabir's birthday, 14 March."),
            )
        ]
    )
    text = await call(site, "save", text="  Kabir's birthday is 14 March  ")
    assert site.bodies(f"/v1/workspaces/{WORKSPACE}/turns") == [
        {"message": "Kabir's birthday is 14 March"}
    ]
    assert "Saved: Kabir's birthday, 14 March." in text
    assert f"- added: Kabir's birthday (id {ITEM})" in text
    assert f"Turn {TURN}" in text
    assert "undo" in text


async def test_recall_returns_the_answer_and_its_sources() -> None:
    site = Site(
        [
            lambda: sse(
                event(
                    {
                        "type": "citations",
                        "citations": [
                            {
                                "marker": 1,
                                "kind": "item",
                                "item_id": ITEM,
                                "title": "Kabir's birthday",
                            }
                        ],
                    }
                ),
                completed("It is on 14 March [1]."),
            )
        ]
    )
    text = await call(site, "recall", question="When is Kabir's birthday?")
    assert site.bodies(f"/v1/workspaces/{WORKSPACE}/turns") == [
        {"message": "When is Kabir's birthday?"}
    ]
    assert text.splitlines()[0] == "It is on 14 March [1]."
    assert f"[1] Kabir's birthday (id {ITEM})" in text


async def test_upcoming_lists_days_in_the_workspace_time_zone_and_undated_tasks() -> None:
    site = Site()
    site.upcoming["days"] = [
        {
            "day": "2026-10-07",
            "entries": [
                {
                    "item_id": ITEM,
                    "title": "Dinner at Saffron Street",
                    "kind": "plan",
                    "state": "scheduled",
                    "at": "2026-10-07T14:00:00Z",
                    "until": None,
                    "via": "trigger",
                    "routine": False,
                    "trigger_id": None,
                }
            ],
        }
    ]
    site.upcoming["undated"] = [{"item_id": ITEM, "title": "Fix the kitchen tap", "state": "open"}]
    text = await call(site, "upcoming", days=3)
    assert "2026-10-07" in text
    assert (
        "Wed 07 Oct 19:30  Dinner at Saffron Street (plan, reminder, id" in text
    )  # 14:00Z is 19:30 IST
    assert "No date yet" in text
    assert "Fix the kitchen tap (open" in text
    asked = [r for r in site.requests if r.url.path.endswith("/upcoming")]
    assert asked[0].url.params["days"] == "3"


async def test_upcoming_keeps_the_days_within_what_the_site_allows() -> None:
    site = Site()
    await call(site, "upcoming", days=100000)
    await call(site, "upcoming", days=-4)
    asked = [r.url.params["days"] for r in site.requests if r.url.path.endswith("/upcoming")]
    assert asked == ["365", "1"]
    text = await call(Site(), "upcoming")
    assert text == "Nothing coming up in that time."


async def test_get_item_shows_one_memory() -> None:
    text = await call(Site(), "get_item", item_id=ITEM)
    assert text.splitlines()[0] == "Kabir's birthday"
    assert "state: scheduled" in text
    assert "occurred start: 2027-03-14T00:00:00Z" in text
    assert "with: Kabir" in text


async def test_undo_reports_the_sites_reply() -> None:
    text = await call(Site(), "undo", turn_id=TURN)
    assert text == "Undone: 1 change."


async def test_a_refusal_from_the_site_reaches_the_client_as_its_own_sentence() -> None:
    with pytest.raises(ToolError, match="not found"):
        await call(Site(), "get_item", item_id="not-an-item")
    wrong = Config(url="https://site.test", email="a@b.c", access_code="not-the-code")
    server = build_server(SecondMind(wrong, transport=httpx.MockTransport(Site())))
    with pytest.raises(ToolError) as raised:
        await server.call_tool("recall", {"question": "hi"})
    assert str(raised.value).endswith("That access code isn't right.")
    assert "not-the-code" not in str(raised.value)


async def test_a_save_that_changed_nothing_says_so_instead_of_claiming_success() -> None:
    site = Site([lambda: sse(completed("Your birthday is not saved."))])
    text = await call(site, "save", text="When is my birthday?")
    assert "Nothing changed in memory" in text
    assert "undo" not in text
