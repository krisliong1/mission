"""
SQLite database initialization for AudioToText auth system.
Creates 4 tables: users, subscriptions, usage, sessions.
"""
import sqlite3
import os
from datetime import datetime, timedelta
from pathlib import Path

DB_PATH = os.path.join(os.path.dirname(__file__), "audiototext.db")


def get_db():
    """Get database connection with row factory."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    """Initialize database with all required tables."""
    conn = get_db()
    cursor = conn.cursor()
    
    # ── Users Table ────────────────────────────────────────────────────────
    # Stores user accounts with email/password or Google OAuth
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT UNIQUE NOT NULL,
            password_hash TEXT,  -- NULL for Google OAuth users
            name TEXT NOT NULL,
            google_id TEXT UNIQUE,  -- NULL for email/password users
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            is_verified BOOLEAN DEFAULT 0
        )
    """)
    
    # ── Subscriptions Table ────────────────────────────────────────────────
    # Tracks user subscription plans (free/pro)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS subscriptions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            plan TEXT DEFAULT 'free',  -- 'free' or 'pro'
            status TEXT DEFAULT 'active',  -- 'active', 'cancelled', 'expired'
            start_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            end_date TIMESTAMP,  -- NULL for free plan
            stripe_customer_id TEXT,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        )
    """)
    
    # ── Usage Table ────────────────────────────────────────────────────────
    # Tracks daily transcription usage per user
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS usage (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            date TEXT NOT NULL,  -- YYYY-MM-DD format
            transcription_count INTEGER DEFAULT 0,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
            UNIQUE(user_id, date)
        )
    """)
    
    # ── Sessions Table ─────────────────────────────────────────────────────
    # Stores JWT tokens for logout invalidation
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            token TEXT NOT NULL,
            expires_at TIMESTAMP NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        )
    """)
    
    # Create indexes for faster queries
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_users_email ON users(email)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_users_google_id ON users(google_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_sessions_token ON sessions(token)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_usage_user_date ON usage(user_id, date)")
    
    conn.commit()
    conn.close()


def create_user(name: str, email: str, password_hash: str = None, google_id: str = None) -> int:
    """Create a new user and return user_id."""
    conn = get_db()
    cursor = conn.cursor()
    
    cursor.execute(
        "INSERT INTO users (name, email, password_hash, google_id) VALUES (?, ?, ?, ?)",
        (name, email, password_hash, google_id)
    )
    user_id = cursor.lastrowid
    
    # Auto-assign free plan
    cursor.execute(
        "INSERT INTO subscriptions (user_id, plan, status) VALUES (?, 'free', 'active')",
        (user_id,)
    )
    
    conn.commit()
    conn.close()
    return user_id


def get_user_by_email(email: str) -> dict:
    """Get user by email."""
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM users WHERE email = ?", (email,))
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None


def get_user_by_id(user_id: int) -> dict:
    """Get user by ID."""
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM users WHERE id = ?", (user_id,))
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None


def get_user_subscription(user_id: int) -> dict:
    """Get user's subscription info."""
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT * FROM subscriptions WHERE user_id = ? ORDER BY id DESC LIMIT 1",
        (user_id,)
    )
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None


def get_today_usage(user_id: int) -> int:
    """Get today's transcription count for user."""
    today = datetime.now().strftime("%Y-%m-%d")
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT transcription_count FROM usage WHERE user_id = ? AND date = ?",
        (user_id, today)
    )
    row = cursor.fetchone()
    conn.close()
    return row["transcription_count"] if row else 0


def increment_usage(user_id: int):
    """Increment transcription count for today."""
    today = datetime.now().strftime("%Y-%m-%d")
    conn = get_db()
    cursor = conn.cursor()
    
    # Check if record exists
    cursor.execute(
        "SELECT id FROM usage WHERE user_id = ? AND date = ?",
        (user_id, today)
    )
    row = cursor.fetchone()
    
    if row:
        cursor.execute(
            "UPDATE usage SET transcription_count = transcription_count + 1 WHERE user_id = ? AND date = ?",
            (user_id, today)
        )
    else:
        cursor.execute(
            "INSERT INTO usage (user_id, date, transcription_count) VALUES (?, ?, 1)",
            (user_id, today)
        )
    
    conn.commit()
    conn.close()


def create_session(user_id: int, token: str, expires_at: datetime):
    """Store session token in database."""
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO sessions (user_id, token, expires_at) VALUES (?, ?, ?)",
        (user_id, token, expires_at)
    )
    conn.commit()
    conn.close()


def invalidate_session(token: str):
    """Remove session token (logout)."""
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM sessions WHERE token = ?", (token,))
    conn.commit()
    conn.close()


def is_valid_session(token: str) -> bool:
    """Check if token is valid (not invalidated, not expired)."""
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT id FROM sessions WHERE token = ? AND expires_at > ?",
        (token, datetime.now())
    )
    row = cursor.fetchone()
    conn.close()
    return row is not None


if __name__ == "__main__":
    # Initialize database when run directly
    init_db()
    print(f"✅ Database initialized at {DB_PATH}")
