/**
 * SEC-005 (plugin part): the event transport must attach `X-API-Key`
 * when `CLAUDE_OFFICE_API_KEY` is configured, and omit it (sending only
 * `Content-Type`) when unset — so the default no-key deployment behaves
 * exactly as before.
 *
 * `buildEventHeaders` is the pure helper `sendEvent` uses to build its
 * fetch headers, so exercising it directly characterizes the actual
 * header logic on the transport path without touching the network.
 */

import { describe, it, expect } from "bun:test";
import { buildEventHeaders } from "../src/index";

describe("SEC-005 buildEventHeaders (X-API-Key)", () => {
  it("omits X-API-Key when no key is configured (default behavior)", () => {
    const headers = buildEventHeaders("");
    expect(headers).toEqual({ "Content-Type": "application/json" });
    expect(headers["X-API-Key"]).toBeUndefined();
  });

  it("attaches X-API-Key when a key is configured", () => {
    const headers = buildEventHeaders("testkey123");
    expect(headers).toEqual({
      "Content-Type": "application/json",
      "X-API-Key": "testkey123",
    });
  });
});
