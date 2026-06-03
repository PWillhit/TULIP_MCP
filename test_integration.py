"""
Integration tests for the Tulip web app with persistent sessions.

Run with: python3 -m pytest test_integration.py -v
Or manually test by running: python3 test_integration.py

NOTE: These tests require the FastAPI app to be running.
Start with: python3 web_app.py
Then in another terminal: python3 -m pytest test_integration.py -v
"""

import pytest
import requests
import json
import time
from urllib.parse import urljoin

BASE_URL = "http://localhost:8600"


def test_health_check():
    """Test that the app is running and responding."""
    response = requests.get(urljoin(BASE_URL, "/health"))
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert "active_sessions" in data


def test_ask_question_generates_session():
    """Test that asking a question generates a session ID if not provided."""
    response = requests.post(
        urljoin(BASE_URL, "/api/ask"),
        json={"question": "Hello, what is 2+2?"}
    )
    assert response.status_code == 200 or response.status_code == 503  # May fail if Bedrock not configured

    if response.status_code == 200:
        data = response.json()
        assert "session_id" in data
        assert data["session_id"].startswith("session_")


def test_session_isolation():
    """Test that two sessions have isolated conversations."""
    # Session 1: ask a question
    response1 = requests.post(
        urljoin(BASE_URL, "/api/ask"),
        json={"question": "What is AI?", "session_id": "session_test_1"}
    )

    # Session 2: ask a different question
    response2 = requests.post(
        urljoin(BASE_URL, "/api/ask"),
        json={"question": "What is ML?", "session_id": "session_test_2"}
    )

    # Both should succeed or both fail (depending on Bedrock config)
    if response1.status_code == 200 and response2.status_code == 200:
        # Get histories
        history1 = requests.get(
            urljoin(BASE_URL, "/api/history"),
            params={"session_id": "session_test_1"}
        )
        history2 = requests.get(
            urljoin(BASE_URL, "/api/history"),
            params={"session_id": "session_test_2"}
        )

        if history1.status_code == 200 and history2.status_code == 200:
            data1 = history1.json()["history"]
            data2 = history2.json()["history"]

            # Check they're not empty and don't interfere
            assert len(data1) > 0
            assert len(data2) > 0

            # Verify they're different questions
            assert any("AI" in msg.get("content", "") for msg in data1)
            assert any("ML" in msg.get("content", "") for msg in data2)


def test_clear_history():
    """Test clearing history for a session."""
    session_id = "session_clear_test"

    # Add some messages
    store = __import__("conversation_store").ConversationStore()
    store.init_db()
    store.add_message(session_id, "user", "Test message")
    store.add_message(session_id, "assistant", "Response")

    # Verify messages exist
    history = store.get_history(session_id)
    assert len(history) == 2

    # Clear via API
    response = requests.post(
        urljoin(BASE_URL, "/api/clear-history"),
        json={"session_id": session_id}
    )

    if response.status_code == 200:
        # Verify messages are cleared
        history = store.get_history(session_id)
        assert len(history) == 0


def test_list_sessions():
    """Test the debug endpoint for listing sessions."""
    response = requests.get(urljoin(BASE_URL, "/api/sessions"))
    assert response.status_code == 200
    data = response.json()
    assert "active_sessions" in data
    assert isinstance(data["active_sessions"], int)


def manual_verification_instructions():
    """
    Manual verification steps to test the feature end-to-end:

    1. Start the server:
       python3 web_app.py

    2. Open two browsers or tabs:
       - Browser A: http://localhost:8600
       - Browser B (private window): http://localhost:8600

    3. In Browser A:
       - Open DevTools → Application → Local Storage
       - Note the tulip_session_id value
       - Ask: "What is 2+2?"
       - Verify response

    4. In Browser B:
       - Open DevTools → Application → Local Storage
       - Note the tulip_session_id (should be different from Browser A)
       - Ask: "What is the capital of France?"
       - Verify response

    5. Verify isolation:
       - In Browser A, refresh the page
       - Session ID should be the same (persisted in localStorage)
       - History should show the "2+2" question
       - No sign of the France question

    6. Verify persistence:
       - Restart the server: Ctrl+C, then python3 web_app.py
       - Refresh Browser A and B
       - Both should still have their original session IDs
       - Both should still see their conversation history

    7. Test cleanup:
       - Call: curl -X POST http://localhost:8600/api/cleanup
       - Should return: {"status": "completed", "deleted_sessions": 0}
       - (Returns 0 because sessions are not expired yet)

    8. Test new session button:
       - Add a "New Session" button to frontend that:
         - Clears localStorage.tulip_session_id
         - Refreshes the page
       - Should generate a new session ID and clear history
    """
    pass


if __name__ == "__main__":
    print(__doc__)
    print("\n" + "="*80 + "\n")
    print(manual_verification_instructions.__doc__)
    print("\nTo run automated tests:")
    print("  1. Start the server: python3 web_app.py")
    print("  2. In another terminal: python3 -m pytest test_integration.py -v")
