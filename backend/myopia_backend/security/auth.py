from __future__ import annotations

import base64
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import json
import os
from typing import Any


def _b64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64url_decode(raw: str) -> bytes:
    pad = "=" * (-len(raw) % 4)
    return base64.urlsafe_b64decode(f"{raw}{pad}")


def hash_password(password: str, iterations: int = 260_000) -> str:
    plain = str(password or "")
    if len(plain) < 8:
        raise ValueError("password must be at least 8 characters")
    salt = os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", plain.encode("utf-8"), salt, iterations)
    return f"pbkdf2_sha256${iterations}${_b64url_encode(salt)}${_b64url_encode(digest)}"


def verify_password(password: str, password_hash: str | None) -> bool:
    if not password_hash:
        return False
    parts = str(password_hash).split("$")
    if len(parts) != 4:
        return False
    algo, iterations_s, salt_s, digest_s = parts
    if algo != "pbkdf2_sha256":
        return False
    try:
        iterations = int(iterations_s)
        salt = _b64url_decode(salt_s)
        expected = _b64url_decode(digest_s)
    except Exception:
        return False
    actual = hashlib.pbkdf2_hmac("sha256", str(password or "").encode("utf-8"), salt, iterations)
    return hmac.compare_digest(actual, expected)


@dataclass(frozen=True)
class AuthTokenPayload:
    sub: int
    username: str
    role: str
    exp: int

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AuthTokenPayload":
        return cls(
            sub=int(data["sub"]),
            username=str(data["username"]),
            role=str(data["role"]),
            exp=int(data["exp"]),
        )


def create_access_token(
    *,
    user_id: int,
    username: str,
    role: str,
    secret: str,
    ttl_minutes: int,
) -> str:
    now = datetime.now(tz=timezone.utc)
    payload = {
        "sub": int(user_id),
        "username": str(username),
        "role": str(role),
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=max(1, int(ttl_minutes)))).timestamp()),
    }
    payload_raw = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    payload_b64 = _b64url_encode(payload_raw)
    sig = hmac.new(secret.encode("utf-8"), payload_b64.encode("ascii"), hashlib.sha256).digest()
    return f"{payload_b64}.{_b64url_encode(sig)}"


def parse_access_token(token: str, *, secret: str) -> AuthTokenPayload:
    value = str(token or "").strip()
    if "." not in value:
        raise ValueError("invalid token format")
    payload_b64, sig_b64 = value.split(".", 1)
    expected_sig = hmac.new(
        secret.encode("utf-8"),
        payload_b64.encode("ascii"),
        hashlib.sha256,
    ).digest()
    sig = _b64url_decode(sig_b64)
    if not hmac.compare_digest(sig, expected_sig):
        raise ValueError("invalid token signature")

    payload = json.loads(_b64url_decode(payload_b64).decode("utf-8"))
    token_payload = AuthTokenPayload.from_dict(payload)
    now_ts = int(datetime.now(tz=timezone.utc).timestamp())
    if token_payload.exp < now_ts:
        raise ValueError("token expired")
    return token_payload

