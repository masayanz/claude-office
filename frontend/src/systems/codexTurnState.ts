import type { BossState, EventType } from "@/types";

export interface CodexTurnState {
  activeTurnId: string | null;
  stoppedTurnId: string | null;
  stopped: boolean;
}

const ACTIVE_BOSS_STATES: ReadonlySet<BossState> = new Set<BossState>([
  "thinking",
  "working",
  "reviewing",
  "waiting",
  "waiting_permission",
  "preparing",
  "delegating",
  "receiving",
]);

export function createCodexTurnState(): CodexTurnState {
  return { activeTurnId: null, stoppedTurnId: null, stopped: false };
}

export function resetCodexTurnState(state: CodexTurnState): void {
  state.activeTurnId = null;
  state.stoppedTurnId = null;
  state.stopped = false;
}

export function beginCodexTurn(
  state: CodexTurnState,
  turnId: string | null | undefined,
): void {
  state.activeTurnId = turnId ?? null;
  state.stoppedTurnId = null;
  state.stopped = false;
}

export function stopCodexTurn(
  state: CodexTurnState,
  turnId: string | null | undefined,
): void {
  state.stoppedTurnId = turnId ?? state.activeTurnId;
  state.stopped = true;
}

/**
 * Accept only events that can still affect the current Main turn.
 *
 * A new prompt always opens a new turn. When a prompt is missing, an explicit
 * different turn_id is also enough to recover; id-less events remain blocked
 * after Stop because they cannot be proven to belong to a newer turn.
 */
export function acceptCodexEvent(
  state: CodexTurnState,
  eventType: EventType,
  turnId: string | null | undefined,
): boolean {
  if (eventType === "user_prompt_submit") {
    beginCodexTurn(state, turnId);
    return true;
  }
  if (!state.stopped) return true;
  if (eventType === "session_start" || eventType === "session_end") {
    return true;
  }
  if (turnId && state.stoppedTurnId && turnId !== state.stoppedTurnId) {
    beginCodexTurn(state, turnId);
    return true;
  }
  return false;
}

/**
 * Whether a state snapshot is an old active view arriving after Stop.
 * The caller keeps the locally displayed completed/idle state for the Main
 * while still reconciling agents, queues, and other snapshot metadata.
 */
export function isStaleCodexBossSnapshot(
  state: CodexTurnState,
  bossState: BossState,
): boolean {
  return state.stopped && ACTIVE_BOSS_STATES.has(bossState);
}
