"""
subscription_routes.py - Subscription management endpoints
Handles plan status, upgrade, and cancellation.
"""
from datetime import date, timedelta
from fastapi import APIRouter, Depends, HTTPException
from auth_utils import get_current_user
from database import get_db

router = APIRouter(prefix="/api/subscription", tags=["subscription"])


@router.get("/status")
def get_subscription_status(current_user=Depends(get_current_user), db=Depends(get_db)):
    """Get current user's subscription status and daily usage."""
    today = str(date.today())
    user_id = current_user["id"]

    # Get subscription info
    sub = db.execute(
        "SELECT plan, expires_at FROM subscriptions WHERE user_id=? ORDER BY id DESC LIMIT 1",
        (user_id,)
    ).fetchone()

    plan = "free"
    expires_at = None
    if sub:
        plan = sub["plan"]
        expires_at = sub["expires_at"]
        # Check expiry
        if expires_at and str(date.today()) > expires_at:
            plan = "free"

    # Get today's usage
    row = db.execute(
        "SELECT COUNT(*) as cnt FROM usage WHERE user_id=? AND date=?",
        (user_id, today)
    ).fetchone()
    daily_used = row["cnt"] if row else 0
    daily_limit = None if plan == "pro" else 3

    return {
        "plan": plan,
        "daily_used": daily_used,
        "daily_limit": daily_limit,
        "expires_at": expires_at,
        "upgrade_url": "/upgrade" if plan == "free" else None,
    }


@router.post("/upgrade")
def upgrade_to_pro(current_user=Depends(get_current_user), db=Depends(get_db)):
    """Upgrade user to Pro plan (mock - real payment handled separately)."""
    user_id = current_user["id"]
    expires_at = str(date.today() + timedelta(days=30))

    # Upsert subscription
    existing = db.execute(
        "SELECT id FROM subscriptions WHERE user_id=?", (user_id,)
    ).fetchone()

    if existing:
        db.execute(
            "UPDATE subscriptions SET plan='pro', expires_at=? WHERE user_id=?",
            (expires_at, user_id)
        )
    else:
        db.execute(
            "INSERT INTO subscriptions (user_id, plan, expires_at) VALUES (?, 'pro', ?)",
            (user_id, expires_at)
        )
    db.commit()

    return {
        "success": True,
        "plan": "pro",
        "expires_at": expires_at,
        "message": "Upgraded to Pro successfully. Enjoy unlimited transcriptions!",
    }


@router.post("/cancel")
def cancel_subscription(current_user=Depends(get_current_user), db=Depends(get_db)):
    """Cancel Pro subscription, downgrade to free."""
    user_id = current_user["id"]

    db.execute(
        "UPDATE subscriptions SET plan='free', expires_at=NULL WHERE user_id=?",
        (user_id,)
    )
    db.commit()

    return {
        "success": True,
        "plan": "free",
        "message": "Subscription cancelled. You have been downgraded to the free plan.",
    }
