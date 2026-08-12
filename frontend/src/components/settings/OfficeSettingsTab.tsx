"use client";

import { useEffect, useRef, useState, type ReactNode } from "react";
import { useAppSettingsStore } from "@/stores/appSettingsStore";
import { useTranslation } from "@/hooks/useTranslation";
import { API_BASE } from "@/utils/api";
import { PRODUCT_NAME } from "@/config/branding";

export function OfficeSettingsTab(): ReactNode {
  const { t } = useTranslation();
  const settings = useAppSettingsStore((state) => state.settings);
  const updateAppSettings = useAppSettingsStore(
    (state) => state.updateAppSettings,
  );
  const uploadOwnerImage = useAppSettingsStore(
    (state) => state.uploadOwnerImage,
  );
  const resetOwnerImage = useAppSettingsStore((state) => state.resetOwnerImage);
  const [companyName, setCompanyName] = useState("");
  const [ownerName, setOwnerName] = useState("");
  const [ownerTitle, setOwnerTitle] = useState("");
  const [ownerMessage, setOwnerMessage] = useState("");
  const [backendPort, setBackendPort] = useState(8000);
  const [frontendPort, setFrontendPort] = useState(3000);
  const [browserMode, setBrowserMode] = useState<"normal" | "app">("normal");
  const [message, setMessage] = useState("");
  const [previewUrl, setPreviewUrl] = useState<string | null>(null);
  const previewUrlRef = useRef<string | null>(null);

  useEffect(() => {
    if (!settings) return;
    // The API-backed settings object is the source of truth when the dialog opens or saves.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setCompanyName(settings.company_name);
    setOwnerName(settings.owner_name);
    setOwnerTitle(settings.owner_title ?? "");
    setOwnerMessage(settings.owner_message ?? "");
    setBackendPort(settings.backend_port);
    setFrontendPort(settings.frontend_port);
    setBrowserMode(settings.browser_mode);
  }, [settings]);

  useEffect(
    () => () => {
      if (previewUrlRef.current) URL.revokeObjectURL(previewUrlRef.current);
    },
    [],
  );

  const save = async () => {
    const updated = await updateAppSettings({
      company_name: companyName,
      owner_name: ownerName,
      owner_title: ownerTitle,
      owner_message: ownerMessage,
      backend_port: backendPort,
      frontend_port: frontendPort,
      browser_mode: browserMode,
    });
    setMessage(
      updated ? t("settings.office.saved") : t("settings.office.saveFailed"),
    );
  };

  const upload = async (file: File | undefined) => {
    if (!file) return;
    if (
      !new Set(["image/png", "image/jpeg", "image/webp"]).has(file.type) ||
      file.size > 5 * 1024 * 1024
    ) {
      setMessage("PNG・JPEG・WebP形式、5MB以下の画像を選択してください。");
      return;
    }
    if (previewUrlRef.current) URL.revokeObjectURL(previewUrlRef.current);
    const localPreview = URL.createObjectURL(file);
    previewUrlRef.current = localPreview;
    setPreviewUrl(localPreview);
    const updated = await uploadOwnerImage(file);
    setMessage(
      updated
        ? t("settings.office.imageSaved")
        : t("settings.office.imageFailed"),
    );
    if (!updated) {
      URL.revokeObjectURL(localPreview);
      previewUrlRef.current = null;
      setPreviewUrl(null);
    }
  };

  const resetImage = async () => {
    const updated = await resetOwnerImage();
    if (updated && previewUrlRef.current) {
      URL.revokeObjectURL(previewUrlRef.current);
      previewUrlRef.current = null;
      setPreviewUrl(null);
    }
    setMessage(
      updated
        ? "オーナー画像をデフォルトに戻しました"
        : t("settings.office.imageFailed"),
    );
  };

  return (
    <div className="space-y-5">
      <div>
        <h3 className="text-lg font-bold text-white">{PRODUCT_NAME}</h3>
        <p className="text-sm text-slate-400">{t("app.subtitle")}</p>
      </div>
      <div>
        <label className="block text-slate-400 text-xs font-bold uppercase tracking-wider mb-2">
          {t("settings.office.companyName")}
        </label>
        <input
          value={companyName}
          onChange={(event) => setCompanyName(event.target.value)}
          className="w-full px-3 py-2 rounded-lg bg-slate-800 border border-slate-700 text-white"
        />
      </div>
      <section className="space-y-4 rounded-lg border border-slate-700 bg-slate-800/30 p-4">
        <h3 className="text-sm font-bold text-purple-300">
          {t("settings.office.owner")}
        </h3>
        <label className="block text-slate-400 text-xs font-bold uppercase tracking-wider mb-2">
          {t("settings.office.ownerName")}
        </label>
        <input
          value={ownerName}
          maxLength={50}
          onChange={(event) => setOwnerName(event.target.value)}
          className="w-full px-3 py-2 rounded-lg bg-slate-800 border border-slate-700 text-white"
        />
        <label className="block text-slate-400 text-xs font-bold uppercase tracking-wider mb-2">
          {t("settings.office.ownerTitle")}
          <input
            value={ownerTitle}
            maxLength={50}
            onChange={(event) => setOwnerTitle(event.target.value)}
            className="mt-2 w-full px-3 py-2 rounded-lg bg-slate-800 border border-slate-700 text-white"
          />
        </label>
        <label className="block text-slate-400 text-xs font-bold uppercase tracking-wider mb-2">
          {t("settings.office.ownerMessage")}
          <textarea
            value={ownerMessage}
            maxLength={200}
            rows={2}
            onChange={(event) => setOwnerMessage(event.target.value)}
            className="mt-2 w-full resize-y px-3 py-2 rounded-lg bg-slate-800 border border-slate-700 text-white"
          />
        </label>
        <label className="block text-slate-400 text-xs font-bold uppercase tracking-wider mb-2">
          {t("settings.office.ownerImage")}
        </label>
        {(previewUrl || settings?.owner_image_url) && (
          // The endpoint is local and may be unavailable while the backend restarts.
          // eslint-disable-next-line @next/next/no-img-element
          <img
            src={
              previewUrl ??
              `${API_BASE}${settings?.owner_image_url}?v=${settings?.owner_image_filename ?? ""}`
            }
            alt={ownerName || "Owner"}
            className="mb-2 h-20 w-20 rounded-lg object-cover border border-slate-700"
          />
        )}
        <input
          type="file"
          accept="image/png,image/jpeg,image/webp"
          onChange={(event) => void upload(event.target.files?.[0])}
          className="block w-full text-sm text-slate-400"
        />
        {settings?.owner_image_url && (
          <button
            type="button"
            onClick={() => void resetImage()}
            className="mt-2 text-sm font-bold text-slate-300 hover:text-white"
          >
            {t("settings.office.resetOwnerImage")}
          </button>
        )}
      </section>
      <div className="grid grid-cols-2 gap-3">
        <label className="text-slate-400 text-xs font-bold">
          {t("settings.office.backendPort")}
          <input
            type="number"
            min={1024}
            max={65535}
            value={backendPort}
            onChange={(event) => setBackendPort(Number(event.target.value))}
            className="mt-2 w-full px-3 py-2 rounded-lg bg-slate-800 border border-slate-700 text-white"
          />
        </label>
        <label className="text-slate-400 text-xs font-bold">
          {t("settings.office.frontendPort")}
          <input
            type="number"
            min={1024}
            max={65535}
            value={frontendPort}
            onChange={(event) => setFrontendPort(Number(event.target.value))}
            className="mt-2 w-full px-3 py-2 rounded-lg bg-slate-800 border border-slate-700 text-white"
          />
        </label>
      </div>
      <label className="block text-slate-400 text-xs font-bold">
        {t("settings.office.browserMode")}
        <select
          value={browserMode}
          onChange={(event) =>
            setBrowserMode(event.target.value as "normal" | "app")
          }
          className="mt-2 w-full px-3 py-2 rounded-lg bg-slate-800 border border-slate-700 text-white"
        >
          <option value="normal">{t("settings.office.browserNormal")}</option>
          <option value="app">{t("settings.office.browserApp")}</option>
        </select>
      </label>
      <button
        type="button"
        onClick={() => void save()}
        className="px-4 py-2 bg-purple-600 hover:bg-purple-500 text-white text-sm font-bold rounded-lg"
      >
        {t("settings.office.save")}
      </button>
      {message && <p className="text-xs text-emerald-400">{message}</p>}
      <p className="text-xs text-slate-500">
        {t("settings.office.restartHint")}
      </p>
    </div>
  );
}
