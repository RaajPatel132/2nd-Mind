"""Guests (S4.12, ADR-0039): a visitor with no account gets a user with tier ``guest``, a copy of
the sample persona, and a signed device cookie that lets them come back as the same guest.

Limits that follow from the tier: the small lifetime quota, the Auto model only, a lower rate of
turns and of links. Limits that belong here: how many new guests one address may make in a day,
and (until the site opens, ``GUESTS_OPEN``) the access code.
"""

import hmac
from datetime import UTC, datetime, time, timedelta

from fastapi import APIRouter, Request, Response

from secondmind.api.client_address import client_address
from secondmind.api.deps import DEVICE_COOKIE, SESSION_COOKIE, ServicesDep, UserIdDep
from secondmind.api.errors import ERROR_RESPONSES, RATE_LIMITED_RESPONSE
from secondmind.api.schemas import GuestIn, MeOut, WorkspaceOut
from secondmind.auth import User, Workspace, WorkspaceKind
from secondmind.core import (
    ForbiddenError,
    NotFoundError,
    RateLimitedError,
    Tier,
    UnauthenticatedError,
)

router = APIRouter(prefix="/v1", tags=["guests"])

RESPONSES = {**ERROR_RESPONSES, **RATE_LIMITED_RESPONSE}


def _set_cookies(response: Response, services: ServicesDep, user: User) -> None:
    secure = services.config.settings.session_cookie_secure
    response.set_cookie(
        SESSION_COOKIE,
        services.signer.sign(user.id),
        max_age=services.signer.ttl_s,
        httponly=True,
        samesite="lax",
        secure=secure,
        path="/",
    )
    response.set_cookie(
        DEVICE_COOKIE,
        services.device().sign(user.id),
        max_age=services.device().ttl_s,
        httponly=True,
        samesite="lax",
        secure=secure,
        path="/",
    )


async def _check_code(request: Request, services: ServicesDep, body: GuestIn | None) -> None:
    """Until the site opens to guests, making one needs the access code too (S4.12)."""
    settings = services.config.settings
    if settings.guests_open or not settings.access_code_required:
        return
    caller = _address(request, services)
    wait = await services.gate.rate_limited(f"login:{caller}", settings.login_attempts_per_minute)
    if wait is not None:
        raise RateLimitedError("Too many attempts. Wait a moment and try again.", wait)
    expected = settings.access_code
    given = (body.access_code if body is not None and body.access_code else "").encode()
    if expected is None or not hmac.compare_digest(given, expected.get_secret_value().encode()):
        raise UnauthenticatedError("That access code isn't right.")


def _address(request: Request, services: ServicesDep) -> str:
    return client_address(
        request.headers.get("x-forwarded-for"),
        request.client.host if request.client else None,
        services.config.settings.trusted_proxy_hops,
    )


async def _returning(request: Request, services: ServicesDep) -> User | None:
    """The guest this device already is, if its cookie is ours and the guest is still here."""
    token = request.cookies.get(DEVICE_COOKIE)
    user_id = services.device().verify(token) if token else None
    if user_id is None:
        return None
    user = await services.identity.get_user(user_id)
    if user is None or user.tier is not Tier.GUEST:
        return None
    workspaces = await services.identity.workspaces_for(user.id)
    return user if any(w.kind is WorkspaceKind.PERSONA_COPY for w in workspaces) else None


def _seconds_to_midnight() -> float:
    now = datetime.now(UTC)
    tomorrow = datetime.combine(now.date() + timedelta(days=1), time.min, tzinfo=UTC)
    return (tomorrow - now).total_seconds()


@router.post("/guest", response_model=MeOut, responses=RESPONSES)
async def start_guest(
    request: Request, response: Response, services: ServicesDep, body: GuestIn | None = None
) -> MeOut:
    """Start as a guest with a copy of the sample persona, or carry on as the guest this device
    already is. At most ``GUEST_NEW_PER_IP_PER_DAY`` new guests per address per UTC day."""
    settings = services.config.settings
    if services.persona is None:
        raise NotFoundError("The sample persona isn't available.")
    await _check_code(request, services, body)
    returning = await _returning(request, services)
    if returning is not None:
        _set_cookies(response, services, returning)
        return MeOut.of(returning, await services.identity.workspaces_for(returning.id))
    made = await services.gate.count_today(f"guests:{_address(request, services)}")
    if made > settings.guest_new_per_ip_per_day:
        raise RateLimitedError(
            "There are a lot of new guests from your network today. Try again tomorrow, "
            "or sign in.",
            _seconds_to_midnight(),
        )
    user = await services.identity.create_user(email=None, tier=Tier.GUEST)
    try:
        await services.persona.make_copy(user.id)
    except LookupError as exc:
        await services.identity.delete_user(user.id)
        raise NotFoundError("The sample persona isn't loaded yet. Try again in a minute.") from exc
    _set_cookies(response, services, user)
    return MeOut.of(user, await services.identity.workspaces_for(user.id))


@router.post("/scratch", response_model=WorkspaceOut, responses=RESPONSES)
async def open_scratch(services: ServicesDep, user_id: UserIdDep) -> WorkspaceOut:
    """A guest's empty memory of their own, made the first time and the same one after."""
    user = await services.identity.get_user(user_id)
    if user is None:
        raise UnauthenticatedError("Sign in first.")
    if user.tier is not Tier.GUEST:
        raise ForbiddenError("A scratch memory is for guests. Your own memory is already empty.")
    existing: Workspace | None = next(
        (
            w
            for w in await services.identity.workspaces_for(user_id)
            if w.kind is WorkspaceKind.SCRATCH
        ),
        None,
    )
    if existing is None:
        existing = await services.identity.create_workspace(
            owner_user_id=user_id,
            kind=WorkspaceKind.SCRATCH,
            timezone=services.config.settings.default_timezone,
        )
    return WorkspaceOut.of(existing)
