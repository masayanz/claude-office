"use client";

import { useCallback, useEffect, useState } from "react";
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

interface ReplayStorageSummary {
  enabled: boolean;
  eventCount: number;
  sessionCount: number;
}

interface ReplayApiResponse {
  event: ReplayFrame["event"];
  state: ReplayFrame["state"];
}

const EMPTY_FILTERS: ReplayFilters = {
  project: "",
  source: "",
  model: "",
  startedFrom: "",
  startedTo: "",
  order: "desc",
};

export function useReplay(): {
  sessions: ReplaySessionSummary[];
  loading: boolean;
  emptyReason: ReplayEmptyReason;
  frames: ReplayFrame[];
  filters: ReplayFilters;
  setFilters: (filters: ReplayFilters) => void;
  refreshSessions: () => Promise<void>;
  loadSession: (sessionId: string) => Promise<ReplayFrame[]>;
} {
  const [sessions, setSessions] = useState<ReplaySessionSummary[]>([]);
  const [frames, setFrames] = useState<ReplayFrame[]>([]);
  const [loading, setLoading] = useState(false);
  const [emptyReason, setEmptyReason] = useState<ReplayEmptyReason>(null);
  const [filters, setFilters] = useState<ReplayFilters>(EMPTY_FILTERS);

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

  const loadSession = useCallback(async (sessionId: string): Promise<ReplayFrame[]> => {
    setLoading(true);
    try {
      const metadataResponse = await apiFetch(
        `/api/v1/replay/sessions/${encodeURIComponent(sessionId)}`,
      );
      if (!metadataResponse.ok) throw new Error(`Replay metadata HTTP ${metadataResponse.status}`);
      const response = await apiFetch(
        `/api/v1/replay/sessions/${encodeURIComponent(sessionId)}/events`,
      );
      if (!response.ok) throw new Error(`Replay HTTP ${response.status}`);
      const payload = (await response.json()) as ReplayApiResponse[];
      const loaded = payload.map((entry) => ({ event: entry.event, state: entry.state }));
      if (loaded.length === 0) {
        setEmptyReason("empty");
        throw new Error("Replay contains no events");
      }
      setFrames(loaded);
      return loaded;
    } catch (error) {
      setEmptyReason((reason) => (reason === "empty" ? reason : "api"));
      throw error;
    } finally {
      setLoading(false);
    }
  }, []);

  return { sessions, loading, emptyReason, frames, filters, setFilters, refreshSessions, loadSession };
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
