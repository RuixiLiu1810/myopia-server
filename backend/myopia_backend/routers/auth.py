from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select

from ..db.models import AuditLog, User
from ..db.session import session_scope
from ..dependencies.rbac import AuthContext, get_current_user
from ..schemas import ChangePasswordRequest, LoginRequest, LoginResponse, UserOut
from ..security.auth import create_access_token, hash_password, verify_password


def _user_out(user: User) -> dict:
    return {
        "id": int(user.id),
        "username": user.username,
        "display_name": user.display_name,
        "role": user.role,
        "is_active": bool(user.is_active),
        "last_login_at": user.last_login_at.isoformat() if user.last_login_at else None,
        "created_at": user.created_at.isoformat(),
        "updated_at": user.updated_at.isoformat(),
    }


def build_auth_router(settings) -> APIRouter:
    router = APIRouter(prefix="/v1/auth", tags=["auth"])

    @router.post("/login", response_model=LoginResponse)
    def login(req: LoginRequest):
        username = req.username.strip().lower()
        if not username:
            raise HTTPException(status_code=400, detail="username cannot be empty")
        if not req.password.strip():
            raise HTTPException(status_code=400, detail="password cannot be empty")

        with session_scope() as session:
            user = session.execute(select(User).where(User.username == username)).scalar_one_or_none()
            if user is None or not bool(user.is_active):
                raise HTTPException(status_code=401, detail="invalid username or password")
            if not user.password_hash:
                raise HTTPException(status_code=403, detail="user does not support password login")
            if not verify_password(req.password, user.password_hash):
                raise HTTPException(status_code=401, detail="invalid username or password")

            user.last_login_at = datetime.now(tz=timezone.utc)
            session.add(
                AuditLog(
                    action="auth.login",
                    actor=user.username,
                    target_type="user",
                    target_id=str(user.id),
                    detail_json={"role": user.role},
                )
            )

            token = create_access_token(
                user_id=int(user.id),
                username=user.username,
                role=user.role,
                secret=settings.auth_secret,
                ttl_minutes=settings.auth_token_ttl_minutes,
            )
            return {
                "access_token": token,
                "token_type": "bearer",
                "expires_in": int(settings.auth_token_ttl_minutes) * 60,
                "role": user.role,
                "username": user.username,
            }

    @router.get("/me", response_model=UserOut)
    def me(ctx: AuthContext = Depends(get_current_user)):
        with session_scope() as session:
            user = session.get(User, int(ctx.user_id))
            if user is None:
                raise HTTPException(status_code=404, detail="user not found")
            return _user_out(user)

    @router.post("/logout")
    def logout(ctx: AuthContext = Depends(get_current_user)):
        with session_scope() as session:
            session.add(
                AuditLog(
                    action="auth.logout",
                    actor=ctx.username,
                    target_type="user",
                    target_id=str(ctx.user_id),
                    detail_json=None,
                )
            )
        return {"ok": True}

    @router.post("/change-password")
    def change_password(req: ChangePasswordRequest, ctx: AuthContext = Depends(get_current_user)):
        old_password = req.old_password or ""
        new_password = req.new_password or ""
        if not old_password.strip():
            raise HTTPException(status_code=400, detail="old_password cannot be empty")
        if not new_password.strip():
            raise HTTPException(status_code=400, detail="new_password cannot be empty")

        with session_scope() as session:
            user = session.get(User, int(ctx.user_id))
            if user is None:
                raise HTTPException(status_code=404, detail="user not found")
            if not user.password_hash:
                raise HTTPException(status_code=403, detail="user does not support password login")
            if not verify_password(old_password, user.password_hash):
                raise HTTPException(status_code=401, detail="invalid current password")
            if old_password == new_password:
                raise HTTPException(status_code=400, detail="new password must differ from current password")
            try:
                user.password_hash = hash_password(new_password)
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            session.add(
                AuditLog(
                    action="auth.change_password",
                    actor=ctx.username,
                    target_type="user",
                    target_id=str(user.id),
                    detail_json=None,
                )
            )
        return {"ok": True}

    return router
