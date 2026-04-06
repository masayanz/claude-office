/**
 * Centralized event type styling configuration.
 *
 * Single source of truth for event type colors and badge classes.
 * Adding a new event type only requires adding one entry to EVENT_TYPE_STYLES.
 */

interface EventTypeStyle {
  text: string;
  badge: string;
}

const DEFAULT_STYLES: EventTypeStyle = {
  text: "text-slate-400",
  badge: "bg-slate-500/20 text-slate-300 border-slate-500/40",
};

const EVENT_TYPE_STYLES: Record<string, EventTypeStyle> = {
  pre_tool_use: {
    text: "text-amber-400",
    badge: "bg-amber-500/20 text-amber-300 border-amber-500/40",
  },
  post_tool_use: {
    text: "text-emerald-400",
    badge: "bg-emerald-500/20 text-emerald-300 border-emerald-500/40",
  },
  user_prompt_submit: {
    text: "text-cyan-400",
    badge: "bg-cyan-500/20 text-cyan-300 border-cyan-500/40",
  },
  permission_request: {
    text: "text-orange-400",
    badge: "bg-orange-500/20 text-orange-300 border-orange-500/40",
  },
  subagent_start: {
    text: "text-blue-400",
    badge: "bg-blue-500/20 text-blue-300 border-blue-500/40",
  },
  subagent_stop: {
    text: "text-purple-400",
    badge: "bg-purple-500/20 text-purple-300 border-purple-500/40",
  },
  session_start: {
    text: "text-green-400",
    badge: "bg-green-500/20 text-green-300 border-green-500/40",
  },
  session_end: {
    text: "text-slate-500",
    badge: "bg-slate-500/20 text-slate-300 border-slate-500/40",
  },
  stop: {
    text: "text-rose-400",
    badge: "bg-rose-500/20 text-rose-300 border-rose-500/40",
  },
  error: {
    text: "text-red-500",
    badge: "bg-red-500/20 text-red-300 border-red-500/40",
  },
  background_task_notification: {
    text: "text-teal-400",
    badge: "bg-teal-500/20 text-teal-300 border-teal-500/40",
  },
};

/**
 * Get the text color class for an event type.
 * @param type - The event type string
 * @returns Tailwind text color class
 */
export function getEventTypeTextColor(type: string): string {
  return (EVENT_TYPE_STYLES[type] ?? DEFAULT_STYLES).text;
}

/**
 * Get the badge classes for an event type.
 * @param type - The event type string
 * @returns Tailwind classes for badge styling (bg, text, border)
 */
export function getEventTypeBadgeClasses(type: string): string {
  return (EVENT_TYPE_STYLES[type] ?? DEFAULT_STYLES).badge;
}
