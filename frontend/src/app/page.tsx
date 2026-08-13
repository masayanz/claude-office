/**
 * AI Office Viewer - Main Page
 *
 * Uses the unified Zustand store, XState machines, and OfficeGame component.
 * Layout and logic are delegated to extracted components and custom hooks.
 *
 * Navigation modes:
 * - "single" (default): the original flat layout with sidebar + canvas + sidebar
 * - "building": cross-section building view (when user configures floors)
 * - "floor": floor-level view wrapping the office canvas
 */

"use client";

import dynamic from "next/dynamic";
import { useState, useEffect, useCallback, useRef } from "react";
import { useWebSocketEvents } from "@/hooks/useWebSocketEvents";
import { useSessions } from "@/hooks/useSessions";
import { useSessionSwitch } from "@/hooks/useSessionSwitch";
import { useFloorConfig } from "@/hooks/useFloorConfig";
import {
  useGameStore,
  selectIsConnected,
  selectDebugMode,
  selectBoss,
} from "@/stores/gameStore";
import { useNavigationStore } from "@/stores/navigationStore";
import { useTourStore } from "@/stores/tourStore";
import { apiFetch, initApiKeyFromBrowser } from "@/utils/api";
import { Menu, X, Map } from "lucide-react";
import { SessionSidebar } from "@/components/layout/SessionSidebar";
import { MobileDrawer } from "@/components/layout/MobileDrawer";
import { MobileAgentActivity } from "@/components/layout/MobileAgentActivity";
import { RightSidebar } from "@/components/layout/RightSidebar";
import { HeaderControls } from "@/components/layout/HeaderControls";
import {
  StatusToast,
  type StatusMessage,
} from "@/components/layout/StatusToast";
import Modal from "@/components/overlay/Modal";
import SettingsModal from "@/components/overlay/SettingsModal";
import type { SettingsTab } from "@/components/overlay/SettingsModal";
import { Breadcrumb } from "@/components/navigation/Breadcrumb";
import { ViewTransition } from "@/components/navigation/ViewTransition";
import { BuildingView } from "@/components/views/BuildingView";
import { FloorView } from "@/components/views/FloorView";
import { CommandCenterView } from "@/components/command/CommandCenterView";
import { ReplayPanel } from "@/components/replay/ReplayPanel";
import { TourOverlay } from "@/components/tour/TourOverlay";
import CommandBar from "@/components/attention/CommandBar";
import AttentionToasts from "@/components/attention/AttentionToasts";
import AgentPopup from "@/components/attention/AgentPopup";
import { useAttentionStore } from "@/stores/attentionStore";
import { usePreferencesStore } from "@/stores/preferencesStore";
import { useAppSettingsStore } from "@/stores/appSettingsStore";
import { useTranslation } from "@/hooks/useTranslation";
import type { Session } from "@/hooks/useSessions";
import { PRODUCT_NAME } from "@/config/branding";
import {
  emptyCodexIntegrationPresentation,
  presentCodexIntegrationStatus,
} from "@/utils/codexIntegrationPresentation";

// ============================================================================
// DYNAMIC IMPORT
// ============================================================================

function LoadingFallback() {
  const { t } = useTranslation();
  return (
    <div className="w-full h-full bg-slate-900 animate-pulse flex items-center justify-center text-white font-mono text-center">
      {t("app.initializingSystems")}
    </div>
  );
}

const OfficeGame = dynamic(
  () =>
    import("@/components/game/OfficeGame").then((m) => ({
      default: m.OfficeGame,
    })),
  {
    ssr: false,
    loading: () => <LoadingFallback />,
  },
);

interface CodexRestoreResponse {
  state?: string;
  status?: string;
  session_count?: number;
  agent_count?: number;
  message?: string;
}

interface NormalizedCodexRestoreResult {
  state: "checking" | "succeeded" | "failed" | "unknown";
  sessionCount: number;
  agentCount: number;
  message?: string;
}

const CODEX_RESTORE_POLL_INTERVAL_MS = 500;
const CODEX_RESTORE_TIMEOUT_MS = 10_000;

function normalizeCodexRestoreResponse(
  payload: unknown,
): NormalizedCodexRestoreResult {
  const response =
    payload && typeof payload === "object"
      ? (payload as CodexRestoreResponse)
      : {};
  const rawStates = [response.state, response.status]
    .filter((value): value is string => typeof value === "string")
    .map((value) => value.toLowerCase());

  let state: NormalizedCodexRestoreResult["state"] = "unknown";
  if (
    rawStates.some((value) => ["failed", "failure", "error"].includes(value))
  ) {
    state = "failed";
  } else if (
    rawStates.some((value) =>
      ["succeeded", "success", "completed", "complete", "done", "ok"].includes(
        value,
      ),
    )
  ) {
    state = "succeeded";
  } else if (
    rawStates.some((value) =>
      [
        "checking",
        "pending",
        "queued",
        "running",
        "in_progress",
        "in-progress",
      ].includes(value),
    )
  ) {
    state = "checking";
  }

  const normalizedCount = (value: unknown): number =>
    typeof value === "number" && Number.isFinite(value) && value >= 0
      ? Math.floor(value)
      : 0;

  return {
    state,
    sessionCount: normalizedCount(response.session_count),
    agentCount: normalizedCount(response.agent_count),
    message:
      typeof response.message === "string" ? response.message : undefined,
  };
}

async function waitForCodexRestore(
  initialPayload: unknown,
): Promise<NormalizedCodexRestoreResult> {
  let result = normalizeCodexRestoreResponse(initialPayload);
  if (result.state === "succeeded") return result;
  if (result.state === "failed") {
    throw new Error(result.message || "Codex restore failed");
  }

  const deadline = Date.now() + CODEX_RESTORE_TIMEOUT_MS;
  while (Date.now() < deadline) {
    await new Promise((resolve) =>
      setTimeout(resolve, CODEX_RESTORE_POLL_INTERVAL_MS),
    );
    const response = await apiFetch("/api/v1/codex/restore/status");
    if (!response.ok) {
      throw new Error(`Restore status HTTP ${response.status}`);
    }
    result = normalizeCodexRestoreResponse(await response.json());
    if (result.state === "succeeded") return result;
    if (result.state === "failed") {
      throw new Error(result.message || "Codex restore failed");
    }
  }

  // The bounded scan can still take longer on slow disks. The backend keeps
  // running it, so a client-side polling deadline is not a restore failure.
  return { ...result, state: "checking" };
}

// ============================================================================
// PAGE COMPONENT
// ============================================================================

export default function V2TestPage(): React.ReactNode {
  // ------------------------------------------------------------------
  // i18n
  // ------------------------------------------------------------------
  const { t, language } = useTranslation();

  // ------------------------------------------------------------------
  // UI-only state
  // ------------------------------------------------------------------
  const [isClearModalOpen, setIsClearModalOpen] = useState(false);
  const [isHelpModalOpen, setIsHelpModalOpen] = useState(false);
  const [isSettingsModalOpen, setIsSettingsModalOpen] = useState(false);
  const [settingsInitialTab, setSettingsInitialTab] =
    useState<SettingsTab>("general");
  const [statusMessage, setStatusMessage] = useState<StatusMessage | null>(
    null,
  );
  const [leftSidebarCollapsed, setLeftSidebarCollapsed] = useState(false);
  const [isMobile, setIsMobile] = useState(false);
  const [mobileMenuOpen, setMobileMenuOpen] = useState(false);
  const [aiSummaryEnabled, setAiSummaryEnabled] = useState<boolean | null>(
    null,
  );
  const [codexIntegration, setCodexIntegration] = useState(
    emptyCodexIntegrationPresentation,
  );
  const [isRestoringCodexSessions, setIsRestoringCodexSessions] =
    useState(false);
  const [isReplayMode, setIsReplayMode] = useState(false);
  const codexRestoreInFlight = useRef(false);

  // Session pending delete drives the delete-confirmation modal
  const [sessionPendingDelete, setSessionPendingDelete] =
    useState<Session | null>(null);

  // ------------------------------------------------------------------
  // Status toast helper (stable reference via useCallback)
  // ------------------------------------------------------------------
  const showStatus = useCallback(
    (text: string, type: "info" | "error" | "success" = "info") => {
      setStatusMessage({ text, type });
      setTimeout(() => setStatusMessage(null), 3000);
    },
    [],
  );

  // ------------------------------------------------------------------
  // Session management hooks
  // ------------------------------------------------------------------
  const { sessions, sessionsLoading, sessionId, setSessionId, fetchSessions } =
    useSessions(showStatus);

  const handleRestoreCodexSessions = useCallback(async (): Promise<void> => {
    if (codexRestoreInFlight.current) return;
    codexRestoreInFlight.current = true;
    setIsRestoringCodexSessions(true);
    showStatus(t("status.restoringCodexSessions"), "info");

    try {
      const response = await apiFetch("/api/v1/codex/restore", {
        method: "POST",
      });
      if (!response.ok) {
        throw new Error(`HTTP ${response.status}`);
      }
      const result = await waitForCodexRestore(await response.json());
      if (result.state === "checking") {
        showStatus(t("status.restoringCodexSessions"), "info");
        return;
      }
      await fetchSessions();
      showStatus(
        t("status.codexSessionsRestored", {
          sessionCount: result.sessionCount,
          agentCount: result.agentCount,
        }),
        "success",
      );
    } catch (error) {
      console.warn("[codex-restore] Failed to restore sessions:", error);
      showStatus(t("status.failedRestoreCodexSessions"), "error");
    } finally {
      codexRestoreInFlight.current = false;
      setIsRestoringCodexSessions(false);
    }
  }, [fetchSessions, showStatus, t]);

  const {
    handleSessionSelect,
    handleDeleteSession,
    handleClearDB,
    handleSimulate,
    handleReset,
    handleRenameSession,
  } = useSessionSwitch({ sessionId, setSessionId, fetchSessions, showStatus });

  // ------------------------------------------------------------------
  // Store subscriptions
  // ------------------------------------------------------------------
  const isConnected = useGameStore(selectIsConnected);
  const debugMode = useGameStore(selectDebugMode);
  // ARC-006: subscribe only to the count, not the whole agents Map. The page
  // uses just `agents.size` (badge) — selecting a primitive means this root
  // component re-renders only when an agent is added/removed, not every frame
  // agents move (which was cascading re-renders through header/sidebar/modals).
  const agentCount = useGameStore((s) => s.agents.size);
  const boss = useGameStore(selectBoss);
  const loadPersistedDebugSettings = useGameStore(
    (state) => state.loadPersistedDebugSettings,
  );
  const loadPreferences = usePreferencesStore((s) => s.loadPreferences);
  const loadAppSettings = useAppSettingsStore((s) => s.loadAppSettings);
  const appLanguage = useAppSettingsStore((s) => s.settings?.language);
  const companyName = useAppSettingsStore((s) => s.settings?.company_name);

  // Navigation store
  const view = useNavigationStore((s) => s.view);
  const buildingConfig = useNavigationStore((s) => s.buildingConfig);

  // Onboarding tour now lives in the Help modal (rarely used).
  const startTour = useTourStore((s) => s.startTour);
  const handleStartTour = (): void => {
    const hasBuildingConfig =
      buildingConfig !== null && (buildingConfig.floors.length ?? 0) > 0;
    const mode = view !== "single" && hasBuildingConfig ? "building" : "single";
    startTour(mode);
    setIsHelpModalOpen(false);
  };

  // Active session count gates the Command Center entry point (>= 2).
  const activeSessionCount = sessions.filter(
    (s) => s.status === "active",
  ).length;

  // ------------------------------------------------------------------
  // Floor config + tour initialization
  // ------------------------------------------------------------------
  useFloorConfig();

  // Watch for edit-building requests from BuildingView. Subscribe to the
  // store so the modal-opening setState runs in an event callback (the store
  // notification) rather than in the effect body.
  useEffect(() => {
    return useNavigationStore.subscribe((state) => {
      if (state.pendingEditBuilding) {
        state.consumeEditBuilding();
        setSettingsInitialTab("building");
        setIsSettingsModalOpen(true);
      }
    });
  }, []);

  useEffect(() => {
    if (new URLSearchParams(window.location.search).get("mode") === "replay") {
      useNavigationStore.getState().goToSingle();
      setIsReplayMode(true);
      const url = new URL(window.location.href);
      url.searchParams.delete("mode");
      window.history.replaceState({}, "", url.pathname + (url.search ? url.search : ""));
    }
  }, []);

  useEffect(() => {
    if (isReplayMode && view !== "single") {
      useNavigationStore.getState().goToSingle();
    }
  }, [isReplayMode, view]);

  const loadTourSeen = useTourStore((s) => s.loadTourSeen);
  useEffect(() => {
    loadTourSeen();
  }, [loadTourSeen]);

  // ------------------------------------------------------------------
  // WebSocket connection — reconnects when sessionId changes
  // ------------------------------------------------------------------
  useWebSocketEvents({ sessionId, enabled: !isReplayMode });

  // ------------------------------------------------------------------
  // One-time initialization effects
  // ------------------------------------------------------------------
  useEffect(() => {
    initApiKeyFromBrowser();
    apiFetch("/api/v1/status")
      .then((res) => res.json())
      .then((data: { aiSummaryEnabled: boolean }) => {
        setAiSummaryEnabled(data.aiSummaryEnabled);
      })
      .catch(() => setAiSummaryEnabled(false));
  }, []);

  // Keep the compact Codex status separate from the AI summary setting above.
  // This endpoint only contains counters and timestamps, so polling is cheap
  // and continues to work even when no session WebSocket is open.
  useEffect(() => {
    let disposed = false;

    const refreshCodexIntegration = async () => {
      try {
        const response = await apiFetch("/api/v1/system/integration-status");
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        const payload: unknown = await response.json();
        if (!disposed) {
          setCodexIntegration(presentCodexIntegrationStatus(payload));
        }
      } catch {
        // Keep the last known timestamp, but recompute its freshness so a
        // stale Live badge naturally changes to waiting while disconnected.
        if (!disposed) {
          setCodexIntegration((current) =>
            presentCodexIntegrationStatus({
              codex: {
                last_live_event_at: current.lastLiveEventAt,
                live_event_count: current.liveEventCount,
                restored_sessions: current.restoredSessionCount,
              },
            }),
          );
        }
      }
    };

    void refreshCodexIntegration();
    const interval = window.setInterval(() => void refreshCodexIntegration(), 3000);
    return () => {
      disposed = true;
      window.clearInterval(interval);
    };
  }, []);

  useEffect(() => {
    loadPersistedDebugSettings();
  }, [loadPersistedDebugSettings]);

  useEffect(() => {
    loadPreferences();
  }, [loadPreferences]);

  useEffect(() => {
    void loadAppSettings();
  }, [loadAppSettings]);

  // Manager launches the Viewer with ?settings=office or ?settings=board.
  // Keep the URL usable as a deep link without retaining the command on refresh.
  useEffect(() => {
    const requestedTab = new URLSearchParams(window.location.search).get(
      "settings",
    );
    if (requestedTab === "office" || requestedTab === "board") {
      setSettingsInitialTab(requestedTab);
      setIsSettingsModalOpen(true);
    }
  }, []);

  // The Manager writes the same app-settings JSON directly. Polling keeps an
  // already-open Viewer in sync without requiring a browser reload.
  useEffect(() => {
    const interval = window.setInterval(() => void loadAppSettings(), 3000);
    return () => window.clearInterval(interval);
  }, [loadAppSettings]);

  useEffect(() => {
    if (appLanguage) usePreferencesStore.setState({ language: appLanguage });
  }, [appLanguage]);

  useEffect(() => {
    document.documentElement.lang = language;
  }, [language]);

  useEffect(() => {
    document.title = companyName
      ? `${companyName} - ${PRODUCT_NAME}`
      : PRODUCT_NAME;
  }, [companyName]);

  // ------------------------------------------------------------------
  // Mobile breakpoint detection
  // ------------------------------------------------------------------
  useEffect(() => {
    const checkMobile = () => setIsMobile(window.innerWidth < 768);
    checkMobile();
    window.addEventListener("resize", checkMobile);
    return () => window.removeEventListener("resize", checkMobile);
  }, []);

  // ------------------------------------------------------------------
  // Cmd+K / Ctrl+K command bar toggle
  // ------------------------------------------------------------------
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (document.querySelector("[role='dialog'][aria-modal='true']")) return;
      if ((e.metaKey || e.ctrlKey) && e.key === "k") {
        e.preventDefault();
        const prefs = usePreferencesStore.getState();
        if (!prefs.commandBarEnabled) return;
        const { isCommandBarOpen, closeCommandBar, openCommandBar } =
          useAttentionStore.getState();
        if (isCommandBarOpen) {
          closeCommandBar();
        } else {
          openCommandBar();
        }
      }
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, []);

  // ------------------------------------------------------------------
  // Derived handlers
  // ------------------------------------------------------------------
  const handleToggleDebug = () =>
    useGameStore.getState().setDebugMode(!debugMode);

  const handleConfirmClearDB = async () => {
    setIsClearModalOpen(false);
    await handleClearDB();
  };

  const handleConfirmDeleteSession = async () => {
    if (!sessionPendingDelete) return;
    const pending = sessionPendingDelete;
    setSessionPendingDelete(null);
    await handleDeleteSession(pending);
  };

  // ------------------------------------------------------------------
  // Render
  // ------------------------------------------------------------------
  return (
    <main className="flex h-screen flex-col bg-neutral-950 p-2 overflow-hidden relative">
      {/* ----------------------------------------------------------------
          Modals
      ---------------------------------------------------------------- */}
      <Modal
        isOpen={isClearModalOpen}
        onClose={() => setIsClearModalOpen(false)}
        title={t("modal.confirmDbWipe")}
        footer={
          <>
            <button
              onClick={() => setIsClearModalOpen(false)}
              className="px-4 py-2 text-slate-400 hover:text-white text-sm font-bold transition-colors"
            >
              {t("modal.cancel")}
            </button>
            <button
              onClick={handleConfirmClearDB}
              className="px-4 py-2 bg-rose-600 hover:bg-rose-500 text-white text-sm font-bold rounded-lg transition-colors shadow-lg shadow-rose-900/20"
            >
              {t("modal.wipeAllData")}
            </button>
          </>
        }
      >
        <p>{t("modal.wipeWarning")}</p>
      </Modal>

      <Modal
        isOpen={isHelpModalOpen}
        onClose={() => setIsHelpModalOpen(false)}
        title={t("modal.keyboardShortcuts")}
        footer={
          <>
            <button
              onClick={handleStartTour}
              className="flex items-center gap-2 px-4 py-2 bg-orange-500/10 hover:bg-orange-500/20 text-orange-400 border border-orange-500/30 text-sm font-bold rounded-lg transition-colors"
            >
              <Map size={16} />
              {t("help.tour")}
            </button>
            <button
              onClick={() => setIsHelpModalOpen(false)}
              className="px-4 py-2 bg-slate-700 hover:bg-slate-600 text-white text-sm font-bold rounded-lg transition-colors"
            >
              {t("modal.close")}
            </button>
          </>
        }
      >
        <div className="space-y-3 font-mono text-sm">
          <div className="flex justify-between items-center py-2 border-b border-slate-700">
            <kbd className="px-2 py-1 bg-slate-800 rounded text-white font-bold">
              D
            </kbd>
            <span className="text-slate-300">{t("modal.toggleDebug")}</span>
          </div>
          <div className="flex justify-between items-center py-2 border-b border-slate-700">
            <kbd className="px-2 py-1 bg-slate-800 rounded text-white font-bold">
              P
            </kbd>
            <span className="text-slate-300">{t("modal.showAgentPaths")}</span>
          </div>
          <div className="flex justify-between items-center py-2 border-b border-slate-700">
            <kbd className="px-2 py-1 bg-slate-800 rounded text-white font-bold">
              Q
            </kbd>
            <span className="text-slate-300">{t("modal.showQueueSlots")}</span>
          </div>
          <div className="flex justify-between items-center py-2">
            <kbd className="px-2 py-1 bg-slate-800 rounded text-white font-bold">
              L
            </kbd>
            <span className="text-slate-300">{t("modal.showPhaseLabels")}</span>
          </div>
        </div>
      </Modal>

      <SettingsModal
        isOpen={isSettingsModalOpen}
        onClose={() => setIsSettingsModalOpen(false)}
        initialTab={settingsInitialTab}
      />

      <Modal
        isOpen={sessionPendingDelete !== null}
        onClose={() => setSessionPendingDelete(null)}
        title={t("modal.deleteSession")}
        footer={
          <>
            <button
              onClick={() => setSessionPendingDelete(null)}
              className="px-4 py-2 text-slate-400 hover:text-white text-sm font-bold transition-colors"
            >
              {t("modal.cancel")}
            </button>
            <button
              onClick={handleConfirmDeleteSession}
              className="px-4 py-2 bg-rose-600 hover:bg-rose-500 text-white text-sm font-bold rounded-lg transition-colors shadow-lg shadow-rose-900/20"
            >
              {t("modal.delete")}
            </button>
          </>
        }
      >
        <p>
          {t("modal.deleteSessionConfirm")}{" "}
          <span className="font-mono text-purple-400">
            {sessionPendingDelete?.projectName ||
              sessionPendingDelete?.id.slice(0, 8)}
          </span>
          ?
        </p>
        <p className="text-slate-400 text-sm mt-2">
          {t("modal.deleteSessionWarning")}{" "}
          {sessionPendingDelete?.eventCount ?? 0} {t("modal.events")}.{" "}
          {t("modal.cannotBeUndone")}
        </p>
      </Modal>

      {/* ----------------------------------------------------------------
          Header
      ---------------------------------------------------------------- */}
      <header className="flex justify-between items-center mb-2 px-1 relative h-12">
        <div className="flex items-center gap-3">
          {isMobile && (
            <button
              onClick={() => setMobileMenuOpen(!mobileMenuOpen)}
              aria-label={mobileMenuOpen ? t("modal.close") : t("mobile.menu")}
              aria-expanded={mobileMenuOpen}
              className="p-2 bg-slate-800 hover:bg-slate-700 rounded-lg text-white transition-colors"
            >
              {mobileMenuOpen ? <X size={20} /> : <Menu size={20} />}
            </button>
          )}
          <div>
            <h1
              className={`font-bold text-white tracking-tight flex items-center gap-2 ${
                isMobile ? "text-lg" : "text-2xl"
              }`}
            >
              <span className="text-orange-500">
                {companyName || PRODUCT_NAME}
              </span>
              {companyName && !isMobile && <span>- {PRODUCT_NAME}</span>}
              {!isMobile && (
                <span className="text-xs font-mono font-normal px-2 py-0.5 bg-slate-800 rounded text-slate-400 border border-slate-700">
                  v0.24.1
                </span>
              )}
            </h1>
            {!isMobile && (
              <p className="text-xs text-slate-400">{t("app.subtitle")}</p>
            )}
          </div>

          {/* Breadcrumb — only when in building/floor view */}
          {!isMobile && <Breadcrumb />}
        </div>

        {!isMobile && (
          <HeaderControls
            isConnected={isConnected}
            debugMode={debugMode}
            aiSummaryEnabled={aiSummaryEnabled}
            codexIntegration={codexIntegration}
            isRestoringCodexSessions={isRestoringCodexSessions}
            activeSessionCount={activeSessionCount}
            onSimulate={handleSimulate}
            onRestoreCodexSessions={handleRestoreCodexSessions}
            onReset={handleReset}
            onClearDB={() => setIsClearModalOpen(true)}
            onToggleDebug={handleToggleDebug}
            onOpenSettings={() => setIsSettingsModalOpen(true)}
            onOpenHelp={() => setIsHelpModalOpen(true)}
            onOpenReplay={() => {
              useNavigationStore.getState().goToSingle();
              setIsReplayMode(true);
            }}
          />
        )}

        {isMobile && (
          <div className="flex items-center gap-2">
            <div
              className={`w-2 h-2 rounded-full ${
                isConnected ? "bg-emerald-400 animate-pulse" : "bg-rose-500"
              }`}
            />
            <span className="text-xs text-slate-400 font-mono">
              {agentCount} {t("header.agents")}
            </span>
          </div>
        )}
      </header>

      {/* ----------------------------------------------------------------
          Mobile Drawer
      ---------------------------------------------------------------- */}
      <MobileDrawer
        isOpen={isMobile && mobileMenuOpen}
        sessions={sessions}
        sessionsLoading={sessionsLoading}
        sessionId={sessionId}
        onClose={() => setMobileMenuOpen(false)}
        onSessionSelect={handleSessionSelect}
        onSimulate={handleSimulate}
        onRestoreCodexSessions={handleRestoreCodexSessions}
        isRestoringCodexSessions={isRestoringCodexSessions}
        onReset={handleReset}
        onClearDB={() => {
          setIsClearModalOpen(true);
          setMobileMenuOpen(false);
        }}
      />

      {/* ----------------------------------------------------------------
          Main Content
      ---------------------------------------------------------------- */}
      {isMobile ? (
        <div className="flex-grow flex flex-col gap-1.5 overflow-hidden min-h-0">
          <div className="flex-[3] border border-slate-800 rounded-lg shadow-2xl bg-slate-900 overflow-hidden relative min-h-0">
            <OfficeGame />
            {isReplayMode && <ReplayPanel onReturnLive={() => {
              useGameStore.getState().resetForSessionSwitch();
              setIsReplayMode(false);
            }} />}
          </div>
          <MobileAgentActivity boss={boss} />
        </div>
      ) : view === "single" ? (
        /* ----------------------------------------------------------------
            Single View (default, original layout)
        ---------------------------------------------------------------- */
        <div className="flex-grow flex gap-2 overflow-hidden min-h-0">
          <SessionSidebar
            sessions={sessions}
            sessionsLoading={sessionsLoading}
            sessionId={sessionId}
            isCollapsed={leftSidebarCollapsed}
            onToggleCollapsed={() =>
              setLeftSidebarCollapsed(!leftSidebarCollapsed)
            }
            onSessionSelect={handleSessionSelect}
            onDeleteSession={setSessionPendingDelete}
            onRenameSession={handleRenameSession}
          />

          <div
            data-tour-id="game-canvas"
            className="flex-grow border border-slate-800 rounded-lg shadow-2xl bg-slate-900 overflow-hidden relative"
          >
            <OfficeGame />
            {isReplayMode && <ReplayPanel onReturnLive={() => {
              useGameStore.getState().resetForSessionSwitch();
              setIsReplayMode(false);
            }} />}
          </div>

          <RightSidebar />
        </div>
      ) : view === "command" ? (
        /* ----------------------------------------------------------------
            Command Center (cross-terminal overview)
        ---------------------------------------------------------------- */
        <CommandCenterView
          sessions={sessions}
          sessionsLoading={sessionsLoading}
          sessionId={sessionId}
          isCollapsed={leftSidebarCollapsed}
          onToggleCollapsed={() =>
            setLeftSidebarCollapsed(!leftSidebarCollapsed)
          }
          onSessionSelect={handleSessionSelect}
          onDeleteSession={setSessionPendingDelete}
          onRenameSession={handleRenameSession}
        />
      ) : (
        /* ----------------------------------------------------------------
            Building / Floor View (animated transitions)
        ---------------------------------------------------------------- */
        <ViewTransition
          view={view}
          buildingView={<BuildingView sessions={sessions} />}
          floorView={
            <FloorView
              sessions={sessions}
              sessionsLoading={sessionsLoading}
              sessionId={sessionId}
              isCollapsed={leftSidebarCollapsed}
              onToggleCollapsed={() =>
                setLeftSidebarCollapsed(!leftSidebarCollapsed)
              }
              onSessionSelect={handleSessionSelect}
              onDeleteSession={setSessionPendingDelete}
              onRenameSession={handleRenameSession}
            />
          }
        />
      )}

      {/* ----------------------------------------------------------------
          Attention System
      ---------------------------------------------------------------- */}
      <CommandBar />
      <AttentionToasts />
      <AgentPopup />

      {/* ----------------------------------------------------------------
          Tour Overlay
      ---------------------------------------------------------------- */}
      <TourOverlay />

      {/* ----------------------------------------------------------------
          Status Toast — pinned bottom-center so it never covers the header
      ---------------------------------------------------------------- */}
      <div className="fixed bottom-6 left-1/2 -translate-x-1/2 z-50 flex items-center pointer-events-none">
        <StatusToast message={statusMessage} />
      </div>
    </main>
  );
}
