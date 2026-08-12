"use client";

import { create } from "zustand";
import { apiFetch } from "@/utils/api";
import { usePreferencesStore } from "@/stores/preferencesStore";

export interface AppSettings {
  language: "ja" | "en" | "es" | "pt-BR";
  backend_host: string;
  backend_port: number;
  frontend_host: string;
  frontend_port: number;
  open_browser_on_start: boolean;
  browser_mode: "normal" | "app";
  restore_codex_sessions: boolean;
  restore_window_minutes: number;
  company_name: string;
  owner_name: string;
  owner_title: string;
  owner_message: string;
  owner_image_filename: string | null;
  owner_image_url: string | null;
  board_mode: BoardMode;
  daily_goals: string[];
  weekly_goals: string[];
  board_memo: string;
  custom_board_title: string;
  custom_board_message: string;
  board_auto_rotate: boolean;
  board_rotate_seconds: number;
  warning?: string;
  owner_image_warning?: string;
}

export type BoardMode =
  "todo" | "daily_goals" | "weekly_goals" | "memo" | "custom";

interface AppSettingsState {
  settings: AppSettings | null;
  isLoaded: boolean;
  ownerImageError: string | null;
  loadAppSettings: () => Promise<void>;
  updateAppSettings: (
    updates: Partial<AppSettings>,
  ) => Promise<AppSettings | null>;
  uploadOwnerImage: (file: File) => Promise<AppSettings | null>;
  resetOwnerImage: () => Promise<AppSettings | null>;
}

function hasSameSettings(
  current: AppSettings | null,
  next: AppSettings,
): boolean {
  return current !== null && JSON.stringify(current) === JSON.stringify(next);
}

export const useAppSettingsStore = create<AppSettingsState>((set, get) => ({
  settings: null,
  isLoaded: false,
  ownerImageError: null,

  loadAppSettings: async () => {
    try {
      const response = await apiFetch("/api/v1/settings");
      if (!response.ok) return;
      const settings = (await response.json()) as AppSettings;
      if (!hasSameSettings(get().settings, settings)) {
        set({ settings, isLoaded: true });
      }
      usePreferencesStore.setState({ language: settings.language });
    } catch (error) {
      console.warn("[app-settings] Failed to fetch:", error);
    }
  },

  updateAppSettings: async (updates) => {
    try {
      const response = await apiFetch("/api/v1/settings", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(updates),
      });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const settings = (await response.json()) as AppSettings;
      set({ settings, isLoaded: true });
      usePreferencesStore.setState({ language: settings.language });
      return settings;
    } catch (error) {
      console.warn("[app-settings] Failed to save:", error);
      return null;
    }
  },

  uploadOwnerImage: async (file) => {
    try {
      const form = new FormData();
      form.append("file", file);
      const response = await apiFetch("/api/v1/settings/owner-image", {
        method: "POST",
        body: form,
      });
      if (!response.ok) {
        const payload = (await response.json().catch(() => null)) as {
          detail?: unknown;
        } | null;
        throw new Error(
          typeof payload?.detail === "string"
            ? payload.detail
            : `HTTP ${response.status}`,
        );
      }
      const settings = (await response.json()) as AppSettings;
      set({ settings, isLoaded: true, ownerImageError: null });
      return settings;
    } catch (error) {
      console.warn("[app-settings] Failed to upload owner image:", error);
      set({
        ownerImageError:
          error instanceof Error ? error.message : "画像の保存に失敗しました。",
      });
      return null;
    }
  },

  resetOwnerImage: async () => {
    try {
      const response = await apiFetch("/api/v1/settings/owner-image", {
        method: "DELETE",
      });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const settings = (await response.json()) as AppSettings;
      set({ settings, isLoaded: true, ownerImageError: null });
      return settings;
    } catch (error) {
      console.warn("[app-settings] Failed to reset owner image:", error);
      set({
        ownerImageError:
          error instanceof Error ? error.message : "画像の保存に失敗しました。",
      });
      return null;
    }
  },
}));
