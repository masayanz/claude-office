/**
 * Tests for the toast filter extracted from `useWebSocketEvents` as part of
 * QA-005. The filter owns two decisions that were previously inlined:
 *   1. Is this event type attention-worthy at all?
 *   2. Do the user's toast-filter preferences suppress it?
 *
 * Pref values are `boolean` (see `PreferencesState`), but the function still
 * honors the original "absent value ⇒ show" default for the attention types
 * via a `!== false` comparison, so we also pass a minimal partial object cast
 * to the interface to exercise that fallback path.
 */
import { describe, expect, it } from "vitest";
import { shouldShowToast } from "@/systems/toastFilter";
import type { PreferencesState } from "@/stores/preferencesStore";
import type { EventType } from "@/types";

/** Build a prefs fixture with every filter explicitly enabled. */
function prefsAll(enabled: boolean): PreferencesState {
  return {
    toastFilterPermission: enabled,
    toastFilterError: enabled,
    toastFilterTaskComplete: enabled,
    toastFilterArrival: enabled,
  } as Partial<PreferencesState> as PreferencesState;
}

describe("shouldShowToast", () => {
  describe("non-attention event types", () => {
    it("returns false for a type outside the attention set (filter prefs ignored)", () => {
      // tool_use is not in ATTENTION_EVENT_TYPES, so it short-circuits.
      expect(shouldShowToast("tool_use" as EventType, prefsAll(true))).toBe(
        false,
      );
      // Even with every pref suppressed, the non-attention return is still false.
      expect(
        shouldShowToast("session_start" as EventType, prefsAll(false)),
      ).toBe(false);
    });
  });

  describe("permission_request", () => {
    it("respects toastFilterPermission = true (shown)", () => {
      const prefs = prefsAll(true);
      expect(shouldShowToast("permission_request", prefs)).toBe(true);
    });
    it("respects toastFilterPermission = false (suppressed)", () => {
      const prefs = prefsAll(true);
      prefs.toastFilterPermission = false;
      expect(shouldShowToast("permission_request", prefs)).toBe(false);
    });
  });

  describe("error", () => {
    it("respects toastFilterError = true (shown)", () => {
      expect(shouldShowToast("error", prefsAll(true))).toBe(true);
    });
    it("respects toastFilterError = false (suppressed)", () => {
      const prefs = prefsAll(true);
      prefs.toastFilterError = false;
      expect(shouldShowToast("error", prefs)).toBe(false);
    });
  });

  describe("stop", () => {
    // `stop` shares the error pref with `error` — this is the original
    // `filterMap` mapping, preserved exactly.
    it("shares toastFilterError with error (true ⇒ shown)", () => {
      expect(shouldShowToast("stop", prefsAll(true))).toBe(true);
    });
    it("shares toastFilterError with error (false ⇒ suppressed)", () => {
      const prefs = prefsAll(true);
      prefs.toastFilterError = false;
      expect(shouldShowToast("stop", prefs)).toBe(false);
    });
    it("is NOT affected by toastFilterPermission", () => {
      const prefs = prefsAll(true);
      prefs.toastFilterPermission = false; // unrelated toggle
      expect(shouldShowToast("stop", prefs)).toBe(true);
    });
  });

  describe("task_completed", () => {
    it("respects toastFilterTaskComplete = true (shown)", () => {
      expect(shouldShowToast("task_completed", prefsAll(true))).toBe(true);
    });
    it("respects toastFilterTaskComplete = false (suppressed)", () => {
      const prefs = prefsAll(true);
      prefs.toastFilterTaskComplete = false;
      expect(shouldShowToast("task_completed", prefs)).toBe(false);
    });
  });

  describe("subagent_start", () => {
    it("respects toastFilterArrival = true (shown)", () => {
      expect(shouldShowToast("subagent_start", prefsAll(true))).toBe(true);
    });
    it("respects toastFilterArrival = false (suppressed)", () => {
      const prefs = prefsAll(true);
      prefs.toastFilterArrival = false;
      expect(shouldShowToast("subagent_start", prefs)).toBe(false);
    });
  });

  describe("background_task_notification", () => {
    // Shares toastFilterArrival with subagent_start, per the original filterMap.
    it("shares toastFilterArrival with subagent_start (true ⇒ shown)", () => {
      expect(
        shouldShowToast("background_task_notification", prefsAll(true)),
      ).toBe(true);
    });
    it("shares toastFilterArrival with subagent_start (false ⇒ suppressed)", () => {
      const prefs = prefsAll(true);
      prefs.toastFilterArrival = false;
      expect(shouldShowToast("background_task_notification", prefs)).toBe(
        false,
      );
    });
  });

  describe("absent-pref fallback", () => {
    // The original inline `filterMap[type] !== false` meant an undefined pref
    // value (e.g. an attention type added to the Set but missing from the map)
    // defaulted to showing the toast. The extracted `default` branch preserves
    // that semantics. We exercise it via a type we add to the attention set's
    // universe but for which filterEnabled() falls through. Since all current
    // attention types are mapped, this test documents the contract by passing
    // a prefs object whose filter field reads back `undefined` at runtime.
    it("defaults to showing when the mapped pref is undefined (legacy !== false semantics)", () => {
      // Build a prefs where every filter reads as undefined at runtime, while
      // still satisfying PreferencesState structurally.
      const prefs = {
        toastFilterPermission: undefined,
        toastFilterError: undefined,
        toastFilterTaskComplete: undefined,
        toastFilterArrival: undefined,
      } as unknown as PreferencesState;
      // Every attention type should pass the `!== false` gate under undefined.
      expect(shouldShowToast("error", prefs)).toBe(true);
      expect(shouldShowToast("stop", prefs)).toBe(true);
      expect(shouldShowToast("permission_request", prefs)).toBe(true);
      expect(shouldShowToast("task_completed", prefs)).toBe(true);
      expect(shouldShowToast("subagent_start", prefs)).toBe(true);
      expect(shouldShowToast("background_task_notification", prefs)).toBe(true);
    });
  });
});
