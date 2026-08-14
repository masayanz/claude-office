/**
 * Agent Machine — Shared types, actions, guards, and delays
 *
 * This module contains everything shared between the arrival and departure
 * sub-machines: context shape, event union, the external action interface,
 * and the reusable action/guard/delay implementations.
 */

import type { Position } from "@/types";
import type { TranslationKey } from "@/i18n";

// ============================================================================
// TYPES
// ============================================================================

export interface AgentMachineContext {
  agentId: string;
  agentName: string | null;
  desk: number | null;
  queueType: "arrival" | "departure" | null;
  queueIndex: number;
  currentPosition: Position;
  targetPosition: Position;
  conversationStep: number;
}

export type AgentMachineEvent =
  | {
      type: "SPAWN";
      agentId: string;
      name: string | null;
      desk: number | null;
      position: Position;
    }
  | {
      type: "SPAWN_AT_DESK";
      agentId: string;
      name: string | null;
      desk: number | null;
      position: Position;
    }
  | {
      type: "SPAWN_IN_ARRIVAL_QUEUE";
      agentId: string;
      name: string | null;
      desk: number | null;
      position: Position;
      queueIndex: number;
    }
  | {
      type: "SPAWN_IN_DEPARTURE_QUEUE";
      agentId: string;
      name: string | null;
      desk: number | null;
      position: Position;
      queueIndex: number;
    }
  | { type: "REMOVE" }
  | { type: "ARRIVED_AT_QUEUE" }
  | { type: "QUEUE_POSITION_CHANGED"; newIndex: number }
  | { type: "BOSS_AVAILABLE" }
  | { type: "ARRIVED_AT_READY" }
  | { type: "BUBBLE_DISPLAYED" }
  | { type: "CONVERSATION_COMPLETE" }
  | { type: "ARRIVED_AT_BOSS" }
  | { type: "BOSS_TIMEOUT" }
  | { type: "ARRIVED_AT_DESK" }
  | { type: "ARRIVED_AT_ELEVATOR" }
  | { type: "ELEVATOR_TIMEOUT" }
  | { type: "ELEVATOR_DOOR_CLOSING" };

// ============================================================================
// EXTERNAL ACTION INTERFACE
// ============================================================================

/**
 * External action handlers that the machine will call.
 * These are injected when spawning the machine.
 */
export interface AgentMachineActions {
  onStartWalking: (
    agentId: string,
    target: Position,
    movementType: string,
  ) => void;
  onQueueJoined: (
    agentId: string,
    queueType: "arrival" | "departure",
    index: number,
  ) => void;
  onQueueLeft: (agentId: string) => void;
  onPhaseChanged: (agentId: string, phase: string) => void;
  onShowBossBubble: (message: CharacterMessage, icon?: string) => void;
  onShowAgentBubble: (
    agentId: string,
    message: CharacterMessage,
    icon?: string,
  ) => void;
  onClearBossBubble: () => void;
  onClearAgentBubble: (agentId: string) => void;
  onSetBossInUse: (by: "arrival" | "departure" | null) => void;
  onOpenElevator: () => void;
  onCloseElevator: () => void;
  onAgentRemoved: (agentId: string) => void;
}

export interface CharacterMessage {
  key: Extract<TranslationKey, `agent.message.${string}`>;
  params?: Record<string, string | number>;
}

/** Compatibility helper retained for callers that only need a farewell key. */
export function getRandomFarewell(): string {
  return "agent.message.farewell";
}

// ============================================================================
// SHARED ACTION FACTORIES
// ============================================================================

/**
 * Build the shared action map used by both arrival and departure machines.
 * The caller injects the external `actions` object at machine creation time.
 */
export function buildSharedActions(actions: AgentMachineActions) {
  return {
    // Phase notifications
    notifyPhaseChange: (
      { context }: { context: AgentMachineContext },
      params: { phase: string },
    ) => {
      actions.onPhaseChanged(context.agentId, params.phase);
    },

    // Walking actions
    startWalkingToQueue: ({ context }: { context: AgentMachineContext }) => {
      const queueType = context.queueType ?? "arrival";
      actions.onStartWalking(
        context.agentId,
        context.targetPosition,
        `to_${queueType}_queue`,
      );
    },
    startWalkingToReady: ({ context }: { context: AgentMachineContext }) => {
      actions.onStartWalking(
        context.agentId,
        context.targetPosition,
        "to_ready",
      );
    },
    startWalkingToBoss: ({ context }: { context: AgentMachineContext }) => {
      actions.onStartWalking(
        context.agentId,
        context.targetPosition,
        "to_boss",
      );
    },
    startWalkingToDesk: ({ context }: { context: AgentMachineContext }) => {
      actions.onStartWalking(
        context.agentId,
        context.targetPosition,
        "to_desk",
      );
    },
    startWalkingToElevator: ({ context }: { context: AgentMachineContext }) => {
      actions.onStartWalking(
        context.agentId,
        context.targetPosition,
        "to_elevator",
      );
    },

    // Queue actions
    joinQueue: ({ context }: { context: AgentMachineContext }) => {
      if (context.queueType) {
        actions.onQueueJoined(
          context.agentId,
          context.queueType,
          context.queueIndex,
        );
      }
    },
    leaveQueue: ({ context }: { context: AgentMachineContext }) => {
      actions.onQueueLeft(context.agentId);
    },

    // Arrival conversation actions
    showArrivalBossBubble: ({ context }: { context: AgentMachineContext }) => {
      actions.onShowBossBubble(
        {
          key: "agent.message.arrivalTask",
          params: { name: context.agentName ?? "Agent" },
        },
        "clipboard",
      );
    },
    showArrivalAgentBubble: ({ context }: { context: AgentMachineContext }) => {
      actions.onShowAgentBubble(
        context.agentId,
        { key: "agent.message.arrivalAccepted" },
        "thumbs-up",
      );
    },

    // Departure conversation actions
    showDepartureBossBubble: ({
      context,
    }: {
      context: AgentMachineContext;
    }) => {
      actions.onShowBossBubble(
        {
          key: "agent.message.departureHandoff",
          params: { name: context.agentName ?? "Agent" },
        },
        "check",
      );
    },
    showDepartureAgentBubble: ({
      context,
    }: {
      context: AgentMachineContext;
    }) => {
      actions.onShowAgentBubble(
        context.agentId,
        { key: "agent.message.departureCompleted" },
        "file-text",
      );
    },
    showFarewellBubble: ({ context }: { context: AgentMachineContext }) => {
      actions.onShowAgentBubble(context.agentId, {
        key: "agent.message.farewell",
      });
    },

    // Bubble lifecycle
    clearBossBubble: () => {
      actions.onClearBossBubble();
    },
    clearAgentBubble: ({ context }: { context: AgentMachineContext }) => {
      actions.onClearAgentBubble(context.agentId);
    },

    // Boss availability
    claimBoss: ({ context }: { context: AgentMachineContext }) => {
      actions.onSetBossInUse(context.queueType);
    },
    releaseBoss: () => {
      actions.onSetBossInUse(null);
    },

    // Elevator actions
    openElevator: () => {
      actions.onOpenElevator();
    },
    closeElevator: () => {
      actions.onCloseElevator();
    },

    // Removal
    removeAgent: ({ context }: { context: AgentMachineContext }) => {
      actions.onAgentRemoved(context.agentId);
    },
  };
}

/**
 * Names of actions that require `assign` and must be defined
 * inside `setup()` at the call site where XState can infer the
 * full event type.  The keys here match the names referenced in
 * state machine entry/transition arrays.
 *
 * Callers should spread `buildSharedActions(actions)` and then add
 * the assign-based actions returned by `buildAssignActions()`.
 */
export const ASSIGN_ACTION_NAMES = [
  "updateQueueIndex",
  "setQueueTypeArrival",
  "setQueueTypeDeparture",
  "clearQueueType",
  "incrementConversationStep",
  "resetConversationStep",
] as const;

// ============================================================================
// SHARED GUARDS
// ============================================================================

export const sharedGuards = {
  isAtFrontOfQueue: ({ context }: { context: AgentMachineContext }) =>
    context.queueIndex === 0,
  isArrival: ({ context }: { context: AgentMachineContext }) =>
    context.queueType === "arrival",
  isDeparture: ({ context }: { context: AgentMachineContext }) =>
    context.queueType === "departure",
};

// ============================================================================
// SHARED DELAYS
// ============================================================================

export const sharedDelays = {
  BOSS_PAUSE: 100,
  ELEVATOR_PAUSE: 500,
  DOOR_CLOSE_DELAY: 520, // Wait for door close animation (500ms) + minimal buffer
  // Maximum time to wait inside a conversing sub-state before forcibly
  // advancing. The bubble normally lives 3000ms; this is a safety net for
  // the case where the bubble is suppressed (boss completing / persistent)
  // and the BUBBLE_DISPLAYED event is never delivered.
  CONVERSATION_TIMEOUT: 5000,
} as const;

// ============================================================================
// DEFAULT CONTEXT
// ============================================================================

export const defaultAgentContext: AgentMachineContext = {
  agentId: "",
  agentName: null,
  desk: null,
  queueType: null,
  queueIndex: -1,
  currentPosition: { x: 0, y: 0 },
  targetPosition: { x: 0, y: 0 },
  conversationStep: 0,
};
