import { describe, expect, it } from "vitest";
import {
  formatRelativeCodexEventTime,
  presentCodexIntegrationStatus,
} from "@/utils/codexIntegrationPresentation";

const NOW = Date.parse("2026-08-12T12:00:00.000Z");

describe("Codex integration presentation", () => {
  it("shows live only for a recent genuine event", () => {
    const result = presentCodexIntegrationStatus(
      {
        codex: {
          last_live_event_at: "2026-08-12T11:59:30.000Z",
          live_event_count: 4,
          restored_sessions: 1,
        },
      },
      NOW,
    );

    expect(result).toEqual({
      state: "live",
      lastLiveEventAt: "2026-08-12T11:59:30.000Z",
      liveEventCount: 4,
      restoredSessionCount: 1,
    });
  });

  it("distinguishes restored-only sessions from an untouched Viewer", () => {
    expect(
      presentCodexIntegrationStatus(
        { codex: { restored_sessions: 2, last_live_event_at: null } },
        NOW,
      ).state,
    ).toBe("restored");
    expect(presentCodexIntegrationStatus({}, NOW).state).toBe("waiting");
  });

  it("does not retain a stale live event as live", () => {
    expect(
      presentCodexIntegrationStatus(
        {
          codex: {
            last_live_event_at: "2026-08-12T11:58:59.000Z",
            live_event_count: 1,
          },
        },
        NOW,
      ).state,
    ).toBe("waiting");
  });

  it("normalizes malformed optional telemetry without displaying invalid values", () => {
    expect(
      presentCodexIntegrationStatus({
        codex: {
          last_live_event_at: "not-a-date",
          live_event_count: -1,
          restored_sessions: "two",
        },
      }),
    ).toEqual({
      state: "waiting",
      lastLiveEventAt: null,
      liveEventCount: 0,
      restoredSessionCount: 0,
    });
  });

  it("formats last-event time with the selected locale", () => {
    expect(
      formatRelativeCodexEventTime("2026-08-12T11:59:55.000Z", "ja", NOW),
    ).toContain("5");
    expect(formatRelativeCodexEventTime(null, "en", NOW)).toBeNull();
  });
});
