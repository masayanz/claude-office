import { describe, expect, it } from "vitest";
import {
  acceptCodexEvent,
  beginCodexTurn,
  createCodexTurnState,
  isStaleCodexBossSnapshot,
  stopCodexTurn,
} from "@/systems/codexTurnState";

describe("Codex turn boundary", () => {
  it("moves through thinking -> working -> thinking -> completed -> idle", () => {
    const state = createCodexTurnState();
    beginCodexTurn(state, "turn-1");

    expect(acceptCodexEvent(state, "pre_tool_use", "turn-1")).toBe(true);
    expect(acceptCodexEvent(state, "post_tool_use", "turn-1")).toBe(true);

    stopCodexTurn(state, "turn-1");
    expect(state.stopped).toBe(true);
    expect(isStaleCodexBossSnapshot(state, "thinking")).toBe(true);
    expect(isStaleCodexBossSnapshot(state, "working")).toBe(true);
    expect(isStaleCodexBossSnapshot(state, "completed")).toBe(false);
    expect(isStaleCodexBossSnapshot(state, "idle")).toBe(false);
  });

  it("rejects delayed same-turn tool events", () => {
    const state = createCodexTurnState();
    beginCodexTurn(state, "turn-1");
    stopCodexTurn(state, "turn-1");

    expect(acceptCodexEvent(state, "post_tool_use", "turn-1")).toBe(false);
    expect(acceptCodexEvent(state, "pre_tool_use", "turn-1")).toBe(false);
    expect(state.stopped).toBe(true);
  });

  it("opens a new turn on a new prompt or explicit turn id", () => {
    const state = createCodexTurnState();
    beginCodexTurn(state, "turn-1");
    stopCodexTurn(state, "turn-1");

    expect(acceptCodexEvent(state, "user_prompt_submit", "turn-2")).toBe(true);
    expect(state.stopped).toBe(false);

    stopCodexTurn(state, "turn-2");
    expect(acceptCodexEvent(state, "pre_tool_use", "turn-3")).toBe(true);
    expect(state.activeTurnId).toBe("turn-3");
    expect(state.stopped).toBe(false);
  });
});
