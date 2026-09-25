"""Dev-only auth (DEV_AUTH=true): a dev user and their private workspace, via a signed cookie.
Refused at start-up when ENV=production. Real auth replaces this in S4/S6."""

from fastapi import APIRouter, Response

from secondmind.api.deps import SESSION_COOKIE, ServicesDep, UserIdDep
from secondmind.api.errors import ERROR_RESPONSES
from secondmind.api.schemas import DevLoginIn, MeOut
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
