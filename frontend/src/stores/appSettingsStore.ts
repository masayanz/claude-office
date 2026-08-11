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
  owner_image_filename: string | null;
  owner_image_url: string | null;
  warning?: string;
}

interface AppSettingsState {
  settings: AppSettings | null;
  isLoaded: boolean;
  loadAppSettings: () => Promise<void>;
  updateAppSettings: (
    updates: Partial<AppSettings>,
  ) => Promise<AppSettings | null>;
  uploadOwnerImage: (file: File) => Promise<AppSettings | null>;
}

export const useAppSettingsStore = create<AppSettingsState>((set) => ({
  settings: null,
  isLoaded: false,

  loadAppSettings: async () => {
    try {
      const response = await apiFetch("/api/v1/settings");
      if (!response.ok) return;
      const settings = (await response.json()) as AppSettings;
      set({ settings, isLoaded: true });
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
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const settings = (await response.json()) as AppSettings;
      set({ settings, isLoaded: true });
      return settings;
    } catch (error) {
      console.warn("[app-settings] Failed to upload owner image:", error);
      return null;
    }
  },
}));
