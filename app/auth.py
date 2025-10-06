import os
from typing import Optional

from fastapi import Depends, HTTPException, Request, status
from itsdangerous import BadSignature, Signer
from passlib.context import CryptContext
from sqlalchemy import select
from sqlalchemy.orm import Session

from .db import get_session
from .models import User


# Use PBKDF2-SHA256 to avoid platform-specific bcrypt issues/warnings.
# You can later add "bcrypt" to schemes for backward-compat if needed.
pwd_context = CryptContext(schemes=["pbkdf2_sha256"], deprecated="auto")


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    return pwd_context.verify(password, password_hash)


def get_current_user(
    request: Request, db: Session = Depends(get_session)
) -> Optional[User]:
    user_id = request.session.get("user_id")
    if not user_id:
        return None
    user = db.get(User, int(user_id))
    return user


def require_user(
    user: Optional[User] = Depends(get_current_user),
):
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)
    return user


def is_admin_username(username: str) -> bool:
    admins = os.getenv("SYNO_ADMIN_USERS", "").split(",")
    admins = [a.strip() for a in admins if a.strip()]
    return username in admins


def get_is_admin(user: Optional[User] = Depends(get_current_user)) -> bool:
    if not user:
        return False
    return is_admin_username(user.username)


def require_admin(user: Optional[User] = Depends(get_current_user)):
    if not user or not is_admin_username(user.username):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN)
    return user
