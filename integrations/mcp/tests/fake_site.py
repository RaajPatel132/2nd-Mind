"""A stand-in for 2nd Mind's HTTP API: just enough of the real shapes for the client and tools."""

import json
from collections.abc import Callable
from typing import Any

import httpx

CODE = "open-sesame-42"
WORKSPACE = "11111111-1111-1111-1111-111111111111"
ITEM = "22222222-2222-2222-2222-222222222222"
TURN = "33333333-3333-3333-3333-333333333333"


def sse(*frames: tuple[str, dict[str, Any]]) -> httpx.Response:
    body = "".join(f"event: {name}\ndata: {json.dumps(data)}\n\n" for name, data in frames)
    return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=body)


def completed(output: str, turn_id: str = TURN) -> tuple[str, dict[str, Any]]:
    return "turn.completed", {"turn_id": turn_id, "turn": {"id": turn_id, "output": output}}


def event(payload: dict[str, Any], seq: int = 1) -> tuple[str, dict[str, Any]]:
    return "turn.event", {"seq": seq, "event": payload}


def error(message: str, status: int = 401) -> httpx.Response:
    return httpx.Response(status, json={"error": {"code": "x", "message": message}})


class Site:
    """Records every request; ``turns`` answers each turn in order."""

    def __init__(self, turns: list[Callable[[], httpx.Response]] | None = None) -> None:
        self.requests: list[httpx.Request] = []
        self.logins = 0
        self.signed_in = False
        self.turns = list(turns or [])
        self.upcoming: dict[str, Any] = {
            "timezone": "Asia/Kolkata",
            "start": "2026-10-06T00:00:00Z",
            "end": "2026-10-20T00:00:00Z",
            "days": [],
            "undated": [],
            "due_soon": 0,
            "note": None,
        }
        self.lapse_next = False  # the next authed request answers 401 once, as an expired session

    def bodies(self, path: str) -> list[dict[str, Any]]:
        return [json.loads(r.content) for r in self.requests if r.url.path == path and r.content]

    def __call__(self, request: httpx.Request) -> httpx.Response:  # noqa: PLR0911
        self.requests.append(request)
        path = request.url.path
        if path == "/v1/auth/dev-login":
            body = json.loads(request.content)
            if body.get("access_code") != CODE:
                return error("That access code isn't right.")
            self.logins += 1
            self.signed_in = True
            return httpx.Response(
                200, json={"user": {"id": "u"}, "workspaces": [{"id": WORKSPACE}]}
            )
        if self.lapse_next:
            self.lapse_next = False
            self.signed_in = False
        if not self.signed_in:
            return error("Sign in first.")
        if path == f"/v1/workspaces/{WORKSPACE}/turns":
            return self.turns.pop(0)()
        if path == f"/v1/workspaces/{WORKSPACE}/upcoming":
            return httpx.Response(200, json=self.upcoming)
        if path == f"/v1/items/{ITEM}":
            return httpx.Response(200, json=ITEM_DETAIL)
        if path == f"/v1/turns/{TURN}/undo":
            return httpx.Response(
                200, json={"id": "44", "status": "completed", "output": "Undone: 1 change."}
            )
        return httpx.Response(404, json={"error": {"code": "not_found", "message": "not found"}})


ITEM_DETAIL = {
    "item": {
        "id": ITEM,
        "title": "Kabir's birthday",
        "kind": "plan",
        "subtype": None,
        "state": "scheduled",
        "text": "Kabir's birthday is on 14 March.",
        "occurred_start": "2027-03-14T00:00:00Z",
        "due_at": None,
    },
    "entities": [{"name": "Kabir"}],
}
