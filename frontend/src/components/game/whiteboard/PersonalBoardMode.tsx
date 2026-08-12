"use client";

import type { ReactNode } from "react";
import type { BoardMode } from "@/stores/appSettingsStore";

export interface PersonalBoardContent {
  mode: Exclude<BoardMode, "todo">;
  dailyGoals: string[];
  weeklyGoals: string[];
  memo: string;
  customTitle: string;
  customMessage: string;
}

export function getPersonalBoardHeader(
  mode: PersonalBoardContent["mode"],
): string {
  switch (mode) {
    case "daily_goals":
      return "TODAY'S GOALS";
    case "weekly_goals":
      return "WEEKLY GOALS";
    case "memo":
      return "MEMO";
    case "custom":
      return "CUSTOM";
  }
}

function dateLabel(): string {
  return new Intl.DateTimeFormat("ja-JP", {
    year: "numeric",
    month: "long",
    day: "numeric",
  }).format(new Date());
}

export function getPersonalBoardText(content: PersonalBoardContent): {
  title?: string;
  lines: string[];
} {
  switch (content.mode) {
    case "daily_goals":
      return {
        title: dateLabel(),
        lines: content.dailyGoals.map((goal, index) => `${index + 1}. ${goal}`),
      };
    case "weekly_goals":
      return {
        lines: content.weeklyGoals.map(
          (goal, index) => `${index + 1}. ${goal}`,
        ),
      };
    case "memo":
      return { lines: content.memo ? [content.memo] : [] };
    case "custom":
      return {
        title: content.customTitle || undefined,
        lines: content.customMessage ? [content.customMessage] : [],
      };
  }
}

export function getAutoRotateModes(
  content: Omit<PersonalBoardContent, "mode">,
): BoardMode[] {
  const modes: BoardMode[] = ["todo"];
  if (content.dailyGoals.length) modes.push("daily_goals");
  if (content.weeklyGoals.length) modes.push("weekly_goals");
  if (content.memo) modes.push("memo");
  if (content.customTitle || content.customMessage) modes.push("custom");
  return modes;
}

/** Renders only Pixi text nodes, so settings values are always plain text. */
export function PersonalBoardMode({
  content,
}: {
  content: PersonalBoardContent;
}): ReactNode {
  const { title, lines } = getPersonalBoardText(content);
  const visibleLines = lines.slice(0, 8);
  const hasMore = lines.length > visibleLines.length;
  const body = visibleLines.length > 0 ? visibleLines : ["（未設定）"];
  if (hasMore) body.push("…");
  const fontSize = body.length <= 3 ? 16 : body.length <= 5 ? 14 : 12;

  return (
    <pixiContainer x={16} y={9}>
      {title && (
        <pixiText
          text={title}
          style={{
            fontFamily: '"Courier New", monospace',
            fontSize: 10,
            fill: "#64748b",
            fontWeight: "bold",
          }}
        />
      )}
      <pixiText
        text={body.join("\n")}
        y={title ? 17 : 4}
        style={{
          fontFamily: '"Courier New", monospace',
          fontSize,
          lineHeight: fontSize + 5,
          fill: "#172033",
          wordWrap: true,
          wordWrapWidth: 296,
          breakWords: true,
        }}
      />
    </pixiContainer>
  );
}
