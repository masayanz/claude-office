import { describe, it, expect } from "vitest";
import {
  timesToCron,
  intervalToCron,
  cronToEditor,
  DEFAULT_BUSINESS_HOURS,
  enterTimesHours,
} from "@/utils/cron";

describe("timesToCron", () => {
  it("generates a sorted hour list at a single minute", () => {
    expect(
      timesToCron(["08:00", "12:00", "15:00", "18:00", "22:00", "23:00"]),
    ).toBe("0 8,12,15,18,22,23 * * *");
  });
});

describe("DEFAULT_BUSINESS_HOURS", () => {
  it("is business hours 8..23 (16 hours, implicit minute 0)", () => {
    expect(DEFAULT_BUSINESS_HOURS).toEqual([
      8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23,
    ]);
  });

  it("generates the equivalent cron 0 8-23 (hour list)", () => {
    const times = DEFAULT_BUSINESS_HOURS.map(
      (h) => `${String(h).padStart(2, "0")}:00`,
    );
    expect(timesToCron(times)).toBe(
      "0 8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23 * * *",
    );
  });
});

describe("enterTimesHours", () => {
  it("empty list → pre-populates with business hours", () => {
    expect(enterTimesHours([])).toEqual(DEFAULT_BUSINESS_HOURS);
  });

  it("does not clobber existing hours", () => {
    expect(enterTimesHours([9, 18])).toEqual([9, 18]);
  });

  it("returns a new copy (does not share the default reference)", () => {
    expect(enterTimesHours([])).not.toBe(DEFAULT_BUSINESS_HOURS);
  });
});

describe("intervalToCron", () => {
  it("every 15min from 7 to 23", () => {
    expect(intervalToCron(15, 7, 23)).toBe("0,15,30,45 7-23 * * *");
  });
});

describe("intervalToCron 24h", () => {
  it("every 15min, 0-23 window (24h)", () => {
    expect(intervalToCron(15, 0, 23)).toBe("0,15,30,45 0-23 * * *");
  });
});

describe("cronToEditor", () => {
  it("recognizes a 0-23 window as 24h (round-trip)", () => {
    expect(cronToEditor("0,15,30,45 0-23 * * *")).toEqual({
      mode: "interval",
      everyMin: 15,
      startHour: 0,
      endHour: 23,
      h24: true,
    });
  });
  it("recognizes an interval", () => {
    expect(cronToEditor("0,15,30,45 7-23 * * *")).toEqual({
      mode: "interval",
      everyMin: 15,
      startHour: 7,
      endHour: 23,
    });
  });
  it("recognizes fixed times", () => {
    expect(cronToEditor("0 8,12,15,18,23 * * *")).toEqual({
      mode: "times",
      minute: 0,
      hours: [8, 12, 15, 18, 23],
    });
  });
  it("falls back to raw for non-standard expressions", () => {
    expect(cronToEditor("*/5 * * * *")).toEqual({ mode: "raw" });
    expect(cronToEditor("0 8 * * 1-5")).toEqual({ mode: "raw" });
  });
});
