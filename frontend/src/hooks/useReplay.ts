"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import type { ReplayFrame } from "@/stores/gameStore";
import { apiFetch } from "@/utils/api";

export interface ReplaySessionSummary {
  id: string;
  projectName: string | null;
  displayName: string | null;
  source: string | null;
  sources: string[];
  startedAt: string;
  endedAt: string | null;
  durationSeconds: number;
  status: string;
  eventCount: number;
  maxAgents: number;
  models: string[];
}

export interface ReplayFilters {
  project: string;
  source: string;
  model: string;
  startedFrom: string;
  startedTo: string;
  order: "asc" | "desc";
}

export type ReplayEmptyReason = "disabled" | "empty" | "filtered" | "api" | null;

export type ReplayLoadPhase =
  | "idle"
  | "metadata"
  | "events"
  | "ready"
  | "completed"
  | "cancelled"
  | "error";

export interface ReplayLoadProgress {
  phase: ReplayLoadPhase;
  stage: string;
  loaded: number;
  total: number;
  percent: number;
  firstPlayable: boolean;
  bufferedSeconds: number;
  loadedTimeRange: { start: string | null; end: string | null };
  error: string | null;
}

export interface ReplayLoadOptions {
  onChunk?: (frames: ReplayFrame[], offset: number) => void;
}

interface ReplayStorageSummary {
  enabled: boolean;
  eventCount: number;
  sessionCount: number;
}

interface ReplayApiResponse {
  event: ReplayFrame["event"];
  state: ReplayFrame["state"];
}

interface ReplayChunkResponse {
  items: ReplayApiResponse[];
  offset: number;
  total: number;
  nextOffset: number;
  hasMore: boolean;
  bufferedSeconds?: number;
  loadedTimeRange?: { start: string | null; end: string | null };
}

const EMPTY_FILTERS: ReplayFilters = {
  project: "",
  source: "",
  model: "",
  startedFrom: "",
  startedTo: "",
  order: "desc",
};

// A smaller first buffer keeps state reconstruction and JSON parsing below a
// visible freeze on Windows.  The server still accepts larger explicit pages.
const REPLAY_CHUNK_SIZE = 500;
const REPLAY_REQUEST_TIMEOUT_MS = 20_000;

const INITIAL_PROGRESS: ReplayLoadProgress = {
  phase: "idle",
  stage: "",
  loaded: 0,
  total: 0,
  percent: 0,
  firstPlayable: false,
  bufferedSeconds: 0,
  loadedTimeRange: { start: null, end: null },
  error: null,
};

export function useReplay(): {
  sessions: ReplaySessionSummary[];
  loading: boolean;
  progress: ReplayLoadProgress;
  emptyReason: ReplayEmptyReason;
  frames: ReplayFrame[];
  filters: ReplayFilters;
  setFilters: (filters: ReplayFilters) => void;
  refreshSessions: () => Promise<void>;
  loadSession: (sessionId: string, options?: ReplayLoadOptions) => Promise<ReplayFrame[]>;
  cancelLoad: () => void;
} {
  const [sessions, setSessions] = useState<ReplaySessionSummary[]>([]);
  const [frames, setFrames] = useState<ReplayFrame[]>([]);
  const [loading, setLoading] = useState(false);
  const [progress, setProgress] = useState<ReplayLoadProgress>(INITIAL_PROGRESS);
  const [emptyReason, setEmptyReason] = useState<ReplayEmptyReason>(null);
  const [filters, setFilters] = useState<ReplayFilters>(EMPTY_FILTERS);
  const loadAbortRef = useRef<AbortController | null>(null);
  const loadGenerationRef = useRef(0);

  const refreshSessions = useCallback(async () => {
    setLoading(true);
    setEmptyReason(null);
    try {
      const params = new URLSearchParams({ order: filters.order });
      if (filters.project.trim()) params.set("project", filters.project.trim());
      if (filters.source) params.set("source", filters.source);
      if (filters.model.trim()) params.set("model", filters.model.trim());
      if (filters.startedFrom) params.set("started_from", localDayBoundary(filters.startedFrom));
      if (filters.startedTo) params.set("started_to", localDayBoundary(filters.startedTo, true));
      const storageResponse = await apiFetch("/api/v1/replay/storage");
      if (!storageResponse.ok) {
        setEmptyReason("api");
        return;
      }
      const storage = (await storageResponse.json()) as ReplayStorageSummary;
      const response = await apiFetch(`/api/v1/replay/sessions?${params.toString()}`);
      if (!response.ok) {
        setEmptyReason("api");
        return;
      }
      const result = (await response.json()) as ReplaySessionSummary[];
      setSessions(result);
      if (result.length === 0) {
        if (!storage.enabled) setEmptyReason("disabled");
        else if (hasReplayFilters(filters)) setEmptyReason("filtered");
        else if (storage.eventCount === 0) setEmptyReason("empty");
        else setEmptyReason("empty");
      }
    } catch {
      setEmptyReason("api");
    } finally {
      setLoading(false);
    }
  }, [filters]);

  useEffect(() => {
    void refreshSessions();
  }, [refreshSessions]);

  const cancelLoad = useCallback(() => {
    loadGenerationRef.current += 1;
    loadAbortRef.current?.abort();
    loadAbortRef.current = null;
    setLoading(false);
    setProgress((current) => ({
      ...current,
      phase: "cancelled",
      stage: "読み込みをキャンセルしました",
    }));
  }, []);

  const loadSession = useCallback(
    async (sessionId: string, options: ReplayLoadOptions = {}): Promise<ReplayFrame[]> => {
      loadAbortRef.current?.abort();
      const abortController = new AbortController();
      loadAbortRef.current = abortController;
      const generation = loadGenerationRef.current + 1;
      loadGenerationRef.current = generation;
      const encodedId = encodeURIComponent(sessionId);
      const startedAt = now();
      const isCurrent = () =>
        loadGenerationRef.current === generation && !abortController.signal.aborted;
      const updateProgress = (patch: Partial<ReplayLoadProgress>) => {
        if (isCurrent()) setProgress((current) => ({ ...current, ...patch }));
      };
      const log = (message: string, details: Record<string, unknown> = {}) => {
        if (typeof console !== "undefined") {
          console.info(`[Replay] ${message}`, { session: sessionId, ...details });
        }
      };

      setFrames([]);
      setEmptyReason(null);
      setLoading(true);
      setProgress({
        ...INITIAL_PROGRESS,
        phase: "metadata",
        stage: "セッション情報を取得中…",
      });
      log("load started");

      const fetchChunk = async (offset: number): Promise<ReplayChunkResponse> => {
        const requestStarted = now();
        const response = await replayFetch(
          `/api/v1/replay/sessions/${encodedId}/events?offset=${offset}&limit=${REPLAY_CHUNK_SIZE}`,
          { signal: abortController.signal },
        );
        if (!response.ok) throw new Error(`Replay HTTP ${response.status}`);
        const payload = (await response.json()) as ReplayChunkResponse | ReplayApiResponse[];
        const parseMs = now() - requestStarted;
        const rawItems = Array.isArray(payload) ? payload : payload.items;
        const items = rawItems.map((entry) => ({ event: entry.event, state: entry.state }));
        const chunk = Array.isArray(payload)
          ? {
              items,
              offset,
              total: offset + items.length,
              nextOffset: offset + items.length,
              hasMore: false,
            }
          : { ...payload, items };
        log("chunk loaded", {
          offset,
          count: items.length,
          total: chunk.total,
          parseMs: Math.round(parseMs),
          responseBytes: response.headers.get("content-length"),
        });
        return chunk;
      };

      try {
        const metadataStarted = now();
        const metadataResponse = await replayFetch(
          `/api/v1/replay/sessions/${encodedId}`,
          { signal: abortController.signal },
        );
        if (!metadataResponse.ok) {
          throw new Error(`Replay metadata HTTP ${metadataResponse.status}`);
        }
        const metadata = (await metadataResponse.json()) as ReplaySessionSummary;
        const total = Math.max(0, metadata.eventCount);
        log("metadata loaded", { total, durationMs: Math.round(now() - metadataStarted) });
        updateProgress({ total, phase: "events", stage: "イベントを読み込み中…" });

        const firstChunk = await fetchChunk(0);
        if (!isCurrent()) {
          const cancelled = new Error("Replay load cancelled");
          cancelled.name = "AbortError";
          throw cancelled;
        }
        if (firstChunk.items.length === 0) {
          setEmptyReason("empty");
          throw new Error("Replay contains no events");
        }
        const firstFrames = firstChunk.items;
        setFrames(firstFrames);
        const firstRange = firstChunk.loadedTimeRange ?? {
          start: firstFrames[0]?.event.timestamp ?? null,
          end: firstFrames.at(-1)?.event.timestamp ?? null,
        };
        updateProgress({
          phase: "ready",
          stage: "再生準備完了（続きの履歴を読み込み中…）",
          loaded: firstFrames.length,
          total: firstChunk.total || total,
          percent: Math.min(100, Math.round((firstFrames.length / Math.max(1, firstChunk.total || total)) * 100)),
          firstPlayable: true,
          bufferedSeconds: firstChunk.bufferedSeconds ?? 0,
          loadedTimeRange: firstRange,
        });
        options.onChunk?.(firstFrames, 0);
        log("first playable", { count: firstFrames.length, elapsedMs: Math.round(now() - startedAt) });

        const drain = async (): Promise<void> => {
          let nextOffset = firstChunk.nextOffset;
          let latestRange = firstRange;
          let bufferedSeconds = firstChunk.bufferedSeconds ?? 0;
          try {
            while (firstChunk.hasMore && isCurrent()) {
              updateProgress({ phase: "events", stage: "イベントを読み込み中…" });
              const chunk = await fetchChunk(nextOffset);
              if (!isCurrent()) return;
              if (chunk.items.length === 0) break;
              setFrames((current) => [...current, ...chunk.items]);
              options.onChunk?.(chunk.items, nextOffset);
              nextOffset = chunk.nextOffset;
              latestRange = chunk.loadedTimeRange ?? {
                start: latestRange.start,
                end: chunk.items.at(-1)?.event.timestamp ?? latestRange.end,
              };
              bufferedSeconds = chunk.bufferedSeconds ?? bufferedSeconds;
              updateProgress({
                loaded: Math.min(nextOffset, chunk.total || total),
                total: chunk.total || total,
                percent: Math.min(
                  100,
                  Math.round((nextOffset / Math.max(1, chunk.total || total)) * 100),
                ),
                bufferedSeconds,
                loadedTimeRange: latestRange,
              });
              if (!chunk.hasMore) break;
            }
            if (isCurrent()) {
              setLoading(false);
              updateProgress({ phase: "completed", stage: "再生データの読み込み完了" });
              log("load completed", { loaded: nextOffset, elapsedMs: Math.round(now() - startedAt) });
            }
          } catch (error) {
            if (!isAbortError(error) && isCurrent()) {
              setLoading(false);
              setProgress((current) => ({
                ...current,
                phase: "error",
                stage: "再生データの読み込みに失敗しました",
                error: replayErrorMessage(error),
              }));
              log("load failed", { error: replayErrorMessage(error) });
            }
          }
        };

        // Let ReplayPanel install the first chunk before prefetch begins.
        setTimeout(() => void drain(), 0);
        return firstFrames;
      } catch (error) {
        if (!isAbortError(error) && isCurrent()) {
          setLoading(false);
          setProgress((current) => ({
            ...current,
            phase: "error",
            stage: "再生データの読み込みに失敗しました",
            error: replayErrorMessage(error),
          }));
          setEmptyReason((reason) => (reason === "empty" ? reason : "api"));
        }
        throw error;
      }
    },
    [],
  );

  useEffect(() => () => loadAbortRef.current?.abort(), []);

  return {
    sessions,
    loading,
    progress,
    emptyReason,
    frames,
    filters,
    setFilters,
    refreshSessions,
    loadSession,
    cancelLoad,
  };
}

function now(): number {
  return typeof performance === "undefined" ? Date.now() : performance.now();
}

function isAbortError(error: unknown): boolean {
  return error instanceof Error && error.name === "AbortError";
}

async function replayFetch(path: string, init: RequestInit = {}): Promise<Response> {
  const requestController = new AbortController();
  let timedOut = false;
  const forwardAbort = () => requestController.abort();
  init.signal?.addEventListener("abort", forwardAbort, { once: true });
  const timeoutId = setTimeout(() => {
    timedOut = true;
    requestController.abort();
  }, REPLAY_REQUEST_TIMEOUT_MS);
  try {
    return await apiFetch(path, { ...init, signal: requestController.signal });
  } catch (error) {
    if (timedOut && !init.signal?.aborted) {
      const timeoutError = new Error("Replay request timed out");
      timeoutError.name = "TimeoutError";
      throw timeoutError;
    }
    throw error;
  } finally {
    clearTimeout(timeoutId);
    init.signal?.removeEventListener("abort", forwardAbort);
  }
}

function replayErrorMessage(error: unknown): string {
  if (error instanceof Error && error.name === "TimeoutError") {
    return "通信がタイムアウトしました。時間をおいて再試行してください。";
  }
  return error instanceof Error ? error.message : "Replay load failed";
}

function hasReplayFilters(filters: ReplayFilters): boolean {
  return Boolean(
    filters.project.trim() ||
      filters.model.trim() ||
      filters.source ||
      filters.startedFrom ||
      filters.startedTo,
  );
}

/** Convert a date input's local calendar day to an absolute UTC boundary. */
export function localDayBoundary(value: string, endOfDay = false): string {
  const [year, month, day] = value.split("-").map(Number);
  const date = new Date(
    year,
    month - 1,
    day,
    endOfDay ? 23 : 0,
    endOfDay ? 59 : 0,
    endOfDay ? 59 : 0,
    endOfDay ? 999 : 0,
  );
  return date.toISOString();
}

export function formatReplaySessionDate(value: string): string {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString();
}

export function formatReplaySeconds(seconds: number): string {
  const safe = Math.max(0, Math.floor(seconds));
  const minutes = Math.floor(safe / 60);
  const remainder = safe % 60;
  return `${minutes}:${String(remainder).padStart(2, "0")}`;
}
