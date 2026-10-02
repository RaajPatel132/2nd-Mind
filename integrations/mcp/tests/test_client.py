import httpx
import pytest

from secondmind_mcp.client import Config, SecondMind, SecondMindError
from tests.fake_site import CODE, ITEM, TURN, Site, completed, error, event, sse

CONFIG = Config(url="https://site.test", email="ada@example.test", access_code=CODE)


def client(site: Site, config: Config = CONFIG) -> SecondMind:
    return SecondMind(config, transport=httpx.MockTransport(site))


def test_the_configuration_comes_from_the_environment_and_names_what_is_missing() -> None:
    config = Config.from_env(
        {
            "SECONDMIND_URL": "https://site.test/",
            "SECONDMIND_EMAIL": "a@b.c",
            "SECONDMIND_ACCESS_CODE": "x",
        }
    )
    assert config.url == "https://site.test"
    with pytest.raises(SecondMindError) as raised:
        Config.from_env({"SECONDMIND_URL": "https://site.test", "SECONDMIND_ACCESS_CODE": "hush"})
    assert "SECONDMIND_EMAIL" in str(raised.value)
    assert "hush" not in str(raised.value)


async def test_a_wrong_access_code_is_the_sites_own_sentence_and_never_the_code() -> None:
    wrong = Config(url="https://site.test", email="a@b.c", access_code="not-the-code")
    with pytest.raises(SecondMindError) as raised:
        await client(Site(), wrong).upcoming(7)
    assert str(raised.value) == "That access code isn't right."
    assert "not-the-code" not in str(raised.value)


async def test_a_turn_returns_the_reply_its_sources_and_what_it_changed() -> None:
    site = Site(
        [
            lambda: sse(
                ("turn.started", {"turn_id": TURN}),
                ("token", {"text": "Pune "}),
                event(
                    {
                        "type": "citations",
                        "citations": [
                            {"marker": 1, "kind": "item", "item_id": ITEM, "title": "Lives in Pune"}
                        ],
                    }
                ),
                event(
                    {
                        "type": "memory_diff",
                        "entries": [{"op": "added", "title": "Lives in Pune", "item_id": ITEM}],
                    },
                    seq=2,
                ),
                completed("You live in Pune [1]."),
            )
        ]
    )
    answer = await client(site).turn("Where do I live?")
    assert answer.text == "You live in Pune [1]."  # the stored reply, not the streamed pieces
    assert [(s.marker, s.title, s.item_id) for s in answer.sources] == [(1, "Lives in Pune", ITEM)]
    assert [(c.op, c.title) for c in answer.changes] == [("added", "Lives in Pune")]
    assert site.bodies("/v1/workspaces/11111111-1111-1111-1111-111111111111/turns") == [
        {"message": "Where do I live?"}
    ]


async def test_a_failed_turn_raises_its_message() -> None:
    site = Site(
        [lambda: sse(("turn.failed", {"error": {"code": "x", "message": "Answers are paused."}}))]
    )
    with pytest.raises(SecondMindError, match=r"Answers are paused\."):
        await client(site).turn("hello")


async def test_a_stream_that_ends_early_is_an_error_not_an_empty_answer() -> None:
    site = Site([lambda: sse(("turn.started", {"turn_id": TURN}), ("token", {"text": "Pu"}))])
    with pytest.raises(SecondMindError, match="ended before the turn did"):
        await client(site).turn("hello")


async def test_a_session_that_lapsed_is_signed_in_again_once() -> None:
    site = Site()
    api = client(site)
    await api.upcoming(7)
    assert site.logins == 1
    site.lapse_next = True
    await api.upcoming(7)
    assert site.logins == 2


async def test_a_turn_after_a_lapse_signs_in_again_and_goes_through() -> None:
    site = Site([lambda: sse(completed("ok"))])
    api = client(site)
    await api.upcoming(7)
    site.lapse_next = True
    answer = await api.turn("hello")
    assert answer.text == "ok"
    assert site.logins == 2


async def test_an_unreachable_site_is_said_plainly() -> None:
    def down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    api = SecondMind(CONFIG, transport=httpx.MockTransport(down))
    with pytest.raises(SecondMindError, match="could not reach"):
        await api.upcoming(7)


async def test_an_error_status_without_the_sites_shape_still_gives_a_sentence() -> None:
    def odd(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("dev-login"):
            return httpx.Response(200, json={"workspaces": [{"id": "w"}]})
        return httpx.Response(502, text="bad gateway")

    with pytest.raises(SecondMindError, match="502"):
        await SecondMind(CONFIG, transport=httpx.MockTransport(odd)).item(ITEM)


def test_error_helper_has_the_sites_shape() -> None:
    assert error("x").json() == {"error": {"code": "x", "message": "x"}}
