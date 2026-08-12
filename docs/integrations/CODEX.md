# Codex Integration

AI Office Viewer can visualize OpenAI Codex activity through user-level global lifecycle hooks. Once
installed, the same integration works in every Codex project without copying an AI Office Viewer hook
file into each repository.

```text
Codex lifecycle hook (`~/.codex/hooks.json`)
        ↓ JSON on stdin
`~/.codex/claude-office-hook.ps1`
        ↓ `py -3.13`, independent of project environments
`codex-adapter/hook.py`
        ↓ allowlisted metadata only
http://127.0.0.1:<backend_port>/api/v1/events
        ↓
AI Office Viewer
```

The adapter is local and fail-open: if AI Office Viewer is stopped or unreachable, Codex continues
normally. It retains only allowlisted, text-free lifecycle metadata so a later Viewer start can
reconstruct the current state without replaying prompts or tool output.

## Global setup (recommended)

Run this once from the AI Office Viewer repository. It backs up the existing user hooks, preserves
unrelated handlers, and adds only the eight AI Office Viewer lifecycle events. Running it again is
safe and updates the stored AI Office Viewer root if the repository was moved.

```powershell
cd <AI Office Viewer root>
.\codex-adapter\install-global-hooks.ps1
```

The installer writes `~/.codex/hooks.json`, a stable launcher, and a root config under
`~/.codex`; existing settings are backed up under `~/.codex/backups/` before each update.
After installation, open a new Codex session and use `/hooks` to review/trust the launcher if
Codex requests it.

Remove only AI Office Viewer's global handlers with:

```powershell
.\codex-adapter\uninstall-global-hooks.ps1
```

Codex loads matching user and project hooks together. Therefore this repository's
`.codex/hooks.json` intentionally contains no AI Office Viewer handler; unrelated project hooks can
remain in place.

## Why Lifecycle Hooks

Lifecycle hooks remain the source for real-time session, tool, and subagent events. At Backend
startup, a separate Codex-only restorer reads a bounded portion of saved rollout JSONL plus the
adapter's sanitized lifecycle metadata to catch up to the current state. It does not depend on
Codex's SQLite schema or the VS Code extension protocol. The global hook does not replace an
existing `notify` command.

After startup restoration, a separate bounded JSONL tail monitor tracks new
rollout records by byte offset. Lifecycle hooks remain the preferred live path;
when a session has JSONL activity but no recent hook delivery it uses
`TAIL_FALLBACK`, and when both sources are active it uses `HYBRID`. New rollout
files are discovered from a bounded `session_index.jsonl` tail and the current
date directories without a recursive filesystem scan. Hook and JSONL records
are normalized before deduplication, so starting Viewer after an existing VS
Code Codex chat does not require restarting VS Code or creating a new chat.

When upgrading Codex, compare the configuration with the
[official Codex Hooks documentation](https://developers.openai.com/codex/hooks).

## Windows setup and one-click start

Requirements:

- Python 3.13 available through the Windows Python launcher;
- `uv` for the backend;
- Bun for the frontend.

```powershell
py -3.13 --version
```

No adapter package installation is required. The global launcher always invokes the adapter from
the configured AI Office Viewer root with `py -3.13`; it does not use the active project's venv or
Node environment.

## Starting and Stopping on Windows

Install dependencies once from the repository root:

```powershell
cd backend
uv sync
cd ..
```

```powershell
cd frontend
bun install
cd ..
```

For normal use, start everything with:

```powershell
.\start_ai_office_viewer.ps1
```

For an always-available Windows control panel, use `.\start_ai_office_viewer_manager.ps1`. Both
the manager and the Web settings screen use the shared `config/app-settings.json` file.

The script reads the configured hosts/ports, passes the API/WS URL to the frontend, checks
`/health` and the configured frontend URL for up to 30 seconds, prevents duplicate starts, and
opens the selected browser mode. It never kills an unrelated process that owns a configured port.

Manual fallback:

```powershell
cd backend
uv run uvicorn app.main:app --host 127.0.0.1 --port 8000
```

```powershell
cd frontend
bun run dev
```

Stopping AI Office Viewer does not require disabling the global hooks and does not stop Codex. When
the Backend starts again, it reconstructs only the current state from recent saved metadata; it does
not replay the missed conversation or full tool history into Event Log.

## Adapter Components

| File | Responsibility |
| --- | --- |
| `codex-adapter/hook.py` | Shared, fail-open Python adapter launcher |
| `codex-adapter/install-global-hooks.ps1` | Idempotent global hook installer and backup creator |
| `codex-adapter/uninstall-global-hooks.ps1` | Removes only AI Office Viewer global handlers |
| `start_ai_office_viewer.ps1` | Backend/frontend readiness check and browser launcher |
| `event_mapper.py` | Hook mapping, metadata allowlist, and tool normalization |
| `event_journal.py` | Text-free lifecycle metadata used for startup restoration |
| `sender.py` | One HTTP POST to the configured local destination |
| `config.py` | Shared settings reader, path, and 0.5-second timeout |
| `main.py` | Reads one stdin JSON object and suppresses adapter failures |
| `diagnostics.py` | Event-free `--check` for settings, loopback endpoint, Python, and Backend health |

The sender uses a direct `HTTPConnection` to the configured local backend, bypasses environment proxies,
does not follow redirects, performs no retry, and keeps no queue.

## Events and Metadata

| Codex hook | AI Office Viewer `event_type` | Event-specific data |
| --- | --- | --- |
| `SessionStart` | `session_start` | safe `project_name` derived from the `cwd` basename |
| `SessionEnd` | `session_end` | none |
| `UserPromptSubmit` | `user_prompt_submit` | fixed message `Codex user prompt` |
| `PreToolUse` | `pre_tool_use` | `tool_name`, `tool_use_id`, optional `agent_id` and `agent_type` |
| `PostToolUse` | `post_tool_use` | `tool_name`, `tool_use_id`, optional `agent_id` and `agent_type` |
| `SubagentStart` | `subagent_start` | `agent_id`, optional `agent_type` |
| `SubagentStop` | `subagent_stop` | `agent_id`, optional `agent_type` |
| `Stop` | `stop` | none |

Every mapped event has `data.source = "codex"`. A short identifier-like `model` value is included
when Codex supplies one. `agent_type` uses the same conservative validation. Missing optional
fields are omitted rather than invented. `project_name` is the safe basename of the hook `cwd`
(for example `you_ne` or `epubreader-`); the full path is never used as the UI project label.

The backend presents the main character as **Codex Main**. Subagents receive stable names in
arrival order within the session: **Codex Agent 1**, **Codex Agent 2**, and so on. The same
`agent_id` follows a child from `SubagentStart`, through tool events, to `SubagentStop`; its display
number remains reserved after the character leaves.

Native rollout mapping is conservative. `session_meta`, `task_started`,
`task_complete`, function-call records and explicit subagent activity are used
only when the current schema provides the required identifiers. Unknown records
are ignored; transcript text is never interpreted to invent an event.

Tool names are normalized as follows:

| Codex tool | AI Office Viewer tool |
| --- | --- |
| `collaborationspawn_agent` | `Agent` |
| `collaborationwait_agent` | `AgentWait` |
| other names, such as `Bash` | unchanged |

`Agent` shows delegation. `AgentWait` shows the main agent reviewing/waiting for subagents; a child
receiving the same event is shown waiting until its matching post-tool event.

## Startup Session Restoration

`restore_codex_sessions` is enabled by default. When the Backend starts, it scans recent Codex
session metadata in the background and restores active sessions before continuing with ordinary
lifecycle hook updates. Manager and Web settings share these values:

```json
{
  "restore_codex_sessions": true,
  "restore_window_minutes": 30
}
```

The primary metadata sources are the bounded tail of `~/.codex/session_index.jsonl` (`id` and
`updated_at` only) and `~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl`. The index preserves recent
root-session discovery even when an open rollout's Windows mtime remains at its creation time. The
restorer reads a 256 KiB head for `session_meta` / model context, a 2 MiB tail for recent state, and
a 64 KiB timestamp probe while ranking candidates. UTC and local date boundaries are both covered;
UUIDv7 creation dates allow an indexed root rollout to be located without recursively parsing every
file. The internal SQLite schema is deliberately not used.

Sanitized adapter metadata in `~/.codex/claude-office-events/YYYY-MM-DD.jsonl` supplies exact
lifecycle pairs when available, including `SessionEnd`, `SubagentStop`, and matching `PreToolUse` /
`PostToolUse` IDs. `SessionEnd` also writes an atomic metadata-only marker at
`~/.codex/claude-office-events/terminal/<session_id>.json`, so an ended session cannot reappear if
its journal line has moved outside the bounded tail. Daily journals are capped at 16 MiB and pruned
on a later hook append once they are more than three calendar days old; marker retention follows the
same rule.

Active detection combines the configured recent-activity window with lifecycle completion markers.
`Stop` is the end of a turn, never the end of a session. A session is excluded only when
`SessionEnd` is known; without an end marker, recent native activity is a conservative fallback.
Restore is capped at 10 active sessions. At most 60 rollout candidates are parsed from at most
5,000 explored filenames, and every content read has a byte limit. The scan runs in a worker thread
and never blocks Backend health or the Viewer UI.

The result is applied as a state snapshot:

- a root thread becomes one Viewer session with `Codex Main`;
- child `session_meta.parent_thread_id` relationships are folded into that root session;
- children without stop/completion become `Codex Agent 1`, `Codex Agent 2`, and so on;
- an unmatched tool start becomes `working`, while `AgentWait` becomes `reviewing` for main or
  `waiting` for a child;
- a matching tool completion prevents a stale `working` state;
- model values are displayed only when present in saved metadata;
- only the basename of `cwd` becomes `project_name`.

Restore records one compact session marker rather than posting every historical hook event, so Event
Log is not flooded. Journal records are compared chronologically, including records whose short-lived
hook processes finish out of order. Snapshot merge is keyed by `session_id`, `agent_id`, and
`tool_use_id`; per-session locks, the scan boundary sequence, and live stop/post tombstones make
newer lifecycle completions win over older native metadata. A later `SubagentStop` or `SessionEnd`
therefore exits normally without duplicate characters.

Use **Codexセッションを再読込** in Manager or **Restore Codex sessions** in the Web MORE menu
to run the scan again.

Limitations:

- sessions older than the configured window are not restored;
- a very brief state at a partially written final JSONL line may be missed;
- only the newest bounded set of rollout candidates is inspected, so extremely high session churn
  can omit an older child whose root/index metadata is no longer recent;
- unknown records in a changed Codex format are ignored rather than guessed;
- restore and tail monitoring are separate services: restore rebuilds the past
  state, while tail monitoring processes only new append data;
- polling is bounded to at most 10 monitored sessions by default and can be
  tuned with `CODEX_TAIL_MAX_SESSIONS` and `CODEX_TAIL_POLL_INTERVAL`;
- startup restoration is currently Codex-only; Claude Code and OpenCode keep their existing
  real-time integrations.

## Security and Privacy

The mapper constructs a new payload from an explicit allowlist and never forwards unknown fields.
Forwarded metadata is limited to:

```text
session_id
receive timestamp
source = codex
model when safely formatted
project_name from the safe `cwd` basename
tool_name and tool_use_id on tool events
turn_id when supplied by Codex
agent_id and safely formatted agent_type when present
```

The adapter does **not** forward or log:

- prompt or input-message text;
- tool input or tool response;
- assistant messages;
- command text, stdout, or stderr;
- transcript paths or contents;
- file contents;
- authentication data, tokens, secrets, or API keys.

The restore parser may read Codex records containing sensitive bodies, but extracts only the listed
metadata into a new object. Unknown fields, full paths, prompts, commands, tool input/output, and
responses are never copied to the ledger, API, restore marker, or logs. Project, model, and agent
type values are restricted metadata. No debug log is enabled by default.

## Failure Behavior

Launcher, JSON parsing, mapping, and sending exceptions are contained inside the adapter. It writes
no routine stdout or stderr and returns success to Codex even when:

- stdin is empty or malformed;
- a hook is unknown or required IDs are missing;
- AI Office Viewer refuses the connection;
- the HTTP request exceeds 0.5 seconds;
- the API returns 4xx or 5xx.

Delivery is attempted once. There is no retry loop or in-memory request queue. The small local
lifecycle metadata ledger is for state restoration only and is never replayed as queued HTTP calls.

The Backend separately records the receive time and count of genuine Codex lifecycle events since
that Backend process started. Restored snapshot markers (`data.restored = true`), non-Codex sources,
and other event families are excluded. Manager and Viewer read only these payload-free values from
`GET /api/v1/system/integration-status`; prompts, session IDs, paths, and credentials are not exposed.

## Verification

1. Start the backend and frontend.
2. Open this repository in a new trusted Codex session.
3. Submit a harmless request that performs one tool action.
4. Start one subagent, let it use a tool, and let it finish.
5. Confirm `Codex Main`, tool activity, `Codex Agent 1`, waiting state, and departure appear.
6. Stop the backend and perform another harmless Codex action.
7. Start the Backend again and confirm the current session returns without duplicate agents.
8. Confirm the next live tool event, subagent stop, and session end update the restored state.

Use only non-sensitive prompts and files during verification.

## Uninstall

Run `codex-adapter\uninstall-global-hooks.ps1`, then start a new Codex session and confirm the
AI Office Viewer entries are gone in `/hooks`. The project-local file is intentionally retained as an
empty, documented hook layer; it does not affect other integrations.

## Troubleshooting

### Hooks do not run

Run `/hooks`, review/trust the user-level launcher, and start a new session. Confirm that hooks are
enabled and that `~/.codex/hooks.json` contains all eight events.

### `py -3.13` or `hook.py` is not found

```powershell
py -3.13 --version
'' | py -3.13 "<AI Office Viewer root>\codex-adapter\hook.py"
```

The empty-input command should exit successfully without output.

### Events appear twice

Check `/hooks`, `.codex/hooks.json`, and `~/.codex/hooks.json`. Remove any old project-local
AI Office Viewer handlers; matching hooks from multiple scopes all run concurrently.

### Main activity appears but subagents do not

Confirm `SubagentStart` and `SubagentStop` in `/hooks`, then explicitly start one child agent. Do
not infer child lifecycle solely from the `Agent` tool event.

### `AgentWait` is shown as a generic tool

Confirm Codex emitted `collaborationwait_agent` and the adapter normalized it to `AgentWait`.
Restart Codex and AI Office Viewer after updating an older adapter/backend pair.

### AI Office Viewer is unavailable

Codex should continue; the next Viewer start can restore recent state metadata. If Codex pauses or fails, temporarily
run `uninstall-global-hooks.ps1` and report the adapter defect. No retry loop, popup, or queue is
used.

### Some metadata is missing

Payloads vary by event and Codex version. Optional `model`, `agent_type`, `agent_id`, and
`tool_use_id` values are omitted when unavailable or unsafe. The adapter never guesses them.

## Related Documentation

- [AI Office Viewer architecture](../architecture/ARCHITECTURE.md)
- [AI Office Viewer quick start](../guides/quickstart.md)
- [Codex hooks research](../research/openai-codex-hooks.md)
