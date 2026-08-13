import { describe, expect, it } from "vitest";
import {
  getDisplayAgentStatus,
  getMainAgentName,
} from "../src/utils/displayLabels";

describe("display labels", () => {
  it("collapses backend and animation state with the required priority", () => {
    expect(getDisplayAgentStatus("error", "departing")).toBe("error");
    expect(getDisplayAgentStatus("waiting", "departing")).toBe("departing");
    expect(getDisplayAgentStatus("working", "walking_to_desk")).toBe("walking");
    expect(getDisplayAgentStatus("working", "idle")).toBe("working");
    expect(getDisplayAgentStatus("thinking", "idle")).toBe("thinking");
    expect(getDisplayAgentStatus("receiving", "idle")).toBe("thinking");
    expect(getDisplayAgentStatus("preparing", "idle")).toBe("preparing");
    expect(getDisplayAgentStatus("reviewing", "idle")).toBe("reviewing");
    expect(getDisplayAgentStatus("waiting_permission", "idle")).toBe("waiting");
    expect(getDisplayAgentStatus("completed", "idle")).toBe("completed");
    expect(getDisplayAgentStatus("error", "idle")).toBe("error");
    expect(getDisplayAgentStatus("delegating", "idle")).toBe("working");
    expect(getDisplayAgentStatus("completing", "idle")).toBe("working");
    expect(getDisplayAgentStatus("on_phone", "idle")).toBe("working");
    expect(getDisplayAgentStatus("phone_ringing", "idle")).toBe("waiting");
  });

  it("uses source names and allows a custom main-agent name", () => {
    expect(getMainAgentName("codex")).toBe("Codex Main");
    expect(getMainAgentName("claude")).toBe("Claude Main");
    expect(getMainAgentName("opencode")).toBe("OpenCode Main");
    expect(getMainAgentName("unknown")).toBe("AI Main");
    expect(getMainAgentName("codex", "My Main Agent")).toBe("My Main Agent");
  });
});
