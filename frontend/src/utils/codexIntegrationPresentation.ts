import type { Locale } from "@/i18n";

export type CodexIntegrationState = "live" | "waiting" | "restored";

export interface CodexIntegrationPresentation {
  state: CodexIntegrationState;
  lastLiveEventAt: string | null;
  liveEventCount: number;
  restoredSessionCount: number;
}

const LIVE_EVENT_FRESH_MS = 60_000;

export const emptyCodexIntegrationPresentation: CodexIntegrationPresentation = {
  state: "waiting",
  lastLiveEventAt: null,
  liveEventCount: 0,
  restoredSessionCount: 0,
};

function asRecord(value: unknown): Record<string, unknown> | null {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}

function nonNegativeInteger(value: unknown): number {
  return typeof value === "number" && Number.isFinite(value) && value >= 0
    ? Math.floor(value)
    : 0;
}

function validTimestamp(value: unknown): string | null {
  return typeof value === "string" && !Number.isNaN(Date.parse(value))
    ? value
    : null;
}

/** Normalize the deliberately small, read-only integration-status response. */
export function presentCodexIntegrationStatus(
  payload: unknown,
  now = Date.now(),
): CodexIntegrationPresentation {
  const root = asRecord(payload);
  const codex = asRecord(root?.codex);
  const lastLiveEventAt = validTimestamp(codex?.last_live_event_at);
  const liveEventCount = nonNegativeInteger(codex?.live_event_count);
  const restoredSessionCount = nonNegativeInteger(codex?.restored_sessions);
  const eventAge = lastLiveEventAt ? now - Date.parse(lastLiveEventAt) : null;

  const state: CodexIntegrationState =
    eventAge !== null && eventAge <= LIVE_EVENT_FRESH_MS
      ? "live"
      : restoredSessionCount > 0
        ? "restored"
        : "waiting";

  return { state, lastLiveEventAt, liveEventCount, restoredSessionCount };
}

/** Return a browser-localized relative time string for the badge tooltip. */
export function formatRelativeCodexEventTime(
  timestamp: string | null,
  locale: Locale,
  now = Date.now(),
): string | null {
  if (!timestamp) return null;
  const eventTime = Date.parse(timestamp);
  if (Number.isNaN(eventTime)) return null;

  const elapsedSeconds = Math.max(0, Math.round((now - eventTime) / 1000));
  const formatter = new Intl.RelativeTimeFormat(locale, { numeric: "auto" });
  if (elapsedSeconds < 60) return formatter.format(-elapsedSeconds, "second");
  if (elapsedSeconds < 3600) {
    return formatter.format(-Math.floor(elapsedSeconds / 60), "minute");
  }
  if (elapsedSeconds < 86_400) {
    return formatter.format(-Math.floor(elapsedSeconds / 3600), "hour");
  }
  return formatter.format(-Math.floor(elapsedSeconds / 86_400), "day");
}
