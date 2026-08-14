"use client";

import { create } from "zustand";
import type { EventType } from "@/types";
import { usePreferencesStore } from "@/stores/preferencesStore";
import { apiFetch } from "@/utils/api";

// ============================================================================
// TYPES
// ============================================================================

export type UrgencyLevel = "critical" | "high" | "low" | "info";

export interface AttentionToast {
  id: string;
  notificationKey: string;
  agentId: string | null;
  agentName: string | null;
  eventType: EventType;
  urgency: number;
  urgencyLevel: UrgencyLevel;
  title: string;
  description: string;
  createdAt: number;
  autoDismissMs: number | null;
  dismissed: boolean;
}

export interface FocusPopupState {
  agentId: string;
  screenX: number;
  screenY: number;
}

interface AttentionState {
  // Toast queue
  toastQueue: AttentionToast[];
  // Command bar
  isCommandBarOpen: boolean;
  commandFilter: string;
  // Focus popup
  focusPopup: FocusPopupState | null;

  // Actions
  processEvent: (event: {
    type: EventType;
    sessionId?: string | null;
    eventId?: string | null;
    turnId?: string | null;
    agentId?: string | null;
    agentName?: string | null;
    taskDescription?: string | null;
    errorType?: string | null;
    message?: string | null;
  }) => void;
  dismissToast: (id: string) => void;
  clearAllToasts: () => void;
  openCommandBar: () => void;
  closeCommandBar: () => void;
  setCommandFilter: (filter: string) => void;
  openFocusPopup: (agentId: string, screenX: number, screenY: number) => void;
  closeFocusPopup: () => void;
  focusAgentTerminal: (
    sessionId: string,
    agentId: string | null,
  ) => Promise<void>;
}

// ============================================================================
// CONSTANTS
// ============================================================================

export const MAX_VISIBLE_TOASTS = 3;

/** Map event types to urgency scores and auto-dismiss timing. */
function scoreEvent(eventType: EventType): {
  urgency: number;
  level: UrgencyLevel;
  autoDismissMs: number | null;
} {
  const prefs = usePreferencesStore.getState();
  switch (eventType) {
    case "permission_request":
      return { urgency: 90, level: "critical", autoDismissMs: 8000 };
    case "error":
      return { urgency: 70, level: "high", autoDismissMs: 8000 };
    case "stop":
      return { urgency: 0, level: "info", autoDismissMs: 0 };
    case "task_completed":
      return {
        urgency: 30,
        level: "low",
        autoDismissMs: prefs.toastAutoDismissLow,
      };
    case "subagent_start":
    case "background_task_notification":
      return {
        urgency: 10,
        level: "info",
        autoDismissMs: prefs.toastAutoDismissInfo,
      };
    default:
      return {
        urgency: 5,
        level: "info",
        autoDismissMs: prefs.toastAutoDismissInfo,
      };
  }
}

// ============================================================================
// STORE
// ============================================================================

export const useAttentionStore = create<AttentionState>()((set, get) => ({
  toastQueue: [],
  isCommandBarOpen: false,
  commandFilter: "",
  focusPopup: null,

  processEvent: (event) => {
    const { urgency, level, autoDismissMs } = scoreEvent(event.type);
    // Stop is an internal lifecycle boundary and is intentionally shown only
    // in the Event Log/Main state, not as a persistent attention card.
    if (urgency <= 5) return;

    const notificationKey = [
      event.sessionId ?? "global",
      event.type,
      event.agentId ?? "main",
      event.eventId ?? event.turnId ?? event.message ?? "event",
    ].join(":");

    const toast: AttentionToast = {
      id: `toast:${notificationKey}`,
      notificationKey,
      agentId: event.agentId ?? null,
      agentName: event.agentName ?? null,
      eventType: event.type,
      urgency,
      urgencyLevel: level,
      title: event.type.replace(/_/g, " "),
      description:
        event.taskDescription ?? event.errorType ?? event.message ?? "",
      createdAt: Date.now(),
      autoDismissMs,
      dismissed: false,
    };

    set((state) => {
      if (
        state.toastQueue.some(
          (existing) => existing.notificationKey === notificationKey,
        )
      ) {
        return state;
      }
      const queue = [...state.toastQueue, toast];
      // Sort by urgency descending
      queue.sort((a, b) => b.urgency - a.urgency);
      // Auto-dismiss oldest low-urgency toasts if over max
      if (queue.filter((t) => !t.dismissed).length > MAX_VISIBLE_TOASTS) {
        const activeSorted = queue
          .filter((t) => !t.dismissed)
          .sort((a, b) => a.createdAt - b.createdAt);
        const toastId = activeSorted[0]?.id;
        const finalQueue = queue.map((t) =>
          t.id === toastId ? { ...t, dismissed: true } : t,
        );
        return { toastQueue: finalQueue.slice(-100) };
      }
      return { toastQueue: queue.slice(-100) };
    });
  },

  dismissToast: (id) =>
    set((state) => ({
      toastQueue: state.toastQueue.map((t) =>
        t.id === id ? { ...t, dismissed: true } : t,
      ),
    })),

  clearAllToasts: () =>
    set((state) => ({
      toastQueue: state.toastQueue.map((t) => ({ ...t, dismissed: true })),
    })),

  openCommandBar: () => set({ isCommandBarOpen: true, commandFilter: "" }),
  closeCommandBar: () => set({ isCommandBarOpen: false, commandFilter: "" }),
  setCommandFilter: (filter) => set({ commandFilter: filter }),

  openFocusPopup: (agentId, screenX, screenY) =>
    set({
      focusPopup: { agentId, screenX, screenY },
    }),

  closeFocusPopup: () => set({ focusPopup: null }),

  focusAgentTerminal: async (sessionId, _agentId) => {
    try {
      const res = await apiFetch(`/api/v1/sessions/${sessionId}/focus`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({}),
      });
      if (!res.ok) {
        get().processEvent({
          type: "error",
          message: `Focus terminal failed: ${res.statusText}`,
        });
      }
    } catch {
      get().processEvent({
        type: "error",
        message: "Cannot reach backend — is the server running?",
      });
    }
    get().closeFocusPopup();
  },
}));

// ============================================================================
// SELECTORS
// ============================================================================

export const selectActiveToasts = (state: AttentionState) =>
  state.toastQueue.filter((t) => !t.dismissed);

export const selectUnreadCount = (state: AttentionState) =>
  state.toastQueue.filter((t) => !t.dismissed).length;

export const selectFocusPopup = (state: AttentionState) => state.focusPopup;
