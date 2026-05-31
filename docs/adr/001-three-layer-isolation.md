# ADR-001: Three-Layer Isolation Architecture

**Date:** 2026-05-31

**Status:** Accepted

## Context

The original Web Chat system had a two-layer architecture:
- **Main Window**: Global persona, no boundaries
- **Projects**: Per-project SOUL.md + MEMORY.md, full boundary enforcement

Topics were nested under projects (session key: `project|topic`), which created several problems:
1. Topics couldn't exist independently — they were always tied to a parent project
2. Switching projects required exiting the current topic first
3. Topic storage was scattered across project directories
4. The mental model was confusing: "Is this topic part of this project or independent?"

## Decision

We decided to make Topics a fully independent third layer:

**New Architecture:**
- **Main Window**: `session_key = "main"` — Global persona, no boundaries
- **Projects**: `session_key = "project-name"` — Per-project SOUL + MEMORY, full boundaries
- **Topics**: `session_key = "topic-{id}"` — Lightweight, independent, global `_topics/` storage

**Key Changes:**
1. Session keys changed from `project|topic` to `topic-{id}` (independent)
2. Topic storage moved from per-project `topics.json` to global `_topics/topics.json`
3. Topic chat history moved to `projects/_topics/topic_{id}_chat.json`
4. Topics no longer require a project parameter in API calls
5. Deleting a project no longer cascades to topics

## Consequences

**Positive:**
- Topics are now truly independent conversation spaces
- Users can switch between projects and topics without context confusion
- Cleaner mental model: three parallel layers, not nested structure
- Topic data is centralized in `_topics/` directory

**Negative:**
- Existing topics created under the old `project|topic` key format are orphaned (migration needed)
- The architecture.html documentation needed major updates

## Implementation

**Backend (bridge.py):**
- Updated `_session_key()` to return `topic-{id}` when topic_id is provided
- Updated `_chat_path()` and `_archive_dir()` to route topic keys to `_topics/` directory
- Updated `_load_topics()` and `_save_topics()` for global storage
- Updated `_handle_topics()` to remove project dependency
- Updated `_build_boundary_reminder()` for independent topic boundaries

**Frontend (chat_test/index.html):**
- Updated `loadTopics()`, `createTopic()`, `deleteTopic()` to remove project parameter
- Updated `switchTopic()` to load from independent topic session
- Updated `exitTopic()` to return to current project (not always main)
- Updated `storageKey()` for independent topic localStorage keys

## Related

- [Architecture Diagram](../../../var/www/html/chat/architecture.html)
- [Web Chat话题架构 Memory Entry](../../MEMORY.md)
