"""FastAPI application factory."""

from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI
from fastapi.openapi.utils import get_openapi
from pydantic.json_schema import models_json_schema

from secondmind.api.errors import install_error_handlers
from secondmind.api.middleware import RequestIdMiddleware
from secondmind.api.routes import auth, memory, system, turns
from secondmind.api.schemas import SSE_EVENTS
from secondmind.api.services import Services
from secondmind.observability import get_logger

log = get_logger(__name__)

API_VERSION = "1.0.0"
SSE_PATH = "/v1/workspaces/{workspace_id}/turns"


def create_app(
    *,
    services: Services | None = None,
    services_factory: Callable[[], Awaitable[Services]] | None = None,
) -> FastAPI:
    """Build the app. Pass ready ``services`` (tests) or a factory run at start-up."""

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        owned: Services | None = None
        if getattr(app.state, "services", None) is None and services_factory is not None:
            owned = await services_factory()
            app.state.services = owned
            log.info(
                "api.started",
                env=owned.config.settings.env,
                config_hash=owned.config.config_hash_short,
                provider_mode=owned.config.routing.mode,
                substitutions=owned.config.routing.substitutions,
            )
        try:
            yield
        finally:
            if owned is not None:
                await owned.aclose()
                log.info("api.stopped")

    app = FastAPI(
        title="2nd Mind API",
        version=API_VERSION,
        description="Chat-first personal memory. The web app is one client of this API.",
        lifespan=lifespan,
        generate_unique_id_function=lambda route: route.name,
        docs_url="/docs/api",
        redoc_url=None,
    )
    if services is not None:
        app.state.services = services
    app.add_middleware(RequestIdMiddleware)
    install_error_handlers(app)
    app.include_router(system.router)
    app.include_router(auth.router)
    app.include_router(turns.router)
    app.include_router(memory.router)
    app.openapi = lambda: build_openapi(app)  # type: ignore[method-assign]
    return app


def build_openapi(app: FastAPI) -> dict[str, Any]:
    """OpenAPI from the code, plus the SSE frame schemas for the streaming turn endpoint."""
    if app.openapi_schema:
        return app.openapi_schema
    schema = get_openapi(
        title=app.title,
        version=app.version,
        description=app.description,
        routes=app.routes,
    )
    _, defs = models_json_schema(
        [(model, "serialization") for model in SSE_EVENTS.values()],
        ref_template="#/components/schemas/{model}",
    )
    components = schema.setdefault("components", {}).setdefault("schemas", {})
    for name, definition in defs.get("$defs", {}).items():
        components.setdefault(name, definition)
    frames = []
    for event, model in SSE_EVENTS.items():
        frames.append(
            {
                "type": "object",
                "title": f"{model.__name__}Frame",
                "required": ["event", "data"],
                "properties": {
                    "event": {"type": "string", "const": event},
                    "data": {"$ref": f"#/components/schemas/{model.__name__}"},
                },
            }
        )
    components["TurnStreamFrame"] = {
        "title": "TurnStreamFrame",
        "description": "One server-sent event on the turn stream: `event:` name, `data:` JSON.",
        "oneOf": frames,
    }
    operation = schema["paths"][SSE_PATH]["post"]
    operation["responses"]["200"]["content"] = {
        "text/event-stream": {"schema": {"$ref": "#/components/schemas/TurnStreamFrame"}}
    }
    app.openapi_schema = schema
    return schema
