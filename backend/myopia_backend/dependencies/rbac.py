from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db.models import User
from ..db.session import get_default_session_factory
from ..security.auth import parse_access_token


bearer_scheme = HTTPBearer(auto_error=False)


@dataclass(frozen=True)
class AuthContext:
    user_id: int
    username: str
    role: str


def _get_db_session() -> Session:
    factory = get_default_session_factory()
    session = factory()
    try:
        yield session
    finally:
        session.close()


def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    session: Session = Depends(_get_db_session),
) -> AuthContext:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="missing bearer token")

    settings = get_settings()
    try:
        payload = parse_access_token(credentials.credentials, secret=settings.auth_secret)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc

    user = session.get(User, int(payload.sub))
    if user is None or not bool(user.is_active):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="user inactive or not found")
    return AuthContext(user_id=int(user.id), username=user.username, role=user.role)


def require_roles(*roles: str) -> Callable[[AuthContext], AuthContext]:
    role_set = {str(r).strip().lower() for r in roles if str(r).strip()}

    def _dependency(ctx: AuthContext = Depends(get_current_user)) -> AuthContext:
        if role_set and str(ctx.role).strip().lower() not in role_set:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="insufficient role")
        return ctx

    return _dependency

