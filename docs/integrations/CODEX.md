# Codex Integration

Claude Office can visualize OpenAI Codex activity through user-level global lifecycle hooks. Once
installed, the same integration works in every Codex project without copying a Claude Office hook
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
Claude Office
```

The adapter is local and fail-open: if Claude Office is stopped or unreachable, Codex continues
normally and that visualization event is dropped.

## Global setup (recommended)

Run this once from the Claude Office repository. It backs up the existing user hooks, preserves
unrelated handlers, and adds only the eight Claude Office lifecycle events. Running it again is
safe and updates the stored Claude Office root if the repository was moved.

```powershell
cd <Claude Office root>
.\codex-adapter\install-global-hooks.ps1
```

The installer writes `~/.codex/hooks.json`, a stable launcher, and a root config under
`~/.codex`; existing settings are backed up under `~/.codex/backups/` before each update.
After installation, open a new Codex session and use `/hooks` to review/trust the launcher if
Codex requests it.

Remove only Claude Office's global handlers with:

```powershell
.\codex-adapter\uninstall-global-hooks.ps1
```

Codex loads matching user and project hooks together. Therefore this repository's
`.codex/hooks.json` intentionally contains no Claude Office handler; unrelated project hooks can
remain in place.

## Why Lifecycle Hooks

Lifecycle hooks provide session, tool, and subagent events as they happen without parsing Codex's
internal JSONL, SQLite files, or VS Code extension protocol. The global hook does not replace an
existing `notify` command.

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
the configured Claude Office root with `py -3.13`; it does not use the active project's venv or
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
.\start_claude_office.ps1
```

For an always-available Windows control panel, use `.\start_ai_office_manager.ps1`. Both
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

Stopping Claude Office does not require disabling the global hooks and does not stop Codex; events
produced while the backend is down are not replayed.

## Adapter Components

| File | Responsibility |
| --- | --- |
| `codex-adapter/hook.py` | Shared, fail-open Python adapter launcher |
| `codex-adapter/install-global-hooks.ps1` | Idempotent global hook installer and backup creator |
| `codex-adapter/uninstall-global-hooks.ps1` | Removes only Claude Office global handlers |
| `start_claude_office.ps1` | Backend/frontend readiness check and browser launcher |
| `event_mapper.py` | Hook mapping, metadata allowlist, and tool normalization |
| `sender.py` | One HTTP POST to the configured local destination |
| `config.py` | Shared settings reader, path, and 0.5-second timeout |
| `main.py` | Reads one stdin JSON object and suppresses adapter failures |

The sender uses a direct `HTTPConnection` to the configured local backend, bypasses environment proxies,
does not follow redirects, performs no retry, and keeps no queue.

## Events and Metadata

| Codex hook | Claude Office `event_type` | Event-specific data |
| --- | --- | --- |
| `SessionStart` | `session_start` | safe `project_name` plus backend-only `working_dir` from `cwd` |
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

Tool names are normalized as follows:

| Codex tool | Claude Office tool |
| --- | --- |
| `collaborationspawn_agent` | `Agent` |
| `collaborationwait_agent` | `AgentWait` |
| other names, such as `Bash` | unchanged |

`Agent` shows delegation. `AgentWait` shows the main agent reviewing/waiting for subagents; a child
receiving the same event is shown waiting until its matching post-tool event.

## Security and Privacy

The mapper constructs a new payload from an explicit allowlist and never forwards unknown fields.
Forwarded metadata is limited to:

```text
session_id
receive timestamp
source = codex
model when safely formatted
working_dir on SessionStart
project_name from the safe `cwd` basename
tool_name and tool_use_id on tool events
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

`working_dir` is forwarded only on `SessionStart` for backend project-root and git/task lookup; the
frontend label uses `project_name` and does not display that path. Project, model, and agent type
values are restricted identifier-like metadata. No debug log is enabled by default.

## Failure Behavior

Launcher, JSON parsing, mapping, and sending exceptions are contained inside the adapter. It writes
no routine stdout or stderr and returns success to Codex even when:

- stdin is empty or malformed;
- a hook is unknown or required IDs are missing;
- Claude Office refuses the connection;
- the HTTP request exceeds 0.5 seconds;
- the API returns 4xx or 5xx.

Delivery is attempted once. There is no retry loop, disk spool, or in-memory queue.

## Verification

1. Start the backend and frontend.
2. Open this repository in a new trusted Codex session.
3. Submit a harmless request that performs one tool action.
4. Start one subagent, let it use a tool, and let it finish.
5. Confirm `Codex Main`, tool activity, `Codex Agent 1`, waiting state, and departure appear.
6. Stop the backend and perform another harmless Codex action.
7. Confirm Codex still completes normally.

Use only non-sensitive prompts and files during verification.

## Uninstall

Run `codex-adapter\uninstall-global-hooks.ps1`, then start a new Codex session and confirm the
Claude Office entries are gone in `/hooks`. The project-local file is intentionally retained as an
empty, documented hook layer; it does not affect other integrations.

## Troubleshooting

### Hooks do not run

Run `/hooks`, review/trust the user-level launcher, and start a new session. Confirm that hooks are
enabled and that `~/.codex/hooks.json` contains all eight events.

### `py -3.13` or `hook.py` is not found

```powershell
py -3.13 --version
'' | py -3.13 "<Claude Office root>\codex-adapter\hook.py"
```

The empty-input command should exit successfully without output.

### Events appear twice

Check `/hooks`, `.codex/hooks.json`, and `~/.codex/hooks.json`. Remove any old project-local
Claude Office handlers; matching hooks from multiple scopes all run concurrently.

### Main activity appears but subagents do not

Confirm `SubagentStart` and `SubagentStop` in `/hooks`, then explicitly start one child agent. Do
not infer child lifecycle solely from the `Agent` tool event.

### `AgentWait` is shown as a generic tool

Confirm Codex emitted `collaborationwait_agent` and the adapter normalized it to `AgentWait`.
Restart Codex and Claude Office after updating an older adapter/backend pair.

### Claude Office is unavailable

Codex should continue and visualization events are dropped. If Codex pauses or fails, temporarily
run `uninstall-global-hooks.ps1` and report the adapter defect. No retry loop, popup, or queue is
used.

### Some metadata is missing

Payloads vary by event and Codex version. Optional `model`, `agent_type`, `agent_id`, and
`tool_use_id` values are omitted when unavailable or unsafe. The adapter never guesses them.

## Related Documentation

- [Claude Office architecture](../architecture/ARCHITECTURE.md)
- [Claude Office quick start](../guides/quickstart.md)
- [Codex hooks research](../research/openai-codex-hooks.md)
