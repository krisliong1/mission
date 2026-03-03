"""
Authentication utilities for AudioToText system.
JWT token generation, password hashing, and auth dependencies.
"""
import jwt
import bcrypt
from datetime import datetime, timedelta
from typing import Optional
from fastapi import HTTPException, Depends, Request
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from database import get_user_by_id, is_valid_session

# ── Configuration ────────────────────────────────────────────────────────
JWT_SECRET = "audiototext-secret-key-change-in-production-2026"
JWT_ALGORITHM = "HS256"
JWT_EXPIRATION_DAYS = 7

security = HTTPBearer(auto_error=False)


def hash_password(password: str) -> str:
    """Hash password using bcrypt."""
    salt = bcrypt.gensalt()
    hashed = bcrypt.hashpw(password.encode('utf-8'), salt)
    return hashed.decode('utf-8')


def verify_password(password: str, password_hash: str) -> bool:
    """Verify password against bcrypt hash."""
    try:
        return bcrypt.checkpw(password.encode('utf-8'), password_hash.encode('utf-8'))
    except Exception:
        return False


def create_jwt_token(user_id: int) -> str:
    """Create JWT token for user (7-day expiration, HS256)."""
    now = datetime.utcnow()
    payload = {
        "sub": user_id,  # "sub" is standard JWT claim for subject (user_id)
        "iat": now,
        "exp": now + timedelta(days=JWT_EXPIRATION_DAYS)
    }
    token = jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)
    return token


def decode_jwt_token(token: str) -> Optional[int]:
    """Decode JWT token and return user_id if valid."""
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        user_id = payload.get("sub")
        if user_id:
            return int(user_id)
        return None
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expired")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid token")


async def get_current_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials = Depends(security)
) -> dict:
    """
    FastAPI dependency to get current authenticated user.
    Raises 401 if not authenticated.
    """
    if not credentials:
        # Return login_url for unauthenticated requests
        raise HTTPException(
            status_code=401,
            detail="Authentication required",
            headers={"login_url": "/login"}
        )
    
    token = credentials.credentials
    user_id = decode_jwt_token(token)
    
    if not user_id:
        raise HTTPException(status_code=401, detail="Invalid token")
    
    # Verify session is not invalidated (logout check)
    if not is_valid_session(token):
        raise HTTPException(status_code=401, detail="Session invalidated")
    
    user = get_user_by_id(user_id)
    if not user:
        raise HTTPException(status_code=401, detail="User not found")
    
    return user


async def get_current_user_optional(
    request: Request,
    credentials: HTTPAuthorizationCredentials = Depends(security)
) -> Optional[dict]:
    """
    FastAPI dependency to get current user (optional - returns None if not authenticated).
    """
    if not credentials:
        return None
    
    try:
        token = credentials.credentials
        user_id = decode_jwt_token(token)
        
        if not user_id:
            return None
        
        if not is_valid_session(token):
            return None
        
        user = get_user_by_id(user_id)
        return user
    except HTTPException:
        return None
