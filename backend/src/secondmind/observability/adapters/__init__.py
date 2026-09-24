"""Trace backend adapters."""

from secondmind.config import Settings
from secondmind.observability import NullTracer, Tracer
from secondmind.observability.adapters.langfuse_tracer import LangfuseTracer


def build_tracer(settings: Settings) -> Tracer:
    """Langfuse when configured and enabled; otherwise a no-op tracer."""
    if not (
        settings.tracing_configured
        and settings.langfuse_host
        and settings.langfuse_public_key
        and settings.langfuse_secret_key
    ):
        return NullTracer()
    return LangfuseTracer(
        host=str(settings.langfuse_host),
        public_key=settings.langfuse_public_key,
        secret_key=settings.langfuse_secret_key.get_secret_value(),
        ui_url=str(settings.langfuse_ui_url) if settings.langfuse_ui_url else None,
        project_id=settings.langfuse_project_id,
        environment=settings.env,
        release=settings.app_version,
        include_content=settings.trace_include_content,
    )


__all__ = ["LangfuseTracer", "build_tracer"]
