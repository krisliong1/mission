"""
middleware.py - Free tier usage limiting middleware
Checks daily transcription quota for authenticated and anonymous users.
"""
from datetime import date
from typing import Optional
from fastapi import Request, HTTPException
from collections import defaultdict
import time

# ── Anonymous IP rate tracking (in-memory, resets on restart) ─────────────────
_ip_usage: dict = defaultdict(lambda: {"count": 0, "date": None})

FREE_DAILY_LIMIT = 3
PRO_DAILY_LIMIT = None  # unlimited


def get_today() -> str:
    return str(date.today())


def check_ip_limit(ip: str) -> dict:
    """Track anonymous users by IP. Returns status dict."""
    today = get_today()
    record = _ip_usage[ip]

    # Reset count if new day
    if record["date"] != today:
        record["count"] = 0
        record["date"] = today

    used = record["count"]
    if used >= FREE_DAILY_LIMIT:
        return {
            "allowed": False,
            "used": used,
            "limit": FREE_DAILY_LIMIT,
            "error": "free_limit_reached",
            "message": f"Daily limit of {FREE_DAILY_LIMIT} transcriptions reached. Upgrade to Pro for unlimited access.",
            "upgrade_url": "/upgrade",
        }
    return {"allowed": True, "used": used, "limit": FREE_DAILY_LIMIT}


def increment_ip_usage(ip: str):
    """Increment anonymous IP usage counter."""
    today = get_today()
    record = _ip_usage[ip]
    if record["date"] != today:
        record["count"] = 0
        record["date"] = today
    record["count"] += 1


def check_user_limit(user_id: int, plan: str, db) -> dict:
    """Check usage for authenticated user from DB."""
    today = get_today()

    # Count today's usage
    row = db.execute(
        "SELECT COUNT(*) FROM usage WHERE user_id=? AND date=?",
        (user_id, today)
    ).fetchone()
    used = row[0] if row else 0

    if plan == "pro":
        return {"allowed": True, "used": used, "limit": None, "plan": "pro"}

    # Free plan
    if used >= FREE_DAILY_LIMIT:
        return {
            "allowed": False,
            "used": used,
            "limit": FREE_DAILY_LIMIT,
            "plan": "free",
            "error": "free_limit_reached",
            "message": f"Daily limit of {FREE_DAILY_LIMIT} transcriptions reached. Upgrade to Pro for unlimited access.",
            "upgrade_url": "/upgrade",
        }
    return {"allowed": True, "used": used, "limit": FREE_DAILY_LIMIT, "plan": "free"}


def record_usage(user_id: Optional[int], db, media_type: str = "audio"):
    """Record a successful transcription in the usage table."""
    today = get_today()
    if user_id:
        db.execute(
            "INSERT INTO usage (user_id, date, count, media_type) VALUES (?, ?, 1, ?)",
            (user_id, today, media_type)
        )
        db.commit()
