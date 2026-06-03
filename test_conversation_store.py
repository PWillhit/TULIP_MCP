import pytest
from conversation_store import ConversationStore
import tempfile
from datetime import datetime, timedelta


@pytest.fixture
def store():
    """Create an in-memory database for testing."""
    s = ConversationStore(db_path=":memory:", ttl_days=7)
    s.init_db()
    return s


def test_session_creation(store):
    """Test that sessions are created correctly."""
    session_id = "test_session_1"
    store._get_or_create_session(session_id)
    history = store.get_history(session_id)
    assert history == []


def test_add_message(store):
    """Test adding messages to a session."""
    session_id = "test_session_1"
    store.add_message(session_id, "user", "Hello")
    store.add_message(session_id, "assistant", "Hi there!")

    history = store.get_history(session_id)
    assert len(history) == 2
    assert history[0] == {"role": "user", "content": "Hello"}
    assert history[1] == {"role": "assistant", "content": "Hi there!"}


def test_session_isolation(store):
    """Test that different sessions are isolated."""
    store.add_message("session_1", "user", "User 1 message")
    store.add_message("session_2", "user", "User 2 message")

    history_1 = store.get_history("session_1")
    history_2 = store.get_history("session_2")

    assert len(history_1) == 1
    assert len(history_2) == 1
    assert history_1[0]["content"] == "User 1 message"
    assert history_2[0]["content"] == "User 2 message"


def test_clear_history(store):
    """Test clearing history for a session."""
    session_id = "test_session_1"
    store.add_message(session_id, "user", "Hello")
    store.add_message(session_id, "assistant", "Hi")

    assert len(store.get_history(session_id)) == 2

    store.clear_history(session_id)
    assert len(store.get_history(session_id)) == 0


def test_rollback_last_message(store):
    """Test rolling back the last message."""
    session_id = "test_session_1"
    store.add_message(session_id, "user", "Message 1")
    store.add_message(session_id, "user", "Message 2")

    assert len(store.get_history(session_id)) == 2

    result = store.rollback_last_message(session_id)
    assert result is True
    assert len(store.get_history(session_id)) == 1
    assert store.get_history(session_id)[0]["content"] == "Message 1"


def test_rollback_empty_session(store):
    """Test rolling back from an empty session."""
    session_id = "empty_session"
    result = store.rollback_last_message(session_id)
    assert result is False


def test_count_active_sessions(store):
    """Test counting active sessions."""
    store.add_message("session_1", "user", "Hello")
    store.add_message("session_2", "user", "Hi")

    count = store.count_active_sessions()
    assert count == 2


def test_cleanup_expired(store):
    """Test cleanup of expired sessions."""
    # Create some sessions
    store.add_message("session_1", "user", "Hello")
    store.add_message("session_2", "user", "Hi")

    # Manually expire one session by updating its expires_at
    conn = store._get_connection()
    cursor = conn.cursor()
    past_time = (datetime.utcnow() - timedelta(days=1)).isoformat()
    cursor.execute("UPDATE sessions SET expires_at = ? WHERE id = ?", (past_time, "session_1"))
    conn.commit()

    # Run cleanup
    deleted = store.cleanup_expired()

    assert deleted == 1
    assert store.count_active_sessions() == 1
    assert len(store.get_history("session_1")) == 0  # Expired session returns empty
    assert len(store.get_history("session_2")) == 1


def test_cache_functionality(store):
    """Test that caching prevents redundant DB queries."""
    session_id = "test_session_1"
    store.add_message(session_id, "user", "Hello")

    # First call populates cache
    history_1 = store.get_history(session_id)
    assert len(history_1) == 1

    # Second call uses cache
    history_2 = store.get_history(session_id)
    assert history_1 == history_2

    # Cache should be invalidated on add_message
    store.add_message(session_id, "assistant", "Hi")
    history_3 = store.get_history(session_id)
    assert len(history_3) == 2


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
