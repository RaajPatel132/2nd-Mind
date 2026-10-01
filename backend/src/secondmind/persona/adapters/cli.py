"""``python -m secondmind.persona.adapters seed``: load the Aditi Rao seed into the template
workspace (``make seed-persona``, and the deploy after a migration). Nothing happens when the
template already holds this version of the seed file. The embeddings of the template's keys are
real (the configured provider) and are recorded on the ledger as the app's own usage."""

import argparse
import asyncio
import sys
from collections.abc import Sequence

from secondmind.agent.adapters import SqlTurnStore, build_runtime
from secondmind.auth.adapters import SqlIdentityStore
from secondmind.config import Step, load_app_config
from secondmind.core import WorkspaceScope
from secondmind.links.adapters import SqlLinkStore
from secondmind.memory.adapters import EMBED_DIMENSIONS
from secondmind.persona import SEED_FILE, TemplateDeps, load_persona, seed_hash, seed_template
from secondmind.persona.adapters.store import SqlPersonaStore
from secondmind.providers import ModelCall
from secondmind.retrieval.adapters import SqlConversationStore


async def _seed(force: bool) -> int:
    config = load_app_config()
    runtime = build_runtime(config)
    try:
        route = runtime.router.route(Step.EMBED)
        model = f"{route.primary.provider}:{route.primary.model}@{EMBED_DIMENSIONS}"
        calls: list[ModelCall] = []

        async def embed(texts: Sequence[str], hits: int = 0) -> list[list[float]] | None:
            if not texts:
                return []
            result = await runtime.router.embed(list(texts), dimensions=EMBED_DIMENSIONS)
            calls.append(result.call)
            return result.vectors

        db = runtime.db
        deps = TemplateDeps(
            identity=SqlIdentityStore(db),
            store=SqlPersonaStore(db),
            memory=runtime.memory,
            turns=lambda scope: SqlTurnStore(db, scope),
            conversation=lambda scope: SqlConversationStore(db, scope),
            sources=lambda scope: SqlLinkStore(db, scope),
            embed=embed,
            embedding_model=model,
        )
        # An installed package isn't next to the seed: the resources directory says where it is.
        path = config.settings.resources_dir / SEED_FILE
        seed = load_persona(path)
        template, seeded = await seed_template(deps, seed, hash_of=seed_hash(path), force=force)
        if seeded is None:
            sys.stdout.write(
                f"The persona template is up to date (seed {seed.id} v{seed.version}).\n"
            )
            return 0
        scope = WorkspaceScope(workspace_id=template.id, user_id=template.owner_user_id)
        store = SqlTurnStore(db, scope)
        for call in calls:  # the app's cost, never a visitor's
            await store.record_usage(seeded.system_turn, call.to_event(), system=True)
        cost = sum(call.usage.cost_usd for call in calls)
        sys.stdout.write(
            f"Loaded the persona template: {len(seeded.items)} memories, "
            f"{len(seeded.entities) - 1} entities, {len(seeded.turns)} past chats; "
            f"{len(calls)} embedding calls, ${cost:.4f}.\n"
        )
        return 0
    finally:
        await runtime.aclose()


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m secondmind.persona.adapters")
    sub = parser.add_subparsers(dest="command", required=True)
    seed = sub.add_parser("seed", help="load the persona seed into the template workspace")
    seed.add_argument("--force", action="store_true", help="reload even if the file is unchanged")
    args = parser.parse_args(argv)
    if args.command == "seed":
        return asyncio.run(_seed(args.force))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
