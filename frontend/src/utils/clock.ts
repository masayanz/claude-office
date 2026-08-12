import type { ClockFormat } from "@/stores/preferencesStore";

export interface ClockParts {
  hour: number;
  minute: number;
  second: number;
  dayPeriod: "AM" | "PM" | null;
}
export interface ClockHands {
  hourAngle: number;
  minuteAngle: number;
  secondAngle: number;
}

/** Return the browser's IANA timezone, with a stable fallback for SSR/tests. */
export function getLocalTimeZone(): string {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
  } catch {
    return "UTC";
  }
}

/** Validate an IANA timezone without allowing Intl errors into rendering. */
export function isValidTimeZone(timeZone: string): boolean {
  try {
    new Intl.DateTimeFormat("en-US", { timeZone }).format();
    return true;
  } catch {
    return false;
  }
}

function resolvedTimeZone(timeZone?: string): string {
  return timeZone && isValidTimeZone(timeZone) ? timeZone : getLocalTimeZone();
}

/**
 * Read a Date in a requested timezone. All clock views use this instead of
 * Date#getHours so analog and digital clocks cannot disagree.
 */
export function getClockParts(date: Date, timeZone?: string): ClockParts {
  const formatter = new Intl.DateTimeFormat("en-US", {
    timeZone: resolvedTimeZone(timeZone),
    hour: "numeric",
    minute: "numeric",
    second: "numeric",
    hourCycle: "h23",
  });
  const parts = formatter.formatToParts(date);
  const value = (type: Intl.DateTimeFormatPartTypes) =>
    Number(parts.find((part) => part.type === type)?.value ?? 0);
  const hour = value("hour");
  return {
    hour,
    minute: value("minute"),
    second: value("second"),
    dayPeriod: hour >= 12 ? "PM" : "AM",
  };
}

export function formatClockTime(
  date: Date,
  format: ClockFormat,
  timeZone?: string,
): string {
  const parts = getClockParts(date, timeZone);
  const hour = format === "12h" ? parts.hour % 12 || 12 : parts.hour;
  return `${format === "12h" ? hour : String(hour).padStart(2, "0")}:${String(parts.minute).padStart(2, "0")}:${String(parts.second).padStart(2, "0")}`;
}

export function getClockPeriod(
  date: Date,
  timeZone?: string,
): "AM" | "PM" {
  return getClockParts(date, timeZone).dayPeriod ?? "AM";
}

export function getClockHands(date: Date, timeZone?: string): ClockHands {
  const { hour, minute, second } = getClockParts(date, timeZone);
  // Include minutes (and seconds) in the hour hand position for a real 12h
  // clock rather than snapping the hand to each hour.
  return {
    hourAngle: (hour % 12 + minute / 60 + second / 3600) * 30,
    minuteAngle: (minute + second / 60) * 6,
    secondAngle: second * 6,
  };
}
