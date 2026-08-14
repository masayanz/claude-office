import type { AgentPhase, PathState } from "@/stores/gameStore";
import type { Position } from "@/types";
import type { TranslationKey } from "@/i18n";
import { getDeskPosition } from "@/machines/positionHelpers";
import {
  CITY_WINDOW_POSITION,
  WHITEBOARD_POSITION,
  WATER_COOLER_POSITION,
  BOSS_RUG_POSITION,
} from "@/constants/positions";
import { ELEVATOR_POSITION } from "./queuePositions";

export type VisualActivityKind =
  | "desk_thinking"
  | "pacing"
  | "whiteboard"
  | "window"
  | "coffee"
  | "stretch"
  | "typing"
  | "monitor"
  | "document"
  | "brief_move"
  | "completed"
  | "error"
  | "walking"
  | "interaction"
  | "desk_idle";

export type VisualActivityLevel = "quiet" | "standard" | "lively";

export interface VisualActivitySettings {
  level: VisualActivityLevel;
  thinkingWalk: boolean;
  ideaEffects: boolean;
  breakActions: boolean;
  interactions: boolean;
}

export const DEFAULT_VISUAL_ACTIVITY_SETTINGS: VisualActivitySettings = {
  level: "standard",
  thinkingWalk: true,
  ideaEffects: true,
  breakActions: true,
  interactions: true,
};

/** Named destinations used by the scheduler and exposed for replay tests. */
export const VISUAL_HOTSPOTS = {
  WHITEBOARD: { x: WHITEBOARD_POSITION.x - 56, y: WHITEBOARD_POSITION.y + 114 },
  WINDOW: { x: CITY_WINDOW_POSITION.x + 46, y: CITY_WINDOW_POSITION.y + 115 },
  WATER_COOLER: { x: WATER_COOLER_POSITION.x + 40, y: WATER_COOLER_POSITION.y + 75 },
  ELEVATOR: { x: ELEVATOR_POSITION.x, y: ELEVATOR_POSITION.y + 70 },
  OWNER_AREA: { x: BOSS_RUG_POSITION.x, y: BOSS_RUG_POSITION.y - 110 },
  DESK: null,
} as const;

export interface VisualActivityState {
  kind: VisualActivityKind;
  messageKey: Extract<TranslationKey, `agent.message.${string}`>;
  labelKey: Extract<TranslationKey, `agent.activity.${string}`>;
  startedAt: number;
  endsAt: number;
  priority: number;
  targetPosition: Position | null;
  movement: "none" | "walk";
}

export interface VisualActivityAgentSnapshot {
  id: string;
  currentPosition: Position;
  targetPosition: Position;
  path: PathState | null;
}

export interface VisualActivityInput {
  id: string;
  entityType: "agent" | "boss";
  logicalState: string;
  phase: AgentPhase;
  desk: number | null;
  currentPosition: Position;
  currentActivity: VisualActivityState | null;
  others: VisualActivityAgentSnapshot[];
  characterType?: string | null;
  isTyping?: boolean;
}

interface ActivityTrack {
  signature: string;
  activity: VisualActivityState | null;
  nextBreakAt: number;
  nextIdeaAt: number;
  returnToDesk: boolean;
  random: () => number;
}

const HOTSPOT_TARGETS: Record<string, Position> = {
  whiteboard: VISUAL_HOTSPOTS.WHITEBOARD,
  window: VISUAL_HOTSPOTS.WINDOW,
  coffee: VISUAL_HOTSPOTS.WATER_COOLER,
  stretch: { x: 820, y: 290 },
};

const MESSAGE_KEYS = {
  thinking: "agent.message.thinking",
  thinkingAlt: "agent.message.thinkingAlt",
  working: "agent.message.working",
  reviewing: "agent.message.reviewing",
  waiting: "agent.message.waiting",
  completed: "agent.message.completed",
  departing: "agent.message.departing",
  idea: "agent.message.idea",
  break: "agent.message.break",
  stretch: "agent.message.stretch",
  walking: "agent.message.walking",
  interaction: "agent.message.interaction",
  whiteboard: "agent.message.whiteboard",
  window: "agent.message.window",
  coffee: "agent.message.coffee",
  error: "agent.message.error",
} as const;

const LABEL_KEYS = {
  thinking: "agent.activity.thinking",
  pacing: "agent.activity.pacing",
  whiteboard: "agent.activity.whiteboard",
  window: "agent.activity.window",
  coffee: "agent.activity.coffee",
  stretch: "agent.activity.stretch",
  typing: "agent.activity.typing",
  monitor: "agent.activity.monitor",
  document: "agent.activity.document",
  completed: "agent.activity.completed",
  error: "agent.activity.error",
  walking: "agent.activity.walking",
  interaction: "agent.activity.interaction",
  idle: "agent.activity.idle",
} as const;

const PRIORITY: Record<VisualActivityKind, number> = {
  error: 100,
  completed: 90,
  walking: 80,
  interaction: 35,
  typing: 60,
  monitor: 60,
  document: 60,
  brief_move: 60,
  whiteboard: 50,
  pacing: 45,
  desk_thinking: 40,
  coffee: 30,
  stretch: 30,
  window: 25,
  desk_idle: 10,
};

function hashSeed(value: string): number {
  let hash = 2166136261;
  for (let index = 0; index < value.length; index += 1) {
    hash ^= value.charCodeAt(index);
    hash = Math.imul(hash, 16777619);
  }
  return hash >>> 0;
}

function createRandom(seed: number): () => number {
  let state = seed >>> 0;
  return () => {
    state += 0x6d2b79f5;
    let value = state;
    value = Math.imul(value ^ (value >>> 15), value | 1);
    value ^= value + Math.imul(value ^ (value >>> 7), value | 61);
    return ((value ^ (value >>> 14)) >>> 0) / 4294967296;
  };
}

function range(random: () => number, min: number, max: number): number {
  return min + random() * (max - min);
}

function normalizeState(state: string): string {
  if (state === "waiting_permission") return "waiting";
  if (state === "reporting" || state === "reporting_done") return "reviewing";
  if (state === "leaving" || state === "in_elevator") return "departing";
  return state;
}

function isClose(a: Position, b: Position, distance = 56): boolean {
  return Math.hypot(a.x - b.x, a.y - b.y) < distance;
}

/** Public for deterministic collision tests and future hotspot reservations. */
export function reserveDistinctTarget(
  desired: Position,
  others: Position[],
  random: () => number,
): Position {
  const candidates = [
    desired,
    { x: desired.x + 64, y: desired.y },
    { x: desired.x - 64, y: desired.y },
    { x: desired.x, y: desired.y + 64 },
    { x: desired.x, y: desired.y - 64 },
  ];
  const shuffled = candidates
    .map((candidate) => ({ candidate, sort: random() }))
    .sort((left, right) => left.sort - right.sort)
    .map(({ candidate }) => candidate);
  return shuffled.find((candidate) => !others.some((other) => isClose(candidate, other))) ?? desired;
}

function targetFor(
  kind: VisualActivityKind,
  input: VisualActivityInput,
  random: () => number,
): Position | null {
  if (kind === "pacing" || kind === "brief_move") {
    if (!input.desk) return null;
    const desk = getDeskPosition(input.desk);
    return reserveDistinctTarget(
      { x: desk.x + (random() > 0.5 ? 56 : -56), y: desk.y - 48 },
      input.others.flatMap((other) => [other.currentPosition, other.targetPosition]),
      random,
    );
  }
  if (kind === "desk_idle" && input.entityType === "agent" && input.desk) {
    return getDeskPosition(input.desk);
  }
  const hotspot =
    kind === "whiteboard"
      ? HOTSPOT_TARGETS.whiteboard
      : kind === "window"
        ? HOTSPOT_TARGETS.window
        : kind === "coffee"
          ? HOTSPOT_TARGETS.coffee
          : kind === "stretch"
            ? HOTSPOT_TARGETS.stretch
            : null;
  if (!hotspot) return null;
  return reserveDistinctTarget(
    hotspot,
    input.others.flatMap((other) => [other.currentPosition, other.targetPosition]),
    random,
  );
}

function makeActivity(
  kind: VisualActivityKind,
  input: VisualActivityInput,
  now: number,
  random: () => number,
  settings: VisualActivitySettings,
  messageKey: VisualActivityState["messageKey"],
  labelKey: VisualActivityState["labelKey"],
): VisualActivityState {
  const movement =
    settings.level !== "quiet" &&
    (kind === "pacing" ||
      kind === "whiteboard" ||
      kind === "window" ||
      kind === "coffee" ||
      kind === "stretch" ||
      kind === "brief_move" ||
      (kind === "desk_idle" && input.entityType === "agent" && input.desk !== null))
      ? "walk"
      : "none";
  return {
    kind,
    messageKey,
    labelKey,
    startedAt: now,
    endsAt: now + range(random, kind === "error" ? 1800 : 2400, kind === "completed" ? 3600 : 6500),
    priority: PRIORITY[kind],
    targetPosition: movement === "walk" ? targetFor(kind, input, random) : null,
    movement,
  };
}

function chooseActivity(
  input: VisualActivityInput,
  now: number,
  random: () => number,
  settings: VisualActivitySettings,
  canBreak: boolean,
  canIdea: boolean,
): VisualActivityState {
  const state = normalizeState(input.logicalState);

  if (state === "error") {
    return makeActivity("error", input, now, random, settings, MESSAGE_KEYS.error, LABEL_KEYS.error);
  }
  if (input.entityType === "agent" && input.phase !== "idle") {
    return makeActivity(
      "walking",
      input,
      now,
      random,
      settings,
      state === "departing" ? MESSAGE_KEYS.departing : MESSAGE_KEYS.walking,
      LABEL_KEYS.walking,
    );
  }
  if (state === "completed") {
    return makeActivity("completed", input, now, random, settings, MESSAGE_KEYS.completed, LABEL_KEYS.completed);
  }
  if (state === "thinking") {
    if (settings.ideaEffects && canIdea && random() < 0.55) {
      return makeActivity("desk_thinking", input, now, random, settings, MESSAGE_KEYS.idea, LABEL_KEYS.thinking);
    }
    if (
      input.characterType === "subagent" &&
      settings.thinkingWalk &&
      random() < 0.55
    ) {
      return makeActivity("pacing", input, now, random, settings, MESSAGE_KEYS.thinkingAlt, LABEL_KEYS.pacing);
    }
    if (input.entityType === "boss" && random() < 0.6) {
      return makeActivity("desk_thinking", input, now, random, settings, MESSAGE_KEYS.thinking, LABEL_KEYS.thinking);
    }
    const choices: Array<[VisualActivityKind, VisualActivityState["messageKey"], VisualActivityState["labelKey"]]> = [
      ["desk_thinking", random() < 0.5 ? MESSAGE_KEYS.thinking : MESSAGE_KEYS.thinkingAlt, LABEL_KEYS.thinking],
      ["whiteboard", MESSAGE_KEYS.whiteboard, LABEL_KEYS.whiteboard],
      ["window", MESSAGE_KEYS.window, LABEL_KEYS.window],
    ];
    if (settings.thinkingWalk) choices.splice(1, 0, ["pacing", MESSAGE_KEYS.thinkingAlt, LABEL_KEYS.pacing]);
    const [kind, messageKey, labelKey] = choices[Math.floor(random() * choices.length)];
    return makeActivity(kind, input, now, random, settings, messageKey, labelKey);
  }
  if (state === "reviewing") {
    return random() < 0.45
      ? makeActivity("whiteboard", input, now, random, settings, MESSAGE_KEYS.whiteboard, LABEL_KEYS.whiteboard)
      : makeActivity("document", input, now, random, settings, MESSAGE_KEYS.reviewing, LABEL_KEYS.document);
  }
  if (state === "working") {
    const choices: Array<[VisualActivityKind, VisualActivityState["labelKey"]]> = [
      ["typing", LABEL_KEYS.typing],
      ["monitor", LABEL_KEYS.monitor],
      ["document", LABEL_KEYS.document],
    ];
    if (input.entityType === "agent" && settings.level === "lively") choices.push(["brief_move", LABEL_KEYS.pacing]);
    const [kind, labelKey] = choices[Math.floor(random() * choices.length)];
    return makeActivity(kind, input, now, random, settings, MESSAGE_KEYS.working, labelKey);
  }
  if (
    settings.interactions &&
    input.others.length > 0 &&
    settings.level === "lively" &&
    random() < 0.04
  ) {
    return makeActivity(
      "interaction",
      input,
      now,
      random,
      settings,
      MESSAGE_KEYS.interaction,
      LABEL_KEYS.interaction,
    );
  }
  if ((state === "waiting" || state === "idle") && settings.breakActions && canBreak) {
    const kind = random() < 0.55 ? "coffee" : random() < 0.5 ? "window" : "stretch";
    const messageKey = kind === "coffee" ? MESSAGE_KEYS.coffee : kind === "stretch" ? MESSAGE_KEYS.stretch : MESSAGE_KEYS.window;
    const labelKey = kind === "coffee" ? LABEL_KEYS.coffee : kind === "stretch" ? LABEL_KEYS.stretch : LABEL_KEYS.window;
    return makeActivity(kind, input, now, random, settings, messageKey, labelKey);
  }
  return makeActivity("desk_idle", input, now, random, settings, MESSAGE_KEYS.waiting, LABEL_KEYS.idle);
}

/**
 * Chooses ambient actions without changing the backend state machine.
 * The clock is injected so live and replay runs can use the same deterministic
 * sequence, while per-entity seeds keep characters from moving in lockstep.
 */
export class VisualActivityScheduler {
  private tracks = new Map<string, ActivityTrack>();
  private sessionSeed = 0;

  setSessionSeed(sessionId: string | null): void {
    const nextSeed = hashSeed(sessionId ?? "live");
    if (nextSeed !== this.sessionSeed) {
      this.sessionSeed = nextSeed;
      this.tracks.clear();
    }
  }

  reset(): void {
    this.tracks.clear();
  }

  update(
    input: VisualActivityInput,
    now: number,
    settings: VisualActivitySettings = DEFAULT_VISUAL_ACTIVITY_SETTINGS,
  ): VisualActivityState | null {
    const normalizedState = normalizeState(input.logicalState);
    const signature = `${input.entityType}:${normalizedState}:${input.phase}:${input.isTyping ? "typing" : "still"}`;
    let track = this.tracks.get(input.id);
    if (!track) {
      track = {
        signature,
        activity: null,
        nextBreakAt: now + range(createRandom(this.sessionSeed ^ hashSeed(input.id)), 30_000, 60_000),
        nextIdeaAt: now + range(createRandom(this.sessionSeed ^ hashSeed(`${input.id}:idea`)), 30_000, 60_000),
        returnToDesk: false,
        random: createRandom(this.sessionSeed ^ hashSeed(input.id)),
      };
      this.tracks.set(input.id, track);
    }

    if (track.signature !== signature) {
      track.signature = signature;
      track.activity = null;
      track.returnToDesk = false;
    }
    if (track.activity && now < track.activity.endsAt) return track.activity;

    if (track.returnToDesk) {
      track.returnToDesk = false;
      const returnActivity = makeActivity(
        "desk_idle",
        input,
        now,
        track.random,
        settings,
        MESSAGE_KEYS.waiting,
        LABEL_KEYS.idle,
      );
      track.activity = returnActivity;
      return returnActivity;
    }

    const canBreak = now >= track.nextBreakAt;
    const canIdea = now >= track.nextIdeaAt;
    const next = chooseActivity(input, now, track.random, settings, canBreak, canIdea);
    track.activity = next;
    if (next.kind === "coffee" || next.kind === "stretch" || next.kind === "window") {
      track.nextBreakAt = now + range(track.random, 30_000, 60_000);
    }
    if (next.movement === "walk" && next.kind !== "desk_idle") {
      track.returnToDesk = true;
    }
    if (canIdea) track.nextIdeaAt = now + range(track.random, 30_000, 60_000);
    return next;
  }
}

export const visualActivityScheduler = new VisualActivityScheduler();
