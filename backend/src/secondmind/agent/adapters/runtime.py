"""Wiring shared by the API and the worker: database, model router (with the offline fake brain),
tracer, memory and the turn runner. Composition roots call :func:`build_runtime`."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path

from secondmind.agent import EntitiesRenamed, TurnCompletedHook, TurnRunner
from secondmind.agent.adapters.store import SqlTurnStore
from secondmind.config import AppConfig, Settings
from secondmind.core import ConfigError, WorkspaceScope
from secondmind.ingestion import IngestSettings, load_replay, offline_responders
from secondmind.memory import Memory, MemorySettings
from secondmind.memory.adapters import Database, embedding_dimensions, sql_memory
from secondmind.observability import Tracer, get_logger
from secondmind.observability.adapters import build_tracer
from secondmind.providers import FakeScript, ModelRouter
from secondmind.providers.adapters import build_router
from secondmind.retrieval import (
    RecallSettings,
    load_recall_replay,
    recall_responders,
    recall_text_responders,
)
from secondmind.retrieval.adapters import SqlConversationStore, SqlRecallStore

log = get_logger(__name__)

REPLAY_DIR = Path("evals") / "cases" / "ingest"
RECALL_REPLAY_DIR = Path("evals") / "cases" / "retrieval"


@dataclass
class Runtime:
    config: AppConfig
    db: Database
    router: ModelRouter
    tracer: Tracer
    memory: Memory
    runner: TurnRunner

    async def aclose(self) -> None:
        await self.runner.aclose()
        await self.router.aclose()
        self.tracer.shutdown()
        await self.db.dispose()


def memory_settings(settings: Settings) -> MemorySettings:
    return MemorySettings(
        bulk_threshold=settings.policy_bulk_threshold,
        core_token_budget=settings.core_token_budget,
        quick_horizon_days=settings.quick_horizon_days,
        quick_recent_days=settings.quick_recent_days,
        verbal_keys_enabled=settings.verbal_keys_enabled,
        quick_frequent_min=settings.quick_frequent_min,
    )


def recall_settings(settings: Settings) -> RecallSettings:
    return RecallSettings(
        soft_channel_enabled=settings.soft_channel_enabled,
        soft_channel_k=settings.soft_channel_k,
        rrf_k=settings.rrf_k,
        history_demotion=settings.history_demotion,
        rerank_enabled=settings.rerank_enabled,
        rerank_top_n=settings.rerank_top_n,
        rerank_min_score=settings.rerank_min_score,
        answer_top_k=settings.answer_top_k,
        list_max_items=settings.list_max_items,
        tool_timeout_ms=settings.tool_timeout_ms,
        count_check_min_score=settings.count_check_min_score,
    )


def ingest_settings(settings: Settings) -> IngestSettings:
    return IngestSettings(
        reconcile_threshold=settings.reconcile_similarity_threshold,
        cue_keys_max=settings.cue_keys_max,
        enrich_enabled=settings.enrich_enabled,
        quick_horizon_days=settings.quick_horizon_days,
        quick_recent_days=settings.quick_recent_days,
    )


def fake_script(settings: Settings) -> FakeScript:
    """Unscripted fake calls replay the golden ingestion and recall cases, else use honest
    heuristics."""
    recall = load_recall_replay(settings.resources_dir / RECALL_REPLAY_DIR)
    return FakeScript(
        responders=offline_responders(load_replay(settings.resources_dir / REPLAY_DIR))
        | recall_responders(recall),
        text_responders=recall_text_responders(),
    )


def build_runtime(
    config: AppConfig,
    *,
    on_entities_renamed: EntitiesRenamed | None = None,
    on_turn_completed: TurnCompletedHook | None = None,
    script: FakeScript | None = None,
) -> Runtime:
    settings = config.settings
    db = Database(str(settings.database_url), pool_size=settings.database_pool_size)
    router = build_router(config, fake_script=script or fake_script(settings))
    tracer = build_tracer(settings)
    memory = Memory(sql_memory(db), memory_settings(settings))

    def stores(scope: WorkspaceScope) -> SqlTurnStore:
        return SqlTurnStore(db, scope)

    def recall_stores(scope: WorkspaceScope) -> SqlRecallStore:
        # Tools time out on their own; the statement timeout stops runaway SQL behind them.
        return SqlRecallStore(db, scope, timeout_ms=settings.tool_timeout_ms, rrf_k=settings.rrf_k)

    def conversation_stores(scope: WorkspaceScope) -> SqlConversationStore:
        return SqlConversationStore(db, scope)

    runner = TurnRunner(
        router=router,
        prompts=config.prompts,
        stores=stores,
        tracer=tracer,
        config_hash=config.config_hash,
        max_message_chars=settings.max_message_chars,
        memory=memory,
        ingest=ingest_settings(settings),
        embed_dimensions=settings.embed_dimensions,
        on_entities_renamed=on_entities_renamed,
        recall_stores=recall_stores,
        recall=recall_settings(settings),
        trigger_threshold=settings.trigger_similarity_threshold,
        conversation_stores=conversation_stores,
        on_turn_completed=on_turn_completed,
    )
    return Runtime(config=config, db=db, router=router, tracer=tracer, memory=memory, runner=runner)


async def check_embedding_dimensions(db: Database, expected: int) -> str | None:
    """A problem message when the migrated vector column doesn't match EMBED_DIMENSIONS."""
    actual = await embedding_dimensions(db)
    if actual is not None and actual != expected:
        return (
            f"EMBED_DIMENSIONS={expected} but memory_keys.embedding is vector({actual}); "
            "migrate with the same EMBED_DIMENSIONS"
        )
    return None


async def require_embedding_dimensions(db: Database, expected: int) -> None:
    """Start-up fails on a mismatch; an unreachable database is left to the readiness check."""
    try:
        problem = await check_embedding_dimensions(db, expected)
    except Exception:
        log.warning("startup.embedding_check_skipped")
        return
    if problem:
        raise ConfigError(problem)


Enqueue = Callable[..., Awaitable[object]]
