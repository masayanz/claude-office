/**
 * Queue slice (ARC-005).
 *
 * Arrival/departure queue ID arrays and their mutators. Several actions also
 * re-index the queued agents' `queueIndex`/`queueType` fields (cross-slice
 * writes via the shared `set`). The enqueue/dequeue pairs share one helper
 * each, parameterized by queueType (QA-003); dequeue uses `get()` to read then
 * atomically write so subscribers never observe a shifted queue with stale
 * indices (QA-006).
 */
import type { StateCreator } from "zustand";
import type { GameStore } from "../gameStore";

export type QueueSlice = {
  arrivalQueue: string[];
  departureQueue: string[];
  enqueueArrival: (agentId: string) => void;
  enqueueDeparture: (agentId: string) => void;
  dequeueArrival: () => string | undefined;
  dequeueDeparture: () => string | undefined;
  advanceQueue: (queueType: "arrival" | "departure") => void;
  syncQueues: (arrivalQueue: string[], departureQueue: string[]) => void;
};

export const initialQueueState = {
  arrivalQueue: [] as string[],
  departureQueue: [] as string[],
};

export const createQueueSlice: StateCreator<GameStore, [], [], QueueSlice> = (
  set,
  get,
) => {
  // QA-003: one helper for the enqueueArrival/enqueueDeparture duplicate pair.
  // Appends the agent (no-op if already queued) and stamps their queueType/
  // queueIndex in the same update.
  const enqueueAgent = (
    agentId: string,
    queueType: "arrival" | "departure",
  ): void =>
    set((state) => {
      const isArrival = queueType === "arrival";
      const queue = isArrival ? state.arrivalQueue : state.departureQueue;
      if (queue.includes(agentId)) return state;

      const newQueue = [...queue, agentId];
      const queueIndex = newQueue.length - 1;

      const agent = state.agents.get(agentId);
      if (agent) {
        const newAgents = new Map(state.agents);
        newAgents.set(agentId, { ...agent, queueType, queueIndex });
        return isArrival
          ? { arrivalQueue: newQueue, agents: newAgents }
          : { departureQueue: newQueue, agents: newAgents };
      }

      return isArrival
        ? { arrivalQueue: newQueue }
        : { departureQueue: newQueue };
    });

  // QA-003: one helper for the dequeueArrival/dequeueDeparture duplicate pair.
  // Pops the front and re-indexes the remainder atomically (QA-006: a single
  // set() so subscribers never see a shifted queue with stale queueIndex).
  const dequeueAgent = (
    queueType: "arrival" | "departure",
  ): string | undefined => {
    const state = get();
    const isArrival = queueType === "arrival";
    const queue = isArrival ? state.arrivalQueue : state.departureQueue;
    if (queue.length === 0) return undefined;

    const [frontId, ...rest] = queue;

    const newAgents = new Map(state.agents);
    rest.forEach((id, idx) => {
      const agent = newAgents.get(id);
      if (agent) {
        newAgents.set(id, { ...agent, queueIndex: idx });
      }
    });
    set(
      isArrival
        ? { arrivalQueue: rest, agents: newAgents }
        : { departureQueue: rest, agents: newAgents },
    );

    return frontId;
  };

  return {
    ...initialQueueState,

    enqueueArrival: (agentId) => enqueueAgent(agentId, "arrival"),
    enqueueDeparture: (agentId) => enqueueAgent(agentId, "departure"),

    dequeueArrival: () => dequeueAgent("arrival"),
    dequeueDeparture: () => dequeueAgent("departure"),

    advanceQueue: (queueType) =>
      set((state) => {
        const queue =
          queueType === "arrival" ? state.arrivalQueue : state.departureQueue;
        if (queue.length === 0) return state;

        // Update all agents' queue indices
        const newAgents = new Map(state.agents);
        queue.forEach((id, idx) => {
          const agent = newAgents.get(id);
          if (agent) {
            newAgents.set(id, { ...agent, queueIndex: idx });
          }
        });

        return { agents: newAgents };
      }),

    syncQueues: (arrivalQueue, departureQueue) =>
      set((state) => {
        // Update agents' queue info based on synced queues
        const newAgents = new Map(state.agents);

        arrivalQueue.forEach((id, idx) => {
          const agent = newAgents.get(id);
          if (agent) {
            newAgents.set(id, {
              ...agent,
              queueType: "arrival",
              queueIndex: idx,
            });
          }
        });

        departureQueue.forEach((id, idx) => {
          const agent = newAgents.get(id);
          if (agent) {
            newAgents.set(id, {
              ...agent,
              queueType: "departure",
              queueIndex: idx,
            });
          }
        });

        return {
          arrivalQueue,
          departureQueue,
          agents: newAgents,
        };
      }),
  };
};
