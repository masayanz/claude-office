import { describe, expect, it } from "vitest";
import { ReplayController } from "./replayController";
import type { ReplayFrame } from "@/stores/gameStore";

function frame(timestamp: string, id: string): ReplayFrame {
  return {
    event: {
      id,
      type: "session_start",
      agentId: "main",
      summary: "Session started",
      timestamp,
      detail: {},
    },
    state: {} as ReplayFrame["state"],
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
});
