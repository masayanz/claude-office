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
  frames: ReplayFrame[];
  filters: ReplayFilters;
  setFilters: (filters: ReplayFilters) => void;
  refreshSessions: () => Promise<void>;
  loadSession: (sessionId: string) => Promise<ReplayFrame[]>;
} {
  const [sessions, setSessions] = useState<ReplaySessionSummary[]>([]);
  const [frames, setFrames] = useState<ReplayFrame[]>([]);
  const [loading, setLoading] = useState(false);
  const [filters, setFilters] = useState<ReplayFilters>(EMPTY_FILTERS);

  const refreshSessions = useCallback(async () => {
    setLoading(true);
    try {
      const params = new URLSearchParams({ order: filters.order });
      if (filters.project.trim()) params.set("project", filters.project.trim());
      if (filters.source) params.set("source", filters.source);
      if (filters.model.trim()) params.set("model", filters.model.trim());
      if (filters.startedFrom) params.set("started_from", `${filters.startedFrom}T00:00:00Z`);
      if (filters.startedTo) params.set("started_to", `${filters.startedTo}T23:59:59Z`);
      const response = await apiFetch(`/api/v1/replay/sessions?${params.toString()}`);
      if (response.ok) setSessions((await response.json()) as ReplaySessionSummary[]);
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
      const response = await apiFetch(
        `/api/v1/replay/sessions/${encodeURIComponent(sessionId)}/events`,
      );
      if (!response.ok) throw new Error(`Replay HTTP ${response.status}`);
      const payload = (await response.json()) as ReplayApiResponse[];
      const loaded = payload.map((entry) => ({ event: entry.event, state: entry.state }));
      setFrames(loaded);
      return loaded;
    } finally {
      setLoading(false);
    }
  }, []);

  return { sessions, loading, frames, filters, setFilters, refreshSessions, loadSession };
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
