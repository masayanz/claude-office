"use client";

import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { FastForward, Pause, Play, RotateCcw, SkipBack, SkipForward, Radio, Search } from "lucide-react";
import { useTranslation } from "@/hooks/useTranslation";
import { useGameStore } from "@/stores/gameStore";
import { useAppSettingsStore } from "@/stores/appSettingsStore";
import { useReplay, formatReplaySeconds, formatReplaySessionDate, type ReplaySessionSummary } from "@/hooks/useReplay";
import { ReplayController, formatReplayDuration, type ReplayControllerSnapshot } from "@/systems/replayController";

interface ReplayPanelProps {
  onReturnLive: () => void;
}

function sessionLabel(session: ReplaySessionSummary): string {
  return session.displayName || session.projectName || session.id.slice(0, 12);
}

export function ReplayPanel({ onReturnLive }: ReplayPanelProps): ReactNode {
  const { t } = useTranslation();
  const {
    sessions,
    loading,
    progress,
    emptyReason,
    frames,
    filters,
    setFilters,
    loadSession,
    cancelLoad,
  } = useReplay();
  const replaySettings = useAppSettingsStore((state) => state.settings);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [loadedId, setLoadedId] = useState<string | null>(null);
  const selectionGeneration = useRef(0);
  const [snapshot, setSnapshot] = useState<ReplayControllerSnapshot>({
    positionMs: 0,
    durationMs: 0,
    currentIndex: -1,
    isPlaying: false,
    speed: 1,
  });
  const [controller] = useState(
    () =>
      new ReplayController({
        compressIdle: replaySettings?.replay_compress_idle ?? true,
        onChange: setSnapshot,
        onFrame: (frame, index) => {
          const store = useGameStore.getState();
          if (frame) store.applyReplayState(frame.state);
          else store.resetForReplay();
          store.setReplayIndex(index);
          store.setReplayClockTime(frame ? new Date(frame.event.timestamp) : null);
        },
      }),
  );

  useEffect(() => {
    useGameStore.getState().resetForReplay();
    return () => controller.dispose();
  }, [controller]);

  useEffect(() => {
    const end = snapshot.currentIndex + 1;
    const start = Math.max(0, end - 500);
    const visible = snapshot.currentIndex < 0 ? [] : frames.slice(start, end).reverse();
    useGameStore.getState().setEventLog(visible.map((entry) => entry.event));
  }, [frames, snapshot.currentIndex]);

  const selected = useMemo(
    () => sessions.find((session) => session.id === selectedId) ?? null,
    [selectedId, sessions],
  );

  const handleSelect = async (sessionId: string) => {
    const generation = selectionGeneration.current + 1;
    selectionGeneration.current = generation;
    setSelectedId(sessionId);
    setLoadedId(null);
    controller.setFrames([]);
    try {
      await loadSession(sessionId, {
        onChunk: (chunk, offset) => {
          if (selectionGeneration.current !== generation) return;
          if (offset === 0) {
            useGameStore.getState().resetForReplay();
            controller.setFrames(chunk);
            controller.setSpeed(replaySettings?.replay_default_speed ?? 1);
            // The 0-second state must already contain Main before playback
            // starts; waiting for the first animation frame made it look
            // like Replay had lost the character.
            controller.seek(0);
          } else {
            controller.appendFrames(chunk);
          }
        },
      });
      if (selectionGeneration.current === generation) setLoadedId(sessionId);
    } catch {
      // The hook exposes the API error state; keep the session selected so
      // the user can retry after the backend recovers.
    }
  };

  const selectedIsLoaded = selectedId !== null && selectedId === loadedId;
  const currentFrame =
    selectedIsLoaded && snapshot.currentIndex >= 0 ? frames[snapshot.currentIndex] ?? null : null;
  const completed =
    frames.length > 0 &&
    !snapshot.isPlaying &&
    snapshot.currentIndex >= frames.length - 1 &&
    snapshot.positionMs >= snapshot.durationMs;

  return (
    <div className="absolute inset-0 z-30 flex min-h-0 overflow-hidden bg-slate-950/55 text-slate-100">
      <aside className="flex w-80 min-w-0 flex-col border-r border-slate-800 bg-slate-950/80 p-4">
        <div className="mb-3 flex items-center justify-between gap-2">
          <div>
            <div className="flex items-center gap-2 text-sm font-bold text-orange-400">
              <Radio size={16} /> {t("replay.title")}
            </div>
            <div className="mt-1 text-[11px] text-slate-500">{t("replay.selectSession")}</div>
          </div>
          <button type="button" onClick={onReturnLive} className="rounded-md border border-emerald-500/40 px-2 py-1 text-[11px] text-emerald-300 hover:bg-emerald-500/10">
            {t("replay.returnLive")}
          </button>
        </div>
        <div className="mb-2 flex items-center gap-2 rounded-md border border-slate-800 bg-slate-900 px-2">
          <Search size={14} className="text-slate-500" />
          <input value={filters.project} onChange={(event) => setFilters({ ...filters, project: event.target.value })} placeholder={t("replay.project")} className="min-w-0 flex-1 bg-transparent py-2 text-xs text-white outline-none" />
        </div>
        <input value={filters.model} onChange={(event) => setFilters({ ...filters, model: event.target.value })} placeholder={t("replay.model")} className="mb-2 w-full rounded-md border border-slate-800 bg-slate-900 px-2 py-2 text-xs text-white outline-none" />
        <div className="mb-2 grid grid-cols-2 gap-2">
          <input type="date" value={filters.startedFrom} onChange={(event) => setFilters({ ...filters, startedFrom: event.target.value })} aria-label={t("replay.fromDate")} className="min-w-0 rounded-md border border-slate-800 bg-slate-900 px-2 py-1.5 text-[10px] text-slate-200" />
          <input type="date" value={filters.startedTo} onChange={(event) => setFilters({ ...filters, startedTo: event.target.value })} aria-label={t("replay.toDate")} className="min-w-0 rounded-md border border-slate-800 bg-slate-900 px-2 py-1.5 text-[10px] text-slate-200" />
        </div>
        <div className="mb-3 grid grid-cols-2 gap-2">
          <select value={filters.source} onChange={(event) => setFilters({ ...filters, source: event.target.value })} className="rounded-md border border-slate-800 bg-slate-900 px-2 py-1.5 text-xs text-slate-200">
            <option value="">{t("replay.allSources")}</option>
            <option value="codex">Codex</option>
            <option value="claude-code">Claude Code</option>
            <option value="opencode">OpenCode</option>
          </select>
          <select value={filters.order} onChange={(event) => setFilters({ ...filters, order: event.target.value as "asc" | "desc" })} className="rounded-md border border-slate-800 bg-slate-900 px-2 py-1.5 text-xs text-slate-200">
            <option value="desc">{t("replay.newest")}</option>
            <option value="asc">{t("replay.oldest")}</option>
          </select>
        </div>
        <div className="min-h-0 flex-1 space-y-2 overflow-y-auto pr-1">
          {loading && sessions.length === 0 ? <div className="p-4 text-center text-xs text-slate-500">{t("replay.loading")}</div> : null}
          {!loading && sessions.length === 0 ? <div className="p-4 text-center text-xs text-slate-500">
            {emptyReason === "disabled"
              ? t("replay.historyDisabled")
              : emptyReason === "filtered"
                ? t("replay.filteredEmpty")
                : emptyReason === "api"
                  ? t("replay.apiError")
                  : t("replay.historyEmpty")}
          </div> : null}
          {sessions.map((session) => (
            <button key={session.id} type="button" aria-pressed={selectedId === session.id} onClick={() => void handleSelect(session.id)} className={`w-full rounded-lg border p-3 text-left transition-colors ${selectedId === session.id ? "border-orange-500/70 bg-orange-500/10" : "border-slate-800 bg-slate-900 hover:border-slate-600"}`}>
              <div className="truncate text-xs font-bold text-white">{sessionLabel(session)}</div>
              <div className="mt-1 text-[10px] text-slate-400">{formatReplaySessionDate(session.startedAt)}</div>
              <div className="mt-2 flex flex-wrap gap-x-3 gap-y-1 text-[10px] text-slate-500">
                <span>{session.source || "AI"}</span><span>{formatReplaySeconds(session.durationSeconds)}</span><span>{session.eventCount} {t("replay.events")}</span><span>{session.maxAgents} {t("replay.agents")}</span>
              </div>
              {session.status === "in_progress" ? <div className="mt-2 text-[10px] text-amber-300">{t("replay.inProgress")}</div> : null}
            </button>
          ))}
        </div>
      </aside>

      <section className="flex min-w-0 flex-1 flex-col p-5">
        <div className="flex items-start justify-between gap-4">
          <div>
            <div className="text-xs font-bold uppercase tracking-[0.2em] text-orange-400">{t("replay.replay")}</div>
            <h2 className="mt-1 text-xl font-bold text-white">{selected ? sessionLabel(selected) : t("replay.selectSession")}</h2>
            {selected ? <div className="mt-1 text-xs text-slate-400">{t("replay.recordedAt")}: {formatReplaySessionDate(selected.startedAt)}</div> : null}
          </div>
          {snapshot.isPlaying && selected ? <div className="rounded-md border border-orange-500/40 bg-orange-500/10 px-3 py-2 text-xs font-bold text-orange-300">▶ {t("replay.replay")} · {sessionLabel(selected)}</div> : null}
          {currentFrame ? <div className="text-right text-xs text-slate-400">{formatReplaySessionDate(currentFrame.event.timestamp)}<div className="mt-1 text-[10px] text-orange-300">{currentFrame.event.summary}</div></div> : null}
          <button type="button" disabled={!selectedIsLoaded || frames.length === 0} onClick={() => controller.play()} className="rounded-md bg-emerald-500 px-3 py-2 text-xs font-bold text-slate-950 hover:bg-emerald-400 disabled:cursor-not-allowed disabled:opacity-40"><Play size={14} className="mr-1 inline" />{t("replay.play")}</button>
        </div>

        <div className="flex-1" />

        {selectedIsLoaded && frames.length > 0 ? (
          <div className="rounded-xl border border-slate-700 bg-slate-950/90 p-4 shadow-2xl">
            <div className="mb-3 flex items-center justify-between text-xs text-slate-400">
              <span>{formatReplayDuration(snapshot.positionMs)}</span>
              <span>{formatReplayDuration(snapshot.durationMs)}</span>
            </div>
            <input aria-label={t("replay.title")} type="range" min={0} max={Math.max(1, snapshot.durationMs)} step={50} value={snapshot.positionMs} onChange={(event) => controller.seek(Number(event.target.value))} className="w-full accent-orange-500" />
            <div className="mt-4 flex flex-wrap items-center gap-2">
              <button type="button" title={t("replay.beginning")} onClick={() => controller.reset()} className="rounded-md border border-slate-700 p-2 text-slate-200 hover:bg-slate-800"><RotateCcw size={15} /></button>
              <button type="button" title={t("replay.back10")} onClick={() => controller.skip(-10)} className="rounded-md border border-slate-700 p-2 text-slate-200 hover:bg-slate-800"><SkipBack size={15} /></button>
              <button type="button" title={snapshot.isPlaying ? t("replay.pause") : t("replay.play")} onClick={() => controller.toggle()} className="rounded-md bg-orange-500 p-2 text-slate-950 hover:bg-orange-400">{snapshot.isPlaying ? <Pause size={15} /> : <Play size={15} />}</button>
              <button type="button" title={t("replay.forward10")} onClick={() => controller.skip(10)} className="rounded-md border border-slate-700 p-2 text-slate-200 hover:bg-slate-800"><SkipForward size={15} /></button>
              <button type="button" title={t("replay.nextEvent")} onClick={() => controller.step()} className="rounded-md border border-slate-700 p-2 text-slate-200 hover:bg-slate-800"><FastForward size={15} /></button>
              <label className="ml-auto flex items-center gap-2 text-xs text-slate-400">{t("replay.speed")}
                <select value={snapshot.speed} onChange={(event) => controller.setSpeed(Number(event.target.value))} className="rounded-md border border-slate-700 bg-slate-900 px-2 py-1 text-xs text-white">
                  {[0.5, 1, 2, 4, 8].map((speed) => <option key={speed} value={speed}>{speed}x</option>)}
                </select>
              </label>
            </div>
            <div className="mt-3 flex items-center justify-between text-[11px] text-slate-500">
              <span>{t("replay.compressIdle")}</span>
              <span>{snapshot.currentIndex + 1} / {frames.length}</span>
            </div>
            {progress.phase === "error" ? (
              <div className="mt-3 rounded-md border border-red-500/30 bg-red-500/10 px-3 py-2 text-[11px] text-red-300">
                <div>{progress.stage}: {progress.error}</div>
                <button type="button" onClick={() => { if (selected) void handleSelect(selected.id); }} className="mt-2 rounded border border-red-300/40 px-2 py-1 hover:bg-red-500/10">再試行</button>
              </div>
            ) : progress.phase !== "completed" && progress.phase !== "idle" && progress.phase !== "cancelled" && progress.total > progress.loaded ? (
              <div className="mt-3 rounded-md border border-orange-500/20 bg-orange-500/5 px-3 py-2 text-[11px] text-slate-400">
                <div className="flex items-center justify-between gap-2"><span>{progress.stage}</span><span>{progress.loaded.toLocaleString()} / {progress.total.toLocaleString()} ({progress.percent}%)</span></div>
                <div className="mt-1 h-1 overflow-hidden rounded-full bg-slate-800"><div className="h-full bg-orange-500" style={{ width: `${progress.percent}%` }} /></div>
                <button type="button" onClick={cancelLoad} className="mt-2 rounded border border-slate-600 px-2 py-1 text-slate-300 hover:bg-slate-800">キャンセル</button>
              </div>
            ) : null}
            {completed ? <div className="mt-3 flex items-center justify-between rounded-md border border-emerald-500/30 bg-emerald-500/10 px-3 py-2 text-xs text-emerald-300"><span>{t("replay.completed")}</span><button type="button" onClick={() => controller.reset()}>{t("replay.repeat")}</button></div> : null}
          </div>
        ) : selected && (loading || progress.phase === "metadata" || progress.phase === "events") ? <ReplayProgress progress={progress} onCancel={cancelLoad} /> : selected && progress.phase === "error" ? <div className="rounded-lg border border-red-500/30 bg-red-500/10 p-4 text-xs text-red-300"><div>{t("replay.apiError")}</div><div className="mt-1 text-[11px] text-red-200/70">{progress.error}</div><div className="mt-3 flex gap-2"><button type="button" onClick={() => void handleSelect(selected.id)} className="rounded-md border border-red-300/40 px-3 py-1.5 hover:bg-red-500/10">再試行</button><button type="button" onClick={() => { selectionGeneration.current += 1; setSelectedId(null); setLoadedId(null); cancelLoad(); }} className="rounded-md border border-slate-600 px-3 py-1.5 text-slate-200 hover:bg-slate-800">セッション一覧へ戻る</button></div></div> : selected && emptyReason === "empty" ? <div className="rounded-lg border border-amber-500/30 bg-amber-500/10 p-4 text-xs text-amber-300">{t("replay.eventEmpty")}</div> : null}
      </section>
    </div>
  );
}

function ReplayProgress({
  progress,
  onCancel,
}: {
  progress: ReturnType<typeof useReplay>["progress"];
  onCancel: () => void;
}): ReactNode {
  const determinate = progress.total > 0;
  const cancellable = progress.phase === "metadata" || progress.phase === "events" || progress.phase === "ready";
  return (
    <div className="rounded-lg border border-orange-500/30 bg-slate-900 p-4 text-xs text-slate-300">
      <div className="flex items-center justify-between gap-3">
        <span>{progress.stage || "再生データを準備しています…"}</span>
        {cancellable ? <button type="button" onClick={onCancel} className="rounded-md border border-slate-600 px-3 py-1.5 text-slate-200 hover:bg-slate-800">キャンセル</button> : null}
      </div>
      {determinate ? <>
        <div className="mt-3 flex justify-between text-[11px] text-slate-400"><span>{progress.loaded.toLocaleString()} / {progress.total.toLocaleString()} events</span><span>{progress.percent}%</span></div>
        <div className="mt-1 h-2 overflow-hidden rounded-full bg-slate-800"><div className="h-full rounded-full bg-orange-500 transition-[width]" style={{ width: `${progress.percent}%` }} /></div>
      </> : <div className="mt-3 h-2 animate-pulse rounded-full bg-slate-800" />}
      <div className="mt-3 flex flex-wrap gap-x-4 gap-y-1 text-[11px] text-slate-500">
        <span>読み込み済み: {progress.loaded.toLocaleString()}</span>
        {progress.firstPlayable ? <span className="text-emerald-300">再生開始可能</span> : null}
        {progress.bufferedSeconds > 0 ? <span>バッファ: {Math.floor(progress.bufferedSeconds)}秒</span> : null}
      </div>
    </div>
  );
}
