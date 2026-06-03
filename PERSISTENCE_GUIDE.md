# Conversation Persistence & Session Isolation Guide

## What Changed

Your Tulip web app now has **persistent conversation storage with per-session isolation**. This solves three critical issues with the old in-memory design:

1. **User Isolation**: Each browser session has its own isolated conversation history. Multiple users no longer see each other's conversations.
2. **Persistence**: Conversations survive server restarts and browser refreshes.
3. **Bounded Memory**: Old conversations are automatically cleaned up after a configurable TTL (default: 7 days).

## How It Works

### Architecture

```
Browser                          Backend
┌─────────────┐                 ┌──────────────────┐
│ localStorage│ ← sessionId ──→ │ ConversationStore│
│             │                 │   (SQLite DB)    │
└─────────────┘                 └──────────────────┘
      ↓                                 ↓
  Persists across              Persists across
  page refreshes               server restarts
```

### Session Lifecycle

1. **Session Creation** (first visit):
   - Frontend generates UUID on first page load
   - Stored in browser's localStorage as `tulip_session_id`
   - Sent with every request to `/api/ask`

2. **Message Storage**:
   - Backend stores in SQLite (`conversations.db`)
   - Separate table per session
   - No mixing of user conversations

3. **Expiry & Cleanup**:
   - Sessions expire after 7 days (configurable via `CONVERSATION_TTL_DAYS`)
   - Background task removes expired sessions every hour
   - Lazy expiry also checks on every read (double cleanup)

### Database Schema

```sql
sessions:
  - id (PRIMARY KEY): UUID from browser
  - created_at: When session was created
  - last_accessed: When this session was last used
  - expires_at: When session will be deleted
  - metadata: Optional JSON (e.g., browser info)

messages:
  - id (PRIMARY KEY): Auto-increment
  - session_id: FK to sessions.id
  - role: 'user' or 'assistant'
  - content: Message text
  - created_at: When message was added
  - Indexes: (session_id), (created_at) for fast queries
```

## Configuration

Add these to your `.env` file (or use defaults):

```bash
# Conversation Storage Configuration
CONVERSATION_TTL_DAYS=7              # How long before old conversations are deleted
CONVERSATION_DB_PATH=./conversations.db  # Where to store the database file
CONVERSATION_CACHE_SIZE=10           # How many sessions to cache in memory
CONVERSATION_CLEANUP_INTERVAL_HOURS=1  # How often cleanup runs (currently unused, runs every hour)
```

## API Changes

### POST /api/ask

**Request:**
```json
{
  "question": "What tables do I have?",
  "session_id": "session_1234567890_abcd"  // Optional; auto-generated if missing
}
```

**Response:**
```json
{
  "answer": "You have tables: ...",
  "success": true,
  "session_id": "session_1234567890_abcd"  // Always returned for client to persist
}
```

### GET /api/history

**Request:**
```bash
GET /api/history?session_id=session_1234567890_abcd
```

**Response:**
```json
{
  "history": [
    {"role": "user", "content": "..."},
    {"role": "assistant", "content": "..."}
  ],
  "session_id": "session_1234567890_abcd"
}
```

### POST /api/clear-history

**Request:**
```json
{
  "session_id": "session_1234567890_abcd"
}
```

**Response:**
```json
{
  "status": "cleared",
  "session_id": "session_1234567890_abcd"
}
```

### New Debug Endpoints

**GET /api/sessions** — Count of active sessions:
```json
{
  "active_sessions": 5
}
```

**POST /api/cleanup** — Manually trigger cleanup:
```json
{
  "status": "completed",
  "deleted_sessions": 2
}
```

## Testing

### Unit Tests

All core database functionality is tested:

```bash
python3 -m pytest test_conversation_store.py -v
```

Tests cover:
- Session creation and isolation
- Message add/retrieval
- Clearing history
- Rollback on error
- TTL-based cleanup
- In-memory caching

### Integration Tests

Verify the full API works:

```bash
# Terminal 1: Start the server
python3 web_app.py

# Terminal 2: Run integration tests
python3 -m pytest test_integration.py -v
```

### Manual Testing

See `test_integration.py` for detailed manual verification steps.

**Quick test:**
1. Open `http://localhost:8600` in two browsers (or private windows)
2. Each should get a different `sessionId` in localStorage
3. Ask different questions in each
4. Verify histories are isolated
5. Refresh one browser—history persists
6. Stop/restart server—refresh both—histories still there

## File Changes Summary

### New Files
- `conversation_store.py` — SQLite abstraction layer
- `test_conversation_store.py` — Unit tests
- `test_integration.py` — Integration tests & manual verification guide

### Modified Files
- `web_app.py` — Updated endpoints to use persistent store instead of in-memory list
- `index.html` — Added session ID management (localStorage)
- `.env.example` — Added new configuration variables

## Migration Notes

### From Old In-Memory System

- **No breaking changes**: Existing API contracts unchanged, just now with `session_id` parameter
- **Old conversations lost**: Since they were in-memory, stopping the server loses them (this was already the case)
- **Backward compatible**: If client doesn't send `session_id`, backend auto-generates one

### If You Had Previous Data

The old `conversation_history` global list is removed. If you had persisted it to JSON or files, you could write a migration script (not included in this release).

## Performance Considerations

- **Memory**: Bounded by TTL + cache size. No unbounded growth.
- **Database**: SQLite is fine for typical usage (100-1000 concurrent sessions)
- **Queries**: Indexes on `session_id` and `created_at` make all operations fast
- **Cleanup**: Runs hourly in background; cost is minimal (delete old rows)

## Future Improvements

Optional enhancements (not implemented):

1. **User Authentication**: Add real user accounts instead of browser sessions
2. **Conversation Export**: Let users download conversation history as JSON
3. **Search**: Add full-text search over messages
4. **Pagination**: Add limit/offset to `/api/history` for large conversations
5. **Analytics**: Track conversation metrics (topic, duration, tool calls)
6. **Rate Limiting by Session**: Instead of by IP address

## Troubleshooting

### Sessions keep expiring
- Check `CONVERSATION_TTL_DAYS` in `.env`
- Default is 7 days—may be too short if you want longer retention
- Update value and restart server

### Database file grows large
- Old sessions should be cleaned up automatically
- Verify cleanup task is running (check logs)
- Manually trigger: `curl -X POST http://localhost:8600/api/cleanup`

### Sessions not isolated
- Verify `session_id` is being sent in request body to `/api/ask`
- Check browser localStorage for `tulip_session_id`
- Frontend should generate UUID on first load

### "session_id required" error
- Backend now requires `session_id` for `/api/history` and `/api/clear-history`
- Frontend auto-generates and sends it for `/api/ask`
- Update any custom clients to send `session_id` parameter

## Questions?

See the plan file: `/home/pwillhit/.claude/plans/nested-percolating-goose.md`
