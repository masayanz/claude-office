/** Pure helpers bridging a friendly editor and a 5-field cron expression. */

/** Default business hours: 8..23 (minute 0). Pre-populates "fixed times" mode. */
export const DEFAULT_BUSINESS_HOURS: number[] = Array.from(
  { length: 16 },
  (_, i) => 8 + i,
);

/**
 * On entering "fixed times" mode: an empty list (coming from interval/raw, or an
 * agent with no agenda) becomes business hours; an existing list is preserved
 * (does not clobber saved fixed times).
 */
export function enterTimesHours(current: number[]): number[] {
  return current.length === 0 ? [...DEFAULT_BUSINESS_HOURS] : current;
}

export type CronEditor =
  | { mode: "times"; minute: number; hours: number[] }
  | {
      mode: "interval";
      everyMin: number;
      startHour: number;
      endHour: number;
      h24?: boolean;
    }
  | { mode: "raw" };

/** ["08:00","22:00"] -> "0 8,22 * * *". Assumes a single minute (uses the first time's). */
export function timesToCron(times: string[]): string {
  const parsed = times.map((t) => {
    const [h, m] = t.split(":").map(Number);
    return { h, m };
  });
  const minute = parsed.length ? parsed[0].m : 0;
  const hours = parsed.map((p) => p.h).sort((a, b) => a - b);
  return `${minute} ${hours.join(",")} * * *`;
}

/** Interval of N min within [startHour, endHour]. N must divide 60. */
export function intervalToCron(
  everyMin: number,
  startHour: number,
  endHour: number,
): string {
  const mins: number[] = [];
  for (let m = 0; m < 60; m += everyMin) mins.push(m);
  return `${mins.join(",")} ${startHour}-${endHour} * * *`;
}

function isPlainHourList(field: string): number[] | null {
  if (!/^\d+(,\d+)*$/.test(field)) return null;
  return field.split(",").map(Number);
}

/** Parses back into the editor; falls back to {mode:"raw"} if it matches neither pattern. */
export function cronToEditor(expr: string): CronEditor {
  const parts = expr.trim().split(/\s+/);
  if (parts.length !== 5) return { mode: "raw" };
  const [min, hour, dom, mon, dow] = parts;
  if (dom !== "*" || mon !== "*" || dow !== "*") return { mode: "raw" };

  // interval: minute = list starting at 0 with a constant step; hour = range a-b
  const minList = isPlainHourList(min);
  const rangeMatch = hour.match(/^(\d+)-(\d+)$/);
  if (minList && minList.length >= 2 && minList[0] === 0 && rangeMatch) {
    const step = minList[1] - minList[0];
    const ok = step > 0 && minList.every((m, i) => m === i * step);
    if (ok) {
      const startHour = Number(rangeMatch[1]);
      const endHour = Number(rangeMatch[2]);
      const editor: CronEditor = {
        mode: "interval",
        everyMin: step,
        startHour,
        endHour,
      };
      // 0-23 window == "24h a day" → reopens with the box checked (round-trip)
      if (startHour === 0 && endHour === 23) editor.h24 = true;
      return editor;
    }
  }

  // fixed times: single minute + hour = plain list
  const hourList = isPlainHourList(hour);
  if (/^\d+$/.test(min) && hourList) {
    return { mode: "times", minute: Number(min), hours: hourList };
  }
  return { mode: "raw" };
}
