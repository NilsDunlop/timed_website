"""Bearer-token authentication shared by every HTTP route."""

import secrets
from collections.abc import Callable
from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

BearerDependency = Callable[[HTTPAuthorizationCredentials | None], None]


def bearer_auth(expected_token: str) -> BearerDependency:
    """Build a dependency which validates one in-memory bearer token."""
    scheme = HTTPBearer(auto_error=False)

    async def require_token(
        credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(scheme)],
    ) -> None:
        if credentials is None or credentials.scheme.lower() != "bearer":
            raise _unauthorized()
        if not secrets.compare_digest(credentials.credentials, expected_token):
            raise _unauthorized()

    return require_token


def _unauthorized() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid or missing bearer token",
        headers={"WWW-Authenticate": "Bearer"},
    )
