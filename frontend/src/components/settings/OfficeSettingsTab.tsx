"use client";

import { useEffect, useState, type ReactNode } from "react";
import { useAppSettingsStore } from "@/stores/appSettingsStore";
import { useTranslation } from "@/hooks/useTranslation";
import { API_BASE } from "@/utils/api";

export function OfficeSettingsTab(): ReactNode {
  const { t } = useTranslation();
  const settings = useAppSettingsStore((state) => state.settings);
  const updateAppSettings = useAppSettingsStore((state) => state.updateAppSettings);
  const uploadOwnerImage = useAppSettingsStore((state) => state.uploadOwnerImage);
  const [companyName, setCompanyName] = useState("");
  const [ownerName, setOwnerName] = useState("");
  const [backendPort, setBackendPort] = useState(8000);
  const [frontendPort, setFrontendPort] = useState(3000);
  const [browserMode, setBrowserMode] = useState<"normal" | "app">("normal");
  const [message, setMessage] = useState("");

  useEffect(() => {
    if (!settings) return;
    setCompanyName(settings.company_name);
    setOwnerName(settings.owner_name);
    setBackendPort(settings.backend_port);
    setFrontendPort(settings.frontend_port);
    setBrowserMode(settings.browser_mode);
  }, [settings]);

  const save = async () => {
    const updated = await updateAppSettings({
      company_name: companyName,
      owner_name: ownerName,
      backend_port: backendPort,
      frontend_port: frontendPort,
      browser_mode: browserMode,
    });
    setMessage(updated ? t("settings.office.saved") : t("settings.office.saveFailed"));
  };

  const upload = async (file: File | undefined) => {
    if (!file) return;
    const updated = await uploadOwnerImage(file);
    setMessage(updated ? t("settings.office.imageSaved") : t("settings.office.imageFailed"));
  };

  return (
    <div className="space-y-5">
      <div>
        <label className="block text-slate-400 text-xs font-bold uppercase tracking-wider mb-2">
          {t("settings.office.companyName")}
        </label>
        <input value={companyName} onChange={(event) => setCompanyName(event.target.value)}
          className="w-full px-3 py-2 rounded-lg bg-slate-800 border border-slate-700 text-white" />
      </div>
      <div>
        <label className="block text-slate-400 text-xs font-bold uppercase tracking-wider mb-2">
          {t("settings.office.ownerName")}
        </label>
        <input value={ownerName} onChange={(event) => setOwnerName(event.target.value)}
          className="w-full px-3 py-2 rounded-lg bg-slate-800 border border-slate-700 text-white" />
      </div>
      <div>
        <label className="block text-slate-400 text-xs font-bold uppercase tracking-wider mb-2">
          {t("settings.office.ownerImage")}
        </label>
        {settings?.owner_image_url && (
          // The endpoint is local and may be unavailable while the backend restarts.
          <img src={`${API_BASE}${settings.owner_image_url}?v=${settings.owner_image_filename ?? ""}`} alt={ownerName}
            className="mb-2 h-20 w-20 rounded-lg object-cover border border-slate-700" />
        )}
        <input type="file" accept="image/png,image/jpeg,image/webp" onChange={(event) => void upload(event.target.files?.[0])}
          className="block w-full text-sm text-slate-400" />
      </div>
      <div className="grid grid-cols-2 gap-3">
        <label className="text-slate-400 text-xs font-bold">
          {t("settings.office.backendPort")}
          <input type="number" min={1024} max={65535} value={backendPort} onChange={(event) => setBackendPort(Number(event.target.value))}
            className="mt-2 w-full px-3 py-2 rounded-lg bg-slate-800 border border-slate-700 text-white" />
        </label>
        <label className="text-slate-400 text-xs font-bold">
          {t("settings.office.frontendPort")}
          <input type="number" min={1024} max={65535} value={frontendPort} onChange={(event) => setFrontendPort(Number(event.target.value))}
            className="mt-2 w-full px-3 py-2 rounded-lg bg-slate-800 border border-slate-700 text-white" />
        </label>
      </div>
      <label className="block text-slate-400 text-xs font-bold">
        {t("settings.office.browserMode")}
        <select value={browserMode} onChange={(event) => setBrowserMode(event.target.value as "normal" | "app")}
          className="mt-2 w-full px-3 py-2 rounded-lg bg-slate-800 border border-slate-700 text-white">
          <option value="normal">{t("settings.office.browserNormal")}</option>
          <option value="app">{t("settings.office.browserApp")}</option>
        </select>
      </label>
      <button type="button" onClick={() => void save()}
        className="px-4 py-2 bg-purple-600 hover:bg-purple-500 text-white text-sm font-bold rounded-lg">
        {t("settings.office.save")}
      </button>
      {message && <p className="text-xs text-emerald-400">{message}</p>}
      <p className="text-xs text-slate-500">{t("settings.office.restartHint")}</p>
    </div>
  );
}
