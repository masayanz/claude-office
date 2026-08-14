import { describe, expect, it } from "vitest";
import {
  reserveDistinctTarget,
  VisualActivityScheduler,
  type VisualActivityInput,
} from "@/systems/visualActivityScheduler";

function input(overrides: Partial<VisualActivityInput> = {}): VisualActivityInput {
  return {
    id: "agent-1",
    entityType: "agent",
    logicalState: "thinking",
    phase: "idle",
    desk: 1,
    currentPosition: { x: 256, y: 432 },
    currentActivity: null,
    others: [],
    ...overrides,
  };
}

describe("VisualActivityScheduler", () => {
  it("keeps the same seeded sequence for the same session and agent", () => {
    const first = new VisualActivityScheduler();
    const second = new VisualActivityScheduler();
    first.setSessionSeed("session-42");
    second.setSessionSeed("session-42");
    const a = first.update(input(), 0);
    const b = second.update(input(), 0);
    expect(a).toEqual(b);
  });

  it("interrupts an ambient action when logical state changes", () => {
    const scheduler = new VisualActivityScheduler();
    scheduler.setSessionSeed("session");
    const thinking = scheduler.update(input(), 0);
    const stable = scheduler.update(input({ currentActivity: thinking }), 100);
    const working = scheduler.update(
      input({ logicalState: "working", currentActivity: stable }),
      100,
    );
    expect(stable).toBe(thinking);
    expect(["typing", "monitor", "document"]).toContain(working?.kind);
  });

  it("does not take a break before the per-agent cooldown", () => {
    const scheduler = new VisualActivityScheduler();
    scheduler.setSessionSeed("session");
    const initial = scheduler.update(input({ logicalState: "waiting" }), 0);
    const beforeCooldown = scheduler.update(
      input({ logicalState: "waiting", currentActivity: initial }),
      29_999,
    );
    const afterCooldown = scheduler.update(
      input({ logicalState: "waiting", currentActivity: beforeCooldown }),
      60_001,
    );
    expect(initial?.kind).toBe("desk_idle");
    expect(beforeCooldown?.kind).toBe("desk_idle");
    expect(["coffee", "window", "stretch"]).toContain(afterCooldown?.kind);
  });

  it("reserves a nearby alternative when a hotspot is occupied", () => {
    const random = () => 0.5;
    const target = reserveDistinctTarget(
      { x: 100, y: 100 },
      [{ x: 100, y: 100 }],
      random,
    );
    expect(target).not.toEqual({ x: 100, y: 100 });
  });
});
