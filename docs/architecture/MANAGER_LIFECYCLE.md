# Manager server lifecycle

## Ownership rule

`ServerLifecycleManager` is the only component allowed to start, stop,
restart, adopt, or unregister the Backend and Frontend. `ProcessRegistry` is
the source of truth for Manager-owned handles and persisted identity records.
Browser processes are kept in `DedicatedViewProcessManager` and are never
placed in the server registry.

## Call graph

```text
ManagerWindow / tray
  ├─ 起動                         ──> ServerLifecycleManager.start_all()
  ├─ 停止                         ──> ServerLifecycleManager.stop_all()
  ├─ 再起動                       ──> ServerLifecycleManager.restart_all()
  ├─ Backendを再起動              ──> ServerLifecycleManager.restart_backend()
  ├─ 終了                         ──> stop_all() when configured
  ├─ 1-second status timer         ──> snapshot() [worker, read-only UI snapshot]
  ├─ 通常ブラウザで開く            ──> BrowserLauncher.open_normal(frontend_url)
  ├─ 専用画面で開く                ──> DedicatedViewLauncher.open(frontend_url)
  ├─ Replay / Web設定 / ログ       ──> URL or file read only
  └─ Codex診断                     ──> API read only; no lifecycle command

ServerLifecycleManager
  ├─ start/stop/restart commands   ──> ProcessRegistry + ProcessController
  ├─ startup recovery              ──> validate_existing_processes()
  ├─ status/snapshot               ──> process probe + port probe + health probe
  └─ health monitor                ──> liveness/readiness observations only

ProcessController (inside lifecycle manager)
  ├─ direct argv subprocess.Popen(..., shell=False)
  ├─ dedicated server log file for stdout/stderr
  └─ graceful signal -> bounded tree stop -> force stop fallback

Backend
  ├─ /health/live                  ──> process/event-loop/HTTP identity only
  ├─ /health/ready                 ──> DB readiness (503 when unavailable)
  ├─ startup restore                ──> background task after HTTP startup
  └─ replay backfill                ──> bounded background transactions
```

## State and health are separate

The lifecycle state is one of `stopped`, `starting`, `running`, `degraded`,
`stopping`, or `error`. Each snapshot also carries independent values for:

- `process_alive`
- `port_listening`
- `liveness_ok`
- `readiness_ok` and `readiness_known`
- consecutive liveness failures
- last successful liveness timestamp
- transition reason and uptime

One failed poll does not overwrite a running lifecycle state. Three
consecutive liveness failures produce `degraded`; five produce a diagnostic
`error` state while leaving the live process owned and stoppable. A later
successful liveness probe recovers a health-derived error to `running`.
Readiness failure produces `degraded` and never invokes restart.

## Startup and identity

On Manager startup, a persisted record is adopted only when PID, executable,
creation time, configured port, and Backend identity (`app`, `component`,
`instance_id`, and database identifier) all match. A busy port without that
identity is reported as an external conflict and is never killed or reused.

## Restore and replay

Backend liveness does not touch SQLite, restore, replay, or JSONL monitor state.
Session restore and Replay backfill run as background work. The Backend
process lock keeps one instance per SQLite database; integration tests use a
separate test database or an in-memory database.

