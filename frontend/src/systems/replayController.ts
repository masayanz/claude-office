import type { ReplayFrame } from "@/stores/gameStore";

export const REPLAY_SPEEDS = [0.5, 1, 2, 4, 8] as const;

export interface ReplayControllerSnapshot {
  positionMs: number;
  durationMs: number;
  currentIndex: number;
  isPlaying: boolean;
  speed: number;
}

export interface ReplayControllerOptions {
  compressIdle?: boolean;
  onFrame: (frame: ReplayFrame | null, index: number) => void;
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
    this.positionMs = 0;
    this.currentIndex = -1;
    this.emit();
    this.options.onFrame(null, -1);
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
    this.options.onFrame(null, -1);
    this.emit();
  }

  seek(positionMs: number): void {
    this.positionMs = Math.min(Math.max(0, positionMs), this.durationMs);
    this.currentIndex = this.findIndex(this.positionMs);
    this.options.onFrame(
      this.currentIndex >= 0 ? this.frames[this.currentIndex].frame : null,
      this.currentIndex,
    );
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
      this.options.onFrame(this.frames[next].frame, next);
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
      if (nextIndex !== this.currentIndex) {
        this.currentIndex = nextIndex;
        this.options.onFrame(
          nextIndex >= 0 ? this.frames[nextIndex].frame : null,
          nextIndex,
        );
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
