import type { ReplayFrame } from "@/stores/gameStore";
import type { GameState as BackendGameState } from "@/types";

export const REPLAY_SPEEDS = [0.5, 1, 2, 4, 8] as const;
export const REPLAY_COMPLETED_DISPLAY_MS = 1_500;

export type ReplayPresentation = "event" | "idle";

export interface ReplayControllerSnapshot {
  positionMs: number;
  durationMs: number;
  currentIndex: number;
  isPlaying: boolean;
  speed: number;
  presentation: ReplayPresentation;
}

export interface ReplayControllerOptions {
  compressIdle?: boolean;
  onFrame?: (
    frame: ReplayFrame | null,
    index: number,
    presentation: ReplayPresentation,
  ) => void;
  onChange?: (snapshot: ReplayControllerSnapshot) => void;
}

interface ScheduledFrame {
  frame: ReplayFrame;
  atMs: number;
}

/**
 * Deterministic, requestAnimationFrame-driven Replay clock.
 *
 * The controller stores a playback timeline separate from recorded timestamps.
 * This lets long idle gaps be capped for review while wall-clock display can
 * still use the selected frame's original timestamp.
 */
export class ReplayController {
  private frames: ScheduledFrame[] = [];
  private presentation: ReplayPresentation = "event";
  private positionMs = 0;
  private durationMs = 0;
  private currentIndex = -1;
  private speed = 1;
  private playing = false;
  private raf: number | null = null;
  private lastTick = 0;
  private readonly options: ReplayControllerOptions;

  constructor(options: ReplayControllerOptions) {
    this.options = options;
  }

  setFrames(frames: ReplayFrame[]): void {
    this.pause();
    this.frames = [];
    let elapsed = 0;
    const compressIdle = this.options.compressIdle ?? true;
    for (let index = 0; index < frames.length; index += 1) {
      if (index > 0) {
        const previous = Date.parse(frames[index - 1].event.timestamp);
        const current = Date.parse(frames[index].event.timestamp);
        const rawDelta = Number.isFinite(previous) && Number.isFinite(current)
          ? Math.max(0, current - previous)
          : 0;
        elapsed += compressIdle && rawDelta > 30_000
          ? Math.min(rawDelta, 5_000)
          : rawDelta;
      }
      this.frames.push({ frame: frames[index], atMs: elapsed });
    }
    this.durationMs = elapsed;
    this.rebuildDuration();
    this.positionMs = 0;
    this.currentIndex = -1;
    this.presentation = "event";
    this.emit();
    this.options.onFrame?.(null, -1, "event");
  }

  /** Append a prefetched chunk without resetting the playback clock. */
  appendFrames(frames: ReplayFrame[]): void {
    if (frames.length === 0) return;
    const position = this.positionMs;
    const wasPlaying = this.playing;
    this.pause();
    const compressIdle = this.options.compressIdle ?? true;
    let elapsed = this.frames.at(-1)?.atMs ?? 0;
    let previousTimestamp = this.frames.length > 0
      ? Date.parse(this.frames[this.frames.length - 1].frame.event.timestamp)
      : Number.NaN;
    for (const frame of frames) {
      const currentTimestamp = Date.parse(frame.event.timestamp);
      const rawDelta = Number.isFinite(previousTimestamp) && Number.isFinite(currentTimestamp)
        ? Math.max(0, currentTimestamp - previousTimestamp)
        : 0;
      elapsed += compressIdle && rawDelta > 30_000
        ? Math.min(rawDelta, 5_000)
        : rawDelta;
      this.frames.push({ frame, atMs: elapsed });
      previousTimestamp = currentTimestamp;
    }
    this.durationMs = elapsed;
    this.rebuildDuration();
    this.positionMs = Math.min(position, this.durationMs);
    this.currentIndex = this.findIndex(this.positionMs);
    this.presentation = this.presentationFor(this.positionMs, this.currentIndex);
    this.emitPresentedFrame();
    if (wasPlaying) this.play();
    this.emit();
  }

  setSpeed(speed: number): void {
    if (REPLAY_SPEEDS.includes(speed as (typeof REPLAY_SPEEDS)[number])) {
      this.speed = speed;
      this.emit();
    }
  }

  getSpeed(): number {
    return this.speed;
  }

  play(): void {
    if (this.playing || this.frames.length === 0) return;
    if (this.positionMs >= this.durationMs && this.durationMs > 0) {
      this.seek(0);
    }
    this.playing = true;
    this.lastTick = typeof performance === "undefined" ? Date.now() : performance.now();
    this.schedule();
    this.emit();
  }

  pause(): void {
    this.playing = false;
    if (this.raf !== null && typeof cancelAnimationFrame !== "undefined") {
      cancelAnimationFrame(this.raf);
    }
    this.raf = null;
    this.emit();
  }

  toggle(): void {
    if (this.playing) this.pause();
    else this.play();
  }

  reset(): void {
    this.pause();
    this.positionMs = 0;
    this.currentIndex = -1;
    this.presentation = "event";
    this.options.onFrame?.(null, -1, "event");
    this.emit();
  }

  seek(positionMs: number): void {
    this.positionMs = Math.min(Math.max(0, positionMs), this.durationMs);
    this.currentIndex = this.findIndex(this.positionMs);
    this.presentation = this.presentationFor(this.positionMs, this.currentIndex);
    this.emitPresentedFrame();
    this.emit();
  }

  skip(seconds: number): void {
    this.seek(this.positionMs + seconds * 1000);
  }

  step(): void {
    this.pause();
    const next = Math.min(this.currentIndex + 1, this.frames.length - 1);
    if (next >= 0) {
      this.positionMs = this.frames[next].atMs;
      this.currentIndex = next;
      this.presentation = "event";
      this.emitPresentedFrame();
      this.emit();
    }
  }

  dispose(): void {
    this.pause();
    this.frames = [];
  }

  getSnapshot(): ReplayControllerSnapshot {
    return {
      positionMs: this.positionMs,
      durationMs: this.durationMs,
      currentIndex: this.currentIndex,
      isPlaying: this.playing,
      speed: this.speed,
      presentation: this.presentation,
    };
  }

  getCurrentFrame(): ReplayFrame | null {
    return this.currentIndex >= 0 ? this.frames[this.currentIndex].frame : null;
  }

  private findIndex(positionMs: number): number {
    let low = 0;
    let high = this.frames.length - 1;
    let result = -1;
    while (low <= high) {
      const middle = Math.floor((low + high) / 2);
      if (this.frames[middle].atMs <= positionMs) {
        result = middle;
        low = middle + 1;
      } else {
        high = middle - 1;
      }
    }
    return result;
  }

  private schedule(): void {
    if (!this.playing || typeof requestAnimationFrame === "undefined") return;
    this.raf = requestAnimationFrame((now) => {
      const delta = Math.max(0, now - this.lastTick);
      this.lastTick = now;
      this.positionMs += delta * this.speed;
      const nextIndex = this.findIndex(this.positionMs);
      const nextPresentation = this.presentationFor(this.positionMs, nextIndex);
      if (nextIndex !== this.currentIndex || nextPresentation !== this.presentation) {
        this.currentIndex = nextIndex;
        this.presentation = nextPresentation;
        this.emitPresentedFrame();
      }
      if (this.positionMs >= this.durationMs) {
        this.positionMs = this.durationMs;
        this.playing = false;
        this.raf = null;
      } else {
        this.schedule();
      }
      this.emit();
    });
  }

  private emit(): void {
    this.options.onChange?.(this.getSnapshot());
  }

  private rebuildDuration(): void {
    const last = this.frames.at(-1);
    if (!last) {
      this.durationMs = 0;
      return;
    }
    this.durationMs = last.atMs;
    if (last.frame.event.type === "stop") {
      this.durationMs += REPLAY_COMPLETED_DISPLAY_MS;
    }
  }

  private presentationFor(positionMs: number, index: number): ReplayPresentation {
    if (index < 0) return "event";
    const current = this.frames[index];
    if (current.frame.event.type !== "stop") return "event";
    const next = this.frames[index + 1];
    const idleAt = current.atMs + REPLAY_COMPLETED_DISPLAY_MS;
    if (positionMs < idleAt) return "event";
    if (next && next.atMs < idleAt) return "event";
    return "idle";
  }

  private emitPresentedFrame(): void {
    const frame = this.currentIndex >= 0 ? this.frames[this.currentIndex].frame : null;
    this.options.onFrame?.(frame, this.currentIndex, this.presentation);
  }
}

/**
 * Return the safe state shown after a Replay Stop's completed presentation.
 * This is intentionally derived in the presentation layer; no synthetic event
 * is written to the Replay database.
 */
export function replayIdleState(state: BackendGameState): BackendGameState {
  return {
    ...state,
    boss: {
      ...state.boss,
      state: "idle",
      currentTask: null,
      lastToolName: null,
    },
  };
}

export function formatReplayDuration(milliseconds: number): string {
  const totalSeconds = Math.max(0, Math.floor(milliseconds / 1000));
  const hours = Math.floor(totalSeconds / 3600);
  const minutes = Math.floor((totalSeconds % 3600) / 60);
  const seconds = totalSeconds % 60;
  return hours > 0
    ? `${hours}:${String(minutes).padStart(2, "0")}:${String(seconds).padStart(2, "0")}`
    : `${String(minutes).padStart(2, "0")}:${String(seconds).padStart(2, "0")}`;
}
