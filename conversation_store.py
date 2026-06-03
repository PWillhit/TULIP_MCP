import sqlite3
import json
import logging
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional
from pathlib import Path

logger = logging.getLogger(__name__)


class ConversationStore:
    """Manages persistent conversation storage with SQLite and session isolation."""

    def __init__(
        self,
        db_path: str = "./conversations.db",
        ttl_days: int = 7,
        cache_size: int = 10,
    ):
        self.db_path = db_path
        self.ttl_days = ttl_days
        self.cache_size = cache_size
        self.connection = None
        self._cache = {}  # Simple dict-based LRU cache

    def _get_connection(self) -> sqlite3.Connection:
        """Get or create database connection."""
        if self.connection is None:
            self.connection = sqlite3.connect(self.db_path, check_same_thread=False)
            self.connection.row_factory = sqlite3.Row
        return self.connection

    def init_db(self) -> None:
        """Create database tables if they don't exist."""
        conn = self._get_connection()
        cursor = conn.cursor()

        # Create sessions table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS sessions (
                id TEXT PRIMARY KEY,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_accessed TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                expires_at TIMESTAMP,
                metadata JSON
            )
        """)

        # Create messages table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                role TEXT NOT NULL CHECK(role IN ('user', 'assistant')),
                content TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(session_id) REFERENCES sessions(id)
            )
        """)

        # Create indexes
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_messages_session_id
            ON messages(session_id)
        """)

        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_messages_created_at
            ON messages(created_at)
        """)

        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_sessions_expires_at
            ON sessions(expires_at)
        """)

        conn.commit()
        logger.info(f"Database initialized at {self.db_path}")

    def _get_or_create_session(self, session_id: str) -> bool:
        """Create session if it doesn't exist. Returns True if created, False if already existed."""
        conn = self._get_connection()
        cursor = conn.cursor()

        # Check if session exists
        cursor.execute("SELECT id FROM sessions WHERE id = ?", (session_id,))
        if cursor.fetchone():
            # Update last_accessed
            cursor.execute(
                "UPDATE sessions SET last_accessed = ? WHERE id = ?",
                (datetime.utcnow().isoformat(), session_id),
            )
            conn.commit()
            return False

        # Create new session
        expires_at = (datetime.utcnow() + timedelta(days=self.ttl_days)).isoformat()
        cursor.execute(
            """
            INSERT INTO sessions (id, created_at, last_accessed, expires_at, metadata)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                session_id,
                datetime.utcnow().isoformat(),
                datetime.utcnow().isoformat(),
                expires_at,
                None,
            ),
        )
        conn.commit()
        logger.info(f"Created new session: {session_id}")
        return True

    def add_message(self, session_id: str, role: str, content: str) -> None:
        """Add a message to conversation history."""
        if not session_id:
            raise ValueError("session_id is required")
        if role not in ("user", "assistant"):
            raise ValueError("role must be 'user' or 'assistant'")

        self._get_or_create_session(session_id)

        conn = self._get_connection()
        cursor = conn.cursor()

        cursor.execute(
            """
            INSERT INTO messages (session_id, role, content, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (session_id, role, content, datetime.utcnow().isoformat()),
        )
        conn.commit()

        # Invalidate cache for this session
        self._cache.pop(session_id, None)

    def get_history(self, session_id: str) -> List[Dict[str, str]]:
        """Get conversation history for a session. Returns empty list if expired."""
        if not session_id:
            raise ValueError("session_id is required")

        # Check cache first
        if session_id in self._cache:
            return self._cache[session_id]

        conn = self._get_connection()
        cursor = conn.cursor()

        # Check if session is expired
        cursor.execute(
            "SELECT expires_at FROM sessions WHERE id = ?", (session_id,)
        )
        row = cursor.fetchone()

        if not row:
            # Session doesn't exist, return empty
            return []

        expires_at = datetime.fromisoformat(row["expires_at"])
        if datetime.utcnow() > expires_at:
            # Session is expired, clean it up and return empty
            self._delete_session(session_id)
            return []

        # Fetch messages
        cursor.execute(
            """
            SELECT role, content FROM messages
            WHERE session_id = ?
            ORDER BY created_at ASC
            """,
            (session_id,),
        )

        messages = [
            {"role": row["role"], "content": row["content"]} for row in cursor.fetchall()
        ]

        # Update last_accessed
        cursor.execute(
            "UPDATE sessions SET last_accessed = ? WHERE id = ?",
            (datetime.utcnow().isoformat(), session_id),
        )
        conn.commit()

        # Store in cache (simple dict, not true LRU, but good enough for 10 sessions)
        self._cache[session_id] = messages

        return messages

    def clear_history(self, session_id: str) -> None:
        """Delete all messages for a session."""
        if not session_id:
            raise ValueError("session_id is required")

        conn = self._get_connection()
        cursor = conn.cursor()

        cursor.execute("DELETE FROM messages WHERE session_id = ?", (session_id,))
        conn.commit()

        # Invalidate cache
        self._cache.pop(session_id, None)

        logger.info(f"Cleared history for session: {session_id}")

    def rollback_last_message(self, session_id: str) -> bool:
        """Delete the last message for a session. Returns True if deleted, False if no messages."""
        if not session_id:
            raise ValueError("session_id is required")

        conn = self._get_connection()
        cursor = conn.cursor()

        # Get the last message ID
        cursor.execute(
            """
            SELECT id FROM messages
            WHERE session_id = ?
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (session_id,),
        )

        row = cursor.fetchone()
        if not row:
            return False

        cursor.execute("DELETE FROM messages WHERE id = ?", (row["id"],))
        conn.commit()

        # Invalidate cache
        self._cache.pop(session_id, None)

        return True

    def _delete_session(self, session_id: str) -> None:
        """Delete a session and all its messages."""
        conn = self._get_connection()
        cursor = conn.cursor()

        cursor.execute("DELETE FROM messages WHERE session_id = ?", (session_id,))
        cursor.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
        conn.commit()

        self._cache.pop(session_id, None)

    def cleanup_expired(self) -> int:
        """Delete expired sessions and return count of deleted sessions."""
        conn = self._get_connection()
        cursor = conn.cursor()

        now = datetime.utcnow().isoformat()

        # Find expired sessions
        cursor.execute(
            "SELECT id FROM sessions WHERE expires_at < ?",
            (now,),
        )

        expired_sessions = [row["id"] for row in cursor.fetchall()]

        # Delete messages for expired sessions
        for session_id in expired_sessions:
            cursor.execute("DELETE FROM messages WHERE session_id = ?", (session_id,))
            self._cache.pop(session_id, None)

        # Delete expired sessions
        cursor.execute("DELETE FROM sessions WHERE expires_at < ?", (now,))
        conn.commit()

        logger.info(f"Cleanup: deleted {len(expired_sessions)} expired sessions")
        return len(expired_sessions)

    def count_active_sessions(self) -> int:
        """Return count of active (non-expired) sessions."""
        conn = self._get_connection()
        cursor = conn.cursor()

        now = datetime.utcnow().isoformat()
        cursor.execute(
            "SELECT COUNT(*) as count FROM sessions WHERE expires_at > ?", (now,)
        )

        return cursor.fetchone()["count"]

    def close(self) -> None:
        """Close database connection."""
        if self.connection:
            self.connection.close()
            self.connection = None
            logger.info("Database connection closed")
