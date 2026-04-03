export function getEventTypeTextColor(type: string): string {
  switch (type) {
    case "pre_tool_use":
      return "text-amber-400";
    case "post_tool_use":
      return "text-emerald-400";
    case "user_prompt_submit":
      return "text-cyan-400";
    case "permission_request":
      return "text-orange-400";
    case "subagent_start":
      return "text-blue-400";
    case "subagent_stop":
      return "text-purple-400";
    case "session_start":
      return "text-green-400";
    case "session_end":
      return "text-slate-500";
    case "stop":
      return "text-rose-400";
    case "error":
      return "text-red-500";
    case "background_task_notification":
      return "text-teal-400";
    default:
      return "text-slate-400";
  }
}

export function getEventTypeBadgeClasses(type: string): string {
  switch (type) {
    case "pre_tool_use":
      return "bg-amber-500/20 text-amber-300 border-amber-500/40";
    case "post_tool_use":
      return "bg-emerald-500/20 text-emerald-300 border-emerald-500/40";
    case "user_prompt_submit":
      return "bg-cyan-500/20 text-cyan-300 border-cyan-500/40";
    case "permission_request":
      return "bg-orange-500/20 text-orange-300 border-orange-500/40";
    case "subagent_start":
      return "bg-blue-500/20 text-blue-300 border-blue-500/40";
    case "subagent_stop":
      return "bg-purple-500/20 text-purple-300 border-purple-500/40";
    case "session_start":
      return "bg-green-500/20 text-green-300 border-green-500/40";
    case "session_end":
      return "bg-slate-500/20 text-slate-300 border-slate-500/40";
    case "stop":
      return "bg-rose-500/20 text-rose-300 border-rose-500/40";
    case "error":
      return "bg-red-500/20 text-red-300 border-red-500/40";
    case "background_task_notification":
      return "bg-teal-500/20 text-teal-300 border-teal-500/40";
    default:
      return "bg-slate-500/20 text-slate-300 border-slate-500/40";
  }
}
