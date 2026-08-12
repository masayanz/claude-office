import { describe, expect, it } from "vitest";
import {
  getAutoRotateModes,
  getPersonalBoardHeader,
  getPersonalBoardText,
  type PersonalBoardContent,
} from "@/components/game/whiteboard/PersonalBoardMode";

const content = (
  mode: PersonalBoardContent["mode"],
  overrides: Partial<PersonalBoardContent> = {},
): PersonalBoardContent => ({
  mode,
  dailyGoals: [],
  weeklyGoals: [],
  memo: "",
  customTitle: "",
  customMessage: "",
  ...overrides,
});

describe("personal whiteboard content", () => {
  it("formats daily and weekly goals as numbered plain text", () => {
    expect(
      getPersonalBoardText(
        content("daily_goals", {
          dailyGoals: ["Codex連携", "Fix Yomica", "公開"],
        }),
      ).lines,
    ).toEqual(["1. Codex連携", "2. Fix Yomica", "3. 公開"]);
    expect(
      getPersonalBoardText(
        content("weekly_goals", { weeklyGoals: ["Ship", "Review"] }),
      ).lines,
    ).toEqual(["1. Ship", "2. Review"]);
  });

  it("keeps memo, custom, long text, and XSS-like input as display text", () => {
    const xss = '<img src=x onerror="alert(1)">';
    expect(getPersonalBoardText(content("memo", { memo: xss })).lines).toEqual([
      xss,
    ]);
    expect(
      getPersonalBoardText(
        content("custom", {
          customTitle: "今月の重点",
          customMessage: "A".repeat(500),
        }),
      ),
    ).toMatchObject({ title: "今月の重点", lines: ["A".repeat(500)] });
    expect(getPersonalBoardText(content("daily_goals")).lines).toEqual([]);
  });

  it("offers every configured display mode and rotates only populated content", () => {
    expect(getPersonalBoardHeader("daily_goals")).toBe("TODAY'S GOALS");
    expect(getPersonalBoardHeader("weekly_goals")).toBe("WEEKLY GOALS");
    expect(getPersonalBoardHeader("memo")).toBe("MEMO");
    expect(getPersonalBoardHeader("custom")).toBe("CUSTOM");
    expect(
      getAutoRotateModes(
        content("daily_goals", {
          dailyGoals: ["1"],
          weeklyGoals: ["2"],
          memo: "3",
          customTitle: "4",
        }),
      ),
    ).toEqual(["todo", "daily_goals", "weekly_goals", "memo", "custom"]);
    expect(getAutoRotateModes(content("daily_goals"))).toEqual(["todo"]);
  });
});
