import { describe, expect, it } from "vitest";
import {
  codexSecondaryLabel,
  isCodexAgentWait,
  isCodexSource,
  shouldEnterCodexWaitState,
} from "@/utils/codexPresentation";

describe("Codex presentation helpers", () => {
  it("recognizes Codex source without changing other providers", () => {
    expect(isCodexSource("codex")).toBe(true);
    expect(isCodexSource("Codex")).toBe(true);
    expect(isCodexSource("claude")).toBe(false);
    expect(isCodexSource("opencode")).toBe(false);
    expect(isCodexSource(undefined)).toBe(false);
  });

  it("recognizes only Codex AgentWait events", () => {
    expect(
      isCodexAgentWait({ source: "codex", toolName: "AgentWait" }),
    ).toBe(true);
    expect(
      isCodexAgentWait({ source: "claude", toolName: "AgentWait" }),
    ).toBe(false);
    expect(isCodexAgentWait({ source: "codex", toolName: "Bash" })).toBe(
      false,
    );
  });

  it("enters waiting state only on AgentWait start, not completion", () => {
    const detail = { source: "codex", toolName: "AgentWait" };
    expect(shouldEnterCodexWaitState("pre_tool_use", detail)).toBe(true);
    expect(shouldEnterCodexWaitState("post_tool_use", detail)).toBe(false);
    expect(
      shouldEnterCodexWaitState("pre_tool_use", {
        source: "codex",
        toolName: "Bash",
      }),
    ).toBe(false);
    expect(
      shouldEnterCodexWaitState("pre_tool_use", {
        source: "opencode",
        toolName: "AgentWait",
      }),
    ).toBe(false);
  });

  it("keeps the secondary label Codex-only and compact", () => {
    expect(codexSecondaryLabel("codex", "gpt-5.6-sol", false)).toBe(
      "gpt-5.6-sol",
    );
    expect(codexSecondaryLabel("codex", "gpt-5.6-sol", true)).toBe(
      "gpt-5.6-sol • waiting",
    );
    expect(codexSecondaryLabel("claude", "claude-opus", true)).toBeNull();
    expect(codexSecondaryLabel("opencode", "some-model", true)).toBeNull();
    expect(codexSecondaryLabel("codex", null, false)).toBeNull();
    expect(codexSecondaryLabel("codex", null, true)).toBe("waiting");
  });

  it("shows only explicit Codex agent roles", () => {
    expect(
      codexSecondaryLabel("codex", "gpt-5.6-sol", false, "explorer"),
    ).toBe("gpt-5.6-sol • explorer");
    expect(
      codexSecondaryLabel("codex", "gpt-5.6-sol", false, "default"),
    ).toBe("gpt-5.6-sol");
    expect(codexSecondaryLabel("codex", null, false, "main")).toBeNull();
  });
});
