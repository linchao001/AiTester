# Auto-Memory Interval Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Align AiTester `auto_memory` triggers with QwenPaw’s turn-interval semantics (default every 5 user turns, FIFO background queue, flush pending on session delete).

**Architecture:** Keep hooks in `ChatService` / `PersonalMemory` (no AgentScope middleware). A session-scoped turn tracker accumulates user-turn markers + message snapshots after each successful `_persist`; when `pending.length >= interval`, enqueue one `auto_memory` job. A single daemon worker drains a FIFO queue serially. Session delete flushes remaining pending ( `/new` analog). No compress / `/memorize` / `/compact` in this slice.

**Tech Stack:** Python 3.11+, FastAPI, `queue.Queue` + `threading`, existing Reme `run_job_sync("auto_memory")`.

**Spec:** User-confirmed scope 2; QwenPaw `MemoryMiddleware` / `add_summarize_task` behavior; [2026-10-10-memory-layer-reme-design.md](../specs/2026-10-10-memory-layer-reme-design.md) (layer-2 hooks; do not copy AgentScope middleware).

## Global Constraints

- Do not block the chat reply path; enqueue only after `_persist` succeeds.
- Soft-fail: tracker / queue / Reme errors never break SessionStore or SSE done.
- `auto_memory_interval` null or `<= 0` disables periodic flush (clear pending on note).
- No `/memorize`, `/compact`, or context-compression flush this slice (leave `flush_pending` usable later).
- Workspace/instance pool changes are out of scope (already covered by rem workspace design).

---

### Task 1: Settings + turn tracker

**Files:**
- Modify: `backend/src/aitester/config.py`
- Create: `backend/src/aitester/memory/reme/auto_memory_turns.py`
- Test: `backend/tests/test_auto_memory_turns.py`

**Interfaces:**
- Produces: `Settings.auto_memory_interval: int | None = 5`
- Produces: `AutoMemoryTurnTracker.note_turn(...) -> list[dict] | None`, `.take_all(session_id) -> FlushBatch | None`, `.reset(session_id)`

- [x] **Step 1: Add config field**

```python
auto_memory_interval: int | None = 5  # None or <=0 disables periodic flush
```

- [x] **Step 2: Implement tracker**

Per `session_id`: `pending` (FIFO markers + message snapshots), `seen` (cap 1000), store `project_id`/`agent_id`.  
`note_turn`: skip if marker in seen; if interval<=0 clear pending/return None; else append; if `len(pending) >= interval` pop first `interval` turns and return concatenated messages.  
`take_all`: return all pending messages + project/agent, then clear session state.

- [x] **Step 3: Unit tests for interval / disable / take_all**

---

### Task 2: FIFO queue in PersonalMemory

**Files:**
- Modify: `backend/src/aitester/memory/reme/personal.py`
- Modify: `backend/tests/test_personal_memory.py`

**Interfaces:**
- Consumes: `AutoMemoryTurnTracker`, `Settings.auto_memory_interval`
- Produces: `note_user_turn(...)`, `flush_session(session_id)`, `schedule_auto_memory` → enqueue (serial worker)

- [x] **Step 1: Replace per-call Thread with queue + one daemon worker**
- [x] **Step 2: Wire note_user_turn / flush_session to tracker + enqueue**
- [x] **Step 3: Tests for interval gate and queue enqueue kwargs**

---

### Task 3: ChatService + session delete

**Files:**
- Modify: `backend/src/aitester/services/chat.py`
- Modify: `backend/tests/test_chat_personal_memory_hooks.py`

**Interfaces:**
- Consumes: `PersonalMemory.note_user_turn`, `PersonalMemory.flush_session`

- [x] **Step 1: `_persist` calls `note_user_turn` (not raw schedule every turn)**
- [x] **Step 2: `drop_session` calls `flush_session` before clearing pending threads**
- [x] **Step 3: Update spy tests — interval=1 still schedules; default 5 needs 5 turns; delete flushes**

---

### Task 4: Docs touch (memory design note)

**Files:**
- Modify: `docs/superpowers/specs/2026-10-10-memory-layer-reme-design.md` (interval semantics paragraph only)

- [x] **Step 1: Document interval + delete flush; note compress/memorize deferred**
