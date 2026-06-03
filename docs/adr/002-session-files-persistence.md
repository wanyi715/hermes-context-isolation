# ADR-002: Session Files Persistence

**Date:** 2026-06-03
**Status:** Accepted

## Context

The Web Chat right sidebar displays files associated with each project/topic (detected from AI responses or listed from workspace). Originally, `session_files` was stored only in memory (`dict` in `ProjectManager`). This caused:

1. Topic file associations lost on bridge restart
2. User had to re-send messages to re-detect files
3. No way to audit what files were associated with each session

## Decision

Persist `session_files` to disk as JSON files alongside existing session data:

**Storage format:** `{project_dir}/{session_key}_files.json`

```python
# e.g. topic files
~/.hermes/projects/_topics/topic-t_123_files.json

# e.g. project files
~/.hermes/projects/earnings/earnings_files.json
```

**Write strategy:** Atomic write (`.tmp` + `os.rename`) on every file detection event. Only writes when new files are detected (no empty writes).

**Read strategy:** Lazy load — `_get_session_files(key)` checks memory first, falls back to disk only on cache miss.

## Alternatives Considered

1. **SQLite database** — More structured, but adds dependency and complexity for a simple key→list mapping. Overkill for 1.6GB RAM environment.
2. **Append to chat_history.json** — Would couple file state with message state, making cleanup harder.
3. **Redis/in-memory only** — Status quo. Loses data on restart.

## Consequences

**Positive:**
- File associations survive restarts
- Topic files persisted globally in `_topics/` directory
- Atomic writes prevent corruption
- Lazy loading avoids startup overhead

**Negative:**
- Small disk I/O overhead on each file detection (negligible)
- Need to clean up `_files.json` when deleting topics (handled)
