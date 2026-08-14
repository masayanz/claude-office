import { describe, expect, it } from "vitest";
import {
  REPLAY_COMPLETED_DISPLAY_MS,
  ReplayController,
  replayIdleState,
} from "./replayController";
import type { ReplayFrame } from "@/stores/gameStore";

function frame(timestamp: string, id: string, type = "session_start"): ReplayFrame {
  return {
    event: {
      id,
      type: type as ReplayFrame["event"]["type"],
      agentId: "main",
      summary: "Session started",
      timestamp,
      detail: {},
    },
    state: {
      boss: { state: type === "stop" ? "completed" : "thinking" },
    } as ReplayFrame["state"],
  };
}

describe("ReplayController", () => {
  it("compresses long idle gaps and seeks to the correct frame", () => {
    const frames = [
      frame("2026-01-01T00:00:00.000Z", "1"),
      frame("2026-01-01T00:01:00.000Z", "2"),
      frame("2026-01-01T00:01:05.000Z", "3"),
    ];
    const seen: number[] = [];
    const controller = new ReplayController({
      compressIdle: true,
      onFrame: (_frame, index) => seen.push(index),
    });

    controller.setFrames(frames);
    expect(controller.getSnapshot().durationMs).toBe(10_000);

    controller.seek(5_000);
    expect(controller.getSnapshot().currentIndex).toBe(1);
    expect(controller.getCurrentFrame()?.event.id).toBe("2");

    controller.seek(0);
    expect(controller.getSnapshot().currentIndex).toBe(0);
    controller.step();
    expect(controller.getCurrentFrame()?.event.id).toBe("2");
    expect(seen).toContain(1);
  });

  it("supports the configured playback speeds", () => {
    const controller = new ReplayController({ onFrame: () => undefined });
    controller.setFrames([frame("2026-01-01T00:00:00.000Z", "1")]);
    controller.setSpeed(4);
    expect(controller.getSpeed()).toBe(4);
    controller.setSpeed(3);
    expect(controller.getSpeed()).toBe(4);
  });

  it("appends prefetched chunks without resetting the clock", () => {
    const seen: number[] = [];
    const controller = new ReplayController({
      onFrame: (_frame, index) => seen.push(index),
    });
    controller.setFrames([frame("2026-01-01T00:00:00.000Z", "1")]);
    controller.seek(0);
    controller.appendFrames([
      frame("2026-01-01T00:00:01.000Z", "2"),
      frame("2026-01-01T00:00:02.000Z", "3"),
    ]);

    expect(controller.getSnapshot().durationMs).toBe(2_000);
    expect(controller.getSnapshot().currentIndex).toBe(0);
    controller.seek(1_500);
    expect(controller.getCurrentFrame()?.event.id).toBe("2");
    expect(seen).toContain(0);
  });

  it("derives completed then idle after the final Stop without adding an event", () => {
    const presentations: string[] = [];
    const controller = new ReplayController({
      onFrame: (_frame, _index, presentation) => presentations.push(presentation),
    });
    controller.setFrames([frame("2026-01-01T00:00:00.000Z", "stop", "stop")]);

    controller.seek(500);
    expect(controller.getSnapshot().presentation).toBe("event");
    controller.seek(2_000);
    expect(controller.getSnapshot().presentation).toBe("idle");
    expect(controller.getCurrentFrame()?.event.id).toBe("stop");
    expect(controller.getSnapshot().durationMs).toBe(REPLAY_COMPLETED_DISPLAY_MS);
    expect(presentations.at(-1)).toBe("idle");
  });

  it("goes directly from completed to the next turn when it starts early", () => {
    const controller = new ReplayController({ onFrame: () => undefined });
    controller.setFrames([
      frame("2026-01-01T00:00:00.000Z", "stop", "stop"),
      frame("2026-01-01T00:00:00.800Z", "prompt", "user_prompt_submit"),
    ]);

    controller.seek(500);
    expect(controller.getSnapshot().presentation).toBe("event");
    controller.seek(800);
    expect(controller.getCurrentFrame()?.event.id).toBe("prompt");
    expect(controller.getSnapshot().presentation).toBe("event");
  });

  it("keeps the derived state safe and transitions the Main to idle", () => {
    const state = frame("2026-01-01T00:00:00.000Z", "stop", "stop").state;
    const idle = replayIdleState(state);
    expect(idle.boss.state).toBe("idle");
    expect(idle.boss.currentTask).toBeNull();
  });

  it("uses playback speed to scale the completed display duration", () => {
    const controller = new ReplayController({ onFrame: () => undefined });
    controller.setFrames([frame("2026-01-01T00:00:00.000Z", "stop", "stop")]);
    controller.setSpeed(2);
    expect(controller.getSnapshot().durationMs / controller.getSpeed()).toBe(750);
    controller.setSpeed(4);
    expect(controller.getSnapshot().durationMs / controller.getSpeed()).toBe(375);
    controller.setSpeed(8);
    expect(controller.getSnapshot().durationMs / controller.getSpeed()).toBe(187.5);
  });
});
