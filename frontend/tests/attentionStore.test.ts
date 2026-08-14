import { beforeEach, describe, expect, it } from "vitest";
import { useAttentionStore } from "@/stores/attentionStore";

const baseEvent = {
  sessionId: "session-1",
  eventId: "event-1",
  type: "error" as const,
  agentId: "main",
  agentName: null,
  taskDescription: null,
  errorType: "test",
  message: null,
};

describe("attention toast lifecycle", () => {
  beforeEach(() => {
    useAttentionStore.setState({ toastQueue: [] });
  });

  it("deduplicates the same session/event notification", () => {
    const processEvent = useAttentionStore.getState().processEvent;
    processEvent(baseEvent);
    processEvent(baseEvent);

    expect(useAttentionStore.getState().toastQueue).toHaveLength(1);
    expect(useAttentionStore.getState().toastQueue[0]?.id).toBe(
      "toast:session-1:error:main:event-1",
    );
  });

  it("does not create a Stop toast", () => {
    useAttentionStore.getState().processEvent({
      ...baseEvent,
      eventId: "stop-1",
      type: "stop",
    });

    expect(useAttentionStore.getState().toastQueue).toHaveLength(0);
  });

  it("keeps at most three active notifications", () => {
    const processEvent = useAttentionStore.getState().processEvent;
    for (let index = 0; index < 5; index += 1) {
      processEvent({
        ...baseEvent,
        eventId: `event-${index}`,
      });
    }

    const active = useAttentionStore
      .getState()
      .toastQueue.filter((toast) => !toast.dismissed);
    expect(active).toHaveLength(3);
  });

  it("uses bounded automatic dismissal durations", () => {
    const processEvent = useAttentionStore.getState().processEvent;
    processEvent({ ...baseEvent, type: "permission_request" });
    processEvent({ ...baseEvent, eventId: "task-1", type: "task_completed" });
    processEvent({ ...baseEvent, eventId: "agent-1", type: "subagent_start" });

    const durations = useAttentionStore
      .getState()
      .toastQueue.map((toast) => toast.autoDismissMs);
    expect(durations).toContain(8000);
    expect(durations).toContain(5000);
    expect(durations).toContain(3000);
  });
});
