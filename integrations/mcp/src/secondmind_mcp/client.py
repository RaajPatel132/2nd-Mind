"""A small client of 2nd Mind's own HTTP API, the one the web app uses (it has no private door).

It signs in the way the site does today (an email and the access code), keeps the session cookie,
signs in again if the session lapses, and reads a turn's server-sent events. Nothing here logs or
returns the access code, and every error it raises is a sentence safe to show a person.
"""

import json
import os
from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass, field
from typing import Any

import httpx


class SecondMindError(Exception):
    """Something a person can be told: the site said no, or could not be reached."""


@dataclass(frozen=True, slots=True)
class Config:
    url: str
    email: str
    access_code: str
    timeout_s: float = 120.0  # a recall can take a quarter of a minute; a save about as long

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "Config":
        env = os.environ if env is None else env
        missing = [
            name
            for name in ("SECONDMIND_URL", "SECONDMIND_EMAIL", "SECONDMIND_ACCESS_CODE")
            if not env.get(name, "").strip()
        ]
        if missing:
            raise SecondMindError(f"set {', '.join(missing)} in the environment of this server")
        return cls(
            url=env["SECONDMIND_URL"].strip().rstrip("/"),
            email=env["SECONDMIND_EMAIL"].strip(),
            access_code=env["SECONDMIND_ACCESS_CODE"],
            timeout_s=float(env.get("SECONDMIND_TIMEOUT_S", "120")),
        )


@dataclass(frozen=True, slots=True)
class Source:
    marker: int
    title: str
    item_id: str | None


@dataclass(frozen=True, slots=True)
class Change:
    op: str
    title: str
    item_id: str | None


@dataclass(slots=True)
class Answer:
    turn_id: str
    text: str
    sources: list[Source] = field(default_factory=list)
    changes: list[Change] = field(default_factory=list)


class SecondMind:
    def __init__(self, config: Config, *, transport: httpx.AsyncBaseTransport | None = None):
        self._config = config
        self._http = httpx.AsyncClient(
            base_url=config.url, timeout=config.timeout_s, transport=transport
        )
        self._workspace: str | None = None

    async def aclose(self) -> None:
        await self._http.aclose()

    # ------------------------------------------------------------------ session

    async def _sign_in(self) -> None:
        body = {"email": self._config.email, "access_code": self._config.access_code}
        try:
            response = await self._http.post("/v1/auth/dev-login", json=body)
        except httpx.HTTPError as exc:
            raise SecondMindError(
                f"could not reach {self._config.url}: {type(exc).__name__}"
            ) from exc
        _check(response)
        self._workspace = str(response.json()["workspaces"][0]["id"])

    async def _workspace_id(self) -> str:
        if self._workspace is None:
            await self._sign_in()
        assert self._workspace is not None  # noqa: S101 - set by _sign_in
        return self._workspace

    async def _send(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        """One request; a 401 means the session lapsed, so sign in once more and retry."""
        for attempt in (1, 2):
            await self._workspace_id()
            try:
                response = await self._http.request(method, path, **kwargs)
            except httpx.HTTPError as exc:
                raise SecondMindError(f"could not reach the site: {type(exc).__name__}") from exc
            if response.status_code != 401 or attempt == 2:
                return response
            self._workspace = None
        raise AssertionError("unreachable")  # pragma: no cover

    # ------------------------------------------------------------------ the tools' calls

    async def turn(self, message: str) -> Answer:
        """Send ``message`` as a turn and read its stream to the end."""
        workspace = await self._workspace_id()
        path = f"/v1/workspaces/{workspace}/turns"
        for attempt in (1, 2):
            try:
                async with self._http.stream("POST", path, json={"message": message}) as response:
                    if response.status_code == 401 and attempt == 1:
                        self._workspace = None
                        workspace = await self._workspace_id()
                        path = f"/v1/workspaces/{workspace}/turns"
                        continue
                    if response.status_code != 200:
                        await response.aread()
                        _check(response)
                    return await _read_turn(response)
            except httpx.HTTPError as exc:
                raise SecondMindError(f"the stream broke: {type(exc).__name__}") from exc
        raise AssertionError("unreachable")  # pragma: no cover

    async def upcoming(self, days: int) -> dict[str, Any]:
        workspace = await self._workspace_id()
        response = await self._send(
            "GET", f"/v1/workspaces/{workspace}/upcoming", params={"days": days}
        )
        _check(response)
        data: dict[str, Any] = response.json()
        return data

    async def item(self, item_id: str) -> dict[str, Any]:
        response = await self._send("GET", f"/v1/items/{item_id}")
        _check(response)
        data: dict[str, Any] = response.json()
        return data

    async def undo(self, turn_id: str) -> Answer:
        response = await self._send("POST", f"/v1/turns/{turn_id}/undo")
        _check(response)
        turn = response.json()
        if turn.get("status") != "completed":
            raise SecondMindError((turn.get("error") or {}).get("message") or "the undo failed")
        return Answer(turn_id=str(turn["id"]), text=str(turn.get("output") or ""))

    async def changes_of(self, turn_id: str) -> list[Change]:
        response = await self._send("GET", f"/v1/turns/{turn_id}/events")
        _check(response)
        return _changes([e["event"] for e in response.json()["events"]])


# ---------------------------------------------------------------------- reading answers


def _check(response: httpx.Response) -> None:
    """Raise the site's own sentence for an error status (its errors have one shape)."""
    if response.status_code < 400:
        return
    try:
        message = str(response.json()["error"]["message"])
    except (ValueError, KeyError, TypeError):
        message = f"the site answered {response.status_code}"
    raise SecondMindError(message)


async def _frames(response: httpx.Response) -> AsyncIterator[tuple[str, dict[str, Any]]]:
    name = ""
    async for line in response.aiter_lines():
        if line.startswith("event: "):
            name = line.removeprefix("event: ")
        elif line.startswith("data: ") and name:
            yield name, json.loads(line.removeprefix("data: "))
            name = ""


async def _read_turn(response: httpx.Response) -> Answer:
    sources: list[Source] = []
    events: list[dict[str, Any]] = []
    async for name, data in _frames(response):
        if name == "turn.event":
            event = data["event"]
            events.append(event)
            if event.get("type") == "citations":
                sources = [
                    Source(c["marker"], c["title"], c.get("item_id") or c.get("turn_id"))
                    for c in event["citations"]
                ]
        elif name == "turn.failed":
            raise SecondMindError(str(data["error"]["message"]) or "the turn failed")
        elif name == "turn.completed":
            turn = data["turn"]
            return Answer(
                turn_id=str(turn["id"]),
                text=str(turn.get("output") or ""),
                sources=sources,
                changes=_changes(events),
            )
    raise SecondMindError("the stream ended before the turn did")


def _changes(events: list[dict[str, Any]]) -> list[Change]:
    out: list[Change] = []
    for event in events:
        if event.get("type") == "memory_diff":
            out += [
                Change(str(e["op"]), str(e.get("title") or ""), e.get("item_id"))
                for e in event["entries"]
            ]
    return out
