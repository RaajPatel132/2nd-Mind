"""HTTP API (FastAPI): routes, schemas, SSE, and the composition root that wires adapters."""

from secondmind.api.app import create_app
from secondmind.api.services import CheckResult, Services, build_services

__all__ = ["CheckResult", "Services", "build_services", "create_app"]
