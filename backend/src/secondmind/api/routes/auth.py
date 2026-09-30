"""Dev auth (DEV_AUTH=true): a user and their private workspace, via a signed cookie. Open in
development; in production it is code sign-in (email plus ACCESS_CODE) until S6's accounts."""

import hmac

from fastapi import APIRouter, Request, Response

from secondmind.api.deps import SESSION_COOKIE, ServicesDep, UserIdDep
from secondmind.api.errors import ERROR_RESPONSES, RATE_LIMITED_RESPONSE
from secondmind.api.routes.turns import usage_for
from secondmind.api.schemas import DevLoginIn, MeOut, UsageOut
from secondmind.core import NotFoundError, RateLimitedError, UnauthenticatedError

router = APIRouter(prefix="/v1", tags=["auth"])


@router.post(
    "/auth/dev-login", response_model=MeOut, responses={**ERROR_RESPONSES, **RATE_LIMITED_RESPONSE}
)
async def dev_login(
    request: Request, services: ServicesDep, response: Response, body: DevLoginIn | None = None
) -> MeOut:
    """Create or reuse the dev user (or the one named in the body) and their private workspace;
    set the session cookie. In production the access code is required first (R.11): compared in
    constant time, attempts per address limited, and each email is its own user, so isolation
    still applies between the people who hold the code."""
    settings = services.config.settings
    if not settings.dev_auth:
        raise NotFoundError("not found")
    if settings.access_code_required:
        await _check_access_code(request, services, body)
    email = body.email if body is not None and body.email else settings.dev_user_email
    user, workspace = await services.identity.ensure_user_with_private_workspace(
        email=email, timezone=settings.default_timezone
    )
    response.set_cookie(
        SESSION_COOKIE,
        services.signer.sign(user.id),
        max_age=services.signer.ttl_s,
        httponly=True,
        samesite="lax",
        secure=settings.session_cookie_secure,
        path="/",
    )
    return MeOut.of(user, [workspace])


async def _check_access_code(
    request: Request, services: ServicesDep, body: DevLoginIn | None
) -> None:
    settings = services.config.settings
    caller = request.client.host if request.client else "unknown"
    wait = await services.gate.rate_limited(f"login:{caller}", settings.login_attempts_per_minute)
    if wait is not None:
        raise RateLimitedError("Too many attempts. Wait a moment and try again.", wait)
    expected = settings.access_code
    given = (body.access_code if body is not None and body.access_code else "").encode()
    # Always compare, whatever was sent, so the time taken says nothing about the code.
    ok = expected is not None and hmac.compare_digest(given, expected.get_secret_value().encode())
    if not ok:
        raise UnauthenticatedError("That access code isn't right.")


@router.post("/auth/logout", status_code=204)
async def logout(response: Response) -> None:
    response.delete_cookie(SESSION_COOKIE, path="/")


@router.get("/me", response_model=MeOut, responses=ERROR_RESPONSES)
async def me(services: ServicesDep, user_id: UserIdDep) -> MeOut:
    user = await services.identity.get_user(user_id)
    if user is None:
        raise UnauthenticatedError("Sign in first.")
    return MeOut.of(user, await services.identity.workspaces_for(user_id))


@router.get("/me/usage", response_model=UsageOut, responses=ERROR_RESPONSES)
async def my_usage(services: ServicesDep, user_id: UserIdDep) -> UsageOut:
    """The signed-in user's quota in dollars (tier, limit, used, remaining) and whether new
    turns are stopped, and why (FR-12.5, ADR-0032)."""
    return await usage_for(services, user_id)
