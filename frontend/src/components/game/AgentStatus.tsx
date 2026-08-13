/**
 * AgentStatus - Active agents panel
 *
 * Shows detailed agent information including name, state, task, last tool call,
 * and internal game state. Designed for 4 agents visible with scrollbar for more.
 */

"use client";

import { useGameStore, selectAgents, selectBoss } from "@/stores/gameStore";
import { useShallow } from "zustand/react/shallow";
import { useTranslation } from "@/hooks/useTranslation";
import {
  Users,
  Briefcase,
  Terminal,
  Activity,
  MapPin,
} from "lucide-react";
import { codexSecondaryLabel } from "@/utils/codexPresentation";
import {
  getDisplayAgentStatus,
  getMainAgentName,
  translateAgentStatus,
} from "@/utils/displayLabels";

function getStatusColor(state: string) {
  switch (state) {
    case "error":
      return "bg-red-500/20 text-red-400 border-red-500/40";
    case "departing":
      return "bg-rose-500/20 text-rose-400 border-rose-500/40";
    case "walking":
      return "bg-indigo-500/20 text-indigo-400 border-indigo-500/40";
    case "thinking":
      return "bg-violet-500/20 text-violet-300 border-violet-500/40";
    case "preparing":
      return "bg-purple-500/20 text-purple-300 border-purple-500/40";
    case "working":
      return "bg-amber-500/20 text-amber-400 border-amber-500/40";
    case "reviewing":
      return "bg-blue-500/20 text-blue-400 border-blue-500/40";
    case "waiting":
      return "bg-cyan-500/20 text-cyan-400 border-cyan-500/40";
    case "completed":
      return "bg-emerald-500/20 text-emerald-300 border-emerald-500/40";
    case "idle":
      return "bg-emerald-500/20 text-emerald-400 border-emerald-500/40";
    default:
      return "bg-slate-800 text-slate-400 border-slate-700";
  }
}

export function AgentStatus() {
  const { t } = useTranslation();
  const agents = useGameStore(useShallow(selectAgents));
  const boss = useGameStore(selectBoss);
  const agentArray = Array.from(agents.values());
  const showMain = Boolean(boss.name || boss.source === "codex");
  const totalCount = agentArray.length + (showMain ? 1 : 0);

  // Sort by number for consistent ordering
  agentArray.sort((a, b) => a.number - b.number);

  return (
    <div className="flex flex-col bg-slate-950 border border-slate-800 rounded-lg overflow-hidden font-mono text-xs h-full">
      {/* Header */}
      <div className="bg-slate-900 px-3 py-2 border-b border-slate-800 flex items-center justify-between flex-shrink-0">
        <div className="flex items-center gap-2 text-slate-300 font-bold uppercase tracking-wider text-[11px]">
          <Users size={14} className="text-blue-400" />
          {t("agentStatus.title")}
        </div>
        <div className="flex items-center gap-1">
          <span className="text-2xl font-bold text-slate-200 tabular-nums">
            {totalCount}
          </span>
          <span className="text-slate-500 text-[10px]">
            {t("agentStatus.agents", { count: totalCount })}
          </span>
        </div>
      </div>

      {/* Agent list - scrollable, fills remaining height */}
      <div className="flex-grow overflow-y-auto p-2 space-y-2 min-h-0">
        {showMain && (
          <div className="bg-slate-900/60 border border-amber-500/30 rounded-md overflow-hidden">
            <div
              className="flex items-center justify-between px-2 py-1.5 border-b border-slate-800/50"
              style={{ borderLeftWidth: 3, borderLeftColor: "#f59e0b" }}
            >
              <div className="flex items-center gap-2 min-w-0">
                <span className="font-bold text-amber-300 truncate">
                  {boss.name || getMainAgentName(boss.source)}
                </span>
                {codexSecondaryLabel(
                  boss.source,
                  boss.model,
                  boss.backendState === "waiting",
                ) && (
                  <span className="text-slate-500 text-[9px] truncate max-w-32">
                    {codexSecondaryLabel(
                      boss.source,
                      boss.model,
                      boss.backendState === "waiting",
                    )}
                  </span>
                )}
                <span className="text-slate-600 text-[9px] flex-shrink-0">
                  MAIN
                </span>
              </div>
            </div>
            <div className="px-2 py-1.5 space-y-1.5">
              <div className="flex items-start gap-2">
                <Briefcase
                  size={11}
                  className="text-slate-500 mt-0.5 flex-shrink-0"
                />
                <div className="text-slate-300 text-[11px] leading-tight min-w-0">
                  {boss.currentTask ? (
                    <span className="line-clamp-2">{boss.currentTask}</span>
                  ) : (
                    <span className="text-slate-600 italic">
                      {t("agentStatus.noTaskSummary")}
                    </span>
                  )}
                </div>
              </div>
              <div className="flex items-start gap-2">
                <Terminal
                  size={11}
                  className="text-slate-500 mt-0.5 flex-shrink-0"
                />
                <div className="text-[11px] leading-tight min-w-0">
                  {boss.lastToolName || boss.bubble.content?.text ? (
                    <span className="text-blue-400 line-clamp-2">
                      {boss.lastToolName || boss.bubble.content?.text}
                    </span>
                  ) : (
                    <span className="text-slate-600 italic">
                      {t("agentStatus.noRecentToolCall")}
                    </span>
                  )}
                </div>
              </div>
              <div className="flex items-center gap-2 pt-1">
                <Activity size={10} className="text-slate-500" />
                {(() => {
                  const status = getDisplayAgentStatus(
                    String(boss.backendState),
                    "idle",
                  );
                  return (
                    <span
                      className={`px-1.5 py-0.5 rounded text-[9px] uppercase font-semibold border ${getStatusColor(status)}`}
                    >
                      {translateAgentStatus(t, status)}
                    </span>
                  );
                })()}
              </div>
            </div>
          </div>
        )}

        {agentArray.length === 0 && !showMain ? (
          <div className="text-slate-600 italic p-4 text-center">
            {t("agentStatus.noAgents")}
          </div>
        ) : (
          agentArray.map((agent) => (
            <div
              key={agent.id}
              className="bg-slate-900/60 border border-slate-800 rounded-md overflow-hidden hover:border-slate-700 transition-colors"
            >
              {/* Agent header with name and color */}
              <div
                className="flex items-center justify-between px-2 py-1.5 border-b border-slate-800/50"
                style={{ borderLeftWidth: 3, borderLeftColor: agent.color }}
              >
                <div className="flex items-center gap-2 min-w-0">
                  <span className="font-bold text-slate-100 truncate">
                    {agent.name || `${t("agentStatus.agent")} #${agent.number}`}
                  </span>
                  {codexSecondaryLabel(
                    agent.source,
                    agent.model,
                    agent.backendState === "waiting",
                    agent.agentType,
                  ) && (
                    <span
                      className="text-slate-500 text-[9px] truncate max-w-24"
                      title={
                        codexSecondaryLabel(
                          agent.source,
                          agent.model,
                          agent.backendState === "waiting",
                          agent.agentType,
                        ) ?? undefined
                      }
                    >
                      {codexSecondaryLabel(
                        agent.source,
                        agent.model,
                        agent.backendState === "waiting",
                        agent.agentType,
                      )}
                    </span>
                  )}
                  <span className="text-slate-600 text-[9px] flex-shrink-0">
                    #{agent.id.slice(0, 7)}
                  </span>
                </div>
                {agent.desk && (
                  <span className="text-slate-500 text-[10px] flex items-center gap-1 flex-shrink-0">
                    <MapPin size={10} />
                    {t("agentStatus.desk")} {agent.desk}
                  </span>
                )}
              </div>

              {/* Agent details */}
              <div className="px-2 py-1.5 space-y-1.5">
                {/* Task/Prompt Summary */}
                <div className="flex items-start gap-2">
                  <Briefcase
                    size={11}
                    className="text-slate-500 mt-0.5 flex-shrink-0"
                  />
                  <div className="text-slate-300 text-[11px] leading-tight min-w-0">
                    {agent.currentTask ? (
                      <span className="line-clamp-2">{agent.currentTask}</span>
                    ) : (
                      <span className="text-slate-600 italic">
                        {t("agentStatus.noTaskSummary")}
                      </span>
                    )}
                  </div>
                </div>

                {/* Last Tool Call */}
                <div className="flex items-start gap-2">
                  <Terminal
                    size={11}
                    className="text-slate-500 mt-0.5 flex-shrink-0"
                  />
                  <div className="text-[11px] leading-tight min-w-0">
                    {agent.bubble.content ? (
                      <span className="text-blue-400 line-clamp-2">
                        <span className="mr-1">
                          {agent.bubble.content.icon}
                        </span>
                        {agent.bubble.content.text}
                      </span>
                    ) : (
                      <span className="text-slate-600 italic">
                        {t("agentStatus.noRecentToolCall")}
                      </span>
                    )}
                  </div>
                </div>

                {/* Single user-facing state badge. Backend and animation internals
                    are intentionally collapsed into one prioritized status. */}
                <div className="flex items-center gap-2 pt-1">
                  <div className="flex items-center gap-1">
                    <Activity size={10} className="text-slate-500" />
                    {(() => {
                      const status = getDisplayAgentStatus(
                        agent.backendState,
                        agent.phase,
                      );
                      return (
                        <span
                          className={`px-1.5 py-0.5 rounded text-[9px] uppercase font-semibold border ${getStatusColor(status)}`}
                        >
                          {translateAgentStatus(t, status)}
                        </span>
                      );
                    })()}
                  </div>
                </div>

                {/* Queue info (if applicable) */}
                {agent.queueType && agent.queueIndex >= 0 && (
                  <div className="text-[9px] text-slate-500 pt-0.5">
                    {t("agentStatus.inQueue", {
                      queueType: agent.queueType,
                      position: agent.queueIndex + 1,
                    })}
                  </div>
                )}
              </div>
            </div>
          ))
        )}
      </div>
    </div>
  );
}
