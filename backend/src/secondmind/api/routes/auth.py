"""Dev-only auth (DEV_AUTH=true): a dev user and their private workspace, via a signed cookie.
Refused at start-up when ENV=production. Real auth replaces this in S4/S6."""

from fastapi import APIRouter, Response

from secondmind.api.deps import SESSION_COOKIE, ServicesDep, UserIdDep
from secondmind.api.errors import ERROR_RESPONSES
from secondmind.api.routes.turns import usage_for
from secondmind.api.schemas import DevLoginIn, MeOut, UsageOut
from secondmind.core import NotFoundError, UnauthenticatedError

router = APIRouter(prefix="/v1", tags=["auth"])


@router.post("/auth/dev-login", response_model=MeOut, responses=ERROR_RESPONSES)
async def dev_login(
    services: ServicesDep, response: Response, body: DevLoginIn | None = None
) -> MeOut:
    """Create or reuse the dev user (or the one named in the body) and their private workspace;
    set the session cookie."""
    settings = services.config.settings
    if not settings.dev_auth:
        raise NotFoundError("not found")
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
    """The signed-in user's token quota: tier, limit, used and remaining (FR-12.5).
    Read only: enforcement arrives in S4."""
    return await usage_for(services, user_id)
