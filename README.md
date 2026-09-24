# 2nd Mind

A chat-first personal memory. Tell it anything (a show to watch, a gift idea, a deadline, a
link) and it decides what the thing is, when it matters, who it concerns and where to keep it.
Ask for anything back by meaning, time, person or category. Every decision it makes is visible
in a **glass box** beside the chat and can be inspected and corrected. Models sit behind a
provider-agnostic layer (Anthropic and OpenAI today, switchable per step by config), every turn
is traced, and every token is metered.

> Status: early development (walking skeleton). The product spec lives in
> [`docs/PRD.md`](docs/PRD.md).

## Run it locally

Requirements: Docker (with Compose v2), `make`. For development outside containers: `uv` and
Node 22+.

```bash
cp .env.example .env     # optional: add ANTHROPIC_API_KEY and/or OPENAI_API_KEY
make up                  # builds and starts everything, runs migrations, prints the URLs
```

With no provider keys the app runs in **fake-provider mode** (deterministic canned replies), so
everything works offline. `make down` stops the stack.

## Develop

```bash
npm install              # git hooks + commit standard
make install             # backend + frontend dependencies
make check               # everything CI runs, except E2E
make help                # all targets
```

See [`CONTRIBUTING.md`](CONTRIBUTING.md) for the commit standard and definition of done, and
[`docs/adr/`](docs/adr/) for architecture decisions.
