/**
 * Agent slice (ARC-005).
 *
 * The frontend-owned agent animation map and its mutators. `addAgent`
 * constructs an AgentAnimationState from a backend agent; `removeAgent` also
 * prunes the arrival/departure queues (cross-slice writes via the shared
 * `set`). The scalar-field mutators all funnel through one `patchAgent`
 * helper (QA-003) instead of repeating the clone-guard-set boilerplate.
 */
import type { StateCreator } from "zustand";
import type { GameStore } from "../gameStore";
import type {
  Position,
  AgentState as BackendAgentState,
  Agent as BackendAgent,
} from "@/types";
import type {
  AgentPhase,
  PathState,
  AgentAnimationState,
  AgentMovement,
} from "./types";
import { DESKS_PER_ROW } from "@/constants/positions";
import { createEmptyBubbleState } from "./shared";

export type AgentSlice = {
  agents: Map<string, AgentAnimationState>;
  addAgent: (backendAgent: BackendAgent, initialPosition: Position) => void;
  removeAgent: (agentId: string) => void;
  updateAgentPhase: (agentId: string, phase: AgentPhase) => void;
  updateAgentPosition: (agentId: string, position: Position) => void;
  updateAgentTarget: (agentId: string, target: Position) => void;
  updateAgentPath: (agentId: string, path: PathState | null) => void;
  updateAgentBackendState: (agentId: string, state: BackendAgentState) => void;
  updateAgentMeta: (
    agentId: string,
    meta: {
      backendState: BackendAgentState;
      name: string | null;
      currentTask: string | null;
    },
  ) => void;
  updateAgentQueueInfo: (
    agentId: string,
    queueType: "arrival" | "departure" | null,
    queueIndex: number,
  ) => void;
  setAgentTyping: (agentId: string, typing: boolean) => void;
  applyAgentMovements: (movements: AgentMovement[]) => void;
};

export const initialAgentState = {
  agents: new Map<string, AgentAnimationState>(),
};

export const createAgentSlice: StateCreator<GameStore, [], [], AgentSlice> = (
  set,
) => {
  // QA-003: one clone-guard-set helper for every scalar-field mutator. Accepts
  // a static patch object, or a function of the current agent when the patch
  // depends on existing fields (e.g. updateAgentMeta's `??` fallbacks). No-op
  // if the agent is absent.
  const patchAgent = (
    agentId: string,
    patch:
      | Partial<AgentAnimationState>
      | ((agent: AgentAnimationState) => Partial<AgentAnimationState>),
  ): void =>
    set((state) => {
      const agent = state.agents.get(agentId);
      if (!agent) return state;
      const delta = typeof patch === "function" ? patch(agent) : patch;
      const newAgents = new Map(state.agents);
      newAgents.set(agentId, { ...agent, ...delta });
      return { agents: newAgents };
    });

  return {
    ...initialAgentState,

    addAgent: (backendAgent, initialPosition) =>
      set((state) => {
        const newAgents = new Map(state.agents);
        const animState: AgentAnimationState = {
          id: backendAgent.id,
          name: backendAgent.name ?? null,
          color: backendAgent.color,
          number: backendAgent.number,
          desk: backendAgent.desk ?? null,
          backendState: backendAgent.state,
          currentTask: backendAgent.currentTask ?? null,
          characterType: backendAgent.characterType ?? null,
          parentSessionId: backendAgent.parentSessionId ?? null,
          parentId: backendAgent.parentId ?? null,
          phase: "arriving",
          currentPosition: initialPosition,
          targetPosition: initialPosition,
          path: null,
          bubble: createEmptyBubbleState(),
          queueType: null,
          queueIndex: -1,
          isTyping: false,
        };
        newAgents.set(backendAgent.id, animState);

        // Update desk count if needed
        const newDeskCount = Math.max(
          state.deskCount,
          Math.ceil((newAgents.size + 1) / DESKS_PER_ROW) * DESKS_PER_ROW,
        );

        return { agents: newAgents, deskCount: newDeskCount };
      }),

    removeAgent: (agentId) =>
      set((state) => {
        const newAgents = new Map(state.agents);
        newAgents.delete(agentId);

        // Also remove from queues
        const newArrivalQueue = state.arrivalQueue.filter(
          (id) => id !== agentId,
        );
        const newDepartureQueue = state.departureQueue.filter(
          (id) => id !== agentId,
        );

        return {
          agents: newAgents,
          arrivalQueue: newArrivalQueue,
          departureQueue: newDepartureQueue,
        };
      }),

    updateAgentPhase: (agentId, phase) => patchAgent(agentId, { phase }),

    updateAgentPosition: (agentId, position) =>
      patchAgent(agentId, { currentPosition: position }),

    updateAgentTarget: (agentId, target) =>
      patchAgent(agentId, { targetPosition: target }),

    updateAgentPath: (agentId, path) => patchAgent(agentId, { path }),

    updateAgentBackendState: (agentId, backendState) =>
      patchAgent(agentId, { backendState }),

    updateAgentMeta: (agentId, meta) =>
      patchAgent(agentId, (agent) => ({
        backendState: meta.backendState,
        name: meta.name ?? agent.name,
        // `??` (not `||`) so an explicit empty-string currentTask clears the
        // previous task — only null/undefined fall back. See QA-012.
        currentTask: meta.currentTask ?? agent.currentTask,
      })),

    updateAgentQueueInfo: (agentId, queueType, queueIndex) =>
      patchAgent(agentId, { queueType, queueIndex }),

    setAgentTyping: (agentId, isTyping) => patchAgent(agentId, { isTyping }),

    // ARC-006: apply every moving agent's position/path delta for one animation
    // tick in a single `set()` (one Map clone), instead of one write — and one
    // clone — per agent per frame. `path` omitted => unchanged; null => cleared.
    applyAgentMovements: (movements) =>
      set((state) => {
        if (movements.length === 0) return state;
        const newAgents = new Map(state.agents);
        for (const { agentId, position, path } of movements) {
          const agent = newAgents.get(agentId);
          if (!agent) continue;
          newAgents.set(
            agentId,
            path === undefined
              ? { ...agent, currentPosition: position }
              : { ...agent, currentPosition: position, path },
          );
        }
        return { agents: newAgents };
      }),
  };
};
