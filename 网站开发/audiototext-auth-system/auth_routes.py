"""
Authentication routes for AudioToText system.
Register, Login, Google OAuth, Logout, Me, Refresh, Forgot-password endpoints.
"""
from fastapi import APIRouter, HTTPException, Depends, Request
from pydantic import BaseModel, validator
from database import get_db, init_db
from auth_utils import hash_password, verify_password, create_jwt_token, get_current_user
from datetime import date
import re

router = APIRouter(prefix="/api/auth", tags=["auth"])


# ── Request Models ─────────────────────────────────────────────────────────

class RegisterRequest(BaseModel):
    name: str
    email: str
    password: str

    @validator("email")
    def validate_email(cls, v):
        pattern = r'^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+$'
        if not re.match(pattern, v):
            raise ValueError("Invalid email format")
        return v.lower()

    @validator("name")
    def validate_name(cls, v):
        if not v or not v.strip():
            raise ValueError("Name is required")
        return v.strip()

    @validator("password")
    def validate_password(cls, v):
        if len(v) < 8:
            raise ValueError("Password must be at least 8 characters")
        return v


class LoginRequest(BaseModel):
    email: str
    password: str


class GoogleAuthRequest(BaseModel):
    id_token: str


class ForgotPasswordRequest(BaseModel):
    email: str


# ── Register ───────────────────────────────────────────────────────────────

@router.post("/register")
def register(req: RegisterRequest):
    db = get_db()
    try:
        existing = db.execute(
            "SELECT id FROM users WHERE email = ?", (req.email,)
        ).fetchone()
        if existing:
            raise HTTPException(status_code=400, detail="Email already registered")

        password_hash = hash_password(req.password)
        cursor = db.execute(
            "INSERT INTO users (name, email, password_hash) VALUES (?, ?, ?)",
            (req.name, req.email, password_hash)
        )
        db.commit()
        user_id = cursor.lastrowid

        # Auto-assign free plan
        db.execute(
            "INSERT INTO subscriptions (user_id, plan) VALUES (?, 'free')",
            (user_id,)
        )
        db.commit()

        token = create_jwt_token({"user_id": user_id, "email": req.email, "name": req.name})
        return {"access_token": token, "token_type": "bearer",
                "user": {"id": user_id, "name": req.name, "email": req.email, "plan": "free"}}
    finally:
        db.close()


# ── Login ──────────────────────────────────────────────────────────────────

@router.post("/login")
def login(req: LoginRequest):
    db = get_db()
    try:
        user = db.execute(
            "SELECT id, name, email, password_hash FROM users WHERE email = ?",
            (req.email.lower(),)
        ).fetchone()

        if not user:
            raise HTTPException(status_code=401, detail="Invalid email or password")

        if not user["password_hash"] or not verify_password(req.password, user["password_hash"]):
            raise HTTPException(status_code=401, detail="Invalid email or password")

        sub = db.execute(
            "SELECT plan FROM subscriptions WHERE user_id = ? ORDER BY id DESC LIMIT 1",
            (user["id"],)
        ).fetchone()
        plan = sub["plan"] if sub else "free"

        token = create_jwt_token({"user_id": user["id"], "email": user["email"], "name": user["name"]})
        return {
            "access_token": token, "token_type": "bearer",
            "user": {"id": user["id"], "name": user["name"], "email": user["email"], "plan": plan}
        }
    finally:
        db.close()


# ── Google OAuth ───────────────────────────────────────────────────────────

@router.post("/google")
def google_auth(req: GoogleAuthRequest, db=Depends(get_db)):
    """Handle Google OAuth — create or link account from Google ID token."""
    # Production: verify with google-auth library
    # Stub: parse JWT payload directly (no signature verification)
    import json as _json
    try:
        parts = req.id_token.split(".")
        pad = len(parts[1]) % 4
        padded = parts[1] + ("=" * (4 - pad if pad else 0))
        import base64 as _b64
        payload = _json.loads(_b64.urlsafe_b64decode(padded).decode())
        email = payload.get("email", "")
        name = payload.get("name", "Google User")
        google_id = payload.get("sub", "")
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid Google ID token")

    if not email:
        raise HTTPException(status_code=400, detail="Email not provided by Google")

    user = db.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
    if user:
        if not user["google_id"]:
            db.execute("UPDATE users SET google_id=? WHERE id=?", (google_id, user["id"]))
            db.commit()
        user_id, user_name = user["id"], user["name"]
    else:
        cursor = db.execute(
            "INSERT INTO users (email, name, google_id, password_hash) VALUES (?,?,?,'')",
            (email, name, google_id)
        )
        db.commit()
        user_id = cursor.lastrowid
        user_name = name
        db.execute("INSERT INTO subscriptions (user_id, plan) VALUES (?, 'free')", (user_id,))
        db.commit()

    token = create_jwt_token({"user_id": user_id, "email": email, "name": user_name})
    return {"access_token": token, "token_type": "bearer",
            "user": {"id": user_id, "email": email, "name": user_name}}


# ── Me ─────────────────────────────────────────────────────────────────────

@router.get("/me")
def me(current_user: dict = Depends(get_current_user)):
    db = get_db()
    try:
        user_id = current_user["id"]
        sub = db.execute(
            "SELECT plan FROM subscriptions WHERE user_id = ? ORDER BY id DESC LIMIT 1",
            (user_id,)
        ).fetchone()
        plan = sub["plan"] if sub else "free"

        today = str(date.today())
        usage = db.execute(
            "SELECT COUNT(*) as count FROM usage WHERE user_id = ? AND date = ?",
            (user_id, today)
        ).fetchone()
        used_today = usage["count"] if usage else 0

        return {
            "id": current_user["id"],
            "name": current_user["name"],
            "email": current_user["email"],
            "plan": plan,
            "usage_today": used_today,
            "daily_limit": 3 if plan == "free" else None
        }
    finally:
        db.close()


# ── Refresh token ──────────────────────────────────────────────────────────

@router.post("/refresh")
def refresh_token(current_user: dict = Depends(get_current_user)):
    """Issue a new JWT for authenticated user."""
    token = create_jwt_token({
        "user_id": current_user["id"],
        "email": current_user["email"],
        "name": current_user["name"],
    })
    return {"access_token": token, "token_type": "bearer"}


# ── Forgot password (stub) ─────────────────────────────────────────────────

@router.post("/forgot-password")
def forgot_password(req: ForgotPasswordRequest):
    """Send password reset email — stub always returns 200."""
    return {"success": True, "message": "If that email exists, a reset link has been sent."}


# ── Logout ─────────────────────────────────────────────────────────────────

@router.post("/logout")
def logout(current_user: dict = Depends(get_current_user)):
    """Invalidate session (client clears token)."""
    return {"message": "Logged out successfully"}
