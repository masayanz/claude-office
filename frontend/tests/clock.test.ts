import { describe, expect, it } from "vitest";
import {
  formatClockTime,
  getClockHands,
  getClockParts,
  getLocalTimeZone,
} from "../src/utils/clock";

const cases = [
  [0, 0],
  [3, 0],
  [6, 30],
  [9, 45],
  [12, 0],
  [15, 30],
  [23, 59],
] as const;

function utcDate(hour: number, minute: number): Date {
  return new Date(Date.UTC(2026, 0, 1, hour, minute));
}

describe("clock utilities", () => {
  it.each(cases)("formats %s:%s in UTC", (hour, minute) => {
    const date = utcDate(hour, minute);
    expect(formatClockTime(date, "24h", "UTC")).toBe(
      `${String(hour).padStart(2, "0")}:${String(minute).padStart(2, "0")}:00`,
    );
  });

  it("supports Asia/Tokyo and the PC local timezone", () => {
    const tokyoMidnight = new Date("2025-12-31T15:00:00.000Z");
    expect(formatClockTime(tokyoMidnight, "24h", "Asia/Tokyo")).toBe(
      "00:00:00",
    );

    const local = new Date(2026, 0, 1, 6, 30, 0);
    const expected = new Intl.DateTimeFormat("en-US", {
      timeZone: getLocalTimeZone(),
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
      hourCycle: "h23",
    }).format(local);
    expect(formatClockTime(local, "24h", getLocalTimeZone())).toBe(
      expected.replace(/^24:/, "00:"),
    );
  });

  it("keeps the hour hand moving through minutes in 12-hour mode", () => {
    expect(getClockHands(utcDate(3, 0), "UTC").hourAngle).toBe(90);
    expect(getClockHands(utcDate(3, 30), "UTC").hourAngle).toBe(105);
    expect(getClockHands(utcDate(6, 30), "UTC").hourAngle).toBe(195);
    expect(getClockParts(utcDate(23, 59), "UTC")).toMatchObject({
      hour: 23,
      minute: 59,
      second: 0,
      dayPeriod: "PM",
    });
  });
});
