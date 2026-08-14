/**
 * WebSocket Event Handler (thin lifecycle binding).
 *
 * Wires the {@link WebSocketController} (transport: connect/reconnect/backoff)
 * to React and dispatches incoming messages to the appropriate domain module:
 *   - `state_update` → {@link reconcileState} (agent diff, spawn policy, office sync)
 *   - `pre/post_tool_use` → {@link TypingTracker} (min-duration typing timer)
 *   - `event` / `git_status` / `reload` / `session_deleted` / `error` → handled inline
 *
 * All heavy logic has been extracted so this file is just plumbing: refs that
 * persist across renders, a controller instance, and a `useEffect` that opens
 * and tears down the connection. See ARC-018.
 */

"use client";

import { useCallback, useEffect, useRef } from "react";
import { useGameStore } from "@/stores/gameStore";
import { useAttentionStore } from "@/stores/attentionStore";
import { usePreferencesStore } from "@/stores/preferencesStore";
import { agentMachineService } from "@/machines/agentMachineService";
import { resetSpawnIndex } from "@/systems/queuePositions";
import { TypingTracker } from "@/systems/typingTracker";
import { reconcileState } from "@/systems/stateReconciler";
import { shouldShowToast } from "@/systems/toastFilter";
import { WebSocketController } from "@/systems/webSocketController";
import {
  acceptCodexEvent,
  beginCodexTurn,
  createCodexTurnState,
  isStaleCodexBossSnapshot,
  resetCodexTurnState,
  stopCodexTurn,
} from "@/systems/codexTurnState";
import type { EventType, WebSocketMessage } from "@/types";
import type { ReplayFrame } from "@/stores/gameStore";
import { apiFetch } from "@/utils/api";
import {
  isCodexAgentWait,
  isCodexSource,
  shouldEnterCodexWaitState,
} from "@/utils/codexPresentation";

// ============================================================================
// TYPES
// ============================================================================

interface UseWebSocketEventsOptions {
  sessionId: string;
  enabled?: boolean;
}

// ============================================================================
// HOOK
// ============================================================================

export function useWebSocketEvents({
  sessionId,
  enabled = true,
}: UseWebSocketEventsOptions): void {
  // ---- Domain-tracking refs (read/written by reconcileState) ----
  const processedAgentsRef = useRef<Set<string>>(new Set());
  const currentSessionIdRef = useRef(sessionId);
  currentSessionIdRef.current = sessionId;
  // Prevents backend queue state from overwriting the frontend's animated queue
  // after the initial mid-session-join sync.
  const initialQueueSyncDoneRef = useRef<string | null>(null);
  // Per-entity last bubble text — suppresses re-enqueue after display clear.
  const lastSeenBubbleTextRef = useRef<Map<string, string>>(new Map());
  const completionTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const codexTurnRef = useRef(createCodexTurnState());

  // ---- Typing tracker (min-duration state machine, extracted) ----
  // Created once; setTyping routes "boss"/"main" → boss store, else agent store.
  const typingTrackerRef = useRef<TypingTracker | null>(null);
  if (typingTrackerRef.current === null) {
    typingTrackerRef.current = new TypingTracker((key, typing) => {
      if (key === "boss" || key === "main") {
        useGameStore.getState().setBossTyping(typing);
      } else {
        useGameStore.getState().setAgentTyping(key, typing);
      }
    });
  }

  // ---- Store actions (stable zustand references) ----
  const setConnected = useGameStore.getState().setConnected;
  const setSessionId = useGameStore.getState().setSessionId;
  const setGitStatus = useGameStore.getState().setGitStatus;
  const addEventLog = useGameStore.getState().addEventLog;
  const hydrateEventLog = useGameStore.getState().hydrateEventLog;

  // ---- Reconnect bookkeeping (clears stale tracking state on a fresh socket) ----
  const handleReconnectReset = useCallback(() => {
    processedAgentsRef.current.clear();
    lastSeenBubbleTextRef.current.clear();
    resetSpawnIndex();
    resetCodexTurnState(codexTurnRef.current);
  }, []);

  const clearCompletionTimer = useCallback(() => {
    if (completionTimerRef.current !== null) {
      clearTimeout(completionTimerRef.current);
      completionTimerRef.current = null;
    }
  }, []);

  const scheduleCompletionIdle = useCallback(() => {
    if (completionTimerRef.current !== null) return;
    completionTimerRef.current = setTimeout(() => {
      completionTimerRef.current = null;
      if (useGameStore.getState().boss.backendState === "completed") {
        useGameStore.getState().updateBossBackendState("idle");
        useGameStore.getState().setBossTyping(false);
        useGameStore.getState().setBossVisualActivity(null);
        useGameStore.getState().clearBubbles("boss");
      }
    }, 1500);
  }, []);

  // ---- Message dispatch ----
  const handleMessage = useCallback(
    (event: MessageEvent) => {
      try {
        const message: WebSocketMessage = JSON.parse(event.data);

        // Validate session id for messages that include it
        // (session_deleted and reload are global).
        if (
          message.type !== "session_deleted" &&
          message.type !== "reload" &&
          message.state?.sessionId &&
          message.state.sessionId !== currentSessionIdRef.current
        ) {
          return;
        }

        switch (message.type) {
          case "state_update":
            if (message.state) {
              const isStaleCodexSnapshot =
                isCodexSource(message.state.boss.source) &&
                isStaleCodexBossSnapshot(
                  codexTurnRef.current,
                  message.state.boss.state,
                );
              if (isStaleCodexSnapshot) {
                const localBossState = useGameStore.getState().boss.backendState;
                const preservedBossState =
                  localBossState === "completed" ? "completed" : "idle";
                reconcileState(
                  {
                    ...message.state,
                    boss: {
                      ...message.state.boss,
                      state: preservedBossState,
                      bubble: null,
                    },
                  },
                  {
                    currentSessionId: currentSessionIdRef.current,
                    processedAgents: processedAgentsRef.current,
                    lastSeenBubbleText: lastSeenBubbleTextRef.current,
                    initialQueueSyncDone: initialQueueSyncDoneRef,
                  },
                );
                useGameStore.getState().setBossTyping(false);
                useGameStore.getState().setBossVisualActivity(null);
                useGameStore.getState().clearBubbles("boss");
                lastSeenBubbleTextRef.current.delete("boss");
                break;
              }

              const isCompletedSnapshot = message.state.boss.state === "completed";
              const localBossState = useGameStore.getState().boss.backendState;
              const isExpiredCompletedSnapshot =
                isCompletedSnapshot &&
                localBossState === "idle" &&
                completionTimerRef.current === null;

              // The backend intentionally keeps ``completed`` until
              // SessionEnd so the session remains visible. Once the local
              // short Done animation has expired, do not rehydrate that
              // historical terminal state on every snapshot.
              if (isExpiredCompletedSnapshot) {
                // Keep the locally expired presentation idle, but still
                // hydrate names, agents, queues, and task metadata from the
                // snapshot. Dropping the whole frame leaves a newly opened
                // tab permanently empty for a completed turn.
                reconcileState(
                  {
                    ...message.state,
                    boss: { ...message.state.boss, state: "idle" },
                  },
                  {
                    currentSessionId: currentSessionIdRef.current,
                    processedAgents: processedAgentsRef.current,
                    lastSeenBubbleText: lastSeenBubbleTextRef.current,
                    initialQueueSyncDone: initialQueueSyncDoneRef,
                  },
                );
                if (isCodexSource(message.state.boss.source)) {
                  useGameStore.getState().setBossTyping(false);
                  useGameStore.getState().setBossVisualActivity(null);
                  useGameStore.getState().clearBubbles("boss");
                  lastSeenBubbleTextRef.current.delete("boss");
                }
                break;
              }

              if (isCompletedSnapshot) {
                // Some transports can deliver the state snapshot without the
                // corresponding event message. Keep the same short Done
                // display in that path too.
                scheduleCompletionIdle();
              } else {
                clearCompletionTimer();
              }
              reconcileState(message.state, {
                currentSessionId: currentSessionIdRef.current,
                processedAgents: processedAgentsRef.current,
                lastSeenBubbleText: lastSeenBubbleTextRef.current,
                initialQueueSyncDone: initialQueueSyncDoneRef,
              });
              if (isCompletedSnapshot && isCodexSource(message.state.boss.source)) {
                useGameStore.getState().setBossTyping(false);
                useGameStore.getState().setBossVisualActivity(null);
                useGameStore.getState().clearBubbles("boss");
                lastSeenBubbleTextRef.current.delete("boss");
              }
            }
            break;

          case "event":
            if (message.event) {
              const eventSource = message.event.detail?.source;
              const isCodexEvent = isCodexSource(eventSource);
              const eventTurnId = message.event.detail?.turnId ?? null;
              const acceptsCodexEvent =
                !isCodexEvent ||
                acceptCodexEvent(
                  codexTurnRef.current,
                  message.event.type,
                  eventTurnId,
                );
              addEventLog(message.event);

              // Clear processed agents on session_start to allow re-detection.
              // Needed when simulation re-runs with the same session id and agent ids.
              if (message.event.type === "session_start") {
                clearCompletionTimer();
                processedAgentsRef.current.clear();
                lastSeenBubbleTextRef.current.clear();
                resetSpawnIndex();
                resetCodexTurnState(codexTurnRef.current);
              }

              if (
                message.event.type === "user_prompt_submit" &&
                isCodexEvent &&
                acceptsCodexEvent
              ) {
                beginCodexTurn(codexTurnRef.current, eventTurnId);
                clearCompletionTimer();
                useGameStore.getState().updateBossBackendState("thinking");
                useGameStore.getState().setBossTyping(false);
                useGameStore.getState().setBossVisualActivity(null);
                useGameStore.getState().clearBubbles("boss");
                lastSeenBubbleTextRef.current.delete("boss");
              }

              const isTurnLifecycleEvent =
                message.event.type === "user_prompt_submit" ||
                message.event.type === "pre_tool_use" ||
                message.event.type === "post_tool_use" ||
                message.event.type === "session_end";
              if (isTurnLifecycleEvent && acceptsCodexEvent) {
                clearCompletionTimer();
              }

              if (message.event.type === "session_end") {
                resetCodexTurnState(codexTurnRef.current);
              }

              // Stop is a turn boundary, not a session boundary. Keep a
              // visible Done state briefly, then return only the frontend
              // display to idle while preserving the Main/session.
              if (
                message.event.type === "stop" &&
                isCodexEvent &&
                acceptsCodexEvent
              ) {
                stopCodexTurn(codexTurnRef.current, eventTurnId);
                clearCompletionTimer();
                typingTrackerRef.current?.stopImmediately("boss");
                useGameStore.getState().setBossTyping(false);
                useGameStore.getState().setBossVisualActivity(null);
                useGameStore.getState().clearBubbles("boss");
                lastSeenBubbleTextRef.current.delete("boss");
                useGameStore.getState().updateBossBackendState("completed");
                scheduleCompletionIdle();
              }

              // Toggle typing animation on tool-use events (min-duration enforced
              // by TypingTracker).
              if (
                acceptsCodexEvent &&
                (message.event.type === "pre_tool_use" ||
                  message.event.type === "post_tool_use")
              ) {
                const agentId = message.event.agentId;
                const typingKey = agentId || "boss";
                if (isCodexAgentWait(message.event.detail)) {
                  typingTrackerRef.current?.stopImmediately(typingKey);
                  if (
                    shouldEnterCodexWaitState(
                      message.event.type,
                      message.event.detail,
                    )
                  ) {
                    if (agentId && agentId !== "main" && agentId !== "boss") {
                      useGameStore
                        .getState()
                        .updateAgentBackendState(agentId, "waiting");
                    } else {
                      useGameStore
                        .getState()
                        .updateBossBackendState("reviewing");
                    }
                  }
                } else if (message.event.type === "pre_tool_use") {
                  typingTrackerRef.current?.onPreToolUse(typingKey);
                } else {
                  typingTrackerRef.current?.onPostToolUse(typingKey);
                }
              }

              // Trigger compaction animation on context_compaction event.
              if (message.event.type === "context_compaction") {
                useGameStore.getState().triggerCompaction();
              }

              // Attention toasts — wire event processing into attention store.
              // `shouldShowToast` (pure) owns the type + preference gate.
              if (
                acceptsCodexEvent &&
                shouldShowToast(
                  message.event.type as EventType,
                  usePreferencesStore.getState(),
                )
              ) {
                useAttentionStore.getState().processEvent({
                  sessionId: currentSessionIdRef.current,
                  eventId: message.event.id,
                  type: message.event.type as EventType,
                  agentId: message.event.agentId ?? null,
                  agentName: message.event.detail?.agentName ?? null,
                  taskDescription:
                    message.event.detail?.taskDescription ?? null,
                  errorType: message.event.detail?.errorType ?? null,
                  message: message.event.detail?.message ?? null,
                  turnId: eventTurnId,
                });
              }
            }
            break;

          case "git_status":
            if (message.gitStatus) {
              setGitStatus(message.gitStatus);
            }
            break;

          case "reload":
            window.location.reload();
            break;

          case "session_deleted":
            // Session was deleted (possibly by another client).
            // Emit custom event for session list components to refetch.
            window.dispatchEvent(
              new CustomEvent("session-deleted", {
                detail: { sessionId: message.session_id },
              }),
            );
            break;

          case "error":
            useAttentionStore.getState().processEvent({
              sessionId: currentSessionIdRef.current,
              eventId: `transport:${message.timestamp}`,
              type: "error",
              agentId: null,
              agentName: null,
              taskDescription: null,
              errorType: null,
              message: message.message ?? null,
            });
            break;
        }
      } catch (error) {
        console.error("[WS] Failed to parse message:", error);
      }
    },
    [
      addEventLog,
      clearCompletionTimer,
      scheduleCompletionIdle,
      setGitStatus,
    ],
  );

  // ---- WebSocket transport controller (created once, opts synced each render) ----
  const controllerRef = useRef<WebSocketController | null>(null);
  if (controllerRef.current === null) {
    const baseUrl =
      process.env.NEXT_PUBLIC_WS_URL ||
      (typeof window !== "undefined"
        ? `ws://${window.location.hostname}:8000`
        : "ws://localhost:8000");
    controllerRef.current = new WebSocketController({
      sessionId,
      enabled,
      baseUrl,
      onMessage: handleMessage,
      onReconnectReset: handleReconnectReset,
      setConnected,
      setSessionId,
      isReplaying: () => useGameStore.getState().isReplaying,
      isCurrentSession: (id) => id === currentSessionIdRef.current,
    });
  }
  // Keep mutable opts fresh so the controller always closes over current state
  // without re-creating the instance (and churning the socket).
  controllerRef.current.opts.sessionId = sessionId;
  controllerRef.current.opts.enabled = enabled;
  controllerRef.current.opts.onMessage = handleMessage;
  controllerRef.current.opts.onReconnectReset = handleReconnectReset;

  // ---- Connection lifecycle ----
  useEffect(() => {
    const isReplaying = useGameStore.getState().isReplaying;
    if (!enabled || !sessionId || isReplaying) {
      controllerRef.current?.disconnect();
      return;
    }

    controllerRef.current?.connect();

    // A session is created by session_start, so the socket cannot subscribe
    // early enough to receive that first event. Hydrate the Event Log from the
    // replay endpoint while retaining any live events that arrive meanwhile.
    const abortController = new AbortController();
    void apiFetch(
      `/api/v1/sessions/${encodeURIComponent(sessionId)}/replay`,
      { signal: abortController.signal },
    )
      .then((response) => (response.ok ? response.json() : null))
      .then((frames: ReplayFrame[] | null) => {
        if (
          !frames ||
          !Array.isArray(frames) ||
          currentSessionIdRef.current !== sessionId
        ) {
          return;
        }
        hydrateEventLog(frames.map((frame) => frame.event));
      })
      .catch((error: unknown) => {
        if (!(error instanceof DOMException && error.name === "AbortError")) {
          console.error("[WS] Failed to hydrate Event Log:", error);
        }
      });

    return () => {
      abortController.abort();
      controllerRef.current?.disconnect();
      typingTrackerRef.current?.clear();
      clearCompletionTimer();
    };
  }, [sessionId, enabled, hydrateEventLog, clearCompletionTimer]);
}

// ============================================================================
// FULL RESET HANDLER
// ============================================================================

/**
 * Perform a full reset of frontend state.
 * Called on reconnection or when switching sessions.
 */
export function resetFrontendState(): void {
  // Reset store (use resetForSessionSwitch to allow WebSocket reconnection).
  useGameStore.getState().resetForSessionSwitch();

  // Reset machine service.
  agentMachineService.reset();

  // Reset spawn positions.
  resetSpawnIndex();
}
