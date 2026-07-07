# Enhancement Ideas

> **Project**: Claude Office Visualizer
> **Date**: 2026-07-06
> **Source**: Derived from the 2026-07-06 Fable audit (`AUDIT.md`) plus forward-looking opportunities.
> Each item has a full implementation plan under [`docs/fable/`](docs/fable/).

These are enhancements, not defect fixes — several build on audit findings (IDs referenced), but each delivers new capability or a step-change in performance/reliability beyond "make it correct." Items are ordered by expected impact within each group.

---

## Performance

### ENH-001 — Frame-Batched Animation Commits & Imperative Pixi Position Updates
**Plan**: [docs/fable/ENH-001-frame-batched-animation.md](docs/fable/ENH-001-frame-batched-animation.md)
**Related audit findings**: ARC-006, QA-006

Today the rAF tick issues one Zustand `set()` per moving agent per frame, each cloning the entire agents Map, and root components subscribe to the whole Map — so header, sidebars, and modals re-render at 60fps whenever anything moves. Restructure the animation loop to (a) write interpolated per-frame positions directly to Pixi display objects, bypassing React entirely, and (b) commit only discrete state changes (waypoint reached, phase change) to Zustand in a single batched `set()` per tick. Expected result: React render work becomes independent of agent count and frame rate; GC pressure from Map clones disappears.

### ENH-002 — Delta-Based WebSocket State Sync
**Plan**: [docs/fable/ENH-002-delta-websocket-sync.md](docs/fable/ENH-002-delta-websocket-sync.md)
**Related audit findings**: ARC-015

Every event currently triggers a full `GameState` serialization (up to 500 history entries + 500 conversation entries) broadcast to every connected client. Introduce versioned delta broadcasts: send the full snapshot on connect, then structured diffs (changed agents, appended history entries) with per-session coalescing so bursts collapse into one frame. Include a client-side resync path (version gap → request snapshot). Cuts broadcast payloads by orders of magnitude on busy sessions and makes broadcast cost proportional to what changed.

### ENH-003 — Incremental Transcript Tailing & Threaded File I/O
**Plan**: [docs/fable/ENH-003-incremental-transcript-tailing.md](docs/fable/ENH-003-incremental-transcript-tailing.md)
**Related audit findings**: ARC-003

Transcript/JSONL reads currently re-read files from the start (worst case 50 MB inside a synchronous call on the event loop). Track per-file byte offsets and parse only newly appended bytes, executing all file I/O via `asyncio.to_thread`. Combined with mtime/size short-circuits, polling cost becomes proportional to new content rather than transcript size, and the event loop never blocks on disk.

### ENH-004 — Session Lifecycle & Memory Management
**Plan**: [docs/fable/ENH-004-session-lifecycle-memory.md](docs/fable/ENH-004-session-lifecycle-memory.md)
**Related audit findings**: ARC-015

The in-memory `sessions: dict[str, StateMachine]` registry grows forever on long-lived servers, `EventRecord` rows are never reaped, and replay materializes an entire session's per-event state dumps into one in-memory JSON response. Add idle-based LRU eviction for in-memory state machines (restorable from the event log — the event-sourcing design already guarantees this), a configurable retention sweep for old event records, and streamed/paginated replay responses.

### ENH-005 — Pixi Rendering Optimizations (Culling, Dirty Flags, Atlases)
**Plan**: [docs/fable/ENH-005-pixi-rendering-optimizations.md](docs/fable/ENH-005-pixi-rendering-optimizations.md)
**Related audit findings**: ARC-006 (complementary)

Beyond ENH-001's state-layer fix, the render layer can do less work: viewport culling for off-screen floors/rooms/agents, dirty-flag rendering so static frames skip redraw entirely (Pixi's `autoStart`/on-demand render mode when no agent is moving), and consolidation of character/furniture sprites into texture atlases to reduce draw calls and GPU texture binds. Targets idle CPU near-zero when the office is quiescent — important for a dashboard-style app that users leave open all day.

### ENH-006 — Per-Session Rate Limiting & Producer-Side Event Batching
**Plan**: [docs/fable/ENH-006-per-session-rate-limiting.md](docs/fable/ENH-006-per-session-rate-limiting.md)
**Related audit findings**: ARC-016

The global 300-events/min limiter falsely throttles concurrent busy sessions and silently drops events (hooks don't retry). Re-key the limiter per session with a higher default, move configuration into `Settings`, and add producer-side improvements: hook-side coalescing of rapid-fire tool events into batched POSTs, plus a bounded retry-with-backoff on 429 so throttling degrades to latency instead of data loss.

---

## Reliability & Developer Experience

### ENH-007 — Unified Event-Contract Codegen for All Producers
**Plan**: [docs/fable/ENH-007-event-contract-codegen.md](docs/fable/ENH-007-event-contract-codegen.md)
**Related audit findings**: ARC-010, ARC-019, SEC-005 (plugin key support rides along)

The backend→frontend type contract is generated and CI-enforced, but the two event *producers* (Python hooks, OpenCode plugin) hand-duplicate the 23-type event contract — and the plugin has already drifted (3 missing types). Extend `gen_types.py` to also emit the plugin's TypeScript event types and a Python constants module for the hooks, add contract tests validating producer output against the backend Pydantic models, and auto-discover models via introspection instead of the hand-curated registry.

### ENH-008 — Full CI Quality Gate with Coverage Reporting
**Plan**: [docs/fable/ENH-008-ci-quality-gate.md](docs/fable/ENH-008-ci-quality-gate.md)
**Related audit findings**: ARC-001, DOC-001 (builds on their remediation)

Once the ARC-001 remediation lands a baseline CI, extend it into a proper quality gate: a path-filtered GitHub Actions matrix (backend, frontend, hooks, opencode-plugin) running format-check, lint, typecheck, and tests per component; coverage collection (pytest-cov, vitest coverage) with thresholds ratcheted upward over time; caching for uv/bun; and a required status check on PRs.

### ENH-009 — Version Bump Automation
**Plan**: [docs/fable/ENH-009-version-bump-automation.md](docs/fable/ENH-009-version-bump-automation.md)
**Related audit findings**: ARC-021/QA-010, DOC-007

Version strings live in 8 locations (7 documented + the drifted `backend/app/config.py`). Add `make bump-version VERSION=x.y.z` that rewrites all of them atomically, derive runtime-reported versions from package metadata (`importlib.metadata` in hooks and backend), and add a CI consistency check that fails if any location disagrees — eliminating the drift class permanently.

### ENH-010 — Frontend Test Infrastructure Build-Out
**Plan**: [docs/fable/ENH-010-frontend-test-infrastructure.md](docs/fable/ENH-010-frontend-test-infrastructure.md)
**Related audit findings**: QA-001/ARC-007, QA-002

Stand up the test harness the frontend's complexity demands: characterization tests for `gameStore` queue/agent actions (a prerequisite for the audit's Phase 2 refactors), XState machine tests driven through the injected `AgentMachineActions` interface, pure-function tests for the spawn-decision table and pathfinding stack, a Playwright smoke test that loads a simulated session end-to-end, and a bun-test scaffold for the OpenCode plugin's session-linking state machine.

### ENH-011 — Generic Async Poller Framework
**Plan**: [docs/fable/ENH-011-generic-poller-framework.md](docs/fable/ENH-011-generic-poller-framework.md)
**Related audit findings**: ARC-013

Three pollers (transcript, task-file, beads) reimplement the same skeleton with inconsistent drift (none await cancelled tasks in `stop_all`; one reads raw `os.environ`). Extract a generic `BasePoller[TState]` — registration, locking, lifecycle, error isolation, and configurable intervals in one place, with `_check(state)` as the sole abstract method — then migrate all three and make the fourth poller (inevitable) a ~50-line subclass.

---

## Features

### ENH-012 — Gemini CLI Integration (Third Event Producer)
**Plan**: [docs/fable/ENH-012-gemini-cli-integration.md](docs/fable/ENH-012-gemini-cli-integration.md)
**Related audit findings**: DOC-014 (existing research: `GEMINI_UPDATE.md`, `docs/research/google-gemini-cli-hooks.md`)

The repo already contains substantial research on Google Gemini CLI's hook system, pending an implementation decision. Turn it into a third event producer alongside Claude Code hooks and the OpenCode plugin: a `gemini-plugin/` (or hooks adapter) that maps Gemini CLI lifecycle events onto the office event contract — ideally consuming the generated contract from ENH-007 so it starts life drift-proof. Broadens the product from "Claude Code visualizer" to "coding-agent office."
