import type { TranslationKey } from "@/i18n";
import type { AgentPhase } from "@/stores/slices/types";

export type DisplayAgentStatus =
  | "error"
  | "departing"
  | "walking"
  | "thinking"
  | "preparing"
  | "working"
  | "reviewing"
  | "waiting"
  | "completed"
  | "idle";

type Translate = (key: TranslationKey) => string;

const WALKING_PHASES = new Set<AgentPhase>([
  "arriving",
  "in_arrival_queue",
  "walking_to_ready",
  "conversing",
  "walking_to_boss",
  "at_boss",
  "walking_to_desk",
]);

const DEPARTING_PHASES = new Set<AgentPhase>([
  "departing",
  "in_departure_queue",
  "walking_to_elevator",
  "in_elevator",
]);

/**
 * Collapse backend work state and frontend choreography into one user-facing
 * status. The order is intentional: a departing agent must never appear as
 * waiting just because its last backend state was waiting.
 */
export function getDisplayAgentStatus(
  backendState: string,
  phase: AgentPhase,
): DisplayAgentStatus {
  const backend = backendState.toLowerCase();

  if (backend === "error") return "error";
  if (backend === "leaving" || DEPARTING_PHASES.has(phase)) return "departing";
  if (WALKING_PHASES.has(phase) || backend === "walking_to_desk") {
    return "walking";
  }
  if (backend === "thinking" || backend === "receiving") return "thinking";
  if (backend === "preparing") return "preparing";
  if (backend === "completed") return "completed";
  if (
    [
      "working",
      "delegating",
      "completing",
      "reporting",
      "reporting_done",
      "on_phone",
    ].includes(backend)
  ) {
    return "working";
  }
  if (backend === "phone_ringing") return "waiting";
  if (backend === "reviewing") return "reviewing";
  if (["waiting", "waiting_permission"].includes(backend)) return "waiting";
  return "idle";
}

export function translateAgentStatus(
  t: Translate,
  status: DisplayAgentStatus,
): string {
  return t(`agentStatus.status.${status}` as TranslationKey);
}

export function translateAgentPhase(t: Translate, phase: AgentPhase): string {
  return t(`agentStatus.phase.${phase}` as TranslationKey);
}

export function translateEventType(t: Translate, eventType: string): string {
  const key = `eventType.${eventType}` as TranslationKey;
  const translated = t(key);
  return translated === key ? eventType.replace(/_/g, " ") : translated;
}

/** Resolve a state source into the stable built-in Main Agent display name. */
export function getMainAgentName(
  source: string | null | undefined,
  customName?: string | null,
): string {
  const custom = customName?.trim();
  if (custom) return custom;

  switch (source?.trim().toLowerCase()) {
    case "codex":
      return "Codex Main";
    case "claude":
    case "claude_code":
    case "claude-code":
      return "Claude Main";
    case "opencode":
    case "open_code":
    case "open-code":
      return "OpenCode Main";
    default:
      return "AI Main";
  }
}
