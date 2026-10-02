"""The real entrypoint, as Claude Desktop runs it: a subprocess speaking MCP over stdio."""

import sys

import pytest
from mcp.client import Client
from mcp.client.stdio import StdioServerParameters

# A port nothing listens on: the server starts, lists its tools and fails a call cleanly.
ENV = {
    "SECONDMIND_URL": "http://127.0.0.1:9",
    "SECONDMIND_EMAIL": "ada@example.test",
    "SECONDMIND_ACCESS_CODE": "never-printed-anywhere",
    "SECONDMIND_TIMEOUT_S": "3",
}


def params(env: dict[str, str]) -> StdioServerParameters:
    return StdioServerParameters(command=sys.executable, args=["-m", "secondmind_mcp"], env=env)


async def test_the_server_starts_over_stdio_and_lists_its_tools() -> None:
    async with Client(params(ENV)) as client:
        names = {tool.name for tool in (await client.list_tools()).tools}
    assert names == {"save", "recall", "upcoming", "get_item", "undo"}


async def test_an_unreachable_site_fails_the_call_with_a_plain_sentence_not_a_crash() -> None:
    async with Client(params(ENV)) as client:
        result = await client.call_tool("upcoming", {"days": 3})
    assert result.is_error
    text = "".join(block.text for block in result.content if hasattr(block, "text"))
    assert "could not reach" in text
    assert "never-printed-anywhere" not in text


def test_without_its_settings_it_exits_and_names_them(capfd: pytest.CaptureFixture[str]) -> None:
    import subprocess  # noqa: PLC0415

    done = subprocess.run(
        [sys.executable, "-m", "secondmind_mcp"],
        env={"PATH": ""},
        capture_output=True,
        text=True,
        check=False,
        timeout=20,
    )
    assert done.returncode == 2
    assert "SECONDMIND_URL" in done.stderr
    assert done.stdout == ""  # stdout is the protocol's: nothing else may be written there
    del capfd
