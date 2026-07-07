/**
 * Attention-toast filter.
 *
 * Owns the "which events generate toasts, and do the user's preferences
 * suppress them" decision that was previously inlined in `useWebSocketEvents`.
 * Pure-TS: no React, no store reads — the {@link PreferencesState} is supplied
 * by the caller so the function is unit-testable in isolation. See QA-005.
 */

import type { EventType } from "@/types";
import type { PreferencesState } from "@/stores/preferencesStore";

/**
 * Event types that drive the attention-store toast pipeline.
 *
 * Only these types ever reach `shouldShowToast`; any other type returns
 * `false`. Kept as a module-level `ReadonlySet` so the per-message allocation
 * from the original inline block is avoided.
 */
const ATTENTION_EVENT_TYPES: ReadonlySet<EventType> = new Set<EventType>([
  "permission_request",
  "error",
  "stop",
  "task_completed",
  "subagent_start",
  "background_task_notification",
]);

/**
 * Map each attention event type to the preference that can suppress it.
 *
 * Note the deliberate sharing: `stop` reuses the error-pref toggle and
 * `subagent_start` / `background_task_notification` both reuse the arrival
 * toggle. Mirrors the original inline `filterMap` exactly.
 */
function filterEnabled(eventType: EventType, prefs: PreferencesState): boolean {
  switch (eventType) {
    case "permission_request":
      return prefs.toastFilterPermission;
    case "error":
    case "stop":
      return prefs.toastFilterError;
    case "task_completed":
      return prefs.toastFilterTaskComplete;
    case "subagent_start":
    case "background_task_notification":
      return prefs.toastFilterArrival;
    default:
      // An attention type missing from the map defaults to showing the toast.
      // This preserves the original inline `filterMap[type] !== false`
      // semantics, where an undefined value fell through to "show".
      return true;
  }
}

/**
 * Decide whether a toast should be shown for the given event type.
 *
 * Returns `false` for any type outside {@link ATTENTION_EVENT_TYPES}.
 * Otherwise returns the preference gate, with the same "absent pref ⇒ show"
 * default the original inline block used.
 *
 * Pure and side-effect free; safe to call from render or event handlers.
 */
export function shouldShowToast(
  eventType: EventType,
  prefs: PreferencesState,
): boolean {
  if (!ATTENTION_EVENT_TYPES.has(eventType)) return false;
  return filterEnabled(eventType, prefs) !== false;
}
