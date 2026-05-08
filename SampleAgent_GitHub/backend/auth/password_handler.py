"""
Password hashing and verification utilities for the SampleAgent GitHub backend using bcrypt.
Provides a passlib CryptContext configured with bcrypt to securely hash and verify
user passwords at registration and login time.

SampleAgent GitHubバックエンド向けのbcryptを使用したパスワードハッシュ化および検証ユーティリティ。
passlibのCryptContextをbcryptで構成し、ユーザー登録およびログイン時のパスワードを
安全にハッシュ化・検証する。
"""
from passlib.context import CryptContext

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)
