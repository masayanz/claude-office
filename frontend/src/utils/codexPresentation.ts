import type { EventDetail } from "@/types";

export const isCodexSource = (source: unknown): boolean =>
  typeof source === "string" && source.toLowerCase() === "codex";

export const isCodexAgentWait = (detail: EventDetail | undefined): boolean =>
  isCodexSource(detail?.source) &&
  typeof detail?.toolName === "string" &&
  detail.toolName.toLowerCase() === "agentwait";

export const shouldEnterCodexWaitState = (
  eventType: "pre_tool_use" | "post_tool_use",
  detail: EventDetail | undefined,
): boolean => eventType === "pre_tool_use" && isCodexAgentWait(detail);

export const codexSecondaryLabel = (
  source: string | null,
  model: string | null,
  waiting: boolean,
  agentType: string | null = null,
): string | null => {
  if (!isCodexSource(source)) return null;
  const normalizedType = agentType?.trim();
  const showType =
    normalizedType &&
    !["default", "main"].includes(normalizedType.toLowerCase())
      ? normalizedType
      : null;
  const parts = [model, showType, waiting ? "waiting" : null].filter(Boolean);
  return parts.length > 0 ? parts.join(" • ") : null;
};
