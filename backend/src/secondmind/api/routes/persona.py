"""The sample persona (S4.11, FR-13.1): a personal copy of Aditi Rao's memory for the caller, moved
to today. Opening, copying or resetting it never calls a model."""

from fastapi import APIRouter

from secondmind.api.deps import ServicesDep, UserIdDep
from secondmind.api.errors import ERROR_RESPONSES, RATE_LIMITED_RESPONSE
from secondmind.api.schemas import WorkspaceOut
from secondmind.core import NotFoundError, RateLimitedError
from secondmind.persona import PersonaService

router = APIRouter(prefix="/v1", tags=["persona"])

PER_MINUTE = 6  # a copy is a second or so of database work: enough to reset a few times


def _persona(services: ServicesDep) -> PersonaService:
    if services.persona is None:
        raise NotFoundError("The sample persona isn't available.")
    return services.persona


async def _limited(services: ServicesDep, user_id: object) -> None:
    wait = await services.gate.rate_limited(f"persona:{user_id}", PER_MINUTE)
    if wait is not None:
        raise RateLimitedError("That was a lot of copies. Wait a moment.", wait)


@router.post(
    "/persona", response_model=WorkspaceOut, responses={**ERROR_RESPONSES, **RATE_LIMITED_RESPONSE}
)
async def open_persona(services: ServicesDep, user_id: UserIdDep) -> WorkspaceOut:
    """The caller's copy of the sample persona, made the first time and the same one after."""
    persona = _persona(services)
    await _limited(services, user_id)
    try:
        return WorkspaceOut.of(await persona.copy_for(user_id))
    except LookupError as exc:
        raise NotFoundError("The sample persona isn't loaded yet. Try again in a minute.") from exc


@router.post(
    "/persona/reset",
    response_model=WorkspaceOut,
    responses={**ERROR_RESPONSES, **RATE_LIMITED_RESPONSE},
)
async def reset_persona(services: ServicesDep, user_id: UserIdDep) -> WorkspaceOut:
    """Replace the caller's copy with a fresh one, moved to today."""
    persona = _persona(services)
    await _limited(services, user_id)
    try:
        return WorkspaceOut.of(await persona.reset_for(user_id))
    except LookupError as exc:
        raise NotFoundError("The sample persona isn't loaded yet. Try again in a minute.") from exc
