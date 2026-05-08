"""
JWT access token creation and verification utilities for the SampleAgent GitHub backend.
Tokens are signed with the application secret key using the configured algorithm (HS256 by default)
and carry a configurable expiry enforced at decode time.

SampleAgent GitHubバックエンド用のJWTアクセストークン生成および検証ユーティリティ。
設定済みアルゴリズム（デフォルトHS256）でアプリケーション秘密鍵によりトークンを署名し、
デコード時に設定された有効期限を強制する。
"""
from datetime import datetime, timedelta
from jose import JWTError, jwt
from typing import Optional
from ..config import settings


def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    to_encode = data.copy()
    expire = datetime.utcnow() + (
        expires_delta or timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    )
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, settings.SECRET_KEY, algorithm=settings.ALGORITHM)


def verify_token(token: str) -> Optional[dict]:
    try:
        payload = jwt.decode(
            token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM]
        )
        return payload
    except JWTError:
        return None
