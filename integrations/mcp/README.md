# secondmind-mcp

An [MCP](https://modelcontextprotocol.io) server for 2nd Mind. Any MCP client (Claude Desktop, an
agent you write) can use a person's memory as long-term memory: keep something, ask what was kept,
see what is coming up. It talks to the site over the same HTTP API the web app uses, so every call
is a real turn with its own glass box (open it in the app) and its own undo.

| Tool | What it does |
|---|---|
| `save(text)` | Keeps a plain statement in the person's words. Replies with what changed in memory and a turn id. A question is answered rather than saved, and the reply says nothing changed. |
| `recall(question)` | Answers from saved memories, with the memories it rests on. |
| `upcoming(days=14)` | Plans, reminders and tasks due in the next days, by day, then open tasks with no date. |
| `get_item(item_id)` | One saved memory in full, by an id `recall` or `upcoming` gave. |
| `undo(turn_id)` | Takes back what an earlier turn changed. Undoing an undo brings it back. |

## Run it

It reads three settings from its environment and speaks MCP over stdio:

| Variable | Meaning |
|---|---|
| `SECONDMIND_URL` | The site, for example `https://2nd-mind.example.org` or `http://localhost:8080` |
| `SECONDMIND_EMAIL` | The email you sign in with (your own private workspace) |
| `SECONDMIND_ACCESS_CODE` | The site's access code, if it asks for one |
| `SECONDMIND_TIMEOUT_S` | Optional. Seconds to wait on a turn (default 120) |

For Claude Desktop, add this to `claude_desktop_config.json` (use the absolute path to this
folder):

```json
{
  "mcpServers": {
    "2nd-mind": {
      "command": "uv",
      "args": ["run", "--project", "/path/to/2nd-Mind/integrations/mcp", "secondmind-mcp"],
      "env": {
        "SECONDMIND_URL": "http://localhost:8080",
        "SECONDMIND_EMAIL": "you@example.com",
        "SECONDMIND_ACCESS_CODE": "the access code"
      }
    }
  }
}
```

Try it against the local stack first (`make up`, then `make seed-persona`): the local site needs no
access code, so leave `SECONDMIND_ACCESS_CODE` empty there.

## What it is not yet

The product spec (PRD 7.18) has this server sign in with an API key, scoped and metered per
client, with writes from an outside agent held for the person's confirmation. API keys do not exist
yet (they are planned with the public API), so for now:

- **It signs in as you, with the site's access code.** The code in this server's environment is as
  sensitive as the site's. Use it for your own deployment, not as a shared service.
- **A save through it is treated like anything you type.** The write policy still applies (secrets
  are refused, sensitive core writes are held for confirmation), but the server is not told the
  client is an outside agent, so it cannot apply the stricter rule for one.
- **It is not metered per client.** Spend is the same as chatting: it draws on your quota and the
  site's caps and kill switch.
- **Calls are not labelled with their client** in the glass box.

[ADR-0046](../../docs/adr/0046-mcp-server-as-an-adapter.md) has the reasoning.

## Develop

Its own package, with its own lock file, so the API image does not carry the MCP SDK:

```bash
make mcp-check      # ruff, mypy --strict and the tests (from the repository root)
```

The tests run the real server as a subprocess over stdio, the tools against a stand-in site, and
(in `backend/tests/unit/test_mcp_contract.py`) the client against the real API, so the stand-in
cannot drift from the shapes the API sends.
