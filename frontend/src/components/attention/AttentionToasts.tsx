"use client";

import {
  useEffect,
  useCallback,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { useShallow } from "zustand/react/shallow";
import {
  useAttentionStore,
  type UrgencyLevel,
  MAX_VISIBLE_TOASTS,
} from "@/stores/attentionStore";
import { useTranslation } from "@/hooks/useTranslation";
import type { TranslationKey } from "@/i18n";
import type { EventType } from "@/types";

const URGENCY_COLORS: Record<UrgencyLevel, string> = {
  critical: "border-red-500 bg-red-950/90 text-red-400",
  high: "border-orange-500 bg-orange-950/90 text-orange-400",
  low: "border-green-500 bg-green-950/90 text-green-400",
  info: "border-blue-500 bg-blue-950/90 text-blue-400",
};

const URGENCY_ICONS: Record<UrgencyLevel, string> = {
  critical: "\u26A0\uFE0F",
  high: "\uD83D\uDD34",
  low: "\u2705",
  info: "\uD83D\uDD35",
};

const TOAST_TITLE_KEYS: Partial<Record<EventType, TranslationKey>> = {
  permission_request: "attention.toast.title.permissionRequest",
  error: "attention.toast.title.error",
  task_completed: "attention.toast.title.taskCompleted",
  subagent_start: "attention.toast.title.agentArrived",
  background_task_notification: "attention.toast.title.backgroundTask",
};

export default function AttentionToasts(): ReactNode {
  const { t } = useTranslation();
  const [desktopRightOffset, setDesktopRightOffset] = useState<number | null>(
    null,
  );
  const toasts = useAttentionStore(
    useShallow((s) => s.toastQueue.filter((t) => !t.dismissed)),
  );
  const dismissToast = useAttentionStore((s) => s.dismissToast);
  const openFocusPopup = useAttentionStore((s) => s.openFocusPopup);

  useEffect(() => {
    const updatePosition = () => {
      const sidebar = document.querySelector<HTMLElement>(
        "[data-agent-status-sidebar]",
      );
      if (window.innerWidth < 768 || !sidebar) {
        setDesktopRightOffset(null);
        return;
      }

      const sidebarRect = sidebar.getBoundingClientRect();
      setDesktopRightOffset(
        Math.max(12, window.innerWidth - sidebarRect.left + 12),
      );
    };

    updatePosition();
    window.addEventListener("resize", updatePosition);
    const sidebar = document.querySelector<HTMLElement>(
      "[data-agent-status-sidebar]",
    );
    const resizeObserver =
      typeof ResizeObserver === "undefined"
        ? null
        : new ResizeObserver(updatePosition);
    if (sidebar && resizeObserver) resizeObserver.observe(sidebar);

    return () => {
      window.removeEventListener("resize", updatePosition);
      resizeObserver?.disconnect();
    };
  }, []);

  const handleToastClick = useCallback(
    (toast: (typeof toasts)[number]) => {
      if (toast.agentId) {
        openFocusPopup(toast.agentId, window.innerWidth / 2, 120);
      }
      dismissToast(toast.id);
    },
    [dismissToast, openFocusPopup],
  );

  if (toasts.length === 0) return null;

  return (
    <div
      className="fixed top-20 right-3 md:right-[21rem] z-40 flex max-h-[min(16rem,calc(100vh-6rem))] w-72 flex-col gap-2 overflow-hidden pointer-events-none"
      style={
        desktopRightOffset === null
          ? undefined
          : { right: `${desktopRightOffset}px` }
      }
    >
      {toasts.slice(0, MAX_VISIBLE_TOASTS).map((toast) => (
        <ToastItem
          key={toast.id}
          toast={toast}
          headline={
            toast.agentName ??
            t(TOAST_TITLE_KEYS[toast.eventType] ?? "attention.toast.event")
          }
          onClick={() => handleToastClick(toast)}
          onDismiss={() => dismissToast(toast.id)}
        />
      ))}
    </div>
  );
}

function ToastItem({
  toast,
  headline,
  onClick,
  onDismiss,
}: {
  toast: {
    id: string;
    urgencyLevel: UrgencyLevel;
    description: string;
    autoDismissMs: number | null;
  };
  headline: string;
  onClick: () => void;
  onDismiss: () => void;
}): ReactNode {
  const { t } = useTranslation();
  const colorClass = URGENCY_COLORS[toast.urgencyLevel];
  const icon = URGENCY_ICONS[toast.urgencyLevel];
  const onDismissRef = useRef(onDismiss);

  useEffect(() => {
    onDismissRef.current = onDismiss;
  }, [onDismiss]);

  useEffect(() => {
    if (toast.autoDismissMs === null) return;
    const timer = setTimeout(
      () => onDismissRef.current(),
      toast.autoDismissMs,
    );
    return () => clearTimeout(timer);
  }, [toast.autoDismissMs, toast.id]);

  return (
    <div
      className={`pointer-events-auto flex items-start gap-2 px-3 py-2 rounded-lg border cursor-pointer animate-in slide-in-from-right-2 duration-300 ${colorClass}`}
      onClick={onClick}
      role="alert"
    >
      <span className="text-sm shrink-0">{icon}</span>
      <div className="flex-1 min-w-0">
        <p className="text-xs font-bold truncate">{headline}</p>
        {toast.description && (
          <p className="text-[11px] opacity-80 truncate">{toast.description}</p>
        )}
      </div>
      <button
        onClick={(e) => {
          e.stopPropagation();
          onDismiss();
        }}
        className="text-xs opacity-50 hover:opacity-100 shrink-0"
        aria-label={t("attention.toast.dismiss")}
      >
        {"\u2715"}
      </button>
    </div>
  );
}
