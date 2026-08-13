import { describe, expect, it } from "vitest";
import { localDayBoundary } from "@/hooks/useReplay";

describe("Replay date filters", () => {
  it("uses the browser local calendar day instead of treating date input as UTC", () => {
    const previousTimezone = process.env.TZ;
    process.env.TZ = "Asia/Tokyo";
    try {
      expect(localDayBoundary("2026-08-13")).toBe("2026-08-12T15:00:00.000Z");
      expect(localDayBoundary("2026-08-13", true)).toBe("2026-08-13T14:59:59.999Z");
    } finally {
      if (previousTimezone === undefined) delete process.env.TZ;
      else process.env.TZ = previousTimezone;
    }
  });
});
