# Audit Remediation Guide

> **Project**: Claude Office Visualizer (claude-office)
> **Date**: 2026-07-06
> **Companion to**: [AUDIT.md](AUDIT.md) (2026-07-06 Fable audit — 69 findings)
> **Audience**: Any capable coding model or human contributor. Every entry below contains the exact steps, file anchors, and verification commands needed to resolve the issue **without re-analyzing the codebase**. Follow the steps literally; where judgment is required, the entry says so explicitly.

---

## How to Use This Guide

1. **Work in phase order** (from AUDIT.md's Remediation Plan): Phase 2 (Critical Architecture + Makefile/CI) → Phase 3a (Security) → Phases 3b/3c/3d (Architecture / Code Quality / Documentation, parallelizable by domain). Phase 1 (Critical Security) is empty for this audit.
2. **Check `Preconditions` before starting any entry.** If a precondition issue is not yet resolved, do that one first.
3. **Re-read every file immediately before editing it.** Several files are touched by multiple issues (see AUDIT.md's File Conflict Map) — a prior fix may have shifted line numbers cited here. Line anchors in this guide were verified on 2026-07-06 against commit `b17c2c4`; treat them as landmarks, not gospel.
4. **Verify after every entry** using the entry's Verification commands. Never batch multiple entries between verification runs on the same component.
5. **Commit per entry** (or per tightly coupled entry group, e.g. ARC-001+DOC-001) with the issue ID in the commit message, e.g. `fix(backend): validate project_root before git invocation (SEC-003)`.
6. **Security entries (SEC-*) must be flagged for manual review** before merging — do not fold them silently into larger changes, and never generate or replace secrets/tokens as part of a fix.
7. **When fixing tests**: a failing test can mean a real code bug. Understand what the test asserts before changing either side; never weaken an assertion just to pass.

## Execution Order (condensed)

| Order | Entries | Why |
|-------|---------|-----|
| 1 | ARC-001 + DOC-001 (+ ARC-009 bundled) | CI/checkall gate everything after; teams.py must be fixed or the new CI fails |
| 2 | SEC-001, SEC-002, SEC-003 | Land security fixes before refactors touch the same files |
| 3 | ARC-008 | Fail-safe hooks installer (user-data risk) |
| 4 | QA-001 (characterization slice) | Locks in frontend queue behavior before any frontend refactor |
| 5 | ARC-002 → ARC-014 → ARC-011 → ARC-012 | Backend structural chain, in dependency order |
| 6 | QA-006 → ARC-004/017 → ARC-005 → QA-003, QA-009 | Frontend structural chain, in dependency order |
| 7 | ARC-003, ARC-010 (+SEC-005), remaining ARC/QA | Parallelizable by domain after the chains above |
| 8 | DOC-003 → DOC-002/006, then remaining DOC | Auth docs define terms the env tables reference |
| 9 | All remaining Low-priority entries | Any order |

Aliased issues (one fix, two IDs): QA-007 = ARC-025, QA-010 = ARC-021, QA-001 = ARC-007. The full procedure appears once; the alias entry points to it.

---
## Architecture (ARC)

### [ARC-001] No CI enforcement of correctness for any component
**Priority**: Critical | **Effort**: M | **Phase**: 2 — Critical Architecture (first, bundled with DOC-001 decision and ARC-009)
**Preconditions**: None (but land ARC-009 in the same change so the new CI passes; DOC-001 doc alignment follows this change)
**Files**: `/Users/probello/Repos/claude-office/Makefile`, `/Users/probello/Repos/claude-office/hooks/Makefile` (new), `/Users/probello/Repos/claude-office/hooks/pyproject.toml`, `/Users/probello/Repos/claude-office/opencode-plugin/Makefile` (new), `/Users/probello/Repos/claude-office/.github/workflows/ci.yml` (new)

**Goal**: `make checkall` from the repo root runs fmt+lint+typecheck+tests for backend, frontend, hooks, and opencode-plugin, and a GitHub Actions workflow runs it on every PR.

**Steps**:
1. Add dev tooling to hooks. `hooks/pyproject.toml` already has `[tool.ruff]` and `[tool.pyright]` sections but its `[dependency-groups] dev` list contains only `pytest>=9.0.3`. Run `cd /Users/probello/Repos/claude-office/hooks && uv add --group dev ruff pyright`.
2. Create `/Users/probello/Repos/claude-office/hooks/Makefile`:
   ```makefile
   .PHONY: install test lint fmt typecheck checkall

   install:
   	uv sync

   test:
   	uv run pytest

   lint:
   	uv run ruff check .

   fmt:
   	uv run ruff format .

   typecheck:
   	uv run pyright

   checkall: fmt lint typecheck test
   ```
3. Create `/Users/probello/Repos/claude-office/opencode-plugin/Makefile`. `opencode-plugin/package.json` scripts are `build`, `dev`, `lint` (tsc --noEmit), `typecheck` (tsc --noEmit); there are no tests yet (QA-002 adds them):
   ```makefile
   .PHONY: install test lint fmt typecheck checkall

   PKG_MGR := $(shell command -v bun >/dev/null 2>&1 && echo "bun" || echo "npm")

   install:
   	$(PKG_MGR) install

   test:
   	@if ls src/*.test.ts tests/*.test.ts >/dev/null 2>&1; then bun test; else echo "opencode-plugin: no tests yet (see QA-002)"; fi

   lint:
   	$(PKG_MGR) run lint

   fmt:
   	@echo "opencode-plugin: no formatter configured"

   typecheck:
   	$(PKG_MGR) run typecheck

   checkall: lint typecheck test
   ```
4. Edit the root `Makefile`. Current targets at lines 44–60 only recurse into `backend` and `frontend`. Change them to:
   ```makefile
   lint:			# Run lint on all components
   	make -C backend lint
   	make -C frontend lint
   	make -C hooks lint
   	make -C opencode-plugin lint
   	cd backend && uv run ruff check ../scripts

   fmt:			# Reformat code
   	make -C backend fmt
   	make -C frontend fmt
   	make -C hooks fmt

   test:			# Run all tests
   	make -C backend test
   	make -C frontend test
   	make -C hooks test
   	make -C opencode-plugin test

   typecheck:			# Run static type checks
   	make -C backend typecheck
   	make -C frontend typecheck
   	make -C hooks typecheck
   	make -C opencode-plugin typecheck

   checkall: fmt lint typecheck test		# Run all checks including tests
   ```
   Note: the `checkall: fmt lint typecheck test` change IS the DOC-001 decision (documented behavior wins); the doc updates themselves are DOC-001's entry (part 2). `backend/Makefile` `checkall` already includes `test`; `frontend/Makefile` `checkall` already runs tests after build — do not change those two files.
5. Create `.github/workflows/ci.yml`. Mirror the action pins already used in `.github/workflows/type-drift.yml` (`actions/checkout@v5`, `astral-sh/setup-uv@v5`, `oven-sh/setup-bun@v2`):
   ```yaml
   name: CI

   on:
     push:
       branches: [main]
     pull_request:

   jobs:
     backend:
       runs-on: ubuntu-latest
       timeout-minutes: 20
       steps:
         - uses: actions/checkout@v5
         - uses: astral-sh/setup-uv@v5
           with:
             python-version: "3.13"
         - run: cd backend && uv sync
         - run: make -C backend checkall
         - run: cd backend && uv run ruff check ../scripts
     frontend:
       runs-on: ubuntu-latest
       timeout-minutes: 20
       steps:
         - uses: actions/checkout@v5
         - uses: oven-sh/setup-bun@v2
         - run: cd frontend && bun install
         - run: make -C frontend checkall
     hooks:
       runs-on: ubuntu-latest
       timeout-minutes: 15
       steps:
         - uses: actions/checkout@v5
         - uses: astral-sh/setup-uv@v5
           with:
             python-version: "3.13"
         - run: cd hooks && uv sync
         - run: make -C hooks checkall
     opencode-plugin:
       runs-on: ubuntu-latest
       timeout-minutes: 15
       steps:
         - uses: actions/checkout@v5
         - uses: oven-sh/setup-bun@v2
         - run: cd opencode-plugin && bun install
         - run: make -C opencode-plugin checkall
   ```

**Verification**:
- `cd /Users/probello/Repos/claude-office && make checkall` — must run all four components and exit 0.
- `cd /Users/probello/Repos/claude-office/hooks && make checkall`
- `cd /Users/probello/Repos/claude-office/opencode-plugin && make checkall`
- After push: `gh run list --limit 3` shows the CI workflow running; `gh run watch` until green.

**Do NOT**:
- Do not change `backend/Makefile` or `frontend/Makefile` (both already run tests in `checkall`).
- Do not add new floating action tags — pin `uses:` refs exactly as in `type-drift.yml`.
- Do not skip the `scripts/` ruff pass; it is what would have caught ARC-009's broken import earlier (paired with the ARC-009 fix).
- Do not update README/CONTRIBUTING/CLAUDE.md here — that is DOC-001 (part 2), done in the same PR but as its own entry.

### [ARC-002] Dual event dispatch through two parallel systems (backend)
**Priority**: High | **Effort**: M | **Phase**: 2 — Critical Architecture (backend chain, before ARC-014 and ARC-011; blocks QA-004)
**Preconditions**: ARC-001 (so CI verifies the refactor)
**Files**: `/Users/probello/Repos/claude-office/backend/app/core/event_processor.py`

**Goal**: `_process_event_internal` routes event-type-specific async enrichment through a single dispatch table instead of ten sequential `if event.event_type ==` blocks, while preserving the exact ordering of broadcasts and the sync `sm.transition()` dispatch table.

**Steps**:
1. Read `backend/app/core/event_processor.py:332-556` (`_process_event_internal`). Current structure: persist → restore/create SM → `sm.transition(event)` → history entry → `if SESSION_START` block (lines 438–449) → unconditional `ensure_task_poller_running` + `_start_beads_if_available` (454–460) → `if SESSION_END` block (465–470) → `broadcast_state`/`broadcast_event` (475–476) → room orchestrator (481–487) → ten sequential `if event.event_type ==` blocks for SUBAGENT_START, SUBAGENT_INFO, AGENT_UPDATE, SUBAGENT_STOP, STOP, USER_PROMPT_SUBMIT, PRE_TOOL_USE, TASK_CREATED, TASK_COMPLETED, TEAMMATE_IDLE (492–546) → `_schedule_overview_broadcast()` (555).
2. Add ten thin async wrapper methods on `EventProcessor`, one per post-broadcast event type, each with the uniform signature `async def _enrich_<name>(self, sm: StateMachine, event: Event, agent_id: str) -> None:` and a body that is exactly the existing block's call, e.g.:
   ```python
   async def _enrich_subagent_start(self, sm: StateMachine, event: Event, agent_id: str) -> None:
       await handle_subagent_start(sm, event, self._ensure_transcript_poller, self._update_agent_state)

   async def _enrich_pre_tool_use(self, sm: StateMachine, event: Event, agent_id: str) -> None:
       await handle_pre_tool_use(sm, event, agent_id, self._get_event_summary(event))
   ```
   (Same pattern for `_enrich_subagent_info`, `_enrich_agent_update`, `_enrich_subagent_stop` (passes `self._persist_synthetic_event`), `_enrich_stop`, `_enrich_user_prompt_submit`, `_enrich_task_created`, `_enrich_task_completed`, `_enrich_teammate_idle`.)
3. In `EventProcessor.__init__` (line 163), build the table once:
   ```python
   self._post_broadcast_enrichers: dict[EventType, Callable[[StateMachine, Event, str], Awaitable[None]]] = {
       EventType.SUBAGENT_START: self._enrich_subagent_start,
       EventType.SUBAGENT_INFO: self._enrich_subagent_info,
       EventType.AGENT_UPDATE: self._enrich_agent_update,
       EventType.SUBAGENT_STOP: self._enrich_subagent_stop,
       EventType.STOP: self._enrich_stop,
       EventType.USER_PROMPT_SUBMIT: self._enrich_user_prompt_submit,
       EventType.PRE_TOOL_USE: self._enrich_pre_tool_use,
       EventType.TASK_CREATED: self._enrich_task_created,
       EventType.TASK_COMPLETED: self._enrich_task_completed,
       EventType.TEAMMATE_IDLE: self._enrich_teammate_idle,
   }
   ```
   Add `from collections.abc import Awaitable, Callable` to imports.
4. Replace the ten `if` blocks (lines 489–546) with:
   ```python
   enricher = self._post_broadcast_enrichers.get(event.event_type)
   if enricher is not None:
       await enricher(sm, event, agent_id)
   ```
5. Keep the SESSION_START block, the unconditional `ensure_task_poller_running` / `_start_beads_if_available` calls, the SESSION_END block, the two broadcasts, and the room-orchestrator block exactly where they are — their relative order is load-bearing (SESSION_START enrichment runs before the poller bootstrap; SESSION_END runs before the broadcast; the ten table entries all run after the broadcast).
6. Add a comment above the table stating the rule for adding a new event type: "1) add to `EventType`, 2) add a sync handler to `state_machine._DISPATCH_TABLE` if it mutates state, 3) add an `_enrich_*` entry here if it needs async enrichment."

**Verification**:
- `cd /Users/probello/Repos/claude-office/backend && make checkall` (runs ruff, pyright, and the full pytest suite including `tests/test_simulation_pipeline.py` and `tests/test_state_machine.py`).
- Behavioral: `cd /Users/probello/Repos/claude-office && make dev-tmux`, then `make simulate`; confirm agents spawn/depart in the UI and no errors in `tmux capture-pane -t claude-office-dev:backend -p`.

**Do NOT**:
- Do not touch `state_machine.py`'s `_DISPATCH_TABLE` (lines 470–486) — the sync path is already table-driven and correct.
- Do not reorder broadcasts relative to enrichment; QA-004's note about "preserving documented broadcast ordering" refers to exactly this.
- Do not merge the SESSION_START/SESSION_END blocks into the post-broadcast table — they intentionally run pre-broadcast.
- Do not change any handler function in `core/handlers/` in this change (that is ARC-014's territory).

### [ARC-003] Blocking synchronous file I/O on the async event loop (backend)
**Priority**: High | **Effort**: M | **Phase**: 3b — Architecture (remaining)
**Preconditions**: ARC-002 (same file `event_processor.py`/handler surfaces; land dispatch consolidation first)
**Files**: `/Users/probello/Repos/claude-office/backend/app/core/state_machine.py`, `/Users/probello/Repos/claude-office/backend/app/core/handlers/agent_handler.py`, `/Users/probello/Repos/claude-office/backend/app/core/handlers/conversation_handler.py`, `/Users/probello/Repos/claude-office/backend/app/core/task_file_poller.py`, `/Users/probello/Repos/claude-office/backend/app/api/routes/sessions.py`

**Goal**: No async code path performs an unbounded synchronous file read or a blocking `Popen.wait`; big reads run via `asyncio.to_thread`, matching the existing pattern at `transcript_poller.py:213`.

**Steps**:
1. **Worst case — 50 MB read inside `sm.transition()`.** In `state_machine.py`, `_handle_subagent_stop` (lines 340–372) calls `sm.token_tracker.count_tool_uses_from_jsonl(event.data.agent_transcript_path)` at line 362; `count_tool_uses_from_jsonl` (`token_tracker.py:147-170`) reads the whole file (capped at `_MAX_TRANSCRIPT_BYTES = 50_000_000`). Remove lines 361–367 (the `if event.data.agent_transcript_path:` block crediting `tool_count`) from the sync handler.
2. Move that credit into the async path: in `core/handlers/agent_handler.py`, find `handle_subagent_stop` (the async handler invoked from `event_processor._process_event_internal` for SUBAGENT_STOP). At its start (after resolving the agent), add:
   ```python
   if event.data and event.data.agent_transcript_path:
       tool_count = await asyncio.to_thread(
           sm.token_tracker.count_tool_uses_from_jsonl,
           event.data.agent_transcript_path,
       )
       if tool_count > 0:
           sm.tool_uses_since_compaction += tool_count
           logger.debug(f"Credited {tool_count} subagent tool uses to safety counter")
   ```
   Add `import asyncio` if missing. Add a code comment noting the consequence: replay (`sessions.py` `get_session_replay`, which only calls `sm.transition`) no longer credits subagent tool uses to the safety-sign counter — cosmetic only.
3. In `core/handlers/conversation_handler.py:139`, `extract_and_set_boss_speech` calls `response = get_last_assistant_response(translated_path)` (a full-file line scan, `jsonl_parser.py:36-88`). Change to `response = await asyncio.to_thread(get_last_assistant_response, translated_path)`. Add `import asyncio`.
4. In `core/handlers/agent_handler.py:319`, `enrich_agent_from_transcript` calls `task_text = get_first_user_prompt(translated_path)` — wrap: `task_text = await asyncio.to_thread(get_first_user_prompt, translated_path)`. At line 357, `extract_and_set_agent_speech` calls `response = get_last_assistant_response(translated_path)` — wrap the same way.
5. In `core/task_file_poller.py`, `_check_for_changes` (lines 192–238) does `state.task_dir.glob("*.json")` and per-file `.stat()` inline, and `_read_task_files` (lines 240–252) does sync `open()`+`json.load`. Extract the sync work into module-level helpers and call via `asyncio.to_thread`:
   ```python
   def _scan_task_dir_sync(task_dir: Path) -> list[tuple[Path, float]]:
       return [(p, p.stat().st_mtime) for p in task_dir.glob("*.json")]
   ```
   In `_check_for_changes`, replace lines 204–215 with `scanned = await asyncio.to_thread(_scan_task_dir_sync, state.task_dir)` and rebuild `current_mtime` from `scanned`. In `_read_task_files`, wrap the per-file read loop body: `task_data = await asyncio.to_thread(_load_json_sync, task_file)` where `_load_json_sync` does the `open`/`json.load` with the same `(json.JSONDecodeError, OSError)` handling.
6. In `api/routes/sessions.py`, `kill_simulation()` (lines 27–45) blocks on `_simulation_process.wait(timeout=5)` at line 37. It is called from async `clear_database` at line 502 and from sync `trigger_simulation` at line 479. In `clear_database`, change line 502 to `simulation_killed = await asyncio.to_thread(kill_simulation)`. Leave `trigger_simulation`'s call as-is only if you also wrap it: change line 479 to `await asyncio.to_thread(kill_simulation)` (`trigger_simulation` is async too). `import asyncio` is already present in sessions.py.
7. Leave `state_machine.py:802` (`self.token_tracker.update_from_event(event)`) alone: its reads are tail-bounded (`_TOKEN_READ_SIZE = 20_000`, `token_tracker.py:22`) and moving it out of `transition` would break replay token accounting. Add a one-line comment there stating the bounded-read invariant.

**Verification**:
- `cd /Users/probello/Repos/claude-office/backend && make checkall` (includes `tests/test_task_file_poller.py`, `tests/test_transcript_poller.py`, `tests/test_subagent_linking.py`).
- Behavioral: `make dev-tmux` + `make simulate`; agents still get names/speech bubbles (transcript enrichment path) and the safety-sign counter still increments on subagent stop.

**Do NOT**:
- Do not make `StateMachine.transition` or any `_handle_*` in `state_machine.py` async — the sync dispatch table is used by replay and must stay synchronous.
- Do not touch `transcript_poller.py`, `beads_poller.py`, or `git_service.py` — they already use `asyncio.to_thread`/`run_in_executor` correctly (`transcript_poller.py:213`, `beads_poller.py:241`, `git_service.py:204`).
- Do not raise or remove the `_MAX_TRANSCRIPT_BYTES` cap in `token_tracker.py`.

### [ARC-004] Distributed agent-state ownership with no single source of truth (frontend)
**Priority**: High | **Effort**: L | **Phase**: 2 — Critical Architecture (frontend chain: QA-001 characterization tests → ARC-017 → ARC-004 → ARC-005)
**Preconditions**: QA-001 characterization slice (tests for gameStore queue actions + machine service notifications), ARC-017 (cycle break / port interface must land first — see its entry)
**Files**: `/Users/probello/Repos/claude-office/frontend/src/stores/gameStore.ts`, `/Users/probello/Repos/claude-office/frontend/src/machines/queueManager.ts`, `/Users/probello/Repos/claude-office/frontend/src/machines/agentMachineService.ts`, `/Users/probello/Repos/claude-office/frontend/src/systems/animationSystem.ts`

**Goal**: Queue membership, slot reservations, and ready-position occupancy have exactly one writer (the Zustand store); `QueueManager`'s private maps are folded into store state; the six `setTimeout(0)` re-entrancy escapes are replaced by an explicit flush queue; the 3-second "stuck boss lock" watchdog is deleted.

**Steps** (three sub-batches, verify between each):

*Batch 1 — fold QueueManager state into the store (3 files).*
1. In `gameStore.ts`, extend the `GameStore` interface (after the Queue State section at lines 166–176) with reservation/occupancy state and actions:
   ```typescript
   // ========== Queue Reservations (single-writer: only QueueManager calls these) ==========
   queueReservations: { arrival: Map<number, string>; departure: Map<number, string> };
   readyOccupants: { arrival: string | null; departure: string | null };
   reserveQueueSlot: (queueType: "arrival" | "departure", index: number, agentId: string) => void;
   releaseQueueSlot: (queueType: "arrival" | "departure", agentId: string) => void;
   setReadyOccupant: (queueType: "arrival" | "departure", agentId: string | null) => void;
   ```
   Implement the three actions with single `set()` calls (copy the Map before mutating, same style as existing agent actions at lines 459–542). Initialize the state in the store creator and clear it in all three reset variants (`reset` line 1018, `resetForReplay` line 1027, `resetForSessionSwitch` line 1037).
2. In `queueManager.ts`, delete the private fields `reservations` (lines 25–28) and `readyOccupant` (lines 30–38). Rewrite every method (`reserveQueueSlot`, release/clear methods, ready-occupant getters/setters) to read from `useGameStore.getState().queueReservations` / `.readyOccupants` and write only through the new store actions. `QueueManager` becomes a stateless policy façade; keep its public API identical so `agentMachineService.ts` call sites (import at line 27) do not change in this batch.
3. Delete any `QueueManager`-reset logic that duplicated store resets; the store resets now cover it.
4. Verify (see commands below), including the QA-001 characterization tests.

*Batch 2 — replace the six `setTimeout(0)` escapes (1 file; = QA-009).*
5. In `agentMachineService.ts`, the six zero-delay timeouts are at lines 426, 474, 516, 535, 570, 609 (`setTimeout(() => this.notifyBossAvailable(), 0)` ×4, `setTimeout(() => this.triggerDeparture(agentId), 0)` at 516, `setTimeout(() => this.notifyBubbleComplete(agentId), 0)` at 535). Add an explicit deferred-notification queue to the service class:
   ```typescript
   private deferred: Array<() => void> = [];
   private flushScheduled = false;

   private defer(fn: () => void): void {
     this.deferred.push(fn);
     if (!this.flushScheduled) {
       this.flushScheduled = true;
       queueMicrotask(() => this.flushDeferred());
     }
   }

   private flushDeferred(): void {
     this.flushScheduled = false;
     const batch = this.deferred;
     this.deferred = [];
     for (const fn of batch) fn();
   }
   ```
   Replace each of the six `setTimeout(..., 0)` calls with `this.defer(() => ...)` preserving the exact callback. In `reset()`, clear `this.deferred` and reset `flushScheduled`. Export nothing new; add `flushDeferredForTest()` public method only if the QA-001 tests need synchronous flushing.
6. Verify.

*Batch 3 — delete the watchdog (1 file).*
7. In `animationSystem.ts`, `checkQueueAdvancement` (lines 378–409) contains the stuck-boss watchdog: fields `bossLockedSince` (line 375) and the `Date.now() - this.bossLockedSince > 3000` auto-release with `console.warn("[AnimationSystem] Auto-releasing stuck boss lock")`. Delete the `bossLockedSince` field and the whole watchdog branch, keeping only the early `return` when `store.boss.inUseBy !== null` (the queue must not advance while the boss is busy). Keep `lastNotifiedAgentId` handling intact.
8. Verify, then manually exercise: run a simulation with 4+ agents and confirm no agent gets stuck at the A0/D0 ready position across two full arrival/departure cycles.

**Verification**:
- After each batch: `cd /Users/probello/Repos/claude-office/frontend && make checkall` (fmt, lint, typecheck, build, then vitest).
- `cd /Users/probello/Repos/claude-office/frontend && bun run test` for the characterization tests specifically.
- Behavioral (after batch 3): `make dev-tmux` + `make simulate` from repo root; watch a full multi-agent cycle; check browser console for errors — the watchdog warning must never be needed because the lock can no longer leak.

**Do NOT**:
- Do not start this refactor before the QA-001 characterization tests exist and pass against the current behavior.
- Do not change the store's existing queue actions' semantics (`enqueueArrival`/`dequeueArrival` etc., lines 548–648) in this issue — QA-003/QA-006 own those.
- Do not delete `hmrCleanup.ts` — it still resets the Pixi app; it just loses queue-manager duties naturally.
- Do not remove the `store.boss.inUseBy !== null` early-return in `checkQueueAdvancement`; only the timer-based force-release goes.
- Do not swap `queueMicrotask` for another `setTimeout` variant.

### [ARC-005] `gameStore.ts` is a god store (~9 concerns)
**Priority**: High | **Effort**: L | **Phase**: 2 — Critical Architecture (after ARC-004/017; blocks QA-003, QA-012, QA-013)
**Preconditions**: ARC-004 (store shape must be settled), QA-006 (single-set dequeue lands before structural moves)
**Files**: `/Users/probello/Repos/claude-office/frontend/src/stores/gameStore.ts`, `/Users/probello/Repos/claude-office/frontend/src/stores/slices/bubbleSlice.ts` (new), `/Users/probello/Repos/claude-office/frontend/src/stores/slices/replaySlice.ts` (new), `/Users/probello/Repos/claude-office/frontend/src/stores/slices/debugSlice.ts` (new)

**Goal**: `gameStore.ts` shrinks by extracting the bubble, replay, and debug/persistence subsystems into Zustand slices, while `useGameStore` keeps the exact same external interface so no consumer import changes.

**Steps** (three sub-batches, verify between each):

*Batch 1 — bubbles.*
1. Create `frontend/src/stores/slices/bubbleSlice.ts` using the Zustand slice pattern:
   ```typescript
   import type { StateCreator } from "zustand";
   import type { GameStore } from "../gameStore";

   export interface BubbleSlice {
     enqueueBubble: GameStore["enqueueBubble"];
     advanceBubble: GameStore["advanceBubble"];
     clearBubbles: GameStore["clearBubbles"];
     getCurrentBubble: GameStore["getCurrentBubble"];
     isBubbleQueueEmpty: GameStore["isBubbleQueueEmpty"];
     hasBubbleText: GameStore["hasBubbleText"];
   }

   export const createBubbleSlice: StateCreator<GameStore, [], [], BubbleSlice> = (set, get) => ({
     // move bodies verbatim from gameStore.ts lines 712-891
   });
   ```
   Move the six bubble action implementations (`enqueueBubble` at line 712 through `hasBubbleText` ending ~line 891) verbatim; they read/write agent `bubbleQueue` state via `set`/`get`, which the slice receives.
2. In `gameStore.ts`, replace the moved block with `...createBubbleSlice(set, get, store)` spread inside the store creator (Zustand slice composition: `create<GameStore>()((...a) => ({ ...coreState(...a), ...createBubbleSlice(...a) }))`). Keep the `GameStore` interface in `gameStore.ts` as the single source of truth (slices import the type from it — no circular value imports, type-only).
3. Verify.

*Batch 2 — replay + connection UI state.*
4. Create `slices/replaySlice.ts` holding `isReplaying`, `replaySpeed`, `replayEvents`, `currentReplayIndex`, `setReplaying`, `setReplaySpeed`, `setReplayEvents`, `setReplayIndex`, and `resetForReplay` (interface lines 239–256, `resetForReplay` at line 1027). Move implementations verbatim; compose as in batch 1.
5. Verify.

*Batch 3 — debug/persistence.*
6. Create `slices/debugSlice.ts` holding `debugMode`, `showPaths`, `showQueueSlots`, `showPhaseLabels`, `showObstacles`, `setDebugMode`, `toggleDebugOverlay`, `loadPersistedDebugSettings` plus the module helpers `loadDebugSettings`/`saveDebugSettings` and `DEBUG_SETTINGS_KEY` (currently `gameStore.ts:276, 298-327, 1001-1012`). Move verbatim.
7. Verify; then grep to confirm no consumer imported the moved helpers directly: `grep -rn "loadDebugSettings\|saveDebugSettings" frontend/src --include=*.ts --include=*.tsx` — only `debugSlice.ts` should match.

**Verification**:
- After each batch: `cd /Users/probello/Repos/claude-office/frontend && make checkall`.
- `grep -rn "from \"@/stores/gameStore\"" frontend/src | wc -l` before and after — count must be unchanged (no consumer churn).

**Do NOT**:
- Do not rename `useGameStore` or move its export out of `stores/gameStore.ts` — the entire point is an unchanged import surface.
- Do not change any action's behavior while moving it (byte-for-byte moves; refactoring of duplicated `new Map(...)` patterns is QA-003).
- Do not extract the agent/queue/boss core — those stay in `gameStore.ts` (they are the hot path ARC-004 just stabilized).
- Do not create nested slice directories beyond `stores/slices/`.

### [ARC-006] Per-frame Zustand writes with O(n) Map copies drive React re-renders at 60fps
**Priority**: High | **Effort**: M | **Phase**: 3b — Architecture (remaining; explicitly after ARC-004/017)
**Preconditions**: ARC-004/ARC-017
**Files**: `/Users/probello/Repos/claude-office/frontend/src/systems/animationSystem.ts`, `/Users/probello/Repos/claude-office/frontend/src/stores/gameStore.ts`, `/Users/probello/Repos/claude-office/frontend/src/app/page.tsx`, `/Users/probello/Repos/claude-office/frontend/src/components/game/OfficeGame.tsx`

**Goal**: The rAF tick issues at most one Zustand `set()` per frame regardless of agent count, and `page.tsx` no longer re-renders the whole page at animation rate.

**Steps**:
1. In `gameStore.ts`, add one batched action to the agent section (near `updateAgentPosition`, line 469):
   ```typescript
   applyFrameUpdates: (updates: Map<string, { position: Position; path: PathState | null | undefined }>) => void;
   ```
   Implementation: single `set((state) => ...)` that clones `state.agents` once, applies every entry (`currentPosition` always; `path` only when the update's `path !== undefined`), and returns `{ agents: newAgents }`. `undefined` means "leave path untouched"; `null` means "clear path" (matches existing `updateAgentPath(agentId, null)` semantics).
2. In `animationSystem.ts`, rewrite `updateAgentPositions` (lines 163–207). Currently, per moving agent per frame it calls `store.updateAgentPosition(agentId, newPosition)` (line ~190) and `store.updateAgentPath(...)` — each cloning the whole Map. Change to: accumulate `const frameUpdates = new Map<...>()` across the loop (still calling `updateAgentObstacle` and `collisionManager.updatePosition` per agent as today, and still collecting arrivals), then after the loop call `store.applyFrameUpdates(frameUpdates)` once, then invoke `this.handleArrival(...)` for each arrival collected (arrival callbacks must run after the batched commit so machines observe final positions).
3. In `page.tsx:146`, the root page subscribes to the whole agents Map: `const agents = useGameStore(useShallow(selectAgents));`. Find what `agents` is used for in that file (header/sidebar counts). Replace with a narrow derived selector, e.g. `const agentCount = useGameStore((s) => s.agents.size);` plus any other scalar the page actually renders. Remove the `useShallow(selectAgents)` subscription entirely from `page.tsx`.
4. In `OfficeGame.tsx:204`, keep the `useShallow(selectAgents)` subscription (the canvas genuinely renders agents) but hoist the repeated scans: lines 489, 529, 541, 635, 649, 699, 717 each run `Array.from(agents.values()).filter(...)` per render. Compute the derived arrays once per render with `useMemo(() => { const all = Array.from(agents.values()); return { working: all.filter(...), queued: all.filter(...), ... }; }, [agents])` and reference the memo fields at each site.

**Verification**:
- `cd /Users/probello/Repos/claude-office/frontend && make checkall`.
- Behavioral: `make dev-tmux` + `make simulate`; in Chrome DevTools Performance panel record 10 s during multi-agent movement — commit counts for the header/sidebar components should be near zero while agents walk (previously ~60/s), and the office canvas still animates smoothly.

**Do NOT**:
- Do not remove per-frame positions from Zustand entirely (imperative Pixi writes). That is a deeper redesign requiring changes to `AgentSprite` rendering; do not attempt it inside this issue — if pursued later, it needs its own design pass.
- Do not delete the existing `updateAgentPosition`/`updateAgentPath` single-agent actions — non-frame callers (spawn, teleport, machine actions) still use them.
- Do not "optimize" collision/obstacle updates into the batch — they are imperative singletons, not store state.

### [ARC-007] Frontend test coverage gap — the most complex logic has zero tests (= QA-001)
**Priority**: High | **Effort**: L | **Phase**: 3b — Architecture (characterization slice is a Phase 2 precondition; remainder here). Alias: this is the same issue as QA-001 — this entry is canonical; do not double-implement.
**Preconditions**: None (the characterization slice must land BEFORE ARC-004/005/QA-003/QA-009)
**Files**: `/Users/probello/Repos/claude-office/frontend/tests/gameStoreQueues.test.ts` (new), `/Users/probello/Repos/claude-office/frontend/tests/gameStoreBubbles.test.ts` (new), `/Users/probello/Repos/claude-office/frontend/tests/gameStoreResets.test.ts` (new), `/Users/probello/Repos/claude-office/frontend/tests/agentMachine.test.ts` (new), `/Users/probello/Repos/claude-office/frontend/tests/stateReconciler.test.ts` (new, after ARC-018), `/Users/probello/Repos/claude-office/frontend/tests/astar.test.ts` (new)

**Goal**: The queue actions, bubble queueing, three reset variants, the agent machine (via injected actions), the 4-branch spawn decision, and A* pathfinding each have vitest coverage; the characterization slice (items 1–3) exists before any Phase 2 frontend refactor starts.

**Steps**:
1. *Characterization slice, part 1 — queue actions.* Create `frontend/tests/gameStoreQueues.test.ts`. Vitest is configured (`frontend/vitest.config.ts` maps `@/ → src/`). Pattern: import `useGameStore` from `@/stores/gameStore`; in `beforeEach` call `useGameStore.getState().reset()`. Seed agents via `useGameStore.getState().addAgent(backendAgent, {x:0,y:0})` (see `BackendAgent` shape in `@/types/generated`). Lock in current behavior of: `enqueueArrival` (dedupe + queueIndex assignment, `gameStore.ts:548-568`), `enqueueDeparture` (570–590), `dequeueArrival`/`dequeueDeparture` (592–630 — assert front-id return and reindexing of remaining agents), `advanceQueue` (632–648), `syncQueues` (650+), and `removeAgent`'s queue pruning (440–457). Include a test documenting the current two-`set()` dequeue behavior so QA-006's change to a single `set()` is a deliberate diff.
2. *Characterization slice, part 2 — bubbles.* Create `gameStoreBubbles.test.ts` covering `enqueueBubble` (immediate option), `advanceBubble`, `clearBubbles`, `getCurrentBubble`, `isBubbleQueueEmpty`, `hasBubbleText` (`gameStore.ts:712-891`) for both `"boss"` and an agent entity id.
3. *Characterization slice, part 3 — resets.* Create `gameStoreResets.test.ts`: populate every store area (agents, queues, bubbles, event log, replay state, debug flags), then snapshot which fields each of `reset()` (line 1018), `resetForReplay()` (1027), `resetForSessionSwitch()` (1037) clears vs. preserves. Assert the exact current behavior (these three differ subtly — that is the point).
4. *Machine tests.* Create `agentMachine.test.ts`: `createAgentMachine(actions)` (`frontend/src/machines/agentMachine.ts:44`) takes an injected `AgentMachineActions` object — build a mock actions object of vi.fn()s, `createActor(createAgentMachine(mock))`, drive it through spawn → walk-to-queue → ready → converse → desk → departure event sequences (event names are in `AgentMachineEvent` in `agentMachineCommon.ts`), asserting the mock calls and state values. No canvas or React needed.
5. *Spawn decision table.* After ARC-018 extracts `reconcileState` (or directly against the hook's logic if ARC-018 has not landed — in that case name the file `useWebSocketEvents.spawn.test.ts` and export the pure helper first): cover the 4 branches at `useWebSocketEvents.ts:113-147` — backend state `"arriving"` → elevator spawn; in `arrivalQueue` → queue position + `skipArrival`; in `departureQueue` → departure position + `skipArrival`; `desk` set → desk position.
6. *Pathfinding.* Create `astar.test.ts` for the A* module used by `calculatePath` (`frontend/src/systems/pathfinding.ts` / `astar.ts`): straight-line path, obstacle detour, no-path-returns-null/empty, and start==goal.
7. Run the full suite and fix only test-side issues; if a test reveals an actual code bug, report it — do not change product code inside this issue.

**Verification**:
- `cd /Users/probello/Repos/claude-office/frontend && bun run test` — all new files pass.
- `cd /Users/probello/Repos/claude-office/frontend && make checkall`.

**Do NOT**:
- Do not modify product source to "make tests pass" — characterization tests document current behavior, bugs and all.
- Do not import React components or Pixi into these tests; every target is pure TS.
- Do not write a Playwright suite here (the audit mentions it as a later addition; keep this issue to vitest units).
- Do not let ARC-004/005 start before steps 1–3 are merged.

### [ARC-008] Hooks installer can silently wipe the user's `settings.json`
**Priority**: High | **Effort**: S | **Phase**: 3b — Architecture (listed in Immediate Actions #4 — do early)
**Preconditions**: None
**Files**: `/Users/probello/Repos/claude-office/hooks/manage_hooks.py`

**Goal**: A malformed `~/.claude/settings.json` aborts installation with a clear error instead of being overwritten with `{}`; every write is atomic (temp file + `os.replace`) and preceded by a one-time `.bak` backup.

**Steps**:
1. In `manage_hooks.py`, `load_settings` (lines 32–41) currently returns `{}` on `json.JSONDecodeError` with only a printed warning. Change the except block to abort:
   ```python
   except json.JSONDecodeError as e:
       raise SystemExit(
           f"ERROR: {path} exists but is not valid JSON ({e}).\n"
           "Refusing to continue: proceeding would overwrite your settings.\n"
           "Fix or move the file, then re-run install."
       ) from e
   ```
2. Rewrite `save_settings` (lines 44–48) to be atomic and keep a backup:
   ```python
   def save_settings(path: Path, settings: dict[str, Any]) -> None:
       """Save settings atomically, backing up the original once per run."""
       path.parent.mkdir(parents=True, exist_ok=True)
       if path.exists():
           backup = path.with_suffix(".json.bak")
           if not backup.exists():
               shutil.copy2(path, backup)
       tmp = path.with_suffix(".json.tmp")
       with open(tmp, "w", encoding="utf-8") as f:
           json.dump(settings, f, indent=2)
           f.write("\n")
       os.replace(tmp, path)
   ```
   Add `import shutil` at the top (`os` is already imported). Note: the `.bak` persists across runs by design (first-mutation snapshot); the `if not backup.exists()` guard keeps the oldest known-good copy.
3. `install_hooks` (line 103) and `uninstall_hooks` (line 145) need no changes — they call `load_settings`/`save_settings` (lines 113/137 and 156/189) and inherit the new safety.
4. Security note: this touches the user's Claude Code configuration file handling — flag the PR for manual review; the change must never regenerate or drop existing keys/permissions in `settings.json` (it only adds fail-safe behavior).

**Verification**:
- `cd /Users/probello/Repos/claude-office/hooks && make checkall` (after ARC-001's Makefile exists; otherwise `uv run pyright && uv run ruff check .`).
- Manual: `CLAUDE_CONFIG_DIR=$(mktemp -d)` then: (a) write invalid JSON to `$CLAUDE_CONFIG_DIR/settings.json`, run `uv run python manage_hooks.py install --hook-cmd claude-office-hook --dry-run` → expect non-zero exit and the ERROR message, file untouched; (b) write valid JSON with an unrelated key (`{"model": "opus"}`), run install (non-dry) → expect hooks added, `"model"` preserved, and a `settings.json.bak` created.

**Do NOT**:
- Do not auto-repair or re-serialize a corrupt settings file — abort is the whole fix.
- Do not change hook registration logic (`create_hook_config`, `is_same_hook`) or `HOOK_TYPES`.
- Do not write the backup on every save inside a single run loop such that it overwrites the pre-run original.

### [ARC-009] `scripts/scenarios/teams.py` is broken (ImportError); `teams` and `quick` scenarios orphaned
**Priority**: High | **Effort**: S | **Phase**: 3b, but bundled into Phase 2 with ARC-001 (CI must pass over `scripts/`)
**Preconditions**: None (land in the same PR as ARC-001)
**Files**: `/Users/probello/Repos/claude-office/scripts/scenarios/_base.py`, `/Users/probello/Repos/claude-office/scripts/scenarios/__init__.py`, `/Users/probello/Repos/claude-office/scripts/simulate_events.py`

**Goal**: `python scripts/simulate_events.py teams` and `... quick` both run; `SCENARIOS` is defined once in the scenarios package; the `# type: ignore[call-arg]` is gone.

**Steps**:
1. `scripts/scenarios/teams.py:15` imports `TeamSimulationContext` from `scripts.scenarios._base`, but `_base.py` defines only `SimulationContext` (class at `_base.py:58`) — confirmed ImportError. Restore the class in `_base.py` (append after `SimulationContext`). The API surface `teams.py` uses (verified by grep): constructor kwargs `team_name`, `project_name`, `verbose`; methods `add_lead(session_id=...)`, `add_teammate(name, session_id=...)`, `log(msg)`; attribute `project_name`; members expose `send_event(...)` (they are `SimulationContext`s):
   ```python
   @dataclass
   class TeamSimulationContext:
       """Groups one lead and N teammate SimulationContexts for team scenarios."""

       team_name: str
       project_name: str = "TeamProject"
       verbose: bool = True
       members: list[SimulationContext] = field(default_factory=list)

       def _add(self, session_id: str) -> SimulationContext:
           ctx = SimulationContext(session_id=session_id, verbose=self.verbose)
           self.members.append(ctx)
           return ctx

       def add_lead(self, session_id: str) -> SimulationContext:
           return self._add(session_id)

       def add_teammate(self, name: str, session_id: str) -> SimulationContext:
           return self._add(session_id)

       def log(self, msg: str) -> None:
           if self.verbose:
               print(msg)
   ```
   Check `SimulationContext`'s actual constructor signature in `_base.py:58` first and match it (it accepts `session_id` and `verbose` — see `simulate_events.py:83-86`). If `dataclass`/`field` are not yet imported in `_base.py`, they are (SimulationContext is a class there — mirror its idiom).
2. Make the scenarios package the single registry. In `scripts/scenarios/__init__.py` (currently exports only basic/complex/edge_cases), add:
   ```python
   from collections.abc import Callable

   from ._base import SimulationContext
   from .basic import run as run_basic
   from .complex import run as run_complex
   from .edge_cases import run as run_edge_cases
   from .quick import run as run_quick
   from .teams import run as run_teams

   SCENARIOS: dict[str, Callable[[SimulationContext], None]] = {
       "basic": run_basic,
       "complex": run_complex,
       "edge_cases": run_edge_cases,
       "quick": run_quick,
       "teams": run_teams,
   }

   __all__ = ["SCENARIOS", "run_basic", "run_complex", "run_edge_cases", "run_quick", "run_teams"]
   ```
   (`scripts/scenarios/quick.py` already defines `run(ctx: SimulationContext)` at line 17.)
3. In `scripts/simulate_events.py`, delete the local `SCENARIOS: dict[str, object]` (lines 35–39) and the per-module run imports (lines 31–33); import instead: `from scripts.scenarios import SCENARIOS`. In `main()`, delete the dead guard `if scenario_fn is None: parser.error(...)` (argparse `choices=list(SCENARIOS.keys())` already rejects unknown scenarios) and change the call `scenario_fn(ctx)  # type: ignore[call-arg]` to plain `scenario_fn(ctx)` — the typed dict makes the ignore unnecessary. Update the module docstring's "three pre-built scenarios" wording to list all five. (This completes most of ARC-031; see that entry.)
4. Update the argparse epilog/choices only via `SCENARIOS` (already derived from `.keys()` — confirm nothing else hardcodes the scenario list: `grep -n "edge_cases\|complex\|basic" scripts/simulate_events.py`).

**Verification**:
- `cd /Users/probello/Repos/claude-office/backend && uv run python -c "from scripts.scenarios import SCENARIOS; import sys; sys.path.insert(0,'..'); print(sorted(SCENARIOS))"` — simpler: `cd /Users/probello/Repos/claude-office && uv run python -c "import sys; sys.path.insert(0,'.'); from scripts.scenarios import SCENARIOS; print(sorted(SCENARIOS))"` → prints all five names, no ImportError.
- `cd /Users/probello/Repos/claude-office/backend && uv run ruff check ../scripts` passes.
- With backend running (`make dev-tmux`): `uv run python scripts/simulate_events.py teams --quiet` completes; the office UI shows the team room merge.

**Do NOT**:
- Do not delete `teams.py` — the audit prefers restoring the context class (the scenario exercises the room-orchestrator merge path that has no other coverage).
- Do not rename existing scenario keys (`basic`, `complex`, `edge_cases`) — README/docs reference them.
- Do not change `_base.py`'s existing `SimulationContext` behavior (compaction simulation, event sending).

### [ARC-010] Event contract hand-duplicated across 4+ places; OpenCode plugin has already drifted
**Priority**: High | **Effort**: M | **Phase**: 3b, sequenced with ARC-001 (contract test only has teeth once CI runs it)
**Preconditions**: ARC-001 (CI), ARC-009 (same PR train)
**Files**: `/Users/probello/Repos/claude-office/opencode-plugin/src/index.ts`, `/Users/probello/Repos/claude-office/backend/tests/test_hooks_contract.py` (new), `/Users/probello/Repos/claude-office/scripts/check_plugin_event_types.py` (new), `/Users/probello/Repos/claude-office/.github/workflows/ci.yml`

**Goal**: The hooks mapper's output is validated against the backend Pydantic `Event` model in CI, and a CI check fails whenever the plugin's hand-written `EventType` union drifts from `backend/app/models/events.py`.

**Steps**:
1. Fix the confirmed drift now: in `opencode-plugin/src/index.ts`, the `type EventType =` union (lines 44–64) ends at `"background_task_notification"`. Append the three missing members so it matches the backend enum (`backend/app/models/events.py:17-42`, 23 values):
   ```typescript
     | "background_task_notification"
     | "task_created"
     | "task_completed"
     | "teammate_idle";
   ```
   Also add the missing optional fields to the plugin's `interface EventData` (index.ts lines ~66–92) to mirror the backend model: `task_id?: string; task_subject?: string; team_name?: string; teammate_name?: string;`.
2. Add the hooks contract test as a backend test (the backend venv has pydantic; hooks does not have the backend installed). Create `backend/tests/test_hooks_contract.py`:
   ```python
   """Contract test: hooks map_event() output must validate against the backend Event model."""
   import sys
   from pathlib import Path

   import pytest

   HOOKS_SRC = Path(__file__).resolve().parents[2] / "hooks" / "src"
   sys.path.insert(0, str(HOOKS_SRC))

   from claude_office_hooks.event_mapper import map_event  # noqa: E402

   from app.models.events import Event  # noqa: E402

   RAW_CASES = [
       ("session_start", {"session_id": "abc123", "cwd": "/tmp/proj", "source": "startup"}),
       ("user_prompt_submit", {"session_id": "abc123", "prompt": "do the thing"}),
       ("pre_tool_use", {"session_id": "abc123", "tool_name": "Read", "tool_input": {"file_path": "/x"}}),
       ("pre_tool_use", {"session_id": "abc123", "tool_name": "Task", "tool_use_id": "t1",
                          "tool_input": {"subagent_type": "general", "description": "d", "prompt": "p"}}),
       ("post_tool_use", {"session_id": "abc123", "tool_name": "Read", "tool_response": "ok"}),
       ("subagent_stop", {"session_id": "abc123", "agent_id": "subagent_t1"}),
       ("stop", {"session_id": "abc123"}),
       ("pre_compact", {"session_id": "abc123"}),
       ("session_end", {"session_id": "abc123", "reason": "exit"}),
       ("notification", {"session_id": "abc123", "message": "needs permission"}),
   ]

   @pytest.mark.parametrize("hook_name,raw", RAW_CASES)
   def test_map_event_output_validates(hook_name: str, raw: dict) -> None:
       payload = map_event(hook_name, raw)
       if payload is None:
           pytest.skip(f"{hook_name} intentionally unmapped for this input")
       Event.model_validate(payload)
   ```
   Before finalizing, read `hooks/src/claude_office_hooks/event_mapper.py` `map_event()`'s signature (module bottom, dispatch at lines 372–406) and adjust the call/raw shapes to its real parameters (it takes the hook event name plus the raw hook stdin dict; some inputs also carry `transcript_path`). If `map_event` returns a payload lacking `session_id`, adapt by asserting on the assembled payload the hook actually POSTs (see `main.py`'s call site).
3. Add the plugin drift check. Create `scripts/check_plugin_event_types.py`:
   ```python
   #!/usr/bin/env python3
   """Fail if opencode-plugin's EventType union is missing backend EventType values."""
   import re
   import sys
   from pathlib import Path

   ROOT = Path(__file__).resolve().parents[1]
   sys.path.insert(0, str(ROOT / "backend"))

   from app.models.events import EventType  # noqa: E402

   plugin_src = (ROOT / "opencode-plugin" / "src" / "index.ts").read_text(encoding="utf-8")
   union_match = re.search(r"type EventType =(.*?);", plugin_src, re.DOTALL)
   if not union_match:
       sys.exit("Could not find `type EventType =` union in opencode-plugin/src/index.ts")
   plugin_values = set(re.findall(r'"([a-z_]+)"', union_match.group(1)))
   backend_values = {e.value for e in EventType}
   missing = sorted(backend_values - plugin_values)
   extra = sorted(plugin_values - backend_values)
   if missing or extra:
       sys.exit(f"Plugin EventType drift. Missing: {missing} Extra: {extra}")
   print(f"Plugin EventType in sync ({len(backend_values)} values).")
   ```
4. Wire both into CI: in `.github/workflows/ci.yml`'s `backend` job, the contract test runs automatically via `make -C backend checkall` (pytest picks up the new file). Add one step to the backend job: `- run: cd backend && uv run python ../scripts/check_plugin_event_types.py`.
5. Leave `hooks/event_mapper.py`'s bare string literals (lines 101, 116, 182, 214) as-is — the contract test now guards them. Do not import backend enums into hooks (hooks must stay dependency-free; see its "never block Claude Code" design).

**Verification**:
- `cd /Users/probello/Repos/claude-office/backend && uv run pytest tests/test_hooks_contract.py -v`
- `cd /Users/probello/Repos/claude-office && backend/.venv/bin/python scripts/check_plugin_event_types.py` (or `cd backend && uv run python ../scripts/check_plugin_event_types.py`) → "in sync (23 values)".
- `cd /Users/probello/Repos/claude-office/opencode-plugin && bun run typecheck`.
- Temporarily delete one union member locally and re-run the drift script → must fail; restore.

**Do NOT**:
- Do not change the wire format or any `EventType` string value.
- Do not add a backend dependency to `hooks/pyproject.toml` or a runtime schema fetch to the plugin.
- Do not auto-generate the plugin's whole `index.ts` — only the drift check is in scope (full generation can be a follow-up once QA-002's restructuring lands).
- Do not "fix" the plugin to *send* the three new event types — OpenCode has no such lifecycle events; only the type contract is being repaired.
### [ARC-011] Layering inversion — `core/` and `services/` import the API layer's WebSocket singleton
**Priority**: Medium | **Effort**: S | **Phase**: 2 — Critical Architecture (promoted; blocks ARC-012, QA-014, QA-015)
**Preconditions**: SEC-003 (lands first — it also touches `git_service.py`; refactors must not lose the hardening)
**Files**: `/Users/probello/Repos/claude-office/backend/app/core/connection_manager.py` (new), `/Users/probello/Repos/claude-office/backend/app/api/websocket.py`, `/Users/probello/Repos/claude-office/backend/app/core/event_processor.py`, `/Users/probello/Repos/claude-office/backend/app/core/broadcast_service.py`, `/Users/probello/Repos/claude-office/backend/app/services/git_service.py`

**Goal**: `ConnectionManager` and its singleton live in `app/core/`, so no `core/` or `services/` module imports from `app/api/`; `app/api/websocket.py` re-exports for backward compatibility.

**Steps**:
1. Create `backend/app/core/connection_manager.py`. Move — verbatim — from `app/api/websocket.py`: the `ConnectionManager` class (lines 53–249), the singleton `manager = ConnectionManager()` (line 252), and `get_manager()`/`override_manager()` (lines 255–267), plus the `asyncio`, `logging`, `typing.Any`, `fastapi.WebSocket`, `starlette.websockets.WebSocketState` imports and the module `logger`.
2. Leave the transport-level validators in `app/api/websocket.py`: `_VALID_ID_PATTERN`, `_ALLOWED_WS_ORIGINS`, `validate_websocket_origin` (lines 26–45, uses `hmac` + settings), and `validate_session_id`. At the top of the slimmed `app/api/websocket.py` add the compatibility re-export:
   ```python
   from app.core.connection_manager import (  # noqa: F401
       ConnectionManager,
       get_manager,
       manager,
       override_manager,
   )
   ```
3. Update the three inverted imports to the new home:
   - `app/core/event_processor.py:21`: `from app.api.websocket import manager` → `from app.core.connection_manager import manager` (used at line 570 for `manager.overview_connections`).
   - `app/core/broadcast_service.py:14`: same change.
   - `app/services/git_service.py:11`: same change.
4. Do not touch `app/main.py` or `app/api/routes/sessions.py` — they import from `app.api.websocket`, which still works via the re-export (keeps this change to 5 files; ARC-012 revisits those call sites anyway).
5. Confirm no other `core/` or `services/` file imports `app.api`: `grep -rn "from app.api" backend/app/core backend/app/services` → must return nothing.

**Verification**:
- `cd /Users/probello/Repos/claude-office/backend && make checkall`.
- `grep -rn "from app.api" /Users/probello/Repos/claude-office/backend/app/core /Users/probello/Repos/claude-office/backend/app/services` → empty.
- Behavioral: `make dev-tmux` + open http://localhost:3000 — WebSocket state updates still stream (git status panel populates).

**Do NOT**:
- Do not change any `ConnectionManager` method body while moving it (QA-015's broadcast-loop dedup is a separate issue).
- Do not move `validate_websocket_origin`/`validate_session_id` into core — they are transport concerns and depend on the API-key settings check.
- Do not delete the re-exports from `app/api/websocket.py` in this change; ARC-012 migrates the remaining consumers.

### [ARC-012] DI seams don't work — overrides rebind a module attribute, not the captured reference
**Priority**: Medium | **Effort**: M | **Phase**: 3b — Architecture (remaining; explicitly after ARC-011)
**Preconditions**: ARC-011, SEC-003
**Files**: `/Users/probello/Repos/claude-office/backend/app/api/routes/sessions.py`, `/Users/probello/Repos/claude-office/backend/app/core/broadcast_service.py`, `/Users/probello/Repos/claude-office/backend/app/services/git_service.py`, `/Users/probello/Repos/claude-office/backend/app/core/event_processor.py`, `/Users/probello/Repos/claude-office/backend/app/db/database.py`

**Goal**: `override_manager()`/`override_event_processor()`/`override_engine()` actually take effect everywhere, because consumers resolve the singleton at use time via `get_manager()`/`get_event_processor()` instead of binding `manager`/`event_processor` at import; the stale `engine` alias is deleted.

**Steps**:
1. In `app/api/routes/sessions.py` (lines 14–15): delete `from app.api.websocket import manager` and `from app.core.event_processor import event_processor`; add `from app.core.connection_manager import get_manager` and `from app.core.event_processor import get_event_processor`. Then update the use sites (grep `manager\.` and `event_processor\.` in the file): `event_processor.clear_all_sessions()` (line 516), `event_processor.remove_session(...)` (line 558), `event_processor.get_event_summary(evt)` (line 460), `manager.broadcast_all(...)` (lines 519, 561) → `get_event_processor().…` / `get_manager().…`.
2. In `app/core/broadcast_service.py`: replace the module-level `from app.core.connection_manager import manager` with `from app.core.connection_manager import get_manager`, and change every `manager.` call site in the file (lines 38, 63, 74, 89, 115, 125 — `broadcast`, `broadcast_room`, `overview_connections`, `broadcast_overview`) to `get_manager().`.
3. In `app/services/git_service.py`: same substitution — the only use is `manager.broadcast(...)`/`manager.broadcast_all(...)` inside `_broadcast_status` (lines 267, 273).
4. In `app/core/event_processor.py`: same substitution for the `manager.overview_connections` check at line 570.
5. In `app/db/database.py`: delete the stale alias `engine = _engine` at line 103 (verified: no module imports `engine` from `app.db.database`). Everything else already goes through `get_engine()`/`get_session_factory()`.
6. `app/main.py` binds `manager` and `event_processor` at import (lines 20–24) and uses them in the WS endpoints. Update it the same way (`get_manager()`, `get_event_processor()` at call sites in `websocket_overview`, `websocket_endpoint`, `websocket_room`, and `lifespan`'s `event_processor.shutdown()`), OR leave main.py for ARC-023's split if that is scheduled next — pick one and note it in the PR. If updating here, that makes 6 files: split step 6 into its own commit.
7. Add a regression test `backend/tests/test_di_seams.py`: override the manager with a stub (`override_manager(StubManager())`), call `broadcast_state` from `broadcast_service`, and assert the stub received the message (this fails on the old import-time binding).

**Verification**:
- `cd /Users/probello/Repos/claude-office/backend && make checkall`.
- `grep -rn "^from app.core.connection_manager import manager\b\|import manager\b" backend/app | grep -v get_manager` → no module-level singleton value imports remain outside `connection_manager.py` itself.
- `cd backend && uv run pytest tests/test_di_seams.py -v`.

**Do NOT**:
- Do not convert route handlers wholesale to `Depends(get_event_processor)` where they already have working `Depends` params (`routes/events.py:56` already does it right — leave it).
- Do not remove `override_engine`/`override_manager`/`override_event_processor` — they are the test seams being made real.
- Do not rename the singletons; external references (tests) may patch `app.core.event_processor.event_processor` — run the full suite and fix only what breaks.

### [ARC-013] Three pollers are structural copy-paste with no shared abstraction
**Priority**: Medium | **Effort**: M | **Phase**: 3b — Architecture (remaining)
**Preconditions**: ARC-003 (poller file I/O fixes land first, same files)
**Files**: `/Users/probello/Repos/claude-office/backend/app/core/base_poller.py` (new), `/Users/probello/Repos/claude-office/backend/app/core/task_file_poller.py`, `/Users/probello/Repos/claude-office/backend/app/core/transcript_poller.py`, `/Users/probello/Repos/claude-office/backend/app/core/beads_poller.py`, `/Users/probello/Repos/claude-office/backend/app/config.py`

**Goal**: One generic `BasePoller[TState]` owns the per-session task registry, poll loop, and lifecycle (`start_polling`/`stop_polling`/`stop_all` that awaits cancelled tasks); the three pollers keep only their `_check` logic; `BEADS_POLL_INTERVAL` moves into `Settings`.

**Steps** (three sub-batches, verify between each):
1. *Batch 1 — base + task_file_poller.* Create `backend/app/core/base_poller.py`:
   ```python
   """Generic per-session polling loop shared by transcript/task-file/beads pollers."""
   import asyncio
   import contextlib
   import logging
   from abc import ABC, abstractmethod

   logger = logging.getLogger(__name__)


   class BasePoller[TState](ABC):
       poll_interval: float = 1.0

       def __init__(self) -> None:
           self._sessions: dict[str, TState] = {}
           self._tasks: dict[str, asyncio.Task[None]] = {}
           self._lock = asyncio.Lock()

       async def start_polling(self, session_id: str, state: TState) -> None:
           async with self._lock:
               if session_id in self._tasks:
                   return
               self._sessions[session_id] = state
               self._tasks[session_id] = asyncio.create_task(self._poll_loop(session_id))

       async def stop_polling(self, session_id: str) -> None:
           async with self._lock:
               task = self._tasks.pop(session_id, None)
               self._sessions.pop(session_id, None)
           if task:
               task.cancel()
               with contextlib.suppress(asyncio.CancelledError):
                   await task

       async def stop_all(self) -> None:
           async with self._lock:
               tasks = list(self._tasks.values())
               self._tasks.clear()
               self._sessions.clear()
           for task in tasks:
               task.cancel()
           for task in tasks:
               with contextlib.suppress(asyncio.CancelledError):
                   await task

       async def _poll_loop(self, session_id: str) -> None:
           while True:
               try:
                   async with self._lock:
                       state = self._sessions.get(session_id)
                   if state is None:
                       return
                   await self._check(session_id, state)
               except asyncio.CancelledError:
                   raise
               except Exception:
                   logger.exception("Poll error (%s) for session %s", type(self).__name__, session_id)
               await asyncio.sleep(self.poll_interval)

       @abstractmethod
       async def _check(self, session_id: str, state: TState) -> None: ...
   ```
   Convert `task_file_poller.py` (`TaskFilePoller`, line 74): subclass `BasePoller[<its state dataclass>]`, set `poll_interval = POLL_INTERVAL_SECONDS` (1.0, line 58), delete its hand-rolled registry/loop/`stop_all` (line 158) and keep `_check_for_changes` renamed/wrapped as `_check(self, session_id, state)` plus its helpers (`_read_task_files`, callback wiring, `init_task_file_poller`/`get_task_file_poller` at lines 322–333). **Before converting, read the current file** — preserve its public methods used by `event_processor.py` (`_ensure_task_file_poller`, `handle_session_start` wiring) exactly.
   Verify: `cd backend && uv run pytest tests/test_task_file_poller.py -v && make checkall`.
2. *Batch 2 — transcript_poller.* Same conversion for `TranscriptPoller` (`transcript_poller.py:46`, `stop_all` at 103, loop at ~148, `init_transcript_poller`/`get_transcript_poller` at 386–395). Keep its zombie-timeout and `asyncio.to_thread` read logic (line 213) inside `_check` untouched.
   Verify: `uv run pytest tests/test_transcript_poller.py -v && make checkall`.
3. *Batch 3 — beads_poller + config knob.* Convert `BeadsPoller` (`beads_poller.py:171`, `stop_all` at 208). Replace the raw env read at lines 44–47 (`os.environ.get("BEADS_POLL_INTERVAL", ...)`) with a `Settings` field: add `BEADS_POLL_INTERVAL: float = 3.0` to `backend/app/config.py` (after `GIT_POLL_INTERVAL`, line 25) and set `self.poll_interval = get_settings().BEADS_POLL_INTERVAL` in `BeadsPoller.__init__`. Keep `has_beads`/`init_beads_poller`/`get_beads_poller` signatures unchanged.
   Verify: `uv run pytest tests/test_beads_poller.py -v && make checkall`.

**Verification**:
- After each batch, the commands listed in that batch.
- Final: `cd /Users/probello/Repos/claude-office/backend && make checkall`; then `make dev-tmux` + `make simulate` — todos panel updates (task poller), agents animate from transcripts (transcript poller).

**Do NOT**:
- Do not change any `_check` business logic (file diffing, zombie detection, beads CLI invocation) — only the scaffolding moves.
- Do not change the public `init_*`/`get_*` singleton accessor names — `event_processor.py` and tests import them.
- Do not "fix" poll intervals while here; keep 1.0/1.0/3.0.

### [ARC-014] `EventData` is a 40-field optional-everything god model
**Priority**: Medium | **Effort**: L | **Phase**: 2 — Critical Architecture (promoted; blocks QA-007/ARC-025 and handler QA work)
**Preconditions**: ARC-002 (dispatch consolidation first — same files), ARC-001 (CI)
**Files**: `/Users/probello/Repos/claude-office/backend/app/models/events.py`, `/Users/probello/Repos/claude-office/backend/app/api/routes/events.py`, `/Users/probello/Repos/claude-office/backend/app/api/routes/sessions.py`, `/Users/probello/Repos/claude-office/backend/app/core/event_processor.py`, `/Users/probello/Repos/claude-office/scripts/gen_types.py`, `/Users/probello/Repos/claude-office/backend/app/core/state_machine.py`, `/Users/probello/Repos/claude-office/backend/app/core/handlers/*`

**Goal**: `Event` becomes a discriminated union of family-specific event models (discriminated on `event_type` via multi-value `Literal`s), each carrying only its family's payload fields, with the wire format unchanged; handlers get narrowed payload types.

**Steps** (three sub-batches; this is the riskiest backend refactor — verify after every batch and stop if tests regress):

*Batch 1 — additive models, no consumer changes (1 file).*
1. In `backend/app/models/events.py`, keep `EventType`, `EventData`, and `Event` exactly as-is (lines 17–107). Below them, add:
   - `class EventDataBase(BaseModel)` with the fields every event may carry (used by `token_tracker.update_from_event` and routing): `project_name, project_dir, working_dir, agent_id, native_agent_id, transcript_path, summary, message, team_name, teammate_name, task_list_id, input_tokens, output_tokens, cache_read_tokens, cache_creation_tokens, floor_id, room_id` (all `| None = None`, types copied from `EventData` lines 48–91). Set `model_config = ConfigDict(extra="ignore")`.
   - Family payload models (each `class XData(EventDataBase)` adding only its fields):
     - `SessionEventData`: `reason` — for SESSION_START, SESSION_END.
     - `ToolEventData`: `tool_name, tool_use_id, tool_input, success, result_summary, error_type, thinking` — for PRE_TOOL_USE, POST_TOOL_USE, PERMISSION_REQUEST.
     - `PromptEventData`: `prompt` — for USER_PROMPT_SUBMIT.
     - `AgentEventData`: `agent_name, agent_type, task_description, result_summary, agent_transcript_path, tool_use_id, thinking, bubble_content, speech_content` — for SUBAGENT_START, SUBAGENT_INFO, SUBAGENT_STOP, AGENT_UPDATE, CLEANUP.
     - `LifecycleEventData`: `notification_type, error_type, reason, bubble_content, speech_content` — for STOP, NOTIFICATION, CONTEXT_COMPACTION, REPORTING, WALKING_TO_DESK, WAITING, LEAVING, ERROR, TEAMMATE_IDLE.
     - `TaskEventData`: `task_id, task_subject` — for TASK_CREATED, TASK_COMPLETED.
     - `BackgroundTaskEventData`: `background_task_id, background_task_output_file, background_task_status, background_task_summary` — for BACKGROUND_TASK_NOTIFICATION.
   - Before finalizing each field list, run `grep -rn "data\.<field>" backend/app` for every `EventData` field and make sure each field lands in (at least) every family whose event types access it. If a field is accessed for an event type outside its family, move it up to `EventDataBase` rather than duplicating.
   - Family event models sharing a base:
     ```python
     class _EventBase(BaseModel):
         session_id: str
         timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))

         @field_validator("session_id")
         @classmethod
         def validate_session_id(cls, v: str) -> str:
             if not re.fullmatch(r"[a-zA-Z0-9_-]{1,128}", v):
                 raise ValueError("session_id must be alphanumeric/dash/underscore, max 128 chars")
             return v

     class SessionEvent(_EventBase):
         event_type: Literal[EventType.SESSION_START, EventType.SESSION_END]
         data: SessionEventData
     ```
     …and analogously `ToolEvent`, `PromptEvent`, `AgentEvent`, `LifecycleEvent`, `TaskEvent`, `BackgroundTaskEvent` (every one of the 23 `EventType` values must appear in exactly one `Literal`).
   - The union + adapter:
     ```python
     AnyEvent = Annotated[
         SessionEvent | ToolEvent | PromptEvent | AgentEvent | LifecycleEvent | TaskEvent | BackgroundTaskEvent,
         Field(discriminator="event_type"),
     ]
     EventAdapter: TypeAdapter[AnyEvent] = TypeAdapter(AnyEvent)
     ```
   - Extend `__all__` with the new names. Add a module-level test `backend/tests/test_event_union.py` asserting: every `EventType` value is accepted by `EventAdapter.validate_python({"event_type": et, "session_id": "s1", "data": {}})` and round-trips `model_dump(mode="json")` identically to the legacy `Event` for a sample payload.
   Verify: `cd backend && make checkall`.

*Batch 2 — switch ingestion and replay to the union (3 files).*
2. `app/api/routes/events.py`: change the body param `event: Event` (line 54) to `event: AnyEvent` (import from `app.models.events`). FastAPI validates discriminated unions natively; the response uses `event.timestamp` only — unchanged.
3. `app/api/routes/sessions.py` `get_session_replay` (lines 420–442): replace the manual `Event(event_type=..., data=EventData.model_validate(rec.data))` construction with `evt = EventAdapter.validate_python({"event_type": rec.event_type, "session_id": rec.session_id, "timestamp": rec.timestamp, "data": rec.data})`, keeping the existing `try/except ValueError` skip-unknown-type behavior (catch `ValidationError` too).
4. `app/core/event_processor.py` `_persist_synthetic_event` (line 614) and any other `Event(`/`EventData(` construction in `core/` (grep `Event(` and `EventData(` across `backend/app/core`): construct the correct family class (e.g. synthetic subagent stop → `AgentEvent`/`AgentEventData`). Type hints on `process_event`/`transition` can stay `Event`-typed only if `Event` remains the annotation-compatible superset; simpler: change annotations in `event_processor.py`, `state_machine.py`, and `core/handlers/*` from `Event` to `AnyEvent` mechanically (`Event` stays exported for tests until batch 3).
   Verify: `cd backend && make checkall`; then `make dev-tmux` + `make simulate` (full pipeline exercises hooks-shaped payloads through the union).

*Batch 3 — narrow handlers and retire the god model (≤5 files at a time).*
5. In `state_machine.py` and each file under `core/handlers/`, handlers that read family-specific fields now receive narrowed payloads. Where a handler serves multiple families (rare after ARC-002), add `isinstance(event, AgentEvent)` narrowing instead of `getattr` guards. Replace `if event.data and event.data.X` with direct access where the family guarantees the field exists as `X | None`.
6. Update `scripts/gen_types.py` `MODELS` (lines 39–60): add the new family models and payload classes; run `make gen-types`; commit the regenerated `frontend/src/types/generated.ts`. The frontend consumes `EventType` and `GameState` — spot-check `frontend` typecheck.
7. Once no production code constructs bare `Event`/`EventData`, mark them deprecated with a comment (do NOT delete yet — external producers' payloads still validate through the union, and tests may reference them; deletion is follow-up QA work).
   Verify after each ≤5-file group: `cd backend && make checkall`; final: `cd frontend && make checkall` (generated types) and the ARC-010 contract test (`uv run pytest tests/test_hooks_contract.py`).

**Verification**:
- All batch commands above; plus `cd /Users/probello/Repos/claude-office && make checkall`.
- Wire-format regression: `uv run pytest tests/test_event_union.py tests/test_api.py tests/test_simulation_pipeline.py -v`.

**Do NOT**:
- Do not change any JSON field name, `EventType` string value, or make any currently-optional field required — producers (hooks, plugin, scenarios) must keep working unmodified.
- Do not set `extra="forbid"` on payload models — hooks send fields opportunistically.
- Do not delete `Event`/`EventData` in this issue.
- Do not touch `hooks/` or `opencode-plugin/` — they are dict-based producers and are guarded by ARC-010's contract test.
- Do not skip the per-field grep in step 1; a field placed in the wrong family becomes a silent `AttributeError` at runtime.

### [ARC-015] Unbounded growth and O(N·state) hot spots
**Priority**: Medium | **Effort**: M | **Phase**: 3b — Architecture (remaining)
**Preconditions**: ARC-002 (same file), ARC-012 (manager access pattern settled)
**Files**: `/Users/probello/Repos/claude-office/backend/app/core/event_processor.py`, `/Users/probello/Repos/claude-office/backend/app/core/state_machine.py`, `/Users/probello/Repos/claude-office/backend/app/core/broadcast_service.py`, `/Users/probello/Repos/claude-office/backend/app/main.py`, `/Users/probello/Repos/claude-office/backend/app/config.py`, `/Users/probello/Repos/claude-office/backend/app/api/routes/sessions.py`

**Goal**: Idle in-memory sessions are evicted automatically; old `EventRecord` rows can be reaped via a retention knob (default off); per-event state serialization is skipped when nobody is listening; replay supports pagination.

**Steps**:
1. *Idle eviction.* Add `last_event_at: datetime = field(default_factory=lambda: datetime.now(UTC))` to `StateMachine` (with the other fields, near line 543). In `event_processor._process_event_internal`, set `sm.last_event_at = datetime.now(UTC)` right after `sm = self.sessions[event.session_id]` (line 386). Add to `EventProcessor`:
   ```python
   async def evict_idle_sessions(self, max_idle_seconds: float) -> int:
       """Drop in-memory StateMachines idle longer than max_idle_seconds. State is replayable from the DB."""
       cutoff = datetime.now(UTC) - timedelta(seconds=max_idle_seconds)
       async with self._sessions_lock:
           stale = [sid for sid, sm in self.sessions.items() if sm.last_event_at < cutoff]
           for sid in stale:
               self.sessions.pop(sid, None)
       return len(stale)
   ```
   In `main.py`'s `lifespan`, start a background task that calls `event_processor.evict_idle_sessions(settings.SESSION_IDLE_EVICT_SECONDS)` every 15 minutes; cancel it on shutdown (mirror the `git_service.start()/stop()` pattern at lines 181–187). Add `SESSION_IDLE_EVICT_SECONDS: int = 21600  # 6h` to `Settings`.
2. *Event retention.* Add `EVENT_RETENTION_DAYS: int = 0  # 0 = keep forever (default)` to `Settings`. Extend `_reap_stale_sessions` in `main.py` (lines 191–208): after the status update, if `settings.EVENT_RETENTION_DAYS > 0`, `DELETE FROM events` (`EventRecord`) whose `session_id` belongs to sessions with `status == "completed"` and `updated_at <` now − retention. Log the count. Default 0 preserves current behavior (deleting events breaks replay for those sessions — the knob is opt-in).
3. *Skip serialization with no listeners.* In `broadcast_service.broadcast_state` (lines 30–45), `sm.to_game_state(session_id)` (serializing up to 500 history + 500 conversation entries) runs before checking for connections. Add an early return first:
   ```python
   if not get_manager().active_connections.get(session_id):
       return
   ```
   (after ARC-012, `get_manager()` is the access pattern; reading the dict without the lock is safe for an existence check — same as the existing `overview_connections` guard at `broadcast_overview_state`, line 115).
4. *Replay pagination.* In `sessions.py` `get_session_replay` (line 402), add optional query params `offset: int = 0, limit: int | None = None`; apply `.offset(offset)` and `.limit(min(limit, 2000))` when `limit` is provided. **Important**: state reconstruction must still replay from event 0 — when `offset > 0`, iterate all events but only append entries with index ≥ offset (the StateMachine must see the full prefix). Defaults keep today's full-response behavior so the frontend needs no change.

**Verification**:
- `cd /Users/probello/Repos/claude-office/backend && make checkall`.
- `curl "http://127.0.0.1:8000/api/v1/sessions/<id>/replay?offset=5&limit=10"` returns ≤10 entries whose first event matches the 6th event of the unpaginated response.
- With `EVENT_RETENTION_DAYS` unset, row counts in `events` table unchanged across restarts.

**Do NOT**:
- Do not default `EVENT_RETENTION_DAYS` to anything but 0 — deleting user session history must be opt-in (flag for review).
- Do not evict sessions that still have WebSocket viewers — acceptable as-is since a reconnect restores from DB, but do not shorten the default below hours.
- Do not throttle/debounce `broadcast_state` itself — the frontend depends on per-event state updates; only the no-listener skip is in scope.

### [ARC-016] Global rate limiter throttles the wrong dimension
**Priority**: Medium | **Effort**: S | **Phase**: 3b — Architecture (remaining)
**Preconditions**: None (touches `events.py` — coordinate with SEC-005 if landing together)
**Files**: `/Users/probello/Repos/claude-office/backend/app/api/routes/events.py`, `/Users/probello/Repos/claude-office/backend/app/config.py`

**Goal**: Event-ingestion rate limiting is keyed per `session_id` with a higher default, and the knob lives in `Settings` instead of a raw `os.environ` read.

**Steps**:
1. In `backend/app/config.py`, add to `Settings` (near `GIT_POLL_INTERVAL`, line 25): `EVENT_RATE_LIMIT: int = 1000  # max events per session per 60s window`. Pydantic-settings maps the `EVENT_RATE_LIMIT` env var automatically, preserving the existing configuration knob name.
2. In `backend/app/api/routes/events.py`: delete `_MAX_REQUESTS = int(os.environ.get("EVENT_RATE_LIMIT", "300"))` (line 20) and the `import os`. Replace the module state and checker (lines 20–48) with a per-session dict:
   ```python
   from app.config import get_settings

   _WINDOW = 60.0  # seconds
   _request_times: dict[str, deque[float]] = {}

   def reset_rate_limiter() -> None:
       """Clear the rate limiter state.  Intended for use between test runs."""
       _request_times.clear()

   def _check_rate_limit(session_id: str) -> None:
       """Raise HTTP 429 if this session's request rate exceeds the limit."""
       now = time.monotonic()
       cutoff = now - _WINDOW
       times = _request_times.setdefault(session_id, deque())
       while times and times[0] < cutoff:
           times.popleft()
       # Opportunistically drop empty buckets so the dict can't grow unbounded.
       for sid in [s for s, dq in _request_times.items() if not dq and s != session_id]:
           del _request_times[sid]
       if len(times) >= get_settings().EVENT_RATE_LIMIT:
           raise HTTPException(status_code=429, detail="Rate limit exceeded. Try again later.")
       times.append(now)
   ```
3. In `receive_event` (line 74), change `_check_rate_limit()` → `_check_rate_limit(event.session_id)` (the parsed body is available; keep the call before `background_tasks.add_task`).
4. Update the docstring at lines 62–63 ("global rate limit (default 300 …)") to "per-session rate limit (default 1000 events per 60 seconds per session, configurable via EVENT_RATE_LIMIT)".
5. Find tests that exercise the limiter: `grep -rn "rate_limit\|_MAX_REQUESTS\|429" backend/tests` — update monkeypatching of `_MAX_REQUESTS` to set the env var before `get_settings.cache_clear()` or to patch `get_settings().EVENT_RATE_LIMIT` via `Settings` override; keep asserting 429 behavior, now per-session (add one assertion that a second session is NOT limited when the first is).

**Verification**:
- `cd /Users/probello/Repos/claude-office/backend && make checkall`.
- `cd backend && uv run pytest tests/test_api.py tests/test_security_hardening.py -v`.

**Do NOT**:
- Do not remove the limiter or the `reset_rate_limiter()` test hook.
- Do not rename the `EVENT_RATE_LIMIT` env var (preserve existing deployments' configuration).
- Do not move the check after `background_tasks.add_task` — the 429 must prevent queuing.

### [ARC-017] Import cycles between `machines/` and `systems/`
**Priority**: Medium | **Effort**: M | **Phase**: 2 — Critical Architecture (one combined refactor with ARC-004; this batch lands FIRST)
**Preconditions**: QA-001/ARC-007 characterization slice
**Files**: `/Users/probello/Repos/claude-office/frontend/src/systems/animationSystem.ts`, `/Users/probello/Repos/claude-office/frontend/src/machines/agentMachineService.ts`, `/Users/probello/Repos/claude-office/frontend/src/systems/gameRuntime.ts` (new), `/Users/probello/Repos/claude-office/frontend/src/systems/hmrCleanup.ts`

**Goal**: `animationSystem.ts` no longer imports `agentMachineService` (confirmed cycle: `animationSystem.ts:17` ↔ `agentMachineService.ts:26`, plus via `queueManager.ts:12`); the animation tick talks to the machine layer through a listener interface wired in one composition root.

**Steps**:
1. In `animationSystem.ts`, define and export a port interface near the top:
   ```typescript
   export interface AnimationListener {
     notifyArrival(agentId: string, phase: AgentPhase): void;
     notifyBubbleComplete(entityId: string): void;
     notifyBossAvailable(): void;
   }
   ```
   Add to the `AnimationSystem` class: `private listener: AnimationListener | null = null;` and `setListener(l: AnimationListener | null): void { this.listener = l; }`.
2. Delete `import { agentMachineService } from "@/machines/agentMachineService";` (line 17) and replace the five call sites — `agentMachineService.notifyArrival(agentId, phase)` (line 302), `.notifyBubbleComplete(agentId)` (361), `.notifyBubbleComplete(entityId)` (367), `.notifyBossAvailable()` (428, 445) — with `this.listener?.notifyArrival(...)` etc.
3. In `queueManager.ts`, remove `import { animationSystem } from "@/systems/animationSystem";` (line 12) if its only use is incidental; check with `grep -n "animationSystem" frontend/src/machines/queueManager.ts` — if used, pass what it needs as a method parameter from `agentMachineService` instead (the store import may remain until ARC-004 batch 1).
4. Create the composition root `frontend/src/systems/gameRuntime.ts`:
   ```typescript
   /**
    * Composition root: wires the animation system's listener port to the
    * agent machine service. Import for side effects exactly once (OfficeGame).
    */
   import { animationSystem } from "./animationSystem";
   import { agentMachineService } from "@/machines/agentMachineService";

   export function wireGameRuntime(): void {
     animationSystem.setListener(agentMachineService);
   }

   export function unwireGameRuntime(): void {
     animationSystem.setListener(null);
   }
   ```
   `agentMachineService` already exposes the three `notify*` methods, so it structurally satisfies `AnimationListener` — add `implements AnimationListener` to its class declaration (type-only import, no cycle: type imports are erased).
5. Call `wireGameRuntime()` where the animation system is started (find `animationSystem.start()` — in `OfficeGame.tsx` or its mount effect) and `unwireGameRuntime()` in `hmrCleanup.performFullCleanup()`/`performSoftReset()` (before `animationSystem.stop()`).
6. Confirm the cycle is gone: `cd frontend && npx madge --circular src/ 2>/dev/null || bunx madge --circular src/` — `machines/ ↔ systems/` must not appear (one-off check; do not add madge as a dependency).

**Verification**:
- `cd /Users/probello/Repos/claude-office/frontend && make checkall`.
- QA-001 characterization tests still green: `bun run test`.
- Behavioral: `make dev-tmux` + `make simulate` — arrivals, bubble completion, and boss-available handoffs all still fire (agents reach desks, bubbles advance, queue advances).

**Do NOT**:
- Do not move boss-availability *policy* in this batch (that is ARC-004 batch 3's watchdog removal) — only the direction of the dependency changes.
- Do not convert `animationSystem`/`agentMachineService` singletons to DI containers; a single setter port is the scoped fix.
- Do not use a runtime value import of `agentMachineService` inside `animationSystem.ts` ever again — `import type` only, if a type is needed.

### [ARC-018] `useWebSocketEvents` mixes transport and domain logic
**Priority**: Medium | **Effort**: M | **Phase**: 3b — Architecture (remaining)
**Preconditions**: ARC-004/017 (agent-state ownership settled first; this hook writes into that machinery)
**Files**: `/Users/probello/Repos/claude-office/frontend/src/hooks/useWebSocketEvents.ts`, `/Users/probello/Repos/claude-office/frontend/src/systems/stateReconciler.ts` (new), `/Users/probello/Repos/claude-office/frontend/src/systems/typingTracker.ts` (new)

**Goal**: The 579-line hook shrinks to a thin lifecycle binding: state reconciliation (agent diffing/spawn policy) lives in a pure-TS `reconcileState()` module and the typing-duration timer state machine lives in a `TypingTracker` class, both unit-testable without React.

**Steps**:
1. Create `frontend/src/systems/typingTracker.ts`. Move the inlined timer logic from `handleMessage` (`useWebSocketEvents.ts:316-365`: `typingTimeoutsRef`, `typingStartTimesRef`, `MIN_TYPING_DURATION_MS`, the pre/post tool-use branches) into:
   ```typescript
   export class TypingTracker {
     constructor(private setTyping: (key: string, typing: boolean) => void, private minDurationMs = MIN_TYPING_DURATION_MS) {}
     onPreToolUse(key: string): void { /* clear pending timeout, record start, setTyping(key, true) */ }
     onPostToolUse(key: string): void { /* enforce min duration then setTyping(key, false) */ }
     clear(): void { /* cancel all timeouts, clear maps */ }
   }
   ```
   The hook instantiates one `TypingTracker` in a ref with a `setTyping` callback that keeps the existing boss/agent routing (`useWebSocketEvents.ts:322-329`).
2. Create `frontend/src/systems/stateReconciler.ts`. Move the entire `handleStateUpdate` body (`useWebSocketEvents.ts:73-277`) into an exported pure function:
   ```typescript
   export interface ReconcilerContext {
     currentSessionId: string;
     processedAgents: Set<string>;
     // any other refs the body reads (grep the moved body for `Ref.current`)
   }
   export function reconcileState(state: GameState, ctx: ReconcilerContext): void { /* moved body; store access via useGameStore.getState() stays */ }
   ```
   The hook's `handleStateUpdate` becomes `useCallback((state) => reconcileState(state, { currentSessionId: currentSessionIdRef.current, processedAgents: processedAgentsRef.current, ... }), [])`. Keep the moved logic byte-identical apart from `ref.current` → `ctx.` substitutions.
3. In the hook, `handleMessage` (line 280) keeps only: JSON parse, session-id filter, and a switch delegating to `reconcileState`, `TypingTracker`, `addEventLog`, and the existing toast/compaction handlers. If the toast filter is a self-contained block, extract `shouldShowToast()` alongside (this satisfies part of QA-005 — note the overlap in the PR so QA-005 doesn't redo it).
4. Do not touch the reconnect machinery (`connect`, `ws.onopen/onmessage/onerror/onclose`, backoff at lines 445–525) — the audit calls it solid.
5. Add tests `frontend/tests/stateReconciler.test.ts` (see ARC-007 step 5) and `frontend/tests/typingTracker.test.ts` (fake timers via `vi.useFakeTimers()`, assert min-duration behavior).

**Verification**:
- `cd /Users/probello/Repos/claude-office/frontend && make checkall`.
- `bun run test` — new tests pass.
- Behavioral: `make dev-tmux` + `make simulate`; typing animations still show ≥ min duration; mid-session reload spawns agents at the right positions (all 4 spawn branches).

**Do NOT**:
- Do not change spawn-decision behavior while moving it — this is a lift-and-shift; QA-005's `resolveSpawn()` refactor comes after and can then restructure.
- Do not extract a `WebSocketController` class for reconnect logic in this pass (audit lists it as optional; the domain extraction is the valuable part — keep the diff focused).
- Do not introduce React context; refs stay in the hook and are passed as plain values.

### [ARC-019] `gen_types.py` uses a manually curated model registry — silent omission risk
**Priority**: Medium | **Effort**: S | **Phase**: 3b — Architecture (remaining)
**Preconditions**: None (re-do the regeneration after ARC-014 if both land)
**Files**: `/Users/probello/Repos/claude-office/scripts/gen_types.py`, `/Users/probello/Repos/claude-office/frontend/src/types/generated.ts` (regenerated)

**Goal**: The exported model set is discovered by introspecting `app.models`, with an explicit exclusion list, so a newly added model cannot be silently omitted from `generated.ts`.

**Steps**:
1. In `scripts/gen_types.py`, replace the hand-maintained `MODELS = [...]` list (lines 39–60) with package introspection:
   ```python
   import importlib
   import inspect
   import pkgutil

   import app.models
   from pydantic import BaseModel

   # Models intentionally NOT exported to the frontend. Add a comment for each entry.
   EXCLUDED_MODELS: set[str] = set()

   def _discover_models() -> list[type[BaseModel]]:
       found: dict[str, type[BaseModel]] = {}
       for mod_info in pkgutil.iter_modules(app.models.__path__):
           module = importlib.import_module(f"app.models.{mod_info.name}")
           for name, obj in inspect.getmembers(module, inspect.isclass):
               if (
                   issubclass(obj, BaseModel)
                   and obj is not BaseModel
                   and obj.__module__ == module.__name__
                   and name not in EXCLUDED_MODELS
                   and not name.startswith("_")
               ):
                   found[name] = obj
       return [found[k] for k in sorted(found)]

   MODELS = _discover_models()
   ```
   Keep the existing comment about TypedDicts (`ConversationEntry`/`HistoryEntry`) not being BaseModels.
2. Run `cd /Users/probello/Repos/claude-office && make gen-types` and diff `frontend/src/types/generated.ts`. Expect (a) reordering (now alphabetical) and (b) possibly newly discovered models the old list missed. If a newly appearing model is genuinely internal-only, add it to `EXCLUDED_MODELS` with a justification comment instead of letting it export.
3. Commit the regenerated `generated.ts` in the same change — `.github/workflows/type-drift.yml` fails otherwise.
4. Confirm the frontend still compiles against the (possibly reordered) declarations.

**Verification**:
- `cd /Users/probello/Repos/claude-office && make gen-types && git diff --stat frontend/src/types/generated.ts` (review the diff — only additions/reordering, no deletions of used types).
- `cd frontend && make checkall`.
- CI: type-drift workflow green on the PR.

**Do NOT**:
- Do not exclude a model just to keep the `generated.ts` diff small — omission risk is the issue being fixed.
- Do not change the JSON-schema generation call (`models_json_schema(..., by_alias=True)`) or the camelCase aliasing.
- Do not sort by definition order or module path — alphabetical sorting keeps future diffs deterministic.

### [ARC-020] Remote-backend support inconsistent between hooks and OpenCode plugin
**Priority**: Medium | **Effort**: S | **Phase**: 3b — Architecture (remaining)
**Preconditions**: None
**Files**: `/Users/probello/Repos/claude-office/hooks/src/claude_office_hooks/config.py`, `/Users/probello/Repos/claude-office/hooks/src/claude_office_hooks/main.py`

**Goal**: One policy: `CLAUDE_OFFICE_API_URL` is honored (any host) by BOTH producers, with the hooks logging a debug-file notice when a non-localhost URL is in use — matching the plugin's behavior, the plugin installer's advertised override, and `main.py`'s own remote-capable `_open_request` docstring. **Security-relevant change: flag for manual review** (hooks would send transcript-derived data and the API key to whatever host the user configures).

**Steps**:
1. In `hooks/src/claude_office_hooks/config.py`, the silent clamp is at lines 17–21:
   ```python
   _raw_api_url = os.environ.get("CLAUDE_OFFICE_API_URL", "http://localhost:8000/api/v1/events")
   _parsed_url = urlparse(_raw_api_url)
   if _parsed_url.hostname not in _LOCALHOST_HOSTNAMES:
       _raw_api_url = "http://localhost:8000/api/v1/events"
   API_URL = _raw_api_url
   ```
   Replace the clamp with a flag the logger can report (config.py must stay silent — no stdout/stderr, and it must not import `debug_logger` if that creates ordering issues; use a module flag):
   ```python
   _raw_api_url = os.environ.get("CLAUDE_OFFICE_API_URL", "http://localhost:8000/api/v1/events")
   _parsed_url = urlparse(_raw_api_url)
   # Remote (non-localhost) backends are honored; main.py logs this to the debug file.
   IS_REMOTE_API_URL = _parsed_url.hostname not in _LOCALHOST_HOSTNAMES
   API_URL = _raw_api_url
   ```
2. In `hooks/src/claude_office_hooks/main.py`, inside the top-level `try:` after `_config = load_config()` (line ~38), add:
   ```python
   from claude_office_hooks.config import IS_REMOTE_API_URL

   if DEBUG and IS_REMOTE_API_URL:
       debug_log(f"Using remote backend: {API_URL}")
   ```
   (`debug_log` is already imported; extend the existing import from `claude_office_hooks.config` instead of a second import line.)
3. Update the docstring/comment in `config.py` describing the localhost restriction so it now documents the honored override (grep the file for "localhost" comments), and confirm `main.py`'s `_open_request` docstring (which already describes remote http/https backends, lines 43–63) is now accurate — no change needed there.
4. Check the hooks tests for the clamp: `grep -rn "CLAUDE_OFFICE_API_URL\|localhost" hooks/tests/` — if a test asserts the clamping behavior, update it to assert the new honor-with-flag behavior.
5. Do NOT modify `opencode-plugin` — it already honors any URL (this is the policy being standardized on). README/env-var documentation updates belong to DOC-006 (part 2).

**Verification**:
- `cd /Users/probello/Repos/claude-office/hooks && make checkall` (or `uv run pytest && uv run pyright && uv run ruff check .` before ARC-001 lands).
- Manual: `CLAUDE_OFFICE_API_URL=http://192.168.1.50:8000/api/v1/events CLAUDE_OFFICE_DEBUG=1 uv run claude-office-hook session_start < /dev/null`; then `grep "remote backend" ~/.claude/claude-office-hooks.log` shows the notice, and the hook still exits 0 with the backend unreachable.

**Do NOT**:
- Do not print anything to stdout/stderr from hooks code — the "never block Claude Code" invariant is absolute.
- Do not auto-upgrade http→https or add TLS verification changes.
- Do not remove `_LOCALHOST_HOSTNAMES` (it now powers the notice flag).
- Do not commit this without the security-review flag in the PR description.

### [ARC-021] Version synchronization fully manual across 7 locations, no bump script (= QA-010)
**Priority**: Medium | **Effort**: M | **Phase**: 3b — Architecture (remaining). Alias: same issue as QA-010 — this entry is canonical.
**Preconditions**: None
**Files**: `/Users/probello/Repos/claude-office/scripts/bump_version.py` (new), `/Users/probello/Repos/claude-office/Makefile`, `/Users/probello/Repos/claude-office/hooks/src/claude_office_hooks/main.py`, `/Users/probello/Repos/claude-office/CLAUDE.md`

**Goal**: `make bump VERSION=x.y.z` rewrites every version location in one command and `--check` mode (runnable in CI) fails when any location drifts; hooks' `__version__` derives from package metadata.

**Steps**:
1. Verified current locations (all at `0.22.0` except the known-stale `backend/app/config.py` — DOC-007's fix):
   - `/Users/probello/Repos/claude-office/pyproject.toml:3` — `version = "0.22.0"`
   - `/Users/probello/Repos/claude-office/backend/pyproject.toml:3` — `version = "0.22.0"`
   - `/Users/probello/Repos/claude-office/hooks/pyproject.toml:3` — `version = "0.22.0"`
   - `/Users/probello/Repos/claude-office/frontend/package.json:3` — `"version": "0.22.0"`
   - `/Users/probello/Repos/claude-office/opencode-plugin/package.json` — `"version": "0.22.0"`
   - `/Users/probello/Repos/claude-office/frontend/src/app/page.tsx:424` — header badge text `v0.22.0`
   - `/Users/probello/Repos/claude-office/hooks/src/claude_office_hooks/main.py:36` — `__version__ = "0.22.0"` (replaced by metadata in step 3)
   - `/Users/probello/Repos/claude-office/backend/app/config.py:13` — `VERSION: str = ...` (include in the script; DOC-007 separately corrects the stale value)
2. Create `scripts/bump_version.py` (stdlib only, runnable via `uv run --no-project`):
   ```python
   #!/usr/bin/env python3
   """Bump or check the project version across all synchronized locations.

   Usage: bump_version.py 0.23.0 | bump_version.py --check
   """
   import re
   import sys
   from pathlib import Path

   ROOT = Path(__file__).resolve().parents[1]

   # (path, regex with one capture group around the version)
   LOCATIONS: list[tuple[str, str]] = [
       ("pyproject.toml", r'^version = "([0-9]+\.[0-9]+\.[0-9]+)"'),
       ("backend/pyproject.toml", r'^version = "([0-9]+\.[0-9]+\.[0-9]+)"'),
       ("hooks/pyproject.toml", r'^version = "([0-9]+\.[0-9]+\.[0-9]+)"'),
       ("frontend/package.json", r'"version": "([0-9]+\.[0-9]+\.[0-9]+)"'),
       ("opencode-plugin/package.json", r'"version": "([0-9]+\.[0-9]+\.[0-9]+)"'),
       ("frontend/src/app/page.tsx", r'v([0-9]+\.[0-9]+\.[0-9]+)'),
       ("backend/app/config.py", r'VERSION: str = "([0-9]+\.[0-9]+\.[0-9]+)"'),
   ]

   def read_versions() -> dict[str, str]:
       out: dict[str, str] = {}
       for rel, pattern in LOCATIONS:
           text = (ROOT / rel).read_text(encoding="utf-8")
           m = re.search(pattern, text, re.MULTILINE)
           if not m:
               sys.exit(f"ERROR: version pattern not found in {rel}")
           out[rel] = m.group(1)
       return out

   def main() -> None:
       if len(sys.argv) != 2:
           sys.exit(__doc__)
       versions = read_versions()
       if sys.argv[1] == "--check":
           if len(set(versions.values())) != 1:
               for rel, v in versions.items():
                   print(f"  {rel}: {v}")
               sys.exit("ERROR: version drift detected")
           print(f"All locations at {next(iter(versions.values()))}")
           return
       new = sys.argv[1]
       if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", new):
           sys.exit(f"ERROR: '{new}' is not x.y.z")
       for rel, pattern in LOCATIONS:
           path = ROOT / rel
           text = path.read_text(encoding="utf-8")
           def repl(m: re.Match[str]) -> str:
               return m.group(0).replace(m.group(1), new)
           new_text, n = re.subn(pattern, repl, text, count=1, flags=re.MULTILINE)
           if n != 1:
               sys.exit(f"ERROR: failed to rewrite {rel}")
           path.write_text(new_text, encoding="utf-8")
           print(f"  {rel}: -> {new}")

   if __name__ == "__main__":
       main()
   ```
   IMPORTANT — validate the `page.tsx` pattern before committing: `grep -n "v0\.\d" frontend/src/app/page.tsx`; if `v0.x.y` appears more than once, tighten the regex with surrounding anchor text from line 424 (per the versioned-config pattern rule: enumerate all matches first).
3. In `hooks/src/claude_office_hooks/main.py:36`, replace `__version__ = "0.22.0"` with:
   ```python
   try:
       from importlib.metadata import version as _pkg_version
       __version__ = _pkg_version("claude-office-hooks")
   except Exception:
       __version__ = "0.0.0+unknown"
   ```
   (inside the existing top-level `try:`; the fallback keeps the never-crash invariant). The bump script intentionally does not list `main.py`.
4. Add to the root `Makefile`:
   ```makefile
   bump:			# Bump version everywhere: make bump VERSION=x.y.z
   	uv run --no-project python scripts/bump_version.py $(VERSION)

   version-check:			# Verify all version locations match
   	uv run --no-project python scripts/bump_version.py --check
   ```
   and add `version-check` as a step in `.github/workflows/ci.yml`'s backend job (after ARC-001).
5. Update the Version Management table in `/Users/probello/Repos/claude-office/CLAUDE.md`: replace the hooks-CLI `main.py` row with `backend/app/config.py` and add a line: "Run `make bump VERSION=x.y.z` — do not edit by hand."

**Verification**:
- `cd /Users/probello/Repos/claude-office && make version-check` → all locations match (after DOC-007 fixes config.py; until then `--check` fails on config.py — coordinate: land DOC-007's one-line bump inside this change if it hasn't landed).
- `git stash`-safe dry run: `make bump VERSION=0.22.1 && git diff --stat` (7 files changed) `&& git checkout -- .`
- `cd hooks && uv run python -c "import claude_office_hooks.main"` exits 0.

**Do NOT**:
- Do not use floating regexes like `[0-9.]+` that could match dependency versions — the patterns above anchor to each file's exact shape; verify each with grep before relying on it.
- Do not bump the actual version as part of this change (tooling only), except aligning `config.py` if DOC-007 hasn't.
- Do not touch `CHANGELOG.md` from the script.

### [ARC-022] Unused/unverified `httpx2` dependency in backend dev group
**Priority**: Medium | **Effort**: S | **Phase**: 3b — Architecture (remaining)
**Preconditions**: None
**Files**: `/Users/probello/Repos/claude-office/backend/pyproject.toml`, `/Users/probello/Repos/claude-office/backend/uv.lock`

**Goal**: The suspicious `httpx2` package (zero imports; name typosquat-adjacent to the real `httpx`, which IS a declared runtime dependency at `>=0.28.1`) is removed from the dev group and the lockfile.

**Steps**:
1. Confirm (already verified, re-confirm at execution time): `grep -rn "httpx2" /Users/probello/Repos/claude-office/backend/app /Users/probello/Repos/claude-office/backend/tests` → no imports; `grep -rn "import httpx" backend/` → only the real `httpx` (used by FastAPI test client tooling).
2. In `backend/pyproject.toml`, delete the line `"httpx2>=2.4.0",` from `[dependency-groups] dev` (line 83).
3. Re-lock and sync: `cd /Users/probello/Repos/claude-office/backend && uv lock && uv sync`.
4. Confirm removal: `grep -n "httpx2" backend/uv.lock` → no matches (previously at lock lines 112/147).
5. Security note: mention in the PR that this removes an unaudited supply-chain package; flag for review.

**Verification**:
- `cd /Users/probello/Repos/claude-office/backend && make checkall` (full test suite must still pass — proves nothing depended on it).
- `grep -rn "httpx2" /Users/probello/Repos/claude-office/backend` → empty.

**Do NOT**:
- Do not remove or downgrade the real `httpx` dependency.
- Do not run `uv sync -U` (no opportunistic upgrades in this change).
- Do not modify the root `uv.lock` (note: `git status` shows a pre-existing modified root `uv.lock` — leave it out of this commit).
### [ARC-023] `main.py` (443 lines) mixes middleware, migration, reaper, WebSockets, REST, and static serving
**Priority**: Low | **Effort**: M | **Phase**: 3b — Architecture (low-priority cleanups)
**Preconditions**: SEC-001, SEC-006 (both edit `main.py` — land security fixes first), ARC-012 (settles singleton access)
**Files**: `/Users/probello/Repos/claude-office/backend/app/main.py`, `/Users/probello/Repos/claude-office/backend/app/api/middleware.py` (new), `/Users/probello/Repos/claude-office/backend/app/db/migrate.py` (new), `/Users/probello/Repos/claude-office/backend/app/api/routes/websockets.py` (new)

**Goal**: `main.py` contains only app construction, lifespan, and static serving; middleware, the inline SQLite migration, and the three WebSocket endpoints live in dedicated modules; the hardcoded `/api/v1/status` prefix is derived from settings.

**Steps** (two sub-batches):
1. *Batch 1.* Create `backend/app/api/middleware.py`; move verbatim from `main.py`: `_LOCALHOST_HOSTS` (line 34), `LocalhostOnlyMiddleware` (41–59), `_NO_AUTH_PATHS` (65), `_is_state_changing` (68–81), `ApiKeyMiddleware` (84–118). Inside the moved code replace the module-global `settings` references with `get_settings()` calls (or `settings = get_settings()` at module top). Create `backend/app/db/migrate.py`; move `_migrate_schema` (129–159) as public `migrate_schema`. Update `main.py` imports and the `lifespan` call site. Fix the hardcoded route decorator at line 240: `@app.get("/api/v1/status")` → `@app.get(f"{settings.API_V1_STR}/status")`.
2. Verify batch 1: `cd backend && make checkall` (`tests/test_security_hardening.py` exercises both middlewares).
3. *Batch 2.* Create `backend/app/api/routes/websockets.py` with `router = APIRouter()`; move the three endpoints as `@router.websocket("/ws/overview")` (from main.py 257–301), `@router.websocket("/ws/{session_id}")` (304–349), `@router.websocket("/ws/room/{room_id}")` (352–389) — preserve the declaration order comment (overview MUST register before `/ws/{session_id}`). In `main.py`, `app.include_router(websockets.router)` (no prefix) placed before any catch-all static route registration. Keep `_reap_stale_sessions`, lifespan, and static serving (`_safe_static_path`, `serve_frontend`) in `main.py`.
4. Verify batch 2 (commands below) and run one manual WS check.

**Verification**:
- `cd /Users/probello/Repos/claude-office/backend && make checkall` (includes `tests/test_websocket_room.py`).
- Behavioral: `make dev-tmux`; open http://localhost:3000 → session view streams state; open the Command Center view → `/ws/overview` connects (backend log shows no "Invalid session ID" for the word "overview", proving route order survived).

**Do NOT**:
- Do not change middleware logic, auth behavior, or `_is_state_changing`'s path set while moving it (SEC-002 owns that change; if SEC-002 landed first, move its updated version untouched).
- Do not register `websockets.router` after the `SERVE_STATIC` catch-all `@app.get("/{path:path}")` block.
- Do not switch the migration to Alembic — the inline approach is documented as intentional (`main.py:135-138`).

### [ARC-024] Seven identical exception blocks in `sessions.py`; no app-level handler; two PATCH endpoints
**Priority**: Low | **Effort**: S | **Phase**: 3b — Architecture (low-priority cleanups)
**Preconditions**: SEC-002, QA-008 (both edit `sessions.py` first), ARC-012
**Files**: `/Users/probello/Repos/claude-office/backend/app/api/routes/sessions.py`, `/Users/probello/Repos/claude-office/backend/app/main.py`, `/Users/probello/Repos/claude-office/backend/app/db/database.py`

**Goal**: One app-level exception handler produces the generic 500 + `logger.exception` behavior; route bodies lose their copy-pasted `except Exception` blocks; the label PATCH delegates to the general session PATCH.

**Steps**:
1. In `main.py` (or `api/middleware.py` after ARC-023), register:
   ```python
   @app.exception_handler(Exception)
   async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
       logger.exception("Unhandled error on %s %s", request.method, request.url.path)
       return JSONResponse(status_code=500, content={"detail": "Internal server error"})
   ```
2. Make rollback automatic: in `db/database.py` `get_db` (lines 77–83), change to:
   ```python
   async def get_db() -> AsyncIterator[AsyncSession]:
       async with _session_factory() as session:
           try:
               yield session
           except Exception:
               await session.rollback()
               raise
           finally:
               await session.close()
   ```
3. In `sessions.py`, remove the boilerplate tails. The pattern `except Exception as e: [await db.rollback();] logger.exception(...); raise HTTPException(status_code=500, detail="Failed to ...") from e` appears in: `list_sessions` (187–189), `update_session_label` (227–230), `update_session` (268–271), `focus_session` (397–399), `get_session_replay` (468–470), `trigger_simulation` (493–495), `clear_database` (525–528), `delete_session` (573–576). For each: keep the `except HTTPException: [await db.rollback();] raise` clause where present (404s must pass through — with step 2, the explicit rollback in those clauses can also go), delete the `except Exception` clause, and unwrap the `try:` if nothing else remains. NOTE: response bodies for failures become `"Internal server error"` instead of per-route messages — grep tests first: `grep -rn "Failed to" backend/tests` and update any assertion on those detail strings.
4. Consolidate the PATCHes: extend `DisplayNameUpdate` (line 233) into `SessionUpdate` with both optional fields `display_name: str | None = None` and `label: str | None = None`; in `update_session` (line 239) apply whichever fields were provided (distinguish "not provided" from explicit null with `body.model_fields_set`). Rewrite `update_session_label` (line 198) as a thin delegate calling the same logic, preserving its route and response shape (frontend callers — `frontend/src/hooks/useSessionSwitch.ts` among others — keep working; removing the route is a separate breaking decision, not taken here).
5. TestClient note: Starlette's TestClient re-raises unhandled exceptions by default; where tests need to assert the 500 JSON, use `TestClient(app, raise_server_exceptions=False)`.

**Verification**:
- `cd /Users/probello/Repos/claude-office/backend && make checkall`.
- `curl -X PATCH http://127.0.0.1:8000/api/v1/sessions/<id> -H 'Content-Type: application/json' -d '{"label":"x","display_name":"y"}'` updates both fields; the `/label` route still works.

**Do NOT**:
- Do not delete the `/label` route (breaking API change out of scope).
- Do not weaken 404 behavior — `HTTPException` must never be swallowed by the generic handler path.
- Do not touch `kill_simulation`'s exception handling here (QA-008 owns it).

### [ARC-025] `StateMachine` carries ~120 lines of alias plumbing forwarding to trackers (superset of QA-007)
**Priority**: Low | **Effort**: M | **Phase**: 3b — Architecture (low-priority cleanups). Alias: QA-007 covers the same block (QA-007 counts the Whiteboard aliases; ARC-025 adds the TokenTracker pair) — this entry is canonical; implement once.
**Preconditions**: ARC-014 (audit: coordinate with the EventData/handler-signature changes to avoid double churn)
**Files**: `/Users/probello/Repos/claude-office/backend/app/core/state_machine.py`, plus every alias call site found by grep (mostly `backend/app/core/handlers/*`, `backend/app/core/room_orchestrator.py`, `backend/tests/*`)

**Goal**: All call sites use `sm.whiteboard.<field>` / `sm.token_tracker.<field>` directly and the backward-compatible property block (`state_machine.py:561-687`) is deleted.

**Steps**:
1. The alias block spans lines 561–687: 14 whiteboard properties (`tool_usage`, `task_completed_count`, `bug_fixed_count`, `coffee_break_count`, `code_written_count`, `recent_error_count`, `recent_success_count`, `consecutive_successes`, `last_incident_time`, `agent_lifespans`, `news_items`, `coffee_cups`, `file_edits` — `tool_usage` is getter-only) plus 2 token properties (`total_input_tokens`, `total_output_tokens`).
2. For EACH alias name, run all of these greps (per the no-semantic-search rule) across `backend/`:
   - `grep -rn "\.<name>" backend/app backend/tests` (attribute access)
   - `grep -rn "<name>=" backend/app backend/tests` (keyword/assignment)
   - `grep -rn "\"<name>\"\|'<name>'" backend/app backend/tests` (string keys, e.g. serialization dicts)
   Classify each hit: `sm.X`/`self.X` on a StateMachine → rewrite to `sm.whiteboard.X` (or `sm.token_tracker.X` for the two token fields); hits on `WhiteboardTracker`/`TokenTracker` themselves or camelCase frontend keys → leave alone.
3. Rewrite call sites in groups of ≤5 files, running `cd backend && uv run pytest` between groups.
4. When zero non-definition hits remain, delete lines 561–687 (both alias sections, including their banner comments) and any now-unused imports (`AgentLifespan`, `NewsItem` — only if unused elsewhere in the file; check `to_game_state`).
5. Re-run the full greps from step 2 to confirm nothing was missed (dynamic `getattr` uses would surface as test failures — the suite covers whiteboard serialization).

**Verification**:
- `cd /Users/probello/Repos/claude-office/backend && make checkall`.
- `sed -n '555,600p' backend/app/core/state_machine.py` shows the class flowing from field declarations straight to "Core methods".
- `make dev-tmux` + `make simulate` — whiteboard stats (tasks completed, coffee cups) still populate in the UI.

**Do NOT**:
- Do not move fields between `WhiteboardTracker`/`TokenTracker` and `StateMachine` — only delete the forwarding layer.
- Do not touch `to_game_state`'s serialization output keys (frontend contract).
- Do not modify test expectations except mechanical `sm.X` → `sm.whiteboard.X` rewrites.

### [ARC-026] React StrictMode disabled globally to work around a `@pixi/react` v8 double-mount race
**Priority**: Low | **Effort**: M | **Phase**: 3b — Architecture (low-priority cleanups)
**Preconditions**: ARC-004/017 (singleton cleanup paths must be settled before re-testing double-mount)
**Files**: `/Users/probello/Repos/claude-office/frontend/next.config.ts`, `/Users/probello/Repos/claude-office/frontend/src/systems/hmrCleanup.ts`, `/Users/probello/Repos/claude-office/frontend/src/components/game/OfficeGame.tsx`

**Goal**: StrictMode is re-enabled if the Pixi double-mount race can be guarded; otherwise the workaround stays but is documented with a reproduction and tracked, not silently global.

**Steps**:
1. Current state: `next.config.ts:13` sets `reactStrictMode: false` with a comment describing the race (StrictMode's dev double-mount vs `@pixi/react` v8 `<Application>` WebGL context creation, hanging the tab on floor view entry).
2. Make Pixi registration idempotent: in `hmrCleanup.ts`, `registerPixiApp` (lines 19–21) just overwrites `currentApp`. Change it to destroy a superseded instance first:
   ```typescript
   export function registerPixiApp(app: PixiApplication): void {
     if (currentApp && currentApp !== app) {
       try {
         currentApp.destroy(true, { children: true, texture: true, textureSource: true });
       } catch {
         // Ignore cleanup errors
       }
     }
     currentApp = app;
   }
   ```
3. In `OfficeGame.tsx`, find the `<Application>` mount and its cleanup effect; ensure the unmount path calls `performFullCleanup()` (it should already — verify) so StrictMode's mount→unmount→mount sequence fully tears down the first context before the second initializes.
4. Flip `reactStrictMode` to `true` locally. Test the exact failure mode from the comment: `cd frontend && make dev`, open http://localhost:3000, enter the floor/building view, hard-reload three times, and enter a session office view. If the tab never hangs and WebGL contexts don't leak (DevTools console shows no "too many WebGL contexts" warning), keep `true` and delete the stale comment.
5. If it still hangs: revert to `false`, but upgrade `@pixi/react` to the latest 8.x first (`bun update @pixi/react` — check changelog for double-mount fixes) and retest once. If still broken, keep the flag `false`, extend the comment with the tested date and versions, and file a tracking issue: `gh issue create --title "Re-enable reactStrictMode once @pixi/react double-mount race is fixed" --body "<repro from next.config.ts comment; tested <date> with @pixi/react <ver>>"`.

**Verification**:
- `cd /Users/probello/Repos/claude-office/frontend && make checkall`.
- Manual repro loop from step 4 recorded in the PR description (pass or documented fail).

**Do NOT**:
- Do not ship `reactStrictMode: true` without performing the manual floor-view test — the failure is a hang, invisible to CI.
- Do not remove `hmrCleanup.ts`'s HMR versioning while here.
- Do not pin/downgrade React itself.

### [ARC-027] Inconsistent dependency pinning; 11 unexplained `@typescript-eslint/*` overrides
**Priority**: Low | **Effort**: S | **Phase**: 3b — Architecture (low-priority cleanups)
**Preconditions**: None
**Files**: `/Users/probello/Repos/claude-office/frontend/package.json`

**Goal**: One documented pinning policy — runtime `dependencies` exact-pinned, `devDependencies` caret — and the override block carries a dated justification.

**Steps**:
1. Current state (verified): `dependencies` mixes exact (`next` 16.2.9, `react`/`react-dom` 19.2.7) with caret (`@pixi/react ^8.0.5`, `@xstate/react ^6.1.0`, `clsx`, `date-fns`, `lucide-react`, `pixi.js`, `react-markdown`, `react-zoom-pan-pinch`, `remark-gfm`, `tailwind-merge`, `xstate`, `zustand`); `package.json:47-58` pins 11 `@typescript-eslint/*` packages to `8.56.1` in `"overrides"` with no explanation.
2. Resolve each caret dependency to its currently-installed version: for each name run `cd frontend && bun pm ls <name>` (or read the resolved version from `bun.lock`). Replace the caret range with that exact version in `dependencies`. Leave `devDependencies` carets as-is (policy: build tools float within minor).
3. Add a policy note using the JSON comment convention (tools ignore unknown keys), as the first key of the file after `"version"`:
   ```json
   "//": [
     "Dependency policy: runtime `dependencies` are exact-pinned; `devDependencies` use caret ranges.",
     "overrides: all @typescript-eslint/* pinned to 8.56.1 (2026-07) — eslint-config-next 16.2.9 pulls mismatched plugin/parser majors; re-evaluate on next eslint-config-next upgrade."
   ],
   ```
   Before writing that justification, sanity-check it: `cd frontend && bun why @typescript-eslint/parser | head -20` — if the actual reason differs (e.g. a specific lint rule crash noted in git history: `git log --oneline -S "typescript-eslint" -- frontend/package.json`), write the real reason and its date instead.
4. Reinstall to confirm the lockfile is stable: `cd frontend && bun install && git diff --stat bun.lock` (expect no resolution changes — pins matched installed versions).

**Verification**:
- `cd /Users/probello/Repos/claude-office/frontend && make checkall` (build + lint + typecheck + tests unchanged).
- `git diff frontend/package.json` shows only range syntax changes and the `"//"` note — no version *values* changed.

**Do NOT**:
- Do not upgrade any package while pinning (same resolved versions, different range syntax).
- Do not remove the `@typescript-eslint` overrides — that is a separate, riskier change.
- Do not add `overrides` comments as trailing `//` line comments — package.json must remain valid JSON.

### [ARC-028] `components/game/` mixes canvas components with DOM panels consumed only by layout
**Priority**: Low | **Effort**: S | **Phase**: 3b — Architecture (low-priority cleanups)
**Preconditions**: None
**Files**: `/Users/probello/Repos/claude-office/frontend/src/components/game/EventLog.tsx`, `/Users/probello/Repos/claude-office/frontend/src/components/game/GitStatusPanel.tsx`, `/Users/probello/Repos/claude-office/frontend/src/components/game/ConversationHistory.tsx` → moved to `/Users/probello/Repos/claude-office/frontend/src/components/layout/`, plus importers

**Goal**: The three DOM-panel components live under `components/layout/` next to their only consumers (`SessionSidebar.tsx`, `MobileDrawer.tsx`, `RightSidebar.tsx` — verified via grep).

**Steps**:
1. Confirm consumer set is still accurate: `grep -rln "components/game/EventLog\|components/game/GitStatusPanel\|components/game/ConversationHistory" frontend/src` (verified today: only the three `layout/` files). Also check relative imports: `grep -rn "\./EventLog\|\./GitStatusPanel\|\./ConversationHistory" frontend/src/components/game`.
2. `git mv frontend/src/components/game/EventLog.tsx frontend/src/components/layout/EventLog.tsx` — likewise for `GitStatusPanel.tsx` and `ConversationHistory.tsx`. If any has a colocated CSS module or subcomponent (check `ls frontend/src/components/game/`), move it too.
3. Update every import found in step 1 from `@/components/game/<Name>` to `@/components/layout/<Name>`.
4. Check for a barrel file: `ls frontend/src/components/game/index.ts* frontend/src/components/layout/index.ts* 2>/dev/null` — update re-exports if present.

**Verification**:
- `cd /Users/probello/Repos/claude-office/frontend && make checkall` (typecheck+build catches any missed import).
- `grep -rn "components/game/EventLog\|components/game/GitStatusPanel\|components/game/ConversationHistory" frontend/src` → empty.

**Do NOT**:
- Do not move other DOM-ish files from `game/` (e.g. `EventDetailModal.tsx`, `LoadingScreen.tsx`, `ZoomControls.tsx`) — they are consumed by the canvas scene composition; scope is exactly the three panels the audit names.
- Do not rename the components or edit their contents.
- Do not create a new `panels/` directory — `layout/` already exists and holds all three consumers.

### [ARC-029] `hooks/install.sh` regenerates the config file wholesale, discarding user edits
**Priority**: Low | **Effort**: S | **Phase**: 3b — Architecture (low-priority cleanups)
**Preconditions**: None
**Files**: `/Users/probello/Repos/claude-office/hooks/install.sh`

**Goal**: Re-running the installer preserves every existing config value (`CLAUDE_OFFICE_DEBUG`, custom `CLAUDE_OFFICE_STRIP_PREFIXES`, and any user-added keys), only filling in missing keys — unless `--force` is passed.

**Steps**:
1. Current behavior: `install.sh` reads only `CLAUDE_OFFICE_API_KEY` from an existing `~/.claude/claude-office-config.env` (lines 45–55) then rewrites the whole file with `cat > "$CONFIG_FILE" <<EOF ... EOF` (lines 57–72), resetting `CLAUDE_OFFICE_DEBUG` to 0 and stomping custom prefixes.
2. Add a `--force` flag to the existing argument loop (lines 19–32): `--force) FORCE=1; shift ;;` with `FORCE=0` default, and mention it in the usage string.
3. Replace the "Save configuration" block with read-modify-write per key:
   ```bash
   # Read existing values (if any) so re-installs preserve user edits.
   EXISTING_PREFIXES=""
   EXISTING_DEBUG=""
   if [ -f "$CONFIG_FILE" ] && [ "$FORCE" != "1" ]; then
       EXISTING_PREFIXES=$(grep '^CLAUDE_OFFICE_STRIP_PREFIXES=' "$CONFIG_FILE" | head -1 | cut -d'=' -f2- | tr -d '"' | tr -d "'")
       EXISTING_DEBUG=$(grep '^CLAUDE_OFFICE_DEBUG=' "$CONFIG_FILE" | head -1 | cut -d'=' -f2)
   fi
   # CLI flag wins; then existing file; then default.
   if [ -z "$STRIP_PREFIXES" ] && [ -n "$EXISTING_PREFIXES" ]; then
       STRIP_PREFIXES="$EXISTING_PREFIXES"
       echo "Preserving existing strip prefixes: $STRIP_PREFIXES"
   fi
   DEBUG_VALUE="${EXISTING_DEBUG:-0}"
   ```
   IMPORTANT: this requires moving the "Use default if not specified" fallback (lines 34–40) AFTER the preservation logic, so precedence is: `--strip-prefixes` flag > existing file > `$DEFAULT_PREFIXES`. Then in the heredoc write `CLAUDE_OFFICE_DEBUG=$DEBUG_VALUE` instead of the hardcoded `0`.
4. Preserve unknown user-added keys: after writing the heredoc, append any line from the old file whose key is not one of the three managed keys. Simplest: before overwriting, `cp "$CONFIG_FILE" "$CONFIG_FILE.prev" 2>/dev/null || true`; after writing, `grep -vE '^(#|CLAUDE_OFFICE_STRIP_PREFIXES=|CLAUDE_OFFICE_DEBUG=|CLAUDE_OFFICE_API_KEY=|$)' "$CONFIG_FILE.prev" >> "$CONFIG_FILE" 2>/dev/null || true; rm -f "$CONFIG_FILE.prev"`.
5. Keep the API-key reuse logic (lines 45–55) exactly as-is — it already preserves the existing key, and per the security rules the installer must NEVER regenerate a key that exists (with `--force`, still reuse the key; `--force` resets only prefixes/debug to flag/default values).

**Verification**:
- `shellcheck hooks/install.sh` (if installed; otherwise `bash -n hooks/install.sh`).
- Manual: create a config with `CLAUDE_OFFICE_DEBUG=1`, custom prefixes, a custom `MY_EXTRA=x` line, and a key; run `./install.sh`; `cat ~/.claude/claude-office-config.env` → all four values preserved. Run `./install.sh --force` → prefixes/debug reset, key still preserved, extra line preserved.

**Do NOT**:
- Do not regenerate `CLAUDE_OFFICE_API_KEY` under any flag combination if one exists (security rule: never replace existing secrets; flag any deviation for manual review).
- Do not change the `uv tool install` / `manage_hooks.py` invocation section.
- Do not switch the config format or location.

### [ARC-030] Orphaned untracked `desktop/` and `tui/` build-artifact directories
**Priority**: Low | **Effort**: S | **Phase**: 3b — Architecture (low-priority cleanups)
**Preconditions**: None
**Files**: `/Users/probello/Repos/claude-office/desktop/` (delete), `/Users/probello/Repos/claude-office/tui/` (delete), `/Users/probello/Repos/claude-office/.gitignore`

**Goal**: The two artifact-only directories are removed from the working tree and gitignored so they cannot reappear as clutter.

**Steps**:
1. Verify they contain ONLY build artifacts before deleting (verified today: `desktop/` holds only `dist/` + `node_modules/`; `tui/` holds only `target/`; neither is tracked by git). Re-check at execution time:
   ```bash
   find /Users/probello/Repos/claude-office/desktop /Users/probello/Repos/claude-office/tui \
     -type f -not -path "*/node_modules/*" -not -path "*/dist/*" -not -path "*/target/*" | head
   git -C /Users/probello/Repos/claude-office ls-files desktop tui
   ```
   Both commands must print nothing. If either prints files, STOP and report instead of deleting (source files would mean these are not orphaned artifacts).
2. Delete: `rm -rf /Users/probello/Repos/claude-office/desktop /Users/probello/Repos/claude-office/tui`.
3. Append to `.gitignore` (with a comment):
   ```
   # Abandoned experiment build outputs — see AUDIT.md ARC-030
   /desktop/
   /tui/
   ```

**Verification**:
- `ls /Users/probello/Repos/claude-office/desktop /Users/probello/Repos/claude-office/tui 2>&1` → "No such file or directory".
- `cd /Users/probello/Repos/claude-office && git status --porcelain | grep -E "desktop|tui"` → only the `.gitignore` change appears in the diff.
- `make checkall` unaffected.

**Do NOT**:
- Do not delete if step 1's checks find tracked or non-artifact files — surface the finding instead.
- Do not remove any other untracked root files (e.g. `AUDIT.md`, `ENHANCEMENTS.md` are intentionally untracked working files; root-file cleanup is DOC-014).

### [ARC-031] `simulate_events.py` dead unknown-scenario guard and weak `dict[str, object]` typing
**Priority**: Low | **Effort**: S | **Phase**: 3b — Architecture (low-priority cleanups)
**Preconditions**: ARC-009 (which relocates and retypes `SCENARIOS`; most of this issue lands there)
**Files**: `/Users/probello/Repos/claude-office/scripts/simulate_events.py`

**Goal**: No dead code paths or `type: ignore` remain in the simulation entry point.

**Steps**:
1. If ARC-009 landed as specified, verify its steps already removed: the local `SCENARIOS: dict[str, object]` (old lines 35–39), the dead `if scenario_fn is None: parser.error(...)` guard in `main()` (argparse `choices=` makes it unreachable), and the `scenario_fn(ctx)  # type: ignore[call-arg]`. If any remain, apply them now exactly as written in ARC-009 step 3.
2. Sweep the file for leftovers: `grep -n "type: ignore\|dict\[str, object\]" scripts/simulate_events.py` → must be empty.
3. Confirm `build_parser`'s `choices=list(SCENARIOS.keys())` and `epilog=__doc__` still render all five scenarios in `--help`.

**Verification**:
- `cd /Users/probello/Repos/claude-office/backend && uv run ruff check ../scripts && uv run pyright ../scripts/simulate_events.py` (pyright may need `cd backend` for the venv; if pyright isn't configured for scripts/, ruff + a `uv run python scripts/simulate_events.py --help` smoke run suffices).
- `uv run python scripts/simulate_events.py definitely-not-a-scenario` → argparse error listing valid choices (exit code 2).

**Do NOT**:
- Do not re-add a runtime unknown-scenario guard "for safety" — argparse `choices` is the single validation point.
- Do not change scenario behavior or default (`complex`).

## Security (SEC)

> All entries in this section change security-relevant behavior. Every PR here must be explicitly flagged for manual security review, must preserve existing user configuration, and must never generate, rotate, or replace an existing secret/API key as a side effect.

### [SEC-001] Effective API key disclosed to any unauthenticated localhost client (CWE-522)
**Priority**: Medium (High on shared hosts) | **Effort**: M | **Phase**: 3a — Security (Immediate Actions #2; blocks any QA/refactor work on `get_status`, `ApiKeyMiddleware`, or the frontend key fetch)
**Preconditions**: None — land before ARC-023 and any frontend key-flow changes
**Files**: `/Users/probello/Repos/claude-office/backend/app/main.py`, `/Users/probello/Repos/claude-office/frontend/src/utils/api.ts`, `/Users/probello/Repos/claude-office/frontend/src/app/page.tsx`, `/Users/probello/Repos/claude-office/backend/tests/test_security_hardening.py`

**Goal**: `GET /api/v1/status` no longer returns `settings.effective_api_key`; the browser obtains the key out-of-band via a `?token=` URL printed to the server console (Jupyter-style), persisted in `sessionStorage`.

**Steps**:
1. Backend — stop disclosing the key. In `main.py` `get_status` (lines 240–254), delete the line `"apiKey": settings.effective_api_key,` and the docstring sentences describing key delivery; keep `aiSummaryEnabled`/`aiSummaryModel`. Adjust the return annotation to `dict[str, bool | str | None]` (unchanged shape otherwise).
2. Backend — deliver the key via the launch console. In `lifespan` (lines 173–179), the auto-key branch currently logs only a truncated prefix. Replace with:
   ```python
   if not settings.has_explicit_key:
       logger.info(
           "Auto-generated API key for state-changing endpoints: %s",
           settings.effective_api_key,
       )
       logger.info(
           "Open the UI with this URL to authorize destructive actions: "
           "http://localhost:3000/?token=%s (dev) or http://localhost:8000/?token=%s (static)",
           settings.effective_api_key,
           settings.effective_api_key,
       )
   ```
   (Console output is the trust boundary: only the launching user sees it; other localhost users cannot.) When `has_explicit_key` is true, log nothing — never echo user-configured secrets.
3. Frontend — token intake. In `frontend/src/utils/api.ts`, add:
   ```typescript
   const KEY_STORAGE = "claude-office-api-key";

   /** Read ?token= from the URL (stripping it from history) or sessionStorage. */
   export function initApiKeyFromBrowser(): void {
     if (typeof window === "undefined") return;
     const params = new URLSearchParams(window.location.search);
     const token = params.get("token");
     if (token) {
       setApiKey(token);
       try { sessionStorage.setItem(KEY_STORAGE, token); } catch { /* ignore */ }
       params.delete("token");
       const qs = params.toString();
       window.history.replaceState({}, "", window.location.pathname + (qs ? `?${qs}` : ""));
       return;
     }
     try {
       const stored = sessionStorage.getItem(KEY_STORAGE);
       if (stored) setApiKey(stored);
     } catch { /* ignore */ }
   }
   ```
4. Frontend — call it and remove the old fetch-based intake. In `page.tsx`: the status fetch at lines 204–208 currently does `if (data.apiKey) setApiKey(data.apiKey);` — delete that line; add `initApiKeyFromBrowser();` at the top of the same effect (before the `apiFetch("/api/v1/status")` call) and import it from `@/utils/api`. Keep the status fetch itself (`aiSummaryEnabled` is still used).
5. Tests. `grep -rn "apiKey" backend/tests` (no existing assertion found today — re-check). Add to `backend/tests/test_security_hardening.py`:
   ```python
   def test_status_does_not_disclose_api_key(self) -> None:
       """GET /api/v1/status must not return the effective API key (SEC-001)."""
       with TestClient(app) as client:
           resp = client.get("/api/v1/status")
           assert resp.status_code == 200
           body = resp.json()
           assert "apiKey" not in body
           assert get_settings().effective_api_key not in resp.text
   ```
   (Match the file's existing import/fixture style — read its header first.)
6. Behavior note for the PR: without a `?token=` URL (or explicit key), the UI's destructive buttons (Clear DB, Simulate) will receive 401s — this is the intended hardening. Document the new flow in DOC-003 (part 2). **Flag for manual security review; do not rotate or modify any user-configured `CLAUDE_OFFICE_API_KEY`.**

**Verification**:
- `cd /Users/probello/Repos/claude-office/backend && make checkall && uv run pytest tests/test_security_hardening.py -v`.
- `curl -s http://127.0.0.1:8000/api/v1/status | grep -c apiKey` → 0.
- Behavioral: start `make dev-tmux`, copy the token URL from the backend log, open it → Clear DB button works; open plain `http://localhost:3000` in a private window → Clear DB returns 401 toast.

**Do NOT**:
- Do not gate `/api/v1/status` behind the key as the fix — the frontend must still read `aiSummaryEnabled` before it has any key.
- Do not put the key in `localStorage` (session-scoped storage only) or in a non-HttpOnly cookie.
- Do not change `ApiKeyMiddleware`, `_is_state_changing`, or WebSocket auth here (SEC-002 owns the path-set change).
- Do not log the explicit user-configured key, ever.

### [SEC-002] Clipboard poisoning and terminal activation via unauthenticated `focus_session`
**Priority**: Medium (High on shared hosts) | **Effort**: S | **Phase**: 3a — Security (Immediate Actions #3; blocks QA-008 and QA edits to `focus_session`/`test_security_hardening.py`)
**Preconditions**: SEC-001 recommended first (frontend then has the token to keep the Focus button working)
**Files**: `/Users/probello/Repos/claude-office/backend/app/main.py`, `/Users/probello/Repos/claude-office/backend/tests/test_security_hardening.py`

**Goal**: `POST /api/v1/sessions/{id}/focus` (which activates Terminal via `osascript` and writes attacker-controlled text to the OS clipboard — `sessions.py:317-399`) requires the effective API key even in the default (no explicit key) configuration.

**Steps**:
1. In `main.py`, extend `_is_state_changing` (lines 68–81) to cover focus:
   ```python
   def _is_state_changing(path: str, method: str) -> bool:
       """Return True if the request targets a destructive or side-effecting endpoint.

       Covers global destructive operations (clearing all sessions, running a
       simulation) and per-session OS side effects (terminal activation +
       clipboard write via /focus). Other per-session mutations remain open in
       the default configuration and are fully gated when an explicit key is set.
       """
       prefix = settings.API_V1_STR + "/sessions"
       return (
           (path == prefix and method == "DELETE")
           or (path == f"{prefix}/simulate" and method == "POST")
           or (path.startswith(f"{prefix}/") and path.endswith("/focus") and method == "POST")
       )
   ```
2. Frontend check (no change expected): the only focus caller is `frontend/src/stores/attentionStore.ts:180`, which already uses `apiFetch` — the `X-API-Key` header is attached automatically once SEC-001's token flow sets the key. Re-verify: `grep -rn "/focus" frontend/src`.
3. Add tests to `backend/tests/test_security_hardening.py`, following the existing `test_delete_requires_key`/`test_simulate_requires_key` patterns (lines 419–460):
   ```python
   def test_focus_requires_key(self) -> None:
       """POST /sessions/{id}/focus must require the effective API key (SEC-002)."""
       with TestClient(app) as client:
           resp = client.post(f"{settings.API_V1_STR}/sessions/some-session/focus")
           assert resp.status_code == 401

   def test_focus_with_key_passes_auth(self) -> None:
       with TestClient(app) as client:
           resp = client.post(
               f"{settings.API_V1_STR}/sessions/does-not-exist/focus",
               headers={"X-API-Key": settings.effective_api_key},
           )
           assert resp.status_code in (404, 500)  # past auth; session lookup fails
   ```
   Match the file's actual fixture/client setup (read the surrounding class first — it may construct settings differently).
4. Decision recorded, not implemented: the audit's "consider dropping the clipboard write" is declined for now (it is a documented feature); requiring the key plus the existing 10 MB cap and truncation (`_validate_clipboard_message`, lines ~290–314) is the mitigation. Note this in the PR.
5. **Flag for manual security review.** The CSRF vector (non-preflighted `text/plain` POST from a hostile web page) is closed because browsers cannot add `X-API-Key` cross-origin without a CORS preflight, which the locked-down CORS config rejects.

**Verification**:
- `cd /Users/probello/Repos/claude-office/backend && uv run pytest tests/test_security_hardening.py -v && make checkall`.
- Behavioral: `curl -X POST http://127.0.0.1:8000/api/v1/sessions/x/focus` → 401; with `-H "X-API-Key: <key from backend log>"` → 404 (past auth). In the UI (with token URL), the attention toast's focus action still works.

**Do NOT**:
- Do not add `/focus` to `_NO_AUTH_PATHS` or the WebSocket skip list by accident — only `_is_state_changing` changes.
- Do not gate other per-session mutations (label, delete-one, preferences) in this change — that is an explicit product decision the audit left alone.
- Do not modify `focus_session`'s subprocess logic (QA-008/ARC-024 handle its error handling; SEC-002 is auth only).

### [SEC-003] Git commands executed in an attacker-influenceable working directory (CWE-426)
**Priority**: Medium | **Effort**: S | **Phase**: 3a — Security (Short-term #1; must land BEFORE ARC-011/ARC-012 touch `git_service.py`)
**Preconditions**: None
**Files**: `/Users/probello/Repos/claude-office/backend/app/services/git_service.py`

**Goal**: Every `git` invocation is hardened against hostile repo config (`core.fsmonitor`, `core.hooksPath`, `core.pager` code-execution vectors), and `project_root` values (which originate from the open `POST /events` endpoint via the DB) are validated before use.

**Steps**:
1. In `git_service.py` `_run_git` (lines 36–49), harden the command and environment:
   ```python
   _GIT_HARDENING: list[str] = [
       "-c", "core.fsmonitor=false",   # status executes fsmonitor commands from repo config
       "-c", "core.hooksPath=/dev/null",
       "-c", "core.pager=cat",
   ]

   def _run_git(self, args: list[str], cwd: Path) -> str:
       """Run a git command and return stdout.

       Hardened: repo-local config cannot inject executables (fsmonitor/hooks/pager)
       and global/system config is ignored — project_root originates from
       unauthenticated event data (SEC-003).
       """
       try:
           result = subprocess.run(
               ["git", *_GIT_HARDENING, *args],
               cwd=cwd,
               capture_output=True,
               text=True,
               timeout=10,
               env={
                   **os.environ,
                   "GIT_CONFIG_GLOBAL": "/dev/null",
                   "GIT_CONFIG_SYSTEM": "/dev/null",
                   "GIT_OPTIONAL_LOCKS": "0",
               },
           )
           return result.stdout.strip()
       except (subprocess.TimeoutExpired, FileNotFoundError) as e:
           logger.warning(f"Git command failed: {e}")
           return ""
   ```
   Add `import os` to the module imports. Place `_GIT_HARDENING` at module level (above the class).
2. Validate roots at configuration time. In `configure` (lines 276–286), before storing:
   ```python
   if project_root is not None:
       root = Path(project_root)
       if not (root.is_absolute() and root.is_dir() and (root / ".git").exists()):
           logger.warning("Rejecting invalid project_root for session %s: %r", session_id, project_root)
           return
       project_root = str(root.resolve())
   ```
   (`get_status` at lines 152–158 already re-checks existence; this stops bad values from ever entering `_sessions`.)
3. Note for reviewers: `GIT_CONFIG_GLOBAL=/dev/null` also disables the user's legitimate global config (e.g. custom `status` aliases are irrelevant here; porcelain output is stable). The branch/ahead-behind/log parsing used by the service (`rev-parse`, `rev-list`, `status --porcelain`, `log --format`) does not depend on user config.
4. Add a regression test `backend/tests/test_git_service_hardening.py`: create a temp git repo (`git init`), set `git config core.fsmonitor "touch /tmp/pwned-$$"` in it, call `git_service.get_status(repo_path=tmp_repo)`, assert the marker file was NOT created and status still returns. Also assert `configure(session_id="s", project_root="/nonexistent")` leaves `_sessions["s"]` unset/None.
5. **Flag for manual security review.**

**Verification**:
- `cd /Users/probello/Repos/claude-office/backend && make checkall && uv run pytest tests/test_git_service_hardening.py -v`.
- Behavioral: `make dev-tmux` with a real session — Git panel still shows branch/commits/changed files.

**Do NOT**:
- Do not pass `shell=True` or string-concatenate the command (it is a list — keep it that way).
- Do not validate `project_root` by string prefix against a hardcoded home directory — the checks are existence/absoluteness/`.git` presence, matching the audit.
- Do not let ARC-011/ARC-012's later edits to this file drop `_GIT_HARDENING` or the env pinning — call this out in the PR description so the refactor preserves it.

### [SEC-004] Docker publishes the API on all host interfaces
**Priority**: Low | **Effort**: S | **Phase**: 3a — Security (remaining)
**Preconditions**: None
**Files**: `/Users/probello/Repos/claude-office/docker-compose.yml`

**Goal**: The container port is published only on loopback, and the loopback-only middleware's behavior under bridge networking is verified and documented.

**Steps**:
1. In `docker-compose.yml`, change the ports mapping (lines 16–17):
   ```yaml
       ports:
         - "127.0.0.1:8000:8000"
   ```
2. Verify the `LocalhostOnlyMiddleware` interaction under bridge networking: inside the container, requests arrive from the docker bridge gateway IP (e.g. `172.17.0.1`), NOT `127.0.0.1` — `main.py:34` allows only `{"127.0.0.1", "::1", "localhost", "testclient"}`. Test: `docker compose up -d --build`, then `curl -s http://127.0.0.1:8000/health`.
   - If it returns `{"status":"ok"}`, the setup works (some network modes preserve loopback); done.
   - If it returns 403, the middleware is rejecting the bridge gateway. Fix by trusting the compose-network source only when containerized: add an env-gated allowance — in `main.py` (or `api/middleware.py` post-ARC-023), extend the allowed set when `TRUSTED_PROXY_HOSTS` is set: `_LOCALHOST_HOSTS | set(os.environ.get("TRUSTED_PROXY_HOSTS", "").split(","))`, and set `TRUSTED_PROXY_HOSTS=172.17.0.1` (or the compose network gateway) in `docker-compose.yml`'s `environment:` with a comment. Since the host port is now bound to 127.0.0.1, only local clients can reach the container, so trusting the gateway IP does not widen exposure. **If this branch is needed, flag it for manual security review.**
3. Document the binding change in the compose file with a one-line comment: `# Loopback-only: this is a localhost tool (SEC-004)`.

**Verification**:
- `docker compose up -d --build && curl -s http://127.0.0.1:8000/health` → `{"status":"ok"}`.
- From another machine on the LAN (or `curl --interface <lan-ip> http://<host-lan-ip>:8000/health` locally): connection refused.
- `docker compose down`.

**Do NOT**:
- Do not disable `LocalhostOnlyMiddleware` to make Docker work — use the narrowly scoped trusted-hosts env if needed.
- Do not change the volume mounts, `CLAUDE_PATH_*` translation env, or the internal port.
- Do not preserve an existing user-customized compose override file edit — if `docker-compose.override.yml` exists, leave it alone and note it.

### [SEC-005] `POST /events` unauthenticated/spoofable; OpenCode plugin cannot send a key
**Priority**: Low | **Effort**: S | **Phase**: 3a — Security (remaining; plugin change targets QA-002's class shape if QA-002 landed — otherwise apply to current `sendEvent`)
**Preconditions**: QA-002 (soft — plugin restructure; do not block on it), ARC-016 (same `events.py` file — coordinate)
**Files**: `/Users/probello/Repos/claude-office/opencode-plugin/src/index.ts`, `/Users/probello/Repos/claude-office/opencode-plugin/README.md`

**Goal**: The OpenCode plugin sends `X-API-Key` from `CLAUDE_OFFICE_API_KEY` when configured, so deployments with an explicit backend key no longer lose every plugin event to silent 401s (also resolves the code half of DOC-012).

**Steps**:
1. In `opencode-plugin/src/index.ts`, find the module-level config block where `API_URL`, `TIMEOUT_MS`, and `DEBUG` are read from env (grep `process.env.CLAUDE_OFFICE`). Add alongside:
   ```typescript
   const API_KEY = process.env.CLAUDE_OFFICE_API_KEY ?? "";
   ```
2. In `sendEvent` (lines 148–170), the fetch at line 155 sends only `Content-Type`. Change the headers to include the key when present:
   ```typescript
   const headers: Record<string, string> = { "Content-Type": "application/json" };
   if (API_KEY) headers["X-API-Key"] = API_KEY;

   const resp = await fetch(API_URL, {
     method: "POST",
     headers,
     body: JSON.stringify(event),
     signal: controller.signal,
   });
   ```
   Keep the existing `if (!resp.ok) debug("Backend responded", resp.status);` — with DEBUG on, a lingering 401 is now diagnosable.
3. Rebuild: `cd /Users/probello/Repos/claude-office/opencode-plugin && bun install && bun run build`.
4. Add a "Configuration" note to `opencode-plugin/README.md` env-var list: `CLAUDE_OFFICE_API_KEY — API key sent as X-API-Key; required when the backend sets an explicit CLAUDE_OFFICE_API_KEY.` (Full docs pass is DOC-012, part 2 — this line just keeps code and README from contradicting.)
5. Backend side: NO change. When `CLAUDE_OFFICE_API_KEY` is explicitly set, `ApiKeyMiddleware` already gates `/events` (`main.py:107` — `requires_auth = settings.has_explicit_key or ...`). Do not force the per-launch auto-key on `/events` in the default config: hooks and the plugin have no discovery channel for it (SEC-001 removed HTTP disclosure), so requiring it would silently break all producers. Record this decision in the PR.
6. **Flag for manual security review** (auth surface decision).

**Verification**:
- `cd /Users/probello/Repos/claude-office/opencode-plugin && bun run typecheck && make checkall` (post-ARC-001).
- Behavioral: start backend with `CLAUDE_OFFICE_API_KEY=testkey123`; run OpenCode with the plugin and `CLAUDE_OFFICE_API_KEY=testkey123 CLAUDE_OFFICE_DEBUG=1` → events appear in the office UI; with a wrong key, debug log shows `Backend responded 401`.

**Do NOT**:
- Do not generate a key, write config files, or modify the user's OpenCode environment from the plugin.
- Do not send the header when the env var is empty (avoid leaking an empty-string comparison path).
- Do not change `/events` auth requirements on the backend in this issue.
- Do not block this small fix on QA-002's refactor; if QA-002 landed first, put `API_KEY` in its config object instead of a bare module constant.

### [SEC-006] Broad `logger.exception` + `rich_tracebacks=True` may write sensitive paths/content to local logs
**Priority**: Low | **Effort**: S | **Phase**: 3a — Security (remaining)
**Preconditions**: None (touches `main.py` — coordinate with SEC-001/ARC-023 ordering; SEC-001 first)
**Files**: `/Users/probello/Repos/claude-office/backend/app/main.py`, `/Users/probello/Repos/claude-office/backend/app/config.py`, `/Users/probello/Repos/claude-office/docker-compose.yml`

**Goal**: Rich tracebacks (which render local variables and full paths) are configurable and disabled in the Docker/production deployment, while client-facing responses remain generic (already the case).

**Steps**:
1. Add to `Settings` in `backend/app/config.py` (near the logging-adjacent settings):
   ```python
   # Rich tracebacks render local variables and full filesystem paths into logs.
   # Useful in development; disable for shared/production deployments (SEC-006).
   LOG_RICH_TRACEBACKS: bool = True
   ```
2. In `main.py`, the logging setup at lines 121–123 runs at import time, before `settings = get_settings()` at line 126. Reorder: move `settings = get_settings()` above the `logging.basicConfig(...)` call, then change the handler:
   ```python
   settings = get_settings()

   logging.basicConfig(
       level=logging.INFO,
       format="%(message)s",
       handlers=[RichHandler(rich_tracebacks=settings.LOG_RICH_TRACEBACKS)],
   )
   ```
3. In `docker-compose.yml` `environment:` block, add `- LOG_RICH_TRACEBACKS=0` with a comment (`# Plain tracebacks in containers (SEC-006)`).
4. Leave the existing `logger.exception` call sites alone (sessions.py lines 188/229/270/398/469/494/527/575, preferences.py lines 38/52/85/115) — the audit confirms client responses are already generic; ARC-024 consolidates the blocks separately. Do not add path-scrubbing filters in this pass (explicitly deferred as over-engineering for a localhost tool; note the decision in the PR).
5. Default `True` preserves current developer experience — no existing configuration changes. **Flag for manual security review.**

**Verification**:
- `cd /Users/probello/Repos/claude-office/backend && make checkall`.
- `LOG_RICH_TRACEBACKS=0 uv run uvicorn app.main:app --port 8000` then trigger any error (e.g. `curl -X POST http://127.0.0.1:8000/api/v1/events -H 'Content-Type: application/json' -d '{"bad":1}'` and check a real exception path) → traceback in console is plain, no syntax-highlighted locals panel.

**Do NOT**:
- Do not remove `RichHandler` entirely or change the log level/format.
- Do not scrub or truncate exception messages globally — over-broad scrubbing destroys debuggability and is not required by the audit.
- Do not flip the default to `False` (that would change every existing dev setup).
## Code Quality (QA)

> Anchor note: all line numbers below were re-verified against the working tree at commit `b17c2c4` (2026-07-06). Where AUDIT.md's line numbers had drifted, the corrected numbers are used here and the correction is called out.

### [QA-001] Frontend test coverage is near-zero relative to its complexity
**Priority**: High | **Effort**: L | **Phase**: Phase 3c (the characterization slice below is a Phase 2 precondition for ARC-004/017, ARC-005, QA-003, QA-009)
**Preconditions**: None — this issue must land BEFORE the frontend refactors (ARC-004/017, ARC-005, QA-003, QA-009)
**Files**: `frontend/tests/gameStore.test.ts` (new), `frontend/tests/astar.test.ts` (new), `frontend/src/stores/gameStore.ts` (read-only), `frontend/src/systems/astar.ts` (read-only)

**Goal**: The queue, bubble, reset, and pathfinding behavior of the frontend is locked in by passing characterization tests so the Phase 2 refactors cannot silently change behavior.

This issue is the same coverage gap as ARC-007 — **see [ARC-007] in the Architecture section for the full coverage strategy** (machine tests via the injected `AgentMachineActions` interface, Playwright smoke test). The QA-specific addition here is the **characterization slice** that is a hard precondition for Phase 2, specified mechanically below.

**Context you can rely on without re-analysis**:
- Vitest is already configured: `frontend/vitest.config.ts` (13 lines) sets only the `@ → src` alias; the environment defaults to `node`, which is fine — `gameStore.ts` and `astar.ts` import no DOM or Pixi APIs.
- Existing tests: `frontend/tests/{commandCenterPath,cron,i18n,overviewStore,smoke}.test.ts` and `frontend/src/systems/exitAnimation.test.ts` (466 lines total). Follow their style.
- `frontend/Makefile` `test` target runs `vitest run` (via bun or npm).

**Steps**:
1. Create `frontend/tests/gameStore.test.ts`. Import `useGameStore` from `@/stores/gameStore`. In `beforeEach`, call `useGameStore.getState().reset()`. Arrange agents through the public `addAgent` action (read its signature at `frontend/src/stores/gameStore.ts:405-437` first — do not poke `setState` internals when an action exists).
2. Cover the **queue actions** (`gameStore.ts` lines 548-630) with these scenarios, asserting on the FINAL store state after each action (do NOT assert on intermediate states — `dequeueArrival`/`dequeueDeparture` currently issue two `set()` calls, which QA-006 collapses to one; final-state assertions survive that fix):
   - `enqueueArrival(id)` appends to `arrivalQueue`, sets that agent's `queueType: "arrival"` and `queueIndex === arrivalQueue.length - 1`.
   - Enqueueing an already-queued id is a no-op (guard at line 550: `if (state.arrivalQueue.includes(agentId)) return state;`).
   - `dequeueArrival()` returns the front id, removes it from `arrivalQueue`, and re-indexes every remaining queued agent to `queueIndex` 0..n-1 (loop at lines 601-606).
   - `dequeueArrival()` on an empty queue returns `undefined` and changes nothing.
   - The same four scenarios for `enqueueDeparture`/`dequeueDeparture` (`queueType: "departure"`).
   - Interleaving: enqueue A then B, dequeue → returns A, and B's `queueIndex` becomes 0.
3. Cover the **bubble subsystem** (`gameStore.ts` lines 712-891): `enqueueBubble` for `entityId === "boss"` and for a regular agent id; `advanceBubble`; `clearBubbles`; `getCurrentBubble`; `isBubbleQueueEmpty`; `hasBubbleText`. For each, assert both the boss branch and the agent-Map branch (every bubble action has an `if (entityId === "boss")` branch followed by an agent branch).
4. Cover the **three reset variants** (`gameStore.ts` lines 1018-1064), characterizing their verified differences:
   - `reset()` (1018-1025): restores `initialState`, fresh `agents` Map.
   - `resetForReplay()` (1027-1035): same as `reset` plus `isReplaying === true`.
   - `resetForSessionSwitch()` (1037-1064): clears game state (agents, queues, `sessionId === "None"`, `deskCount === 8`) but PRESERVES `debugMode`/debug settings and `whiteboardMode`, and sets `isReplaying === false`. Set a non-default `debugMode` and `whiteboardMode` before calling it and assert they survive.
5. Cover `updateAgentMeta` (lines 509-522): `name: undefined` keeps the old name (`??` at line 518); a new `currentTask` string replaces the old one. **Do NOT write a case for `currentTask: ""`** — that behavior is the known QA-012 bug and changes when QA-012 lands (QA-012 adds its own test).
6. Create `frontend/tests/astar.test.ts`. Read `frontend/src/systems/astar.ts` first to learn the `PathGrid` interface — all three exports accept an injectable grid, so no canvas is needed:
   - `findPath(start, end, ignoreAgentId?, grid)` — line 126
   - `gridPathToWorld(gridPath, grid)` — line 284
   - `findWorldPath(start, end, ignoreAgentId?, grid)` — line 299
   Build a small stub grid (e.g. 10×10, all walkable, plus a variant with a wall) and cover: (a) start === end; (b) unobstructed straight-line path; (c) path routes around an obstacle; (d) fully-blocked destination — read the code and characterize whatever it currently returns (likely an empty array); (e) `gridPathToWorld` converts grid coordinates to world positions; (f) `findWorldPath` equals the composition of the other two.
7. Run the suite. **If any test fails, treat it as a potential real bug**: the audit history includes queue-slot-collision defects in exactly this layer. Diagnose the store/pathfinding code before touching the assertion — never weaken a test to make it pass. (Exception: behavior explicitly listed as a known bug — QA-012 — is excluded from characterization by step 5.)

**Verification**:
- `cd /Users/probello/Repos/claude-office/frontend && make test` — all tests pass, including the 6 pre-existing files.
- `cd /Users/probello/Repos/claude-office/frontend && make checkall`
- Confirm the new files are picked up: the vitest run output lists `tests/gameStore.test.ts` and `tests/astar.test.ts`.

**Do NOT**:
- Do not modify `gameStore.ts` or `astar.ts` in this issue (except to fix a genuine bug the tests expose — and then document it).
- Do not assert on intermediate two-`set()` states in dequeue actions (breaks under QA-006).
- Do not add jsdom or Pixi mocks — the chosen units run in plain node.
- Do not test the `currentTask: ""` case (QA-012 owns it).

---

### [QA-002] opencode-plugin has zero tests and its "lint" script is just the type checker
**Priority**: High | **Effort**: L | **Phase**: Phase 3c (must land BEFORE SEC-005's plugin-key change and ARC-010's plugin contract change — they target the extracted class)
**Preconditions**: None
**Files**: `opencode-plugin/src/index.ts`, `opencode-plugin/src/sessionTracker.ts` (new), `opencode-plugin/tests/sessionTracker.test.ts` (new), `opencode-plugin/package.json`, `opencode-plugin/eslint.config.js` (new)

**Goal**: Session-linking state lives in a constructor-injected, unit-tested `SessionTracker` class; `bun test` passes covering the documented event-ordering scenarios; `bun run lint` runs real ESLint.

**Context** (verified): `opencode-plugin/src/index.ts` is 715 lines. The 7 module-level structures are at lines 185-230:
```ts
185  const activeSessions = new Set<string>();
192  const childToParent = new Map<string, string>();
197  const childToAgent = new Map<string, string>();
211  const pendingTaskCalls = new Map<string, string[]>(); // parentId -> callID[]
218  const childSessionToCallId = new Map<string, string>();
224  const childSessionToParent = new Map<string, string>();
230  const childStopped = new Set<string>();
```
The plugin factory is `const plugin: Plugin = async (ctx: PluginInput): Promise<Hooks> =>` at line 236, returning six hooks (`event`, `chat.message`, `tool.execute.before`, `tool.execute.after`, `permission.ask`, `experimental.session.compacting`). `sendEvent` is at line 148. `package.json` currently has `"lint": "tsc --noEmit"` — identical to `"typecheck"` — no ESLint config exists anywhere in `opencode-plugin/`, and there are no tests.

**Steps**:
1. Grep for every use of each of the seven structure names in `index.ts` (e.g. `grep -n 'pendingTaskCalls\|childToParent\|childToAgent\|childSessionToCallId\|childSessionToParent\|childStopped\|activeSessions' opencode-plugin/src/index.ts`) and read each site. This is the complete behavior inventory you are extracting.
2. Create `opencode-plugin/src/sessionTracker.ts` exporting `class SessionTracker`. Move the seven structures in as private fields. Give it methods that correspond 1:1 to the mutation/read patterns found in step 1 — preserve semantics exactly. The expected API (adjust names to match what the call sites actually do, but keep this shape):
   - `registerTaskCall(parentSessionId: string, callID: string): void` — appends to the parent's pending-callID FIFO (`pendingTaskCalls`).
   - `linkChildSession(childSessionId: string): { parentId: string; callID: string } | undefined` — FIFO-shifts the OLDEST pending callID (this approximate matching is the behavior the code itself documents) and records `childSessionToCallId`, `childSessionToParent`, `childToParent`.
   - `setChildAgent(childSessionId: string, agentName: string): void` / `getChildAgent(...)` — wraps `childToAgent`.
   - `markSessionActive(id)` / `isSessionActive(id)` — wraps `activeSessions`.
   - `markChildStopped(childSessionId: string): boolean` — returns `false` if already in `childStopped` (duplicate suppression), `true` and records it otherwise.
   - `clearSession(id: string): void` — the cleanup performed on session end.
   The constructor takes the transport as an injected dependency where the current top-level code calls it: `constructor(private sendEvent: (event: BackendEvent) => Promise<void>)` (import the `BackendEvent` type from where `index.ts` defines it, exporting it if needed).
3. In `index.ts`, delete the seven module-level structures and instantiate `const tracker = new SessionTracker(sendEvent);` — decide placement by re-reading how HMR/module lifetime is handled: if the structures must survive across plugin factory invocations, keep the instance at module level (same lifetime as today); otherwise inside the factory. Replace every direct structure access with the corresponding tracker method. Behavior must be byte-for-byte equivalent — this is a pure extraction.
4. Create `opencode-plugin/tests/sessionTracker.test.ts` using `bun:test` (`import { describe, it, expect } from "bun:test";`). Inject a recording stub `sendEvent`. Cover these event-ordering scenarios (enumerated from the audit):
   - **FIFO callID matching**: register callIDs C1 then C2 for parent P; first child session links to C1, second child links to C2.
   - **Child arrives with no pending callID**: `linkChildSession` returns `undefined` (or the current fallback — characterize what the code does).
   - **Duplicate stop suppression**: `markChildStopped(X)` returns `true` first, `false` on the second call — the plugin must emit `subagent_stop` at most once per child.
   - **Interleaved parents**: callIDs registered for two different parents do not cross-match.
   - **Cleanup**: after `clearSession(P)`, previously pending callIDs for P no longer match new children.
5. Add to `opencode-plugin/package.json` scripts: `"test": "bun test"`.
6. Add real ESLint: `cd opencode-plugin && bun add -d eslint typescript-eslint @eslint/js`. Create `opencode-plugin/eslint.config.js` (flat config) extending `tseslint.configs.recommended` over `src/**/*.ts`, ignoring `dist/`. Change the scripts: `"lint": "eslint src --max-warnings=0"` (keep `"typecheck": "tsc --noEmit"` as is). Fix any lint findings it surfaces (mechanical fixes only; if a finding reveals a real bug, fix and note it).
7. If ESLint's initial run produces rule noise that is not a bug (style-level), prefer targeted `eslint.config.js` adjustments over inline disables, and keep the config minimal.

**Verification**:
- `cd /Users/probello/Repos/claude-office/opencode-plugin && bun run build && bun run typecheck && bun run lint && bun test` — all pass.
- `grep -n "new Map\|new Set" opencode-plugin/src/index.ts` — no session-tracking structures remain at module level in `index.ts`.
- Manual smoke (optional but recommended): rebuild and confirm `dist/index.js` still exports `ClaudeOfficePlugin` and default.

**Do NOT**:
- Do not change `sendEvent`'s fetch behavior, headers, or the event-type union in this issue (SEC-005 adds the API key; ARC-010 fixes the union — both target the post-extraction shape).
- Do not "improve" the FIFO matching heuristic — extraction must preserve current behavior; the tests characterize it.
- Do not point `"lint"` back at `tsc`.

---

### [QA-003] `gameStore.ts` heavy copy-paste duplication
**Priority**: High | **Effort**: M | **Phase**: Phase 3c
**Preconditions**: QA-001 (characterization tests), QA-006 (single-set dequeue), ARC-005 (store slicing — this fix applies inside the post-split agent slice; if executing before ARC-005, apply to `gameStore.ts` directly and note it)
**Files**: `frontend/src/stores/gameStore.ts` (or the agents slice file created by ARC-005)

**Goal**: The eight per-field agent updaters share one `patchAgent` helper and the arrival/departure queue actions share parameterized helpers, removing ~150 duplicated lines with zero public-API change.

**Context** (verified): the pattern `const newAgents = new Map(state.agents)` appears **20 times** (lines 407, 441, 464, 474, 484, 494, 504, 514, 529, 539, 558, 580, 600, 620, 639, 653, 758, 816, 852, 1074). The eight updaters are: `updateAgentPhase` (459-467), `updateAgentPosition` (469-477), `updateAgentTarget` (479-487), `updateAgentPath` (489-497), `updateAgentBackendState` (499-507), `updateAgentMeta` (509-522), `updateAgentQueueInfo` (524-532), `setAgentTyping` (534-542). Queue pairs: `enqueueArrival` (548-568) / `enqueueDeparture` (570-590), `dequeueArrival` / `dequeueDeparture` (shape depends on QA-006 having landed).

**Steps**:
1. Re-read the current file first — QA-006 and possibly ARC-005 have changed it since these anchors were captured.
2. Add a module-level helper above the store creator (type names per the store's existing imports from `@/types`):
   ```ts
   function patchAgent(
     state: Pick<GameStore, "agents">,
     agentId: string,
     patch: Partial<Agent>,
   ): { agents: Map<string, Agent> } | Pick<GameStore, "agents"> {
     const agent = state.agents.get(agentId);
     if (!agent) return state;
     const newAgents = new Map(state.agents);
     newAgents.set(agentId, { ...agent, ...patch });
     return { agents: newAgents };
   }
   ```
3. Rewrite each of the eight updaters as a delegation, e.g.:
   ```ts
   updateAgentPhase: (agentId, phase) =>
     set((state) => patchAgent(state, agentId, { phase })),
   ```
   For `updateAgentMeta`, preserve its exact per-field semantics inside the patch object: `{ backendState: meta.backendState, name: meta.name ?? agent.name, currentTask: ... }` — because the fallback needs `agent`, either keep `updateAgentMeta` hand-rolled or give `patchAgent` an overload accepting `(agent) => patch`. Use whichever the current QA-012 state of line 519 dictates (`??` after QA-012).
4. Parameterize the queue actions: add internal `enqueue(state, queueType: "arrival" | "departure", agentId)` and `dequeue(queueType)` helpers keyed on `queueType` selecting `arrivalQueue` vs `departureQueue`; keep the four public action names (`enqueueArrival`, `enqueueDeparture`, `dequeueArrival`, `dequeueDeparture`) as thin delegations — the public interface (lines 139-268) must not change.
5. Do NOT force the bubble actions (712-891) or `addAgent`/`removeAgent` through `patchAgent` unless the substitution is trivially identical — they contain extra logic (queue math, desk count).

**Verification**:
- `cd /Users/probello/Repos/claude-office/frontend && make checkall` — the QA-001 characterization tests must pass UNCHANGED. If one fails, the refactor changed behavior: fix the refactor, not the test.
- `grep -c "new Map(state.agents)" frontend/src/stores/gameStore.ts` — count drops from 20 to roughly 8-10 (helper + the non-updater sites).

**Do NOT**:
- Do not rename or re-type any public store action.
- Do not modify any QA-001 test.
- Do not fold in ARC-006's performance changes (frame batching) — separate issue.

---

### [QA-004] 229-line event-processing method with sequential if-dispatch
**Priority**: Medium | **Effort**: S | **Phase**: Phase 3c
**Preconditions**: ARC-002 (which performs the actual consolidation — this entry is the residual verification/cleanup pass)
**Files**: `backend/app/core/event_processor.py`

**Goal**: After ARC-002 lands, `_process_event_internal` contains no sequential `if event.event_type ==` chain, the dispatch table provably covers all 23 event types, and the documented broadcast ordering is preserved.

**Context** (verified, pre-ARC-002): `_process_event_internal` spans lines 332-555; the if-chain proper is **lines 438-546** (audit's 332-555 included setup). Branch order: SESSION_START (438), SESSION_END (465), SUBAGENT_START (492), SUBAGENT_INFO (503), AGENT_UPDATE (509), SUBAGENT_STOP (515), STOP (521), USER_PROMPT_SUBMIT (527), PRE_TOOL_USE (533), TASK_CREATED (539), TASK_COMPLETED (542), TEAMMATE_IDLE (545). Two ordering constraints are documented in comments that MUST survive:
- Lines 472-487: the default `broadcast_state`/`broadcast_event` calls plus the room-orchestrator broadcast run mid-sequence (after SESSION_END handling, before SUBAGENT_START..TEAMMATE_IDLE handlers).
- Lines 548-554: the comment block explaining that `self._schedule_overview_broadcast()` (line 555) is scheduled LAST, after all field mutations, debounced ~50 ms.

**Steps**:
1. Confirm ARC-002 has landed (its dispatch table replaces the chain). If not, stop — do ARC-002 first; this issue is the same code.
2. Verify the two comment blocks above still exist adjacent to the code they describe, and that the relative order (default broadcasts → per-type async enrichment as before → overview broadcast last) matches the pre-refactor sequence. Note the pre-refactor subtlety: the default broadcasts at old lines 475-476 fired BEFORE the SUBAGENT_START-through-TEAMMATE_IDLE handlers — ARC-002's table must have preserved that; if it silently reordered them, flag it as a bug against ARC-002.
3. Add a completeness test (in the existing event-processor test module under `backend/tests/`): iterate `EventType` (all 23 members, `backend/app/models/events.py:20-42`) and assert each has an entry in the new dispatch table (or is in an explicit documented no-op set). This makes future additions fail loudly instead of silently missing a path.
4. Delete any leftover dead branches or now-unused imports that ARC-002's consolidation orphaned.

**Verification**:
- `grep -c "if event.event_type ==" backend/app/core/event_processor.py` returns `0`.
- `cd /Users/probello/Repos/claude-office/backend && make checkall` (backend checkall already includes pytest).

**Do NOT**:
- Do not reorder broadcasts "for cleanliness" — the ordering comments at (old) lines 369-372 and 548-554 document load-bearing behavior.
- Do not begin this before ARC-002 merges; you would duplicate its diff.

---

### [QA-005] `useWebSocketEvents` handlers overly long and deeply nested
**Priority**: Medium | **Effort**: M | **Phase**: Phase 3c
**Preconditions**: None strictly; coordinate with ARC-018 (same file) — if ARC-018 is scheduled, execute this as its first slice so helpers are extracted exactly once
**Files**: `frontend/src/hooks/useWebSocketEvents.ts`, `frontend/src/systems/spawnDecision.ts` (new), `frontend/src/systems/typingTracker.ts` (new), `frontend/src/systems/toastFilter.ts` (new), `frontend/tests/spawnDecision.test.ts` (new), `frontend/tests/typingTracker.test.ts` (new), `frontend/tests/toastFilter.test.ts` (new)

**Goal**: The spawn decision, typing-duration timer, and toast filter are pure, unit-tested modules; `useWebSocketEvents.ts` shrinks accordingly with identical runtime behavior.

**Context** (verified): the file is 579 lines. `handleStateUpdate` is lines 73-277; `handleMessage` is lines 280-442. Audit correction: the spawn decision is a **5-branch** chain (not 4), lines 113-146: (1) `backendAgent.state === "arriving"` → `getNextSpawnPosition()`; (2) `isInArrivalQueue`; (3) `isInDepartureQueue`; (4) `backendAgent.desk` → `getDeskPosition(...)` + `skipArrival = true`; (5) fallback → `getNextSpawnPosition()`. Setup variables are computed at lines 97-111; results consumed at 148-163 (`store.addAgent`, `agentMachineService.spawnAgent`). The typing timer lives at lines 314-364 with refs at 48-51 (`typingStartTimesRef`, `typingTimeoutsRef`, `MIN_TYPING_DURATION_MS = 500`); `"main"` is treated as boss at line 324; effect teardown clears the maps at lines 542-557. Toast filtering is lines 371-402: `attentionEventTypes` Set (373-380) and `filterMap` (383-390) with the pass condition `filterMap[message.event.type as string] !== false` (391); note `stop` maps to `prefs.toastFilterError` and `background_task_notification` to `prefs.toastFilterArrival`.

**Steps**:
1. Create `frontend/src/systems/spawnDecision.ts` exporting a pure function. Move lines 97-146 verbatim into it, replacing direct imports with injected providers so it needs no module mocks:
   ```ts
   export interface SpawnDeps {
     getNextSpawnPosition: () => Position;
     getQueuePosition: (queueType: "arrival" | "departure", index: number) => Position; // match the actual calls in branches 2-3 — read lines 116-138 and mirror exactly
     getDeskPosition: (deskNum: number) => Position;
   }
   export function resolveSpawn(backendAgent: /* the backend agent type used at line 97 */, arrivalQueue: string[], departureQueue: string[], deps: SpawnDeps): {
     spawnPosition: Position; skipArrival: boolean;
     queueType: "arrival" | "departure" | undefined; queueIndex: number | undefined;
   }
   ```
   In the hook, call `resolveSpawn(...)` passing the real position functions.
2. Create `frontend/src/systems/typingTracker.ts` exporting `class TypingTracker`: constructor takes `(setTyping: (key: string, isTyping: boolean) => void, minDurationMs = 500)`; methods `onPreToolUse(key: string)` (clear pending timeout, record start time, `setTyping(key, true)`), `onPostToolUse(key: string)` (compute `remaining = minDuration - elapsed`; schedule delayed off or turn off immediately), and `dispose()` (clear all timeouts/maps — wire it into the effect teardown at lines 542-557). The hook keeps one instance in a ref; the `typingKey = agentId || "boss"` and `"main"`→boss routing (lines 320-329) stay in the hook's `setTyping` closure.
3. Create `frontend/src/systems/toastFilter.ts` exporting `shouldShowToast(eventType: EventType, prefs: PreferencesState): boolean` containing the `attentionEventTypes` Set and `filterMap`, returning `false` for non-attention types and preserving the exact `!== false` semantics (an undefined pref shows the toast). The hook then reads: `if (shouldShowToast(type, usePreferencesStore.getState())) { useAttentionStore.getState().processEvent(...) }`. Leave the second, unconditional `processEvent` in `case "error"` (lines 426-435) untouched.
4. Tests:
   - `spawnDecision.test.ts`: one case per branch (all 5), with stub deps returning sentinel positions; assert `spawnPosition`, `skipArrival`, `queueType`, `queueIndex` per branch.
   - `typingTracker.test.ts`: use `vi.useFakeTimers()`. Cases: post-tool-use before 500 ms elapses schedules a delayed off exactly at the remaining time; post after ≥500 ms turns off immediately; a second pre-tool-use cancels a pending off-timer; `dispose()` clears pending timers (advance timers, assert no late callback).
   - `toastFilter.test.ts`: each of the six attention types × pref `false` (suppressed) / pref `true` (shown) / pref `undefined` (shown); one non-attention type returns `false`; assert `stop` respects `toastFilterError` and `background_task_notification` respects `toastFilterArrival`.
5. If any test exposes behavior that looks wrong (e.g. a branch that can never fire), do not paper over it — report it; a failing characterization may be a real bug.

**Verification**:
- `cd /Users/probello/Repos/claude-office/frontend && make checkall`
- `wc -l frontend/src/hooks/useWebSocketEvents.ts` — meaningfully below 579.
- Manual smoke: `make dev-tmux`, run `make simulate` from the root, confirm agents spawn at desks/queues and toasts still appear.

**Do NOT**:
- Do not touch `connect` (445-524) or the reconnect logic — that is ARC-018's scope and the audit notes it is solid.
- Do not change the `"main"`→boss mapping or the `!== false` default-show semantics.
- Do not convert the hook to a class — only extract the three pure pieces.

---

### [QA-006] Dequeue actions issue two separate `set()` calls, creating transient inconsistent state
**Priority**: Medium | **Effort**: S | **Phase**: Phase 3c — land BEFORE QA-003 and before the ARC-004/017 refactor, immediately after QA-001's characterization tests exist
**Preconditions**: QA-001 (characterization slice)
**Files**: `frontend/src/stores/gameStore.ts` (lines 592-610 and 612-630)

**Goal**: `dequeueArrival` and `dequeueDeparture` each perform exactly one atomic `set()`, so subscribers can never observe a shifted queue with stale `queueIndex` values.

**Context** (verified): `dequeueArrival` calls `set({ arrivalQueue: rest })` at line 597 and then a second `set({ agents: newAgents })` at line 607, with `newAgents` built from the `state` snapshot captured at line 593 (before the first `set`). `dequeueDeparture` mirrors this at lines 617 and 627.

**Steps**:
1. Rewrite `dequeueArrival` (lines 592-610) to this exact shape:
   ```ts
   dequeueArrival: () => {
     const state = get();
     if (state.arrivalQueue.length === 0) return undefined;

     const [frontId, ...rest] = state.arrivalQueue;

     // Re-index remaining queued agents in the same atomic update
     const newAgents = new Map(state.agents);
     rest.forEach((id, idx) => {
       const agent = newAgents.get(id);
       if (agent) {
         newAgents.set(id, { ...agent, queueIndex: idx });
       }
     });
     set({ arrivalQueue: rest, agents: newAgents });

     return frontId;
   },
   ```
2. Apply the identical transformation to `dequeueDeparture` (lines 612-630) with `departureQueue`.
3. Run the QA-001 characterization tests — they assert final state only and must pass unchanged. If a test elsewhere depended on observing the intermediate state, that is exactly the bug class this fixes; investigate the consumer, do not reintroduce the second `set`.

**Verification**:
- `cd /Users/probello/Repos/claude-office/frontend && make checkall`
- `awk '/dequeueArrival: \(\)/,/^    },/' frontend/src/stores/gameStore.ts | grep -c "set("` returns `1` (repeat for `dequeueDeparture`).
- Manual smoke: `make dev-tmux` + root `make simulate`; watch several agents queue and dequeue without index glitches.

**Do NOT**:
- Do not also introduce the `patchAgent`/queue-parameterization helpers here — that is QA-003, sequenced after this so the behavior change is not buried in a structural diff.
- Do not change what the function returns (`frontId` / `undefined`).

---

### [QA-007] ~120 lines of backward-compat property boilerplate in StateMachine
**Priority**: Medium | **Effort**: S | **Phase**: Phase 3c
**Preconditions**: ARC-014 (discriminated-union conversion changes handler signatures on the same file — coordinate)
**Files**: `backend/app/core/state_machine.py`, `backend/app/core/handlers/agent_handler.py`, `backend/tests/test_state_machine.py`

**Goal**: The alias block is deleted and all call sites use the trackers directly.

**This is the same issue as ARC-025 — see [ARC-025] in the Architecture section for the full procedure.** The steps below are the QA-specific additions from direct verification (they correct/complete the audit's anchors); where they conflict with ARC-025's text, these verified anchors win.

**Steps**:
1. Locate the alias block: `backend/app/core/state_machine.py` lines **561-684** (audit said 561-687; 686-688 is the next section header). It contains **15 properties**: 13 delegating to `self.whiteboard` (`WhiteboardTracker`, field at line 559) — `tool_usage` (getter-only), plus getter+setter pairs for `task_completed_count`, `bug_fixed_count`, `coffee_break_count`, `code_written_count`, `recent_error_count`, `recent_success_count`, `consecutive_successes`, `last_incident_time`, `agent_lifespans`, `news_items`, `coffee_cups`, `file_edits` — and 2 pairs delegating to `self.token_tracker` (lines 666-684): `total_input_tokens`, `total_output_tokens`.
2. Migrate the verified real usages (the aliases are nearly dead — these are the only known callers):
   - `backend/app/core/handlers/agent_handler.py:81` — `for lifespan in sm.agent_lifespans:` → `sm.whiteboard.agent_lifespans`.
   - `backend/tests/test_state_machine.py:34-35, 129-130, 138-139` — `sm.total_input_tokens` / `sm.total_output_tokens` reads/writes → `sm.token_tracker.total_input_tokens` / `...total_output_tokens`.
3. Per the global grep discipline: before deleting, grep `backend/` (source AND tests) for each of the 15 property names as `sm.<name>` / `machine.<name>` attribute accesses to catch any caller step 2 missed; migrate any found.
4. Delete lines 561-684 wholesale (both sub-blocks including their header comments at 561-564 and 666-668).

**Verification**:
- `cd /Users/probello/Repos/claude-office/backend && make checkall` (pyright strict will also catch any missed attribute access).
- `grep -n "Backward-compatible property aliases" backend/app/core/state_machine.py` returns nothing.

**Do NOT**:
- Do not remove the `whiteboard`/`token_tracker` fields themselves — only the alias plumbing.
- Do not change tracker internals or `to_game_state()` (line 704), which already uses the trackers directly.

---

### [QA-008] Silently swallowed exception in simulation-process cleanup
**Priority**: Medium | **Effort**: S | **Phase**: Phase 3c
**Preconditions**: SEC-002 (touches the same file — land SEC-002 first)
**Files**: `backend/app/api/routes/sessions.py` (lines 27-45)

**Goal**: A failed simulation-process kill is logged with a traceback and reported as `False` instead of silently claiming success.

**Context** (verified — audit's line window ~25-55 corrected to 27-45): the function is `kill_simulation()` at lines 27-45; the swallow is lines 40-41 (`except Exception:` / `pass`); it currently returns `True` at line 44 whenever a process object existed, even if termination failed. A module-level `logger = logging.getLogger(__name__)` already exists at line 20.

**Steps**:
1. First `grep -rn "kill_simulation" backend/` to enumerate callers and tests. Confirm how the boolean is consumed (the audit notes `clear_database` blocks on this); note any test asserting `True` on the failure path — such a test encodes the bug and should be updated to assert `False` (with the reasoning documented in the test).
2. Replace the current body's exception handling:
   ```python
   def kill_simulation() -> bool:
       """Kill any running simulation process.

       Returns:
           True if a process was killed, False if no process was running
           or termination failed.
       """
       global _simulation_process
       if _simulation_process is not None:
           try:
               _simulation_process.terminate()
               _simulation_process.wait(timeout=5)
           except subprocess.TimeoutExpired:
               _simulation_process.kill()
           except Exception:
               logger.warning(
                   "Failed to terminate simulation process", exc_info=True
               )
               return False
           finally:
               _simulation_process = None
           return True
       return False
   ```
   Note: `return False` inside the `except` still executes the `finally` block, so `_simulation_process` is always cleared — same as today.
3. Add a unit test in the sessions-route test module (find it via `grep -rln "kill_simulation" backend/tests/`; create `backend/tests/test_kill_simulation.py` if none exists): monkeypatch `_simulation_process` with a stub whose `terminate()` raises `RuntimeError`; assert the function returns `False`, the module global is reset to `None`, and (via `caplog`) a warning was logged.

**Verification**:
- `cd /Users/probello/Repos/claude-office/backend && make checkall`
- `grep -A1 "except Exception:" backend/app/api/routes/sessions.py | grep -c "pass"` returns `0`.

**Do NOT**:
- Do not change the `TimeoutExpired` → `kill()` escalation path — it is correct and stays success-returning.
- Do not switch to `logger.exception` at ERROR level; this is a best-effort cleanup, WARNING is proportionate.
- Do not touch `focus_session` or other routes in this file (SEC-002/ARC-024 territory).

---

### [QA-009] `setTimeout(…, 0)` used as ordering mechanism in agent state machine service
**Priority**: Medium | **Effort**: M | **Phase**: Phase 2 (executed INSIDE the ARC-004/017 single-writer refactor — see AUDIT.md blocking relationships)
**Preconditions**: QA-001 (characterization tests), ARC-004/017 in progress (this is one of its work items, not a standalone change)
**Files**: `frontend/src/machines/agentMachineService.ts`

**Goal**: The six zero-delay timeouts are replaced by an explicit, flushable deferral queue so notification ordering is deterministic and testable.

**Context** (verified — all six audit line numbers are exact):
| Line | Call | Enclosing method |
|---|---|---|
| 426 | `setTimeout(() => this.notifyBossAvailable(), 0);` | `releaseReadyAndNotify` (420-428) |
| 474 | `setTimeout(() => this.notifyBossAvailable(), 0);` | `handleQueueJoined` (430-476) |
| 516 | `setTimeout(() => this.triggerDeparture(agentId), 0);` | `handlePhaseChanged` (497-518) |
| 535 | `setTimeout(() => this.notifyBubbleComplete(agentId), 0);` | `handleShowBossBubble` (520-547) |
| 570 | `setTimeout(() => this.notifyBossAvailable(), 0);` | `handleSetBossInUse` (566-572) |
| 609 | `setTimeout(() => this.notifyBossAvailable(), 0);` | `handleAgentRemoved` (597-613) |

All six exist solely to escape re-entrant XState `send()` calls (the XState v5 alternative is `raise`/deferred events; if the ARC-004/017 refactor moves these notifications into machine actions, prefer `raise` there instead of this service-level queue).

**Steps**:
1. Add to `AgentMachineService` a private deferral mechanism:
   ```ts
   private deferred: Array<() => void> = [];
   private flushScheduled = false;

   private defer(fn: () => void): void {
     this.deferred.push(fn);
     if (!this.flushScheduled) {
       this.flushScheduled = true;
       queueMicrotask(() => this.flushDeferred());
     }
   }

   /** Exposed for tests: run all deferred notifications now, in FIFO order. */
   flushDeferred(): void {
     this.flushScheduled = false;
     const batch = this.deferred;
     this.deferred = [];
     for (const fn of batch) fn();
   }
   ```
   Rationale for `queueMicrotask` over `setTimeout(0)`: it still runs strictly after the current synchronous XState transition completes (the only property the code needs) but is immune to timer clamping/reordering under React batching changes, and `flushDeferred()` makes tests deterministic without fake timers.
2. Replace each of the six `setTimeout(() => X, 0)` calls with `this.defer(() => X)`. Do not change WHICH notification fires or its guard conditions (e.g. the `actualIndex === 0 && !freshStore.boss.inUseBy` guard before line 474 stays).
3. If ARC-004/017's redesign deletes any of the six sites outright (e.g. the boss-availability watchdog removal makes `notifyBossAvailable` a direct queue-owner concern), delete rather than convert — the goal is fewer deferral points, not a prettier deferral.
4. Extend the ARC-004/017 characterization tests: after driving a queue-join to index 0, call `service.flushDeferred()` and assert `notifyBossAvailable`'s observable effect; assert repeated `defer` calls in one tick produce one FIFO flush.

**Verification**:
- `cd /Users/probello/Repos/claude-office/frontend && make checkall`
- `grep -c "setTimeout(" frontend/src/machines/agentMachineService.ts` returns `0` (no timer-based ordering remains; the 3-second stuck-boss watchdog is separately deleted by ARC-004 — if that hasn't happened yet, the count instead equals the watchdog's timer count only).
- Manual smoke: `make dev-tmux` + root `make simulate`; agents queue at the boss, converse, and depart without stalls.

**Do NOT**:
- Do not execute this as a standalone PR ahead of ARC-004/017 — the audit sequences it inside that refactor because the ownership model determines which notifications survive.
- Do not swap in `Promise.resolve().then(...)` chains per-site — the point is ONE flushable queue.
- Do not delete the deferral guards' conditions while converting.

---

### [QA-010] Version string manually synchronized across 7 files
**Priority**: Medium | **Effort**: M | **Phase**: Phase 3b/3c
**Preconditions**: None (DOC-007 adds an 8th location that the automation must include)
**Files**: see ARC-021

**Goal**: One `make bump VERSION=x.y.z` (or CI check) keeps every version location in sync.

**This is the same issue as ARC-021 — see [ARC-021] in the Architecture section for the full procedure.** The steps below are the QA-specific verified additions the automation must incorporate.

**Steps**:
1. Enumerate the sync targets from these verified anchors — all 7 documented locations are currently in sync at `0.22.0`: `pyproject.toml:3`, `backend/pyproject.toml:3`, `hooks/pyproject.toml:3`, `hooks/src/claude_office_hooks/main.py:36` (`__version__ = "0.22.0"` — note it is indented inside a guarded block), `frontend/package.json:3`, `opencode-plugin/package.json:3`, `frontend/src/app/page.tsx:424` (`v0.22.0` badge inside the `<h1>`, lines 423-425).
2. Include the **8th, drifted** location in the automation: `backend/app/config.py:13` (`VERSION: str = "0.14.0"`) — DOC-007 fixes the value and adds it to CLAUDE.md's table; the bump script MUST cover it or the drift recurs (it already regressed once after being fixed in v0.15.0).
3. For `hooks/main.py`, prefer deriving `__version__` via `importlib.metadata.version("claude-office-hooks")` (confirm the exact distribution name in `hooks/pyproject.toml` first) with the hardcoded string as fallback — the hooks CLI's defense-in-depth design means the import must never raise, so wrap it in try/except.

**Verification**:
- Run the new bump target against a scratch version, `git diff` shows exactly the 8 locations changed, then revert.
- `grep -rn "0\.22\.0" pyproject.toml backend/pyproject.toml hooks/pyproject.toml frontend/package.json opencode-plugin/package.json hooks/src/claude_office_hooks/main.py frontend/src/app/page.tsx backend/app/config.py` — all show the same version after a bump.

**Do NOT**:
- Do not bump the actual project version as part of landing the tooling.

---

### [QA-011] Unconditional `console.log` in production path
**Priority**: Low | **Effort**: S | **Phase**: Phase 3c
**Preconditions**: None (if ARC-004/017 is mid-flight on this file, land there instead)
**Files**: `frontend/src/machines/agentMachineService.ts` (lines 526-529)

**Goal**: The boss-bubble skip log only fires when debug mode is on.

**Context** (verified): the only `console.` call in the file is inside `handleShowBossBubble`:
```ts
526    if (isCompleting || hasPersistentBubble) {
527      console.log(
528        `[AgentMachineService] Skipping boss bubble "${text.slice(0, 30)}..." - isCompleting=${isCompleting}, hasPersistentBubble=${hasPersistentBubble}`,
529      );
```
The canonical non-React gating pattern in this codebase is `useGameStore.getState().debugMode` (see `frontend/src/components/attention/CommandBar.tsx:97`); `agentMachineService.ts` already calls `useGameStore.getState()` elsewhere (e.g. in `handleQueueJoined`), so the import exists.

**Steps**:
1. Wrap the log:
   ```ts
   if (isCompleting || hasPersistentBubble) {
     if (useGameStore.getState().debugMode) {
       console.log(
         `[AgentMachineService] Skipping boss bubble "${text.slice(0, 30)}..." - isCompleting=${isCompleting}, hasPersistentBubble=${hasPersistentBubble}`,
       );
     }
   ```
   Keep the message text byte-identical.

**Verification**:
- `cd /Users/probello/Repos/claude-office/frontend && make checkall`
- With the app running and debug mode OFF, the browser console shows no `[AgentMachineService]` lines during simulate.

**Do NOT**:
- Do not delete the log — it is useful under debug.
- Do not gate any other behavior in `handleShowBossBubble` (the early-return logic stays unconditional).

---

### [QA-012] `||` vs `??` inconsistency in `updateAgentMeta`
**Priority**: Low | **Effort**: S | **Phase**: Phase 3c
**Preconditions**: QA-001 (so the surrounding behavior is characterized); coordinate with QA-003/ARC-005 which restructure the same action
**Files**: `frontend/src/stores/gameStore.ts` (line 519), `frontend/tests/gameStore.test.ts`

**Goal**: An explicitly-provided empty-string `currentTask` clears the previous task instead of silently keeping it.

**Context** (verified at exactly line 519; note the asymmetry with line 518):
```ts
518          name: meta.name ?? agent.name,
519          currentTask: meta.currentTask || agent.currentTask,
```

**Steps**:
1. `grep -rn "updateAgentMeta" frontend/src/` and read each call site to confirm none deliberately passes `""` expecting fallback-to-previous (if one does, that caller is relying on the bug — fix the caller to pass `undefined`).
2. Change line 519 to:
   ```ts
   currentTask: meta.currentTask ?? agent.currentTask,
   ```
3. Add the deferred test case to `frontend/tests/gameStore.test.ts` (QA-001 deliberately excluded it): `updateAgentMeta` with `currentTask: ""` results in `currentTask === ""`; with `currentTask: undefined` the previous task is kept.

**Verification**:
- `cd /Users/probello/Repos/claude-office/frontend && make checkall`
- `sed -n '519p' frontend/src/stores/gameStore.ts` shows `??` (line number valid until QA-003/ARC-005 restructure the file).

**Do NOT**:
- Do not "fix" other `||` usages in the file wholesale — only this line was audited as a behavior bug; flag others if you spot them, don't change them.

---

### [QA-013] Magic numbers in domain logic
**Priority**: Low | **Effort**: M | **Phase**: Phase 3c
**Preconditions**: Coordinate with ARC-005/QA-003 (gameStore) and ARC-002/QA-004 (event_processor) if in flight on the same lines
**Files**: `frontend/src/constants/positions.ts`, `frontend/src/systems/queuePositions.ts`, `frontend/src/stores/gameStore.ts`, `frontend/src/components/game/OfficeGame.tsx`, `frontend/src/hooks/useWebSocketEvents.ts`, `frontend/src/components/game/Whiteboard.tsx`, `backend/app/core/state_machine.py`, `backend/app/core/event_processor.py`

**Goal**: Desk-layout numbers, history caps, the whiteboard marker palette, and the tool-icon dict are named constants defined once per component (backend and frontend each own their copy, cross-referenced by comment).

**Verified anchors** (the audit's generic claim, made concrete):
- Desks-per-row `4` is hardcoded in FOUR frontend places and one backend place:
  1. `frontend/src/systems/queuePositions.ts:228` — `const rowSize = 4;` inside `getDeskPosition` (227-241).
  2. `frontend/src/stores/gameStore.ts:431-433` — `Math.ceil((newAgents.size + 1) / 4) * 4` inside `addAgent`.
  3. `frontend/src/components/game/OfficeGame.tsx:257-259` — `Math.max(8, Math.ceil(agents.size / 4) * 4)`.
  4. `frontend/src/hooks/useWebSocketEvents.ts:235` — `state.office.deskCount ?? 8` (minimum-desk default).
  5. `backend/app/core/state_machine.py:722` — `min(self.MAX_AGENTS, max(8, ((len(self.agents) + 3) // 4) * 4))` inside `to_game_state` (`MAX_AGENTS = 8` is already a named class attr at line 520).
  The minimum `8` is likewise duplicated (OfficeGame.tsx:258, gameStore.ts:1045 `deskCount: 8`, useWebSocketEvents.ts:235, state_machine.py:722).
- History caps `500`: backend `backend/app/core/event_processor.py:432-433` and `769-770` (two identical `if len(sm.history) > 500: sm.history = sm.history[-500:]` pairs); backend `state_machine.py:522` already names `MAX_CONVERSATION_ENTRIES = 500`; frontend `gameStore.ts:275` already names `MAX_EVENT_LOG = 500`.
- Inline color palette: `frontend/src/components/game/Whiteboard.tsx:84` — `const markerColors = [0xef4444, 0x22c55e, 0x3b82f6];` declared inside a function body (rebuilt per call). (`TrashCanSprite.tsx:39 PAPER_COLORS` and `skyRenderer.ts:19-22` are already module-level constants — leave them.)
- `tool_icons` dict rebuilt per call: `backend/app/core/state_machine.py:828-838`, inside method `tool_to_thought` (starts line 816), called on every PRE/POST_TOOL_USE event; fallback lookup `tool_icons.get(tool_name, "⚙️")` at line 842.

**Steps**:
1. Frontend: in `frontend/src/constants/positions.ts` (the constants directory already exists with `canvas.ts`, `positions.ts`, `quotes.ts`) add:
   ```ts
   /** Desk grid shape — keep in sync with backend state_machine.py DESKS_PER_ROW. */
   export const DESKS_PER_ROW = 4;
   export const MIN_DESK_COUNT = 8;
   ```
   Replace the literals at anchors 1-4 above (`rowSize`, both `/ 4) * 4` round-ups, and the `?? 8` / `Math.max(8, ...)` minimums, plus `deskCount: 8` at gameStore.ts:1045) with imports of these constants.
2. Backend: in `backend/app/core/state_machine.py`, next to `MAX_AGENTS` (line 520) add class attributes `DESKS_PER_ROW: int = 4` and `MIN_DESK_COUNT: int = 8` with a comment `# Keep in sync with frontend/src/constants/positions.ts`, and rewrite line 722 as:
   ```python
   desk_count = min(
       self.MAX_AGENTS,
       max(self.MIN_DESK_COUNT, ((len(self.agents) + self.DESKS_PER_ROW - 1) // self.DESKS_PER_ROW) * self.DESKS_PER_ROW),
   )
   ```
3. Backend: hoist the icon dict — add module-level `_TOOL_ICONS: dict[str, str] = { ... }` above the `StateMachine` class with the exact 9 entries from lines 828-838 (Read, Write, Edit, Bash, Glob, Grep, WebSearch, WebFetch, Task) and change `tool_to_thought` to use `_TOOL_ICONS.get(tool_name, "⚙️")`.
4. Backend: in `event_processor.py`, add module-level `MAX_HISTORY_ENTRIES = 500` and use it at both cap sites (432-433 and 769-770) — note ARC-002/QA-004 may have moved these lines; grep for `sm.history[-500:]` to relocate.
5. Frontend: hoist `markerColors` in `Whiteboard.tsx` to a module-level `const MARKER_COLORS = [0xef4444, 0x22c55e, 0x3b82f6];` above the component and use it at (old) line 84.
6. A single shared backend↔frontend constant source is explicitly OUT of scope (it would require extending the `gen_types.py` pipeline) — the cross-reference comments are the required deliverable; if you want to propose pipeline work, raise it, don't build it.

**Verification**:
- `cd /Users/probello/Repos/claude-office/backend && make checkall && cd ../frontend && make checkall`
- `grep -rn "rowSize = 4\|/ 4) \* 4" frontend/src/` returns nothing; `grep -n "500" backend/app/core/event_processor.py` shows only the named-constant definition.
- Visual smoke: desks still render 4-per-row with an 8-desk minimum.

**Do NOT**:
- Do not change any numeric VALUE — this is naming only.
- Do not build a generated shared-constants mechanism in this issue.
- Do not touch already-named constants (`MAX_EVENT_LOG`, `MAX_CONVERSATION_ENTRIES`, `PAPER_COLORS`, skyRenderer palettes).

---

### [QA-014] Hardcoded WebSocket origin allowlist couples security check to default ports
**Priority**: Low | **Effort**: S | **Phase**: Phase 3c
**Preconditions**: ARC-011 (moves `ConnectionManager`; this file is on its path — if ARC-011 is in flight, apply there)
**Files**: `backend/app/api/websocket.py` (lines 15-23, 26-45), `backend/app/config.py` (lines 16-22)

**Goal**: The WebSocket origin allowlist is derived from `settings.BACKEND_CORS_ORIGINS` at call time while remaining localhost-only.

**Context** (verified): `_ALLOWED_WS_ORIGINS` is a hardcoded frozenset at `websocket.py:16-23` ({`http://localhost:3000`, `http://127.0.0.1:3000`, `http://localhost:8000`, `http://127.0.0.1:8000`}). `config.py:16-22` defines `BACKEND_CORS_ORIGINS: list[str]` with the SAME entries plus `http://0.0.0.0:3000`. `validate_websocket_origin` (26-45) checks `origin.rstrip("/") in _ALLOWED_WS_ORIGINS` for browser clients and requires `X-API-Key` for non-browser clients; it already imports `get_settings` lazily inside the function body.

**Steps**:
1. Replace the frozenset literal with a call-time derivation that filters to loopback hosts (this keeps the localhost-only guarantee even if someone adds a LAN origin to `BACKEND_CORS_ORIGINS`):
   ```python
   from urllib.parse import urlparse

   _LOCALHOST_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})


   def _allowed_ws_origins() -> frozenset[str]:
       """Origins permitted for WebSocket connections, derived from settings.

       Filtered to loopback hosts so a CORS-config change can never open the
       WS stream to non-local origins.
       """
       from app.config import get_settings

       return frozenset(
           origin.rstrip("/")
           for origin in get_settings().BACKEND_CORS_ORIGINS
           if urlparse(origin).hostname in _LOCALHOST_HOSTS
       )
   ```
2. In `validate_websocket_origin`, change the browser branch to `return origin.rstrip("/") in _allowed_ws_origins()`. Delete the old `_ALLOWED_WS_ORIGINS` constant (grep the backend first for other references, including tests).
3. Deliberate behavior note: `http://0.0.0.0:3000` (present in `BACKEND_CORS_ORIGINS`) is NOT a loopback hostname and will remain excluded from the WS allowlist — same effective behavior as today. State this in the PR description.
4. Update/extend `backend/tests/test_security_hardening.py` (it exercises origin checks): keep existing accept/reject cases green; add one asserting a non-localhost origin present in `BACKEND_CORS_ORIGINS` is still rejected for WS.

**Verification**:
- `cd /Users/probello/Repos/claude-office/backend && make checkall`
- `grep -n "_ALLOWED_WS_ORIGINS" backend/` returns nothing.

**Do NOT**:
- Do not simply reuse `BACKEND_CORS_ORIGINS` unfiltered — that would silently widen the WS trust boundary to whatever CORS allows (the audit's "keep localhost-only" clause).
- Do not weaken any failing security test — a failure here means the derivation changed the boundary; fix the derivation.

---

### [QA-015] Broadcast send-and-prune loop implemented three times in `websocket.py`
**Priority**: Low | **Effort**: S | **Phase**: Phase 3c
**Preconditions**: ARC-011 (same file — apply after/with it)
**Files**: `backend/app/api/websocket.py`

**Goal**: `broadcast_all` and `broadcast_overview` reuse the existing `_broadcast_to_connections` helper instead of inlining their own copies of the send-and-prune loop.

**Context** (verified — this corrects the audit's framing): the helper ALREADY exists — `_broadcast_to_connections` at lines 66-99, used by `broadcast` (122-132) and `broadcast_room` (193-201). The two inline duplicates are:
- `broadcast_all` (lines 142-170) — iterates `(session_id, conn)` tuples across all keys of `active_connections` and prunes per-key.
- `broadcast_overview` (lines 228-249) — iterates the flat `overview_connections` list and prunes it.

**Steps**:
1. Read `_broadcast_to_connections` (66-99) and note its exact signature and return contract (which failed connections it reports / how it prunes). If it currently prunes a specific structure internally, refactor it minimally so it: takes `connections: list[WebSocket]` + `message`, performs the guarded `send_json` loop (preserving the `client_state == WebSocketState.CONNECTED` check and the `logger.warning` on failure), and RETURNS `list[WebSocket]` of failed connections — leaving structure-specific pruning to each caller. Update `broadcast` and `broadcast_room` for the new contract in the same commit.
2. Rewrite `broadcast_overview` (228-249) as: copy `overview_connections` under `self._lock`; call the helper; if failures, re-acquire the lock and remove them from `overview_connections` (identical to today's semantics, minus the duplicated loop).
3. Rewrite `broadcast_all` (142-170): under the lock, snapshot `pairs: list[tuple[str, WebSocket]]`; call the helper with `[conn for _, conn in pairs]`; prune failed connections from their owning `active_connections[session_id]` lists by identity, preserving today's per-key cleanup (including removing emptied keys if the current code does).
4. Preserve lock discipline exactly: snapshot under lock → send WITHOUT holding the lock → prune under lock. All three call sites already follow this; do not hold `self._lock` across `send_json`.

**Verification**:
- `cd /Users/probello/Repos/claude-office/backend && make checkall`
- `grep -c "send_json" backend/app/api/websocket.py` — only the helper (and any single-recipient sends) call it; the three broadcast methods contain no send loops.

**Do NOT**:
- Do not merge the three broadcast methods into one — their audiences (per-session, all, overview) are distinct APIs; only the loop is shared.
- Do not change message shapes or add new locking.

---

### [QA-016] `OfficeGame.tsx` and `page.tsx` approaching God-component size
**Priority**: Low | **Effort**: M | **Phase**: Phase 3c — explicitly opportunistic: apply when these files are next touched (ARC-006 will touch both)
**Preconditions**: ARC-006 (its selector-narrowing and frame-batching changes reshape the same regions — do this alongside or after, never before)
**Files**: `frontend/src/components/game/OfficeGame.tsx` (748 lines), `frontend/src/app/page.tsx` (577 lines), `frontend/src/hooks/usePixiApp.ts` (new)

**Goal**: The Pixi application lifecycle lives in a dedicated `usePixiApp` hook and page-level inline widgets are extracted, bringing both files under ~500 lines.

**Verified extraction anchors in OfficeGame.tsx**:
- Pixi lifecycle pieces for `usePixiApp`: the module-scope `extend({ Container, Text, Graphics, Sprite })` at line 98; `const appRef = useRef<PixiApplication | null>(null)` at line 180; the unmount cleanup effect at 194-201 (nulls `appRef`, calls `performSoftReset()`); the `<Application>` props block at 328-340 (`key={
   `pixi-app-${hmrVersion}`}`, `width={CANVAS_WIDTH}`, `height={CANVAS_HEIGHT}`, `backgroundColor`, `autoDensity`, `resolution` from `window.devicePixelRatio`, `onInit={(app) => { appRef.current = app; }}`). The hook should own `appRef`, the `onInit` callback, the cleanup effect, and export the Application props object; the JSX element itself stays in the component.
- Derived-data candidates for extraction into selectors/pure helpers: `occupiedDesks` useMemo (234-242), `deskTasks` useMemo (245-254), `deskCount` useMemo (257-259). Note the O(n²) nested scan at line 699 (`Array.from(agents.values()).find(...)` inside a render map) — replace with a precomputed Map when touching this region.
- The seven full-Map render passes are at lines 489, 529, 541, 635, 649, 699, 717 (audit's cited line 204 is actually the store subscription `useGameStore(useShallow(selectAgents))`, not a pass).

**Verified anchors in page.tsx**: the root-page whole-Map subscription is line 146 (`const agents = useGameStore(useShallow(selectAgents));`) — ARC-006 owns narrowing it; the surrounding subscription block is 144-151. The `v0.22.0` header badge is at lines 423-425 (inline widget candidate; remember it is a version-sync location per CLAUDE.md — if extracted, update the Version Management table's "Frontend display" row to the new file path).

**Steps**:
1. When ARC-006 (or any substantial change) next touches `OfficeGame.tsx`: create `frontend/src/hooks/usePixiApp.ts` owning `appRef` + `onInit` + the cleanup effect + the memoized Application props; replace the inline pieces listed above.
2. Move `occupiedDesks`/`deskTasks`/`deskCount` computation into pure exported helpers (e.g. in `frontend/src/systems/` or a `selectors.ts`) so they can be unit-tested; keep the `useMemo` wrappers in the component.
3. When next touching `page.tsx`: extract self-contained header/sidebar inline widgets into `frontend/src/components/layout/` components; update the CLAUDE.md version table if the badge moves.
4. Keep each extraction to ≤5 files per PR (phased execution) and run the visual smoke after each.

**Verification**:
- `cd /Users/probello/Repos/claude-office/frontend && make checkall`
- `wc -l frontend/src/components/game/OfficeGame.tsx frontend/src/app/page.tsx` — both trending toward < 500.
- Visual smoke: `make dev-tmux`, root `make simulate` — canvas renders, zoom/pan works, HMR does not double-mount (StrictMode is disabled globally per ARC-026; the `pixi-app-${hmrVersion}` key is the HMR guard — preserve it).

**Do NOT**:
- Do not do this as a standalone big-bang refactor — the audit marks it "when next touched".
- Do not remove the `hmrVersion` key or the `performSoftReset()` cleanup — they guard against the documented @pixi/react double-mount race.
- Do not narrow the page.tsx agents selector here if ARC-006 hasn't landed — that's its change to make.

---

## Documentation (DOC)

### [DOC-001] `make checkall` is documented as running tests, but it does not
**Priority**: Critical | **Effort**: S | **Phase**: Phase 2 (must land in the SAME change as ARC-001 — they share the root `Makefile` and the decision must be made once)
**Preconditions**: None (bundled with ARC-001)
**Files**: `Makefile` (root), `README.md`, `CONTRIBUTING.md` (verify only), `CLAUDE.md` (verify only)

**Goal**: `make checkall` from the repo root actually runs the test suites, matching what README, CONTRIBUTING, and CLAUDE.md already claim.

**Verified facts (these correct AUDIT.md)**:
- Root `Makefile` line 60: `checkall: fmt lint typecheck` — omits `test`. A root `test` target EXISTS (runs `make -C backend test` and `make -C frontend test`) but is documented nowhere.
- `backend/Makefile` line 21: `checkall: fmt lint typecheck test` — already includes tests.
- **Audit correction**: `frontend/Makefile` does NOT omit tests. Its `checkall` (lines 36-38) is `checkall: fmt lint typecheck build` followed by a recipe line running `$(PKG_MGR) run test`. Only the root Makefile has the gap — do not modify `frontend/Makefile` for this issue.
- Docs already describe the intended behavior: `README.md:203` (`| \`make checkall\` | Run format, lint, typecheck, and tests |`), `CONTRIBUTING.md:83` ("runs format, lint, typecheck, and tests for all components") with the PR checklist gating on it at line 198, and `CLAUDE.md:18` (`make checkall      # Lint, typecheck, test all components`).

**The decision**: two ways to reconcile — (A) make the Makefile match the docs by adding `test` to the root `checkall` chain, or (B) correct the three documents to say checkall excludes tests and document `make test` separately. **Option A is recommended**: it matches the project owner's global standard (checkall = fmt + lint + typecheck + test), matches what `backend/Makefile` and `frontend/Makefile` already do, and keeps the PR checklist meaningful. Option B would leave the project's primary verification gate not running tests.

**Steps (recommended Option A)**:
1. In the root `Makefile`, change the `checkall` line (line 60):
   ```makefile
   checkall: fmt lint typecheck test		# Run all checks
   ```
   Order matters: tests run last, after formatting/lint/typecheck, mirroring `backend/Makefile`. (ARC-001, in the same change, extends `lint`/`typecheck`/`test` to also recurse into `hooks/` and `opencode-plugin/` — this step is only the `test` inclusion.)
2. In `README.md`, add the missing `make test` row to the Available Commands table (lines 198-206), after the `make checkall` row at line 203:
   ```markdown
   | `make test` | Run all test suites without the other checks |
   ```
3. Verify (no edit expected) that `README.md:203`, `CONTRIBUTING.md:80-85`, `CONTRIBUTING.md:194-202`, and `CLAUDE.md:18` now describe reality exactly. They already say "and tests", so with step 1 no doc text changes are needed — do not reword them.

**Steps (alternative Option B — only if the project owner rejects A)**:
1. Leave the root `Makefile` unchanged.
2. Edit `README.md:203` to `| \`make checkall\` | Run format, lint, and typecheck (tests run separately via \`make test\`) |` and add the `make test` row.
3. Edit `CONTRIBUTING.md:83` comment to `# runs format, lint, and typecheck for all components` and add an explicit `- [ ] \`make test\` passes` line to the PR checklist (after line 198).
4. Edit `CLAUDE.md:18` comment to `# Lint, typecheck all components (tests: make test)`.

**Verification**:
- `cd /Users/probello/Repos/claude-office && make checkall` — output must show pytest running (backend) AND vitest running (frontend). Paste the tail of the output in the PR; do not claim success without it.
- `grep -n "^checkall:" Makefile backend/Makefile frontend/Makefile` — root shows `fmt lint typecheck test`.
- `grep -n "make test" README.md` — the new table row exists.

**Do NOT**:
- Do not modify `frontend/Makefile` or `backend/Makefile` — both already run tests in checkall.
- Do not resolve this differently from ARC-001's Makefile edits — one Makefile, one combined change.
- Do not reword unrelated README/CONTRIBUTING sections while in the files.

---

> File-conflict discipline for the DOC entries (from AUDIT.md's conflict map): `backend/README.md` is touched by DOC-002/003/004/006/010 — do DOC-003's Authentication section first, then apply the rest in ONE combined editing pass. `docs/architecture/ARCHITECTURE.md` is touched by DOC-003/004/006/008/016 — same rule. `docs/reference/ai-summary.md` is touched by DOC-005 and DOC-009 — one pass. After any earlier DOC edit to a shared file, re-verify line anchors before editing again; anchors below reflect the pre-remediation state.

### [DOC-002] Static-serving docs omit the required `SERVE_STATIC=1` gate
**Priority**: High | **Effort**: S | **Phase**: Phase 3d (land together with DOC-006 so the env-table row exists where the examples reference it)
**Preconditions**: None (pairs with DOC-006)
**Files**: `backend/README.md`, `docs/guides/deployment.md`

**Goal**: Every static-serving instruction states the `SERVE_STATIC=1` requirement, and the `docker run` example actually produces a working deployment.

**Verified facts**: static serving is gated at `backend/app/main.py:32` (`_SERVE_STATIC = os.environ.get("SERVE_STATIC", "").lower() in ("1", "true", "yes")`) and applied at `main.py:411` (`if _SERVE_STATIC and STATIC_DIR.exists():`). `docker-compose.yml` sets `SERVE_STATIC=1` (and `DATABASE_URL=sqlite+aiosqlite:////app/data/visualizer.db`); the `docker run` example sets neither.

**Steps**:
1. `backend/README.md`, section `### With Static Frontend` (lines 127-137): it currently claims the server "automatically serves" a built frontend and its example runs `uv run uvicorn app.main:app --host 0.0.0.0 --port 8000` with no env var. Rewrite the claim to state that serving the static build requires `SERVE_STATIC` to be truthy AND `backend/static/` to exist (built via `make build-static`), and change the example command to:
   ```bash
   SERVE_STATIC=1 uv run uvicorn app.main:app --host 0.0.0.0 --port 8000
   ```
   Add one sentence: "Without `SERVE_STATIC=1` the API still runs but the root URL serves no frontend."
2. `docs/guides/deployment.md`, `### Using Docker CLI` (lines 244-256): add two `-e` lines to the `docker run` example so it matches what `docker-compose.yml` sets — insert after line 254 (`-e CLAUDE_PATH_CONTAINER=/claude-data \`):
   ```
     -e SERVE_STATIC=1 \
     -e DATABASE_URL=sqlite+aiosqlite:////app/data/visualizer.db \
   ```
   (The missing `DATABASE_URL` means the DB is currently written to a non-volume path — same class of gap; the compose file is the source of truth.)
3. Add the `SERVE_STATIC` row to the deployment guide's env table (lines 97-106) — coordinate with DOC-006, which owns the table rows; if DOC-006 already landed, verify the row exists instead of adding it twice:
   ```markdown
   | `SERVE_STATIC` | (unset) | Set to `1`/`true`/`yes` to serve the built frontend from `backend/static/` |
   ```

**Verification**:
- `grep -n "SERVE_STATIC" backend/README.md docs/guides/deployment.md` — at least one hit in each, including inside the `docker run` code block.
- `grep -rn "automatically serves" backend/README.md` — no matches.
- Functional check: `make build-static`, then `cd backend && SERVE_STATIC=1 uv run uvicorn app.main:app --port 8000` → `curl -s http://localhost:8000/ | head -1` returns HTML; without the env var it does not.

**Do NOT**:
- Do not change any backend code (the gate itself is correct and intentional since v0.15.0).
- Do not touch `docker-compose.yml` (it is already correct; SEC-004 owns its port-binding change).

---

### [DOC-003] API authentication model (`X-API-Key`) is completely undocumented
**Priority**: High | **Effort**: M | **Phase**: Phase 3d — MUST land before DOC-006 (it defines how the key variables are described)
**Preconditions**: Check SEC-001's status first — it changes how the frontend obtains the key; one sentence below has two variants
**Files**: `backend/README.md`, `docs/architecture/ARCHITECTURE.md`, `docs/guides/deployment.md`, `hooks/README.md`

**Goal**: A user can configure and script against the secured backend using only the docs — key sources, header name, gated endpoints, discovery, and WebSocket rules are all documented.

**Verified implementation facts (write the docs from these — do not re-derive)**:
- Key sources: explicit `CLAUDE_OFFICE_API_KEY` Settings field (`backend/app/config.py:47`, default `""`); per-launch auto-generated fallback `_auto_api_key: str = secrets.token_hex(32)` (`config.py:52`); `effective_api_key` property returns explicit-or-auto (`config.py:54-57`).
- Header: `X-API-Key`, checked by `ApiKeyMiddleware` (`backend/app/main.py:84-118`) with `hmac.compare_digest` (line 115).
- Keyless (auto) mode gates ONLY the destructive global operations per `_is_state_changing` (`main.py:68-81`): `DELETE /api/v1/sessions` and `POST /api/v1/sessions/simulate`. (SEC-002 adds `focus_session` — if it has landed, include it.)
- Explicit-key mode gates EVERYTHING except `_NO_AUTH_PATHS = {"/health", "/docs", "/redoc"}` (main.py:65), the OpenAPI JSON path, `OPTIONS` requests, and `/ws/` handshakes (skipped at main.py:99-104 — WS has its own check).
- Discovery: `GET /api/v1/status` (`main.py:240-254`) returns `aiSummaryEnabled`, `aiSummaryModel`, and — pre-SEC-001 — `apiKey: settings.effective_api_key`.
- WebSockets (`backend/app/api/websocket.py:26-45`): browser clients (Origin header present) must match the localhost origin allowlist; non-browser clients (no Origin) must send `X-API-Key` matching the effective key; failures close with code 4003.
- Hooks client: `hooks/src/claude_office_hooks/main.py:80-84` sends `X-API-Key` automatically when a key is configured; the key is read from the `CLAUDE_OFFICE_API_KEY` env var or `~/.claude/claude-office-config.env` (env takes precedence, `hooks/src/claude_office_hooks/config.py:77`).
- OpenCode plugin sends NO key at all (see DOC-012/SEC-005).

**Steps**:
1. In `backend/README.md`, insert a new `## Authentication` section between the end of the Configuration section and `## API Endpoints` (line 165). Use this text (adjust the SEC-001 sentence per its status):
   ```markdown
   ## Authentication

   State-changing endpoints are protected by an API key sent in the `X-API-Key` header.
   Keys are compared in constant time (`hmac.compare_digest`).

   | Mode | How the key is set | What requires the key |
   |------|--------------------|-----------------------|
   | Auto-generated (default) | A random token is generated on every launch | Destructive global operations only: `DELETE /api/v1/sessions` and `POST /api/v1/sessions/simulate` |
   | Explicit | `CLAUDE_OFFICE_API_KEY` env var or `backend/.env` | All endpoints except `/health`, `/docs`, `/redoc`, the OpenAPI schema, and CORS preflight |

   Example:

   ```bash
   curl -X DELETE http://localhost:8000/api/v1/sessions \
     -H "X-API-Key: $CLAUDE_OFFICE_API_KEY"
   ```

   **Discovery**: `GET /api/v1/status` reports AI-summary availability<VARIANT>.

   **Clients**: the Claude Code hooks send `X-API-Key` automatically when
   `CLAUDE_OFFICE_API_KEY` is set (env var or `~/.claude/claude-office-config.env`).
   The OpenCode plugin does not yet send a key — see its Known Limitations.

   **WebSockets**: browser connections must come from an allowed localhost origin;
   non-browser clients (no `Origin` header) must present `X-API-Key` with the
   effective key or the handshake is closed with code 4003.
   ```
   For `<VARIANT>`: if SEC-001 has NOT landed, use " and the effective API key, which the localhost frontend uses to authenticate"; if SEC-001 HAS landed, describe the new key-delivery mechanism SEC-001 chose (read its diff — do not guess).
2. In `docs/architecture/ARCHITECTURE.md`, add a short `### Authentication` subsection at the end of `## Configuration Reference` (after the table and the Docker note at line 783) summarizing the same two-mode model in 5-6 lines and linking to the backend README section for details. (DOC-006 adds the `CLAUDE_OFFICE_API_KEY` table row itself.)
3. In `docs/guides/deployment.md`, in `## Configuration` (line 93 area), add 2-3 sentences: production/shared deployments should set an explicit `CLAUDE_OFFICE_API_KEY`; all state-changing calls then need the `X-API-Key` header. (DOC-006 adds the table row.)
4. In `hooks/README.md`, in the `## Configuration` section (lines 101-111), extend the config-file example with the key line and one sentence:
   ```bash
   # Optional: API key for a backend started with CLAUDE_OFFICE_API_KEY
   CLAUDE_OFFICE_API_KEY=
   ```
   plus: "When set (here or as an environment variable — env wins), the hook sends it as `X-API-Key` on every event POST."

**Verification**:
- `grep -n "X-API-Key" backend/README.md docs/architecture/ARCHITECTURE.md docs/guides/deployment.md hooks/README.md` — hits in all four.
- `grep -n "## Authentication" backend/README.md` — section exists; backend README's Table of Contents (if present near the top) updated to include it.
- Cross-check every documented claim against the implementation facts above (endpoint list, header name, close code 4003) — no invented behavior.

**Do NOT**:
- Do not document the pre-SEC-001 key-in-status behavior as a recommendation — it is being changed for a reason; state facts only.
- Do not change any code or generate/rotate any keys.
- Do not add env-table rows here (DOC-006 owns tables; this issue owns prose sections).

---

### [DOC-004] Command Center (v0.20 headline feature) has no architecture or component documentation
**Priority**: High | **Effort**: M | **Phase**: Phase 3d
**Preconditions**: DOC-003 (ARCHITECTURE.md/backend README shared-file ordering)
**Files**: `docs/architecture/ARCHITECTURE.md`, `backend/README.md`, `frontend/README.md`

**Goal**: ARCHITECTURE.md has a Command Center section structurally mirroring the Multi-Floor section, and the backend README's WebSocket table lists `/ws/overview`.

**Verified inventory (build the docs from this)**:
- Frontend: `frontend/src/components/command/` has 13 files — `CommandCenterBackground.tsx`, `CommandCenterBoard.tsx`, `CommandCenterCanvas.tsx`, `CommandCenterDecor.tsx`, `CommandCenterFurniture.tsx`, `CommandCenterPeer.tsx`, `CommandCenterView.tsx`, `CommandCenterZones.tsx`, `ExitingPeer.tsx`, `PeerPopup.tsx`, `layout.ts`, `sessionMatchesFloor.ts`, `useCommandCenterPeers.ts`; plus `stores/overviewStore.ts`, `hooks/useOverviewWebSocket.ts`, and systems `commandCenterGrid.ts`, `commandCenterMotion.ts`, `exitAnimation.ts`.
- Backend: `build_overview(sessions: dict[str, StateMachine]) -> OverviewState` at `backend/app/core/room_orchestrator.py:313`; async `build_overview_snapshot` at `backend/app/core/event_processor.py:598`; models in `backend/app/models/overview.py` — `OverviewBucket = Literal["needs_you", "working", "done"]` (line 29; the "ended" bucket is applied frontend-side), `OverviewEntry` (line 32: `session_id`, `bucket`, `state: BossState`, `current_task`, `todo_done`, `todo_total`, `subagent_count`, camelCase aliases), `OverviewState` (line 46: `entries`, `last_updated`).
- Endpoint: `@app.websocket("/ws/overview")` at `backend/app/main.py:257` (declared BEFORE `/ws/{session_id}` so "overview" is not captured as a session id); connection cap `_MAX_OVERVIEW_CONNECTIONS = 16` (`main.py:38`), refusal close code 4013, origin failure 4003, initial `state_update` snapshot on connect.
- Broadcast: debounced at 50 ms — `self._overview_flush_interval = 0.05` (`event_processor.py:176`), coalescing `_flush_overview_broadcast` at `:576`, scheduled last in event processing (see the comment at `event_processor.py:548-554`), cancelled on shutdown.

**Steps**:
1. In `docs/architecture/ARCHITECTURE.md`, insert a new `## Command Center` section immediately after the Multi-Floor section (which spans lines 659-765), before `## Configuration Reference` (line 767). Mirror the Multi-Floor structure exactly (intro paragraph → `### Components` file/purpose table → `### Data Flow` with a Mermaid diagram + numbered sequence → `### WebSocket`). Populate with the verified inventory above. For the Data Flow numbered sequence use:
   1. Any processed event schedules an overview broadcast (`_schedule_overview_broadcast`, debounced ~50 ms, no-op with zero watchers).
   2. `build_overview_snapshot` collects per-session `StateMachine`s and calls `build_overview()` to bucket each session (`needs_you` / `working` / `done`).
   3. `manager.broadcast_overview` fans the `OverviewState` out to at most 16 `/ws/overview` clients.
   4. `useOverviewWebSocket` feeds `overviewStore`; the `command/` components render sessions as peers in the Command Center room ("ended" bucket derived frontend-side).
   For each `command/` component, write the one-line purpose by reading the file's header comment — do not invent purposes.
2. Add the section to ARCHITECTURE.md's Table of Contents (line 5 area), matching the existing ToC format.
3. In `backend/README.md`, add a row to the `### WebSocket` table (lines 219-224):
   ```markdown
   | `/ws/overview` | Cross-session Command Center feed (`OverviewState`, max 16 clients) |
   ```
   (Match the existing table's column shape exactly — read it first.)
4. In `frontend/README.md`, add a Command Center row/entry to `## Key Components` (line 227 area) pointing at `components/command/`, `overviewStore.ts`, `useOverviewWebSocket.ts`. Keep it to 2-3 lines — DOC-011 owns the full inventory sync of that README.

**Verification**:
- `grep -n "## Command Center" docs/architecture/ARCHITECTURE.md` and `grep -n "ws/overview" backend/README.md` — both present.
- Mermaid syntax check: render the new diagram (IDE preview or `npx -y @mermaid-js/mermaid-cli`) — no errors.
- Every file path named in the new section exists on disk (`test -e` each).

**Do NOT**:
- Do not document aspirational behavior — only what the verified anchors show.
- Do not restructure the Multi-Floor section while mirroring it.
- Do not duplicate the full component inventory into frontend/README.md (DOC-011's job).

---

### [DOC-005] ai-summary.md documents an API method removed in v0.17.0
**Priority**: High | **Effort**: S | **Phase**: Phase 3d (same file as DOC-009 — apply both in one pass)
**Preconditions**: None
**Files**: `docs/reference/ai-summary.md`

**Goal**: The doc's API Methods list matches `backend/app/core/summary_service.py` — no section documents `summarize_tool_call`.

**Verified facts**: the doc is 323 lines. `## API Methods` is at line 18; `### 1. summarize_tool_call(tool_name, tool_input)` spans lines 20-42 (ending with the `---` at 42) and even says at line 40 "**Status:** Defined but not currently called in production code" — in reality `summarize_tool_call` has ZERO matches anywhere in `backend/` (deleted in v0.17.0). The remaining numbered sections: `### 2. summarize_agent_task` (44), `### 3. summarize_user_prompt` (62), `### 4. generate_agent_name(description, existing_names=None)` (82), `### 5. summarize_response` (163), `### 6. detect_report_request` (183).

**Steps**:
1. Delete lines 20-42 inclusive (the whole `### 1. summarize_tool_call` section including its trailing `---`).
2. Renumber the surviving sections 2→1, 3→2, 4→3, 5→4, 6→5 in their headings. Then grep the file for in-document references to the old numbering (e.g. "see method 4") and fix any.
3. While renumbering section 4 (now 3), correct its heading to the actual signature — `generate_agent_name(description, existing_names=None, agent_type=None)` (`summary_service.py:131-136` — the current heading omits `agent_type`).
4. Optional flag (do not expand scope): `generate_agent_name_fallback` (`summary_service.py:183`) and `dedupe_name` (`:389`) are public-ish and undocumented — add a one-line mention under `## Helper Methods` (line 263) or note it in the PR description for a follow-up.
5. Apply DOC-009's link fixes in this same editing pass (see DOC-009).

**Verification**:
- `grep -rn "summarize_tool_call" docs/` returns nothing.
- `grep -n "^### " docs/reference/ai-summary.md` — numbered sections run 1..5 with no gaps.
- Cross-check each remaining documented method exists: `grep -n "def summarize_agent_task\|def summarize_user_prompt\|def generate_agent_name\|def summarize_response\|def detect_report_request" backend/app/core/summary_service.py` — 5 hits.

**Do NOT**:
- Do not rewrite the surviving sections' prose — only delete, renumber, and fix the one signature.
- Do not document private methods (`_sanitize_untrusted`, `_call_with_retry`, `_extract_first_sentence`).

---

### [DOC-006] Environment-variable references incomplete across all four components
**Priority**: High | **Effort**: M | **Phase**: Phase 3d
**Preconditions**: DOC-003 (defines how key variables are described); coordinate with DOC-002 (SERVE_STATIC rows) and DOC-016 (DATABASE_URL correction) in the same passes
**Files**: `docs/architecture/ARCHITECTURE.md`, `backend/README.md`, `frontend/README.md`, `hooks/README.md`

**Goal**: ARCHITECTURE.md's Configuration Reference is the complete canonical table; each component README documents exactly its own variables.

**Verified inventory** (from `backend/app/config.py`, raw `os.environ` reads, and frontend greps — note two audit corrections):
- Backend Settings fields (config.py): `DATABASE_URL` (24), `GIT_POLL_INTERVAL` (25), `CLAUDE_CODE_OAUTH_TOKEN` (27), `SUMMARY_MODEL` (28), `SUMMARY_ENABLED` (29), `SUMMARY_MAX_TOKENS` (30), `CLAUDE_PATH_HOST` (32), `CLAUDE_PATH_CONTAINER` (33), `ZOMBIE_SUBAGENT_TIMEOUT_SECONDS` = 90 (41), `CLAUDE_OFFICE_API_KEY` = "" (47), `BACKEND_CORS_ORIGINS` (16-22).
- Raw env reads (only three exist in backend/app): `SERVE_STATIC` (main.py:32), `BEADS_POLL_INTERVAL` default 3.0 (beads_poller.py:44), `EVENT_RATE_LIMIT` default 300 (events.py:20). **Audit correction**: `ZOMBIE_SUBAGENT_TIMEOUT_SECONDS` is a pydantic Settings field, not a raw read — document it like the other Settings fields.
- ARCHITECTURE.md table (lines 771-781) already has: DATABASE_URL, GIT_POLL_INTERVAL, CLAUDE_CODE_OAUTH_TOKEN, SUMMARY_ENABLED, SUMMARY_MODEL, SUMMARY_MAX_TOKENS, CLAUDE_PATH_HOST, CLAUDE_PATH_CONTAINER, BEADS_POLL_INTERVAL. Missing: `ZOMBIE_SUBAGENT_TIMEOUT_SECONDS`, `CLAUDE_OFFICE_API_KEY`, `SERVE_STATIC`, `EVENT_RATE_LIMIT`, `BACKEND_CORS_ORIGINS`.
- backend/README.md table (145-154, 8 vars): missing `CLAUDE_OFFICE_API_KEY`, `ZOMBIE_SUBAGENT_TIMEOUT_SECONDS`, `SERVE_STATIC`, `BEADS_POLL_INTERVAL` (and `EVENT_RATE_LIMIT`).
- frontend/README.md: documents NO env vars; code reads `NEXT_PUBLIC_API_URL` (`frontend/src/utils/api.ts:10`, fallback `http://localhost:8000`), `NEXT_PUBLIC_WS_URL` (`useWebSocketEvents.ts:465` and `useOverviewWebSocket.ts:50`, fallback `ws://${hostname}:8000`), `NEXT_PUBLIC_I18N_DEBUG` (`i18n/index.ts:25`, compared `=== "true"`).
- hooks/README.md Configuration (101-111): missing `CLAUDE_OFFICE_API_URL` (localhost-only clamp at `hooks/src/claude_office_hooks/config.py:17-21` — any non-localhost URL is silently replaced with the default; note ARC-020 may change this policy, document whatever is current when you edit) and `CLAUDE_OFFICE_API_KEY` (DOC-003 adds the prose; add the table/example row here if DOC-003 didn't).

**Steps**:
1. ARCHITECTURE.md (lines 767-783), add these rows to the table (keep existing row format):
   ```markdown
   | `CLAUDE_OFFICE_API_KEY` | (empty — auto-generated per launch) | Explicit API key; gates all state-changing endpoints when set (see Authentication) |
   | `ZOMBIE_SUBAGENT_TIMEOUT_SECONDS` | `90` | Seconds before an unresponsive subagent is reaped |
   | `SERVE_STATIC` | (unset) | Set to `1`/`true`/`yes` to serve the built frontend from `backend/static/` |
   | `EVENT_RATE_LIMIT` | `300` | Max event POSTs accepted per 60 s window |
   | `BACKEND_CORS_ORIGINS` | localhost origins | Allowed CORS origins (localhost only by default) |
   ```
   Before writing the `ZOMBIE_SUBAGENT_TIMEOUT_SECONDS` and `EVENT_RATE_LIMIT` descriptions, read their usage sites (`config.py:34-41` comment block; `events.py:16-48`) and adjust wording to match actual semantics.
2. Same table: apply DOC-016's `DATABASE_URL` default correction (see DOC-016) in this pass.
3. backend/README.md (145-154): add the same five missing rows (this README is allowed to duplicate backend-owned vars since it is the component's own reference; keep descriptions consistent with ARCHITECTURE.md).
4. frontend/README.md: add a new `## Environment Variables` section (place before `## Testing`, line 332):
   ```markdown
   ## Environment Variables

   | Variable | Default | Description |
   |----------|---------|-------------|
   | `NEXT_PUBLIC_API_URL` | `http://localhost:8000` | Backend HTTP API base URL |
   | `NEXT_PUBLIC_WS_URL` | `ws://<hostname>:8000` | Backend WebSocket base URL |
   | `NEXT_PUBLIC_I18N_DEBUG` | (unset) | Set to `true` to log i18n lookups |
   ```
   (Verify the I18N_DEBUG description against `i18n/index.ts:25` context before writing it.)
5. hooks/README.md Configuration section: extend the config example/table with `CLAUDE_OFFICE_API_URL` (state the localhost-only clamp explicitly: "non-localhost values are currently ignored and reset to the default") and `CLAUDE_OFFICE_API_KEY` (if DOC-003 didn't already add it).

**Verification**:
- For each backend variable name in the inventory: `grep -n "<VAR>" docs/architecture/ARCHITECTURE.md` — present.
- `grep -c "NEXT_PUBLIC_" frontend/README.md` ≥ 3.
- `grep -n "CLAUDE_OFFICE_API_URL" hooks/README.md` — present.
- Every documented default matches the source anchor cited above (spot-check each against the file:line).

**Do NOT**:
- Do not document variables that don't exist in code (e.g. do not invent a frontend port variable).
- Do not describe `ZOMBIE_SUBAGENT_TIMEOUT_SECONDS` or `EVENT_RATE_LIMIT` semantics from memory — read the usage sites first.
- Do not change any code (ARC-016 moves `EVENT_RATE_LIMIT` into Settings separately; the env var name stays valid).

---

### [DOC-007] `backend/app/config.py` VERSION stale at 0.14.0 and missing from the version-sync procedure
**Priority**: High | **Effort**: S | **Phase**: Phase 3d (this is a CODE change — review as code)
**Preconditions**: None (QA-010/ARC-021's bump automation must include this location once added)
**Files**: `backend/app/config.py`, `CLAUDE.md`

**Goal**: The OpenAPI-surfaced version matches the release version, and the sync procedure structurally prevents this from drifting again.

**Verified facts**: `backend/app/config.py:13` is `VERSION: str = "0.14.0"` while all 7 documented locations are at `0.22.0` (root/backend/hooks pyprojects line 3, `hooks/src/claude_office_hooks/main.py:36`, `frontend/package.json:3`, `opencode-plugin/package.json:3`, `frontend/src/app/page.tsx:424`). CLAUDE.md's Version Management table is at lines 65-73 and does not list `config.py`. This exact field drifted once before (fixed in v0.15.0, regressed since).

**Steps**:
1. Read the CURRENT release version from root `pyproject.toml` line 3 (do not assume 0.22.0 — releases may have happened since this guide was written).
2. Edit `backend/app/config.py:13` to that version:
   ```python
       VERSION: str = "0.22.0"
   ```
3. Add a row to CLAUDE.md's Version Management table (after the `| Backend | \`backend/pyproject.toml\` |` row at line 68):
   ```markdown
   | Backend runtime | `backend/app/config.py` (`VERSION`) |
   ```
4. Optional hardening (preferred if QA-010's bump script is NOT being built soon): replace the literal with metadata derivation so it can never drift:
   ```python
   from importlib.metadata import PackageNotFoundError, version as _pkg_version

   try:
       _VERSION = _pkg_version("claude-office-backend")  # confirm the exact name in backend/pyproject.toml [project].name first
   except PackageNotFoundError:
       _VERSION = "0.0.0-dev"
   ```
   and `VERSION: str = _VERSION`. Only do this if you verify the package is importable by that name in the uv environment (`cd backend && uv run python -c 'from importlib.metadata import version; print(version("<name>"))'` succeeds); otherwise stick with steps 2-3.

**Verification**:
- `cd /Users/probello/Repos/claude-office/backend && uv run python -c "from app.config import get_settings; print(get_settings().VERSION)"` prints the current release version.
- `cd /Users/probello/Repos/claude-office/backend && make checkall`
- Start the backend and confirm `/docs` shows the right version in the header.
- `grep -n "config.py" CLAUDE.md` — the new table row exists.

**Do NOT**:
- Do not bump any of the other 7 locations — they are already correct; this is not a release.
- Do not implement step 4 without the installed-package verification — a wrong package name silently yields the fallback.

---

### [DOC-008] ARCHITECTURE.md Related Documentation links broken or stale
**Priority**: Medium | **Effort**: S | **Phase**: Phase 3d
**Preconditions**: None (same-file coordination with DOC-004/006/016 — one combined pass)
**Files**: `docs/architecture/ARCHITECTURE.md` (lines 785-790)

**Goal**: All four Related Documentation links resolve, with no stale "not yet created" annotations.

**Verified failures (current lines 787-790, end of file)**:
- `[README.md](../README.md)` resolves to `docs/README.md` (the docs index) — wrong target; must be `../../README.md`.
- `[CLAUDE.md](../CLAUDE.md)` resolves to `docs/CLAUDE.md` — does not exist; must be `../../CLAUDE.md`.
- `[WHITEBOARD.md](WHITEBOARD.md)` — does not exist; the doc lives at `docs/reference/whiteboard-modes.md`; the "_(not yet created)_" annotation is stale.
- `[Claude Code JSONL Format](research/claude-code-jsonl-format.md)` — wrong directory (resolves under `docs/architecture/`); actual file is `docs/research/claude-code-jsonl-format.md`; annotation stale.

**Steps**:
1. Replace lines 787-790 with:
   ```markdown
   - [README.md](../../README.md) - Project overview and quick start
   - [CLAUDE.md](../../CLAUDE.md) - AI assistant instructions and commands
   - [Whiteboard Modes](../reference/whiteboard-modes.md) - Whiteboard multi-mode display documentation
   - [Claude Code JSONL Format](../research/claude-code-jsonl-format.md) - Transcript file format research
   ```

**Verification**:
- From the file's directory, every relative link resolves:
  ```bash
  cd /Users/probello/Repos/claude-office/docs/architecture && \
  for p in ../../README.md ../../CLAUDE.md ../reference/whiteboard-modes.md ../research/claude-code-jsonl-format.md; do \
    test -f "$p" && echo "OK $p" || echo "BROKEN $p"; done
  ```
  — four OK lines.
- `grep -n "not yet created" docs/architecture/ARCHITECTURE.md` returns nothing.

**Do NOT**:
- Do not create `WHITEBOARD.md` — the content already exists at `docs/reference/whiteboard-modes.md`; fix the link, not the filesystem.
- Do not touch other sections of the file in this issue.

---

### [DOC-009] ai-summary.md relative links broken
**Priority**: Medium | **Effort**: S | **Phase**: Phase 3d (apply in the SAME pass as DOC-005 — same file; DOC-005's deletion shifts line numbers, so anchor by content)
**Preconditions**: None
**Files**: `docs/reference/ai-summary.md` (Related Documentation, currently lines 320-323 — the last lines of the file)

**Goal**: Both Related Documentation links resolve from `docs/reference/`.

**Verified current text**:
```markdown
322  - [Architecture](ARCHITECTURE.md) - System design and component overview
323  - [PRD](../PRD.md) - Full product requirements including AI summary integration
```
`ARCHITECTURE.md` resolves to `docs/reference/ARCHITECTURE.md` (missing); `../PRD.md` resolves to `docs/PRD.md` (missing — PRD.md is at the repo root).

**Steps**:
1. In the `## Related Documentation` section (find by heading, not line number, if DOC-005 already landed), change the two links:
   ```markdown
   - [Architecture](../architecture/ARCHITECTURE.md) - System design and component overview
   - [PRD](../../PRD.md) - Full product requirements including AI summary integration
   ```
2. If DOC-013 has landed (PRD relabeled as a historical snapshot), append " (historical snapshot)" to the PRD link text.

**Verification**:
- `cd /Users/probello/Repos/claude-office/docs/reference && test -f ../architecture/ARCHITECTURE.md && test -f ../../PRD.md && echo OK` prints OK.
- `grep -n "](ARCHITECTURE.md)\|](../PRD.md)" docs/reference/ai-summary.md` returns nothing.

**Do NOT**:
- Do not edit anything outside the Related Documentation section (DOC-005 owns the rest of the file's changes).

---

### [DOC-010] Backend README inventory drift
**Priority**: Medium | **Effort**: M | **Phase**: Phase 3d (part of the single combined backend/README.md pass, after DOC-003)
**Preconditions**: DOC-003 (shared file)
**Files**: `backend/README.md`

**Goal**: The structure tree, tests description, event-type table, and preferences list match the current source.

**Verified drift (backend/README.md is 346 lines)**:
- Structure tree (lines 248-309): the `core/` listing (259-280) omits `beads_poller.py`, `product_mapper.py`, `token_tracker.py` (actual `core/` has 19 modules); the models list (285-290) omits `overview.py`.
- Tests list (295-305): lists conftest + 9 test files; actual `backend/tests/` has **21** test files (missing: test_beads_poller, test_floor_config, test_models_phase4, test_pr44_critical_regressions, test_product_mapper, test_room_orchestrator, test_security_hardening, test_simulation_pipeline, test_state_machine_teams, test_subagent_linking, test_team_detection, test_websocket_room).
- Event-type table (230-244): lists 13 of 23 types; missing `agent_update`, `cleanup`, `reporting`, `walking_to_desk`, `waiting`, `leaving`, `error`, `task_created`, `task_completed`, `teammate_idle` (enum at `backend/app/models/events.py:20-42`).
- Preferences list (214-217): lists `clock_type`, `clock_format`, `auto_follow_new_sessions`; missing `language` (and `building_config` is stored but documented nowhere — verify against `frontend/src/stores/preferencesStore.ts:117-124` and the backend preferences handling before adding it).

**Steps**:
1. Update the `core/` entries in the structure tree to include `beads_poller.py`, `product_mapper.py`, `token_tracker.py` with one-line descriptions taken from each module's docstring (read them — do not invent). Add `overview.py` to the models list the same way. Then `ls backend/app/core/ backend/app/models/` and reconcile any OTHER file the tree misses or lists-but-deleted.
2. Replace the exhaustive tests list (295-305) with a short paragraph, per the audit's remedy — exhaustive lists are structurally guaranteed to drift:
   ```markdown
   Tests live in `tests/` (21 files at time of writing) covering the state machine,
   pollers, security hardening, simulation pipeline, team/room orchestration, and
   dedicated regression suites. Run them with `make test`.
   ```
3. Add the 10 missing rows to the event-type table (230-244), matching the existing columns; source each description from how the type is handled (grep the type name in `backend/app/core/event_processor.py` / `core/handlers/`) — do not guess semantics.
4. Add `language` to the preferences list (214-217); add `building_config` ONLY if verification confirms it round-trips through the preferences API.
5. Do NOT touch the WebSocket table here if DOC-004 already added `/ws/overview`; otherwise add that row too (one of the two issues must own it in your combined pass — not both).

**Verification**:
- Event-type parity: every one of the 23 enum values in `backend/app/models/events.py:20-42` appears exactly once in the table (compare lists manually or with a small shell loop).
- `ls backend/app/core/*.py | wc -l` matches the number of `core/` entries in the tree (± `__init__.py` if the tree omits it consistently).
- `grep -n "language" backend/README.md` — present in the preferences list.

**Do NOT**:
- Do not restore an exhaustive test-file list — the summary replaces it deliberately.
- Do not invent event-type descriptions — derive each from its handler.
- Do not edit sections owned by DOC-002 (static serving), DOC-003 (auth), DOC-004 (WS table), or DOC-006 (env table) beyond what your combined-pass plan assigns.

---

### [DOC-011] Frontend README inventory drift and missing test documentation
**Priority**: Medium | **Effort**: S | **Phase**: Phase 3d
**Preconditions**: Coordinate with DOC-004 (Key Components addition) and DOC-006 (env-var section) — one combined pass on `frontend/README.md`
**Files**: `frontend/README.md`

**Goal**: The structure tree reflects the real `src/` layout at directory granularity (immune to file-level drift), and the Testing section documents the vitest suite.

**Verified drift (frontend/README.md is 361 lines)**: the structure tree (lines 126-225) omits `utils/` (api.ts, bubbleText.ts, cron.ts, event-type-styles.ts), 7 of 10 `components/` subdirectories (`attention`, `command`, `debug`, `navigation`, `settings`, `tour`, `views` — only game/, layout/, overlay/ are listed), `stores/overviewStore.ts`, four hooks (`useFloorConfig`, `useFloorSessions`, `useOverviewWebSocket`, `useRoomSessions`), and three systems modules (`commandCenterGrid.ts`, `commandCenterMotion.ts`, `exitAnimation.ts`). The Testing section (332-343) mentions only typecheck/lint/checkall — no `make test`, no vitest.

**Steps**:
1. Replace the deep file-level tree (126-225) with a pruned directory-level tree, per the audit's remedy. Target shape (verify against `ls frontend/src/` and `ls frontend/src/components/` before writing — do not trust this list blindly):
   ```
   src/
   ├── app/            # Next.js app router pages
   ├── components/
   │   ├── attention/  # Toasts / command bar
   │   ├── command/    # Command Center (cross-session overview)
   │   ├── debug/
   │   ├── game/       # PixiJS office canvas
   │   ├── layout/
   │   ├── navigation/
   │   ├── overlay/
   │   ├── settings/
   │   ├── tour/
   │   └── views/
   ├── constants/
   ├── hooks/          # WebSocket, floors, overview, ...
   ├── i18n/
   ├── machines/       # XState agent machines + queue manager
   ├── stores/         # Zustand stores (game, overview, preferences, ...)
   ├── systems/        # animation, pathfinding, queue positions, ...
   ├── types/          # incl. generated.ts (backend contract)
   └── utils/
   ```
   Write each directory's one-line comment from what its files actually do (spot-read one or two files per directory).
2. Rewrite the Testing section (332-343) to include the unit suite:
   ```markdown
   ## Testing

   ```bash
   make test        # vitest run (unit tests in tests/ and src/**/*.test.ts)
   make typecheck   # tsc --noEmit
   make lint        # eslint --max-warnings=0
   make checkall    # fmt + lint + typecheck + build + test
   ```

   Unit tests use [Vitest](https://vitest.dev/) with the `@ → src` alias configured
   in `vitest.config.ts`. Tests live in `tests/` plus colocated `*.test.ts` files.
   ```
   (The checkall description matches `frontend/Makefile:36-38` — fmt, lint, typecheck, build, then test.)
3. Leave the `PRD` link handling (line 360) to DOC-013; leave the env-var section to DOC-006; leave the Key Components Command Center entry to DOC-004 — coordinate in one pass.

**Verification**:
- `ls frontend/src/components/` — every subdirectory appears in the tree, no extras/missing.
- `grep -n "vitest\|make test" frontend/README.md` — both present.
- File-level mentions inside the tree section drop to near zero (directory granularity achieved).

**Do NOT**:
- Do not enumerate individual files in the new tree (that is the drift mechanism being removed).
- Do not document a coverage tool — none is configured.

---

### [DOC-012] OpenCode plugin's incompatibility with `CLAUDE_OFFICE_API_KEY` undocumented
**Priority**: Medium | **Effort**: S | **Phase**: Phase 3d
**Preconditions**: None (SEC-005 later fixes the incompatibility itself — this note is amended/removed then)
**Files**: `opencode-plugin/README.md`

**Goal**: Users who set an explicit backend API key learn from the plugin README why their OpenCode events silently disappear, and a tracking issue exists for key support.

**Verified facts**: `opencode-plugin/src/index.ts` `sendEvent` (line 148, fetch at 155-160) sends only `Content-Type` — no `X-API-Key`; errors are swallowed (debug-logged only, lines 167-173). With an explicit `CLAUDE_OFFICE_API_KEY`, the backend's `ApiKeyMiddleware` gates ALL endpoints including `POST /api/v1/events`, so every plugin event 401s silently. README headings: `## Configuration` (38-46), `## Event Mapping` (48), Table of Contents at lines 5-15.

**Steps**:
1. Insert a new section after the Configuration section (i.e. between line 46 and line 48's `## Event Mapping`):
   ```markdown
   ## Known Limitations

   - **No API-key support.** The plugin does not send the `X-API-Key` header. If the
     backend is started with an explicit `CLAUDE_OFFICE_API_KEY`, every event the
     plugin sends is rejected with HTTP 401 and silently dropped (the plugin
     deliberately never surfaces transport errors). Workaround: run the backend
     without an explicit key when using OpenCode — the default auto-generated-key
     mode leaves `POST /api/v1/events` open. Tracked in issue #NN.
   ```
2. Add `- [Known Limitations](#known-limitations)` to the Table of Contents (lines 5-15) in position.
3. Create the tracking issue and substitute its number for `#NN`:
   ```bash
   cd /Users/probello/Repos/claude-office && gh issue create \
     --title "opencode-plugin: support CLAUDE_OFFICE_API_KEY (X-API-Key header)" \
     --body "The plugin's sendEvent (src/index.ts:148) sends no X-API-Key. With an explicit backend key, all plugin events 401 silently. Add key support mirroring the Python hooks (hooks/src/claude_office_hooks/main.py:80-84). Relates to audit findings SEC-005 and DOC-012."
   ```
   (Creating a GitHub issue is an outward-facing action — confirm with the project owner first unless the remediation run has standing approval for issue creation.)

**Verification**:
- `grep -n "Known Limitations" opencode-plugin/README.md` — heading + ToC entry.
- `gh issue list --search "X-API-Key" --state open` shows the new issue; the README references its real number.

**Do NOT**:
- Do not implement key support here — that is SEC-005, which lands after QA-002's class extraction.
- Do not soften the wording — "silently dropped" is the verified behavior and the reason this note exists.

---

### [DOC-013] PRD.md is stale but linked as current requirements
**Priority**: Medium | **Effort**: S | **Phase**: Phase 3d
**Preconditions**: None
**Files**: `PRD.md`, `frontend/README.md` (line 360), `hooks/README.md` (line 276), `docs/reference/ai-summary.md` (via DOC-009)

**Goal**: PRD.md is explicitly labeled a historical snapshot and every inbound link says so.

**Verified facts**: `PRD.md` (1248 lines) header says `**Version:** 2.6`, `**Date:** January 22, 2026`, `**Status:** Production Ready (all features implemented, documentation complete)` (lines 4-6) — roughly 15 releases stale. Inbound doc links: `frontend/README.md:360` (`- [PRD](../PRD.md) - Full product requirements`), `hooks/README.md:276` (`- [PRD Section 7](../PRD.md#7-claude-code-hook-integration) - Hook integration details`), `docs/reference/ai-summary.md:323` (broken path — DOC-009 fixes it). The root README does not link to PRD.md.

**Steps**:
1. In `PRD.md`, insert after line 2 (the second `#` title line):
   ```markdown

   > **Historical snapshot.** This PRD describes the product as of v2.6 / January 2026
   > and is retained for reference only — it is not updated per release. For current
   > behavior see [README.md](README.md), [CHANGELOG.md](CHANGELOG.md), and
   > [docs/architecture/ARCHITECTURE.md](docs/architecture/ARCHITECTURE.md).
   ```
2. Change line 6 to:
   ```markdown
   **Status:** Historical snapshot (superseded by CHANGELOG.md and docs/)
   ```
3. Relabel the inbound links:
   - `frontend/README.md:360` → `- [PRD](../PRD.md) - Original product requirements (historical snapshot)`
   - `hooks/README.md:276` → `- [PRD Section 7](../PRD.md#7-claude-code-hook-integration) - Hook integration details (historical snapshot)`
   - The ai-summary.md link label is handled by DOC-009 step 2.
4. Moving the file into an archives directory is the audit's alternative remedy — do NOT do both. The banner approach is chosen here because three inbound links (plus external bookmarks) keep working; only relabel.

**Verification**:
- `head -12 PRD.md` shows the banner and new Status line.
- `grep -rn "](../PRD.md)\|](../../PRD.md)" --include="*.md" .` — every hit's link text contains "historical" (except AUDIT.md/remediation artifacts).

**Do NOT**:
- Do not edit PRD.md's body content — the snapshot stays as-is; that is the point of the label.
- Do not move/rename the file (breaks three inbound links and external references).

---

### [DOC-014] Working artifacts clutter the repo root
**Priority**: Low | **Effort**: S | **Phase**: Phase 3d
**Preconditions**: Coordinate with DOC-015 (docs index gains entries for anything moved under `docs/`)
**Files**: `GEMINI_UPDATE.md`, `hacker_news_release.md`, `reddit_release.md`, `TODO.md`, `todos.md`, `docs/README.md`

**Goal**: The repo root contains no stale working artifacts; research content lives under `docs/research/`, release drafts under `docs/archives/`.

**Verified inventory (all five exist at root and are git-tracked)**:
- `GEMINI_UPDATE.md` — 1003 lines; a real research document ("Gemini CLI Hooks Integration", status "Research complete, pending implementation decision"). NOTE: `docs/research/google-gemini-cli-hooks.md` ALREADY EXISTS — these likely overlap.
- `hacker_news_release.md` — 63 lines, draft Show HN post.
- `reddit_release.md` — 136 lines, draft Reddit announcement for v0.15.0.
- `TODO.md` — 6 lines, "PR #20 Remaining Items": items 1 (Attention/Command System) and 2 (Click-to-Focus) unchecked; 4 and 5 checked; there is no item 3.
- `todos.md` — 0 lines, empty tracked file.
- `docs/archives/` does not exist yet; `docs/` has `architecture/ guides/ reference/ research/ superpowers/`.

**Steps**:
1. `GEMINI_UPDATE.md`: first compare against `docs/research/google-gemini-cli-hooks.md`. If the existing research doc supersedes or duplicates it, merge any unique content into the docs/research file and `git rm GEMINI_UPDATE.md`; if GEMINI_UPDATE.md is the more complete document, `git mv GEMINI_UPDATE.md docs/research/gemini-cli-hooks-integration.md` and reconcile/delete the older file. Do not keep two overlapping Gemini research docs.
2. `mkdir -p docs/archives && git mv hacker_news_release.md reddit_release.md docs/archives/`.
3. `TODO.md`: verify each unchecked item against the codebase before deleting — item 1 (Attention/Command System) appears shipped (`frontend/src/components/attention/` and the v0.20 Command Center exist) and item 2 (Click-to-Focus) appears shipped (`focus_session` endpoint in `backend/app/api/routes/sessions.py`). If both verify as implemented, `git rm TODO.md`; if either is genuinely unshipped, file it as a GitHub issue first, then remove the file.
4. `git rm todos.md` (empty).
5. `grep -rn "GEMINI_UPDATE\|hacker_news_release\|reddit_release\|todos.md" --include="*.md" --include="*.json" --include="Makefile" .` — fix any inbound references to the moved/removed files (expect none, but verify).
6. Add the moved research doc (if step 1 moved it) and, optionally, an Archives note to `docs/README.md` — coordinate with DOC-015's Research table.

**Verification**:
- `ls /Users/probello/Repos/claude-office/*.md` — the five artifacts are gone from root (remaining root .md files: README, CHANGELOG, CONTRIBUTING, CLAUDE, PRD, AUDIT and remediation outputs).
- `git status` shows renames (R) not delete+add where `git mv` was used.
- Step 5's grep is clean.

**Do NOT**:
- Do not delete `GEMINI_UPDATE.md` without the comparison in step 1 — it is 1003 lines of real research; move/merge, don't destroy.
- Do not delete `TODO.md` without verifying its two unchecked items — if either is unimplemented, that's a real work item to preserve as an issue.
- Do not gitignore `desktop/`/`tui/` here — that's ARC-030.

---

### [DOC-015] docs index omits the research docs
**Priority**: Low | **Effort**: S | **Phase**: Phase 3d
**Preconditions**: DOC-014 (may add a research file and `docs/archives/`)
**Files**: `docs/README.md` (27 lines)

**Goal**: The docs index lists every document under `docs/`, including the research directory.

**Verified facts**: `docs/README.md` has tables for Guides (quickstart, deployment), Architecture (ARCHITECTURE.md), Reference (ai-summary, whiteboard-modes), Contributing (DOCUMENTATION_STYLE_GUIDE.md). Not indexed: `docs/research/` — `claude-code-jsonl-format.md`, `google-gemini-cli-hooks.md`, `openai-codex-hooks.md` — and the `docs/superpowers/` directory.

**Steps**:
1. Run `ls docs/research/` to get the CURRENT file list (DOC-014 may have added/renamed one).
2. Append a Research table after the existing tables, matching their format. For each file, read its first heading/paragraph and write a one-line description — do not invent. Template:
   ```markdown
   ## Research

   | Document | Description |
   |----------|-------------|
   | [Claude Code JSONL Format](research/claude-code-jsonl-format.md) | <from the doc's own intro> |
   | [Gemini CLI Hooks](research/google-gemini-cli-hooks.md) | <from the doc's own intro> |
   | [OpenAI Codex Hooks](research/openai-codex-hooks.md) | <from the doc's own intro> |
   ```
3. If DOC-014 created `docs/archives/`, add a one-line note (not a full table): `Archived release announcements live in [archives/](archives/).`
4. `docs/superpowers/` — inspect its contents first; if it is internal tooling config (skill plans) rather than documentation, either add a one-line note or deliberately leave it unindexed, stating the choice in the PR description.

**Verification**:
- Every file in `ls docs/research/` appears in the index: `for f in docs/research/*.md; do grep -q "$(basename $f)" docs/README.md && echo "OK $f" || echo "MISSING $f"; done`.
- All new links resolve (same `test -f` loop pattern as DOC-008).

**Do NOT**:
- Do not restructure the existing tables — append only.
- Do not index files that don't exist yet.

---

### [DOC-016] Minor consistency nits
**Priority**: Low | **Effort**: S | **Phase**: Phase 3d (each nit rides the combined pass of the file it touches)
**Preconditions**: ARC-001 (for the CI badge — the workflow must exist first); coordinate with DOC-006 (ARCHITECTURE.md table pass)
**Files**: `docs/architecture/ARCHITECTURE.md`, `README.md`, `backend/README.md`

**Goal**: The four verified nits are fixed: complete preferences endpoint list, accurate `DATABASE_URL` default, single release-history line, and CI/release badges.

**Steps** (each with its verified anchors inline):
1. **Preferences endpoints** — ARCHITECTURE.md lines 612-614 list only `GET /api/v1/preferences` and `PUT /api/v1/preferences/{key}`. Actual routes (`backend/app/api/routes/preferences.py`): `@router.get("")` (28), `@router.get("/{key}")` (42), `@router.put("/{key}")` (56), `@router.delete("/{key}")` (91). Add the two missing entries:
   ```markdown
   - `GET /api/v1/preferences/{key}` — read a single preference
   - `DELETE /api/v1/preferences/{key}` — remove a stored preference
   ```
   (Read the two route handlers' docstrings first and match their actual semantics in the description.)
2. **DATABASE_URL default** — ARCHITECTURE.md line 773 documents `sqlite+aiosqlite:///visualizer.db` (relative). Actual (`backend/app/config.py:7-8,24`): an ABSOLUTE path to `visualizer.db` inside the `backend/` directory. Change the table cell to `sqlite+aiosqlite:///<backend dir>/visualizer.db` with the note "(absolute path; docker-compose overrides to `/app/data/visualizer.db`)". Apply in the same pass as DOC-006's new rows. Check `backend/README.md`'s env table (lines 145-154) for the same discrepancy and fix it identically.
3. **Repeated release-history line** — `README.md` has the identical sentence `For the full release history, see [CHANGELOG.md](CHANGELOG.md).` at lines 51, 58, 66, and 81 (after the v0.22.0, v0.21.0, v0.20.0, and v0.14.0 entries; the v0.15.0 entry has none). Delete the occurrences at lines 51, 58, and 66; keep ONLY the final one (line 81, closing the What's New section). Tidy any doubled blank lines left behind.
4. **Badges** — README.md lines 3-7 currently have License/GitHub/platform/YouTube badges; no CI or version badge. Add two (the CI badge only AFTER ARC-001's workflow exists — read `.github/workflows/` for the actual filename, do not assume `ci.yml`):
   ```markdown
   ![CI](https://github.com/paulrobello/claude-office/actions/workflows/<workflow-file>/badge.svg)
   ![GitHub Release](https://img.shields.io/github/v/release/paulrobello/claude-office)
   ```
   Use the auto-updating release badge, NOT a hardcoded version badge — a hardcoded one would become a 9th manual version-sync location (see QA-010).

**Verification**:
- `grep -c "For the full release history" README.md` returns `1`.
- `grep -n "DELETE /api/v1/preferences" docs/architecture/ARCHITECTURE.md` — present.
- `grep -rn "sqlite+aiosqlite:///visualizer.db" docs/ backend/README.md` returns nothing (relative-default wording gone).
- Badge URLs return 200: `curl -s -o /dev/null -w "%{http_code}" <badge url>` (CI badge only meaningful after ARC-001's first workflow run).

**Do NOT**:
- Do not add a hardcoded `version-x.y.z` shields badge (manual sync burden).
- Do not add the CI badge before the workflow exists — a permanently-gray badge is worse than none.
- Do not rewrite the What's New entries themselves — only deduplicate the trailing line.
