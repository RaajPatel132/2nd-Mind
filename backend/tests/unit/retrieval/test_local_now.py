"""Found in R.3 (B1): the rerank prompt showed "now" in the server's timezone."""

from datetime import UTC, datetime

from secondmind.retrieval.select import local_now


def test_the_rerank_prompt_gets_the_workspace_wall_clock() -> None:
    now = datetime(2026, 9, 28, 18, 30, tzinfo=UTC)
    assert local_now(now, "Asia/Kolkata") == "2026-09-29T00:00+05:30"
    assert local_now(now, "America/New_York") == "2026-09-28T14:30-04:00"
