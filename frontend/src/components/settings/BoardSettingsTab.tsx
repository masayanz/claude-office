"use client";

import { useState, type ReactNode } from "react";
import {
  type AppSettings,
  type BoardMode,
  useAppSettingsStore,
} from "@/stores/appSettingsStore";
import { useTranslation } from "@/hooks/useTranslation";

const BOARD_MODES: Array<{ value: BoardMode; label: string }> = [
  { value: "todo", label: "TODO" },
  { value: "daily_goals", label: "今日の目標" },
  { value: "weekly_goals", label: "今週の目標" },
  { value: "memo", label: "メモ" },
  { value: "custom", label: "カスタム" },
];

function GoalEditor({
  label,
  goals,
  onChange,
}: {
  label: string;
  goals: string[];
  onChange: (goals: string[]) => void;
}): ReactNode {
  const updateGoal = (index: number, value: string) => {
    onChange(goals.map((goal, i) => (i === index ? value : goal)));
  };
  const move = (index: number, direction: -1 | 1) => {
    const next = index + direction;
    if (next < 0 || next >= goals.length) return;
    const reordered = [...goals];
    [reordered[index], reordered[next]] = [reordered[next], reordered[index]];
    onChange(reordered);
  };

  return (
    <section className="space-y-2 rounded-lg border border-slate-700 bg-slate-800/50 p-3">
      <h3 className="text-sm font-bold text-slate-200">{label}</h3>
      {goals.map((goal, index) => (
        <div className="flex items-center gap-2" key={index}>
          <input
            aria-label={`${label} ${index + 1}`}
            value={goal}
            maxLength={100}
            onChange={(event) => updateGoal(index, event.target.value)}
            className="min-w-0 flex-1 rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-sm text-white"
          />
          <button
            type="button"
            onClick={() => move(index, -1)}
            disabled={index === 0}
            className="rounded px-2 py-1 text-slate-300 disabled:opacity-30"
            aria-label="上へ移動"
          >
            ↑
          </button>
          <button
            type="button"
            onClick={() => move(index, 1)}
            disabled={index === goals.length - 1}
            className="rounded px-2 py-1 text-slate-300 disabled:opacity-30"
            aria-label="下へ移動"
          >
            ↓
          </button>
          <button
            type="button"
            onClick={() => onChange(goals.filter((_, i) => i !== index))}
            className="rounded px-2 py-1 text-rose-300 hover:bg-rose-500/10"
            aria-label="削除"
          >
            ×
          </button>
        </div>
      ))}
      <button
        type="button"
        onClick={() => onChange([...goals, ""])}
        className="text-sm font-bold text-purple-300 hover:text-purple-200"
      >
        + 目標を追加
      </button>
    </section>
  );
}

export function BoardSettingsTab(): ReactNode {
  const { t } = useTranslation();
  const settings = useAppSettingsStore((state) => state.settings);
  const [saveSucceeded, setSaveSucceeded] = useState<boolean | null>(null);

  return (
    <>
      <BoardSettingsForm
        key={JSON.stringify(settings)}
        settings={settings}
        onSaveResult={setSaveSucceeded}
      />
      {saveSucceeded !== null && (
        <p
          className={`text-xs ${saveSucceeded ? "text-emerald-400" : "text-rose-400"}`}
        >
          {t(
            saveSucceeded
              ? "settings.office.saved"
              : "settings.office.saveFailed",
          )}
        </p>
      )}
    </>
  );
}

function BoardSettingsForm({
  settings,
  onSaveResult,
}: {
  settings: AppSettings | null;
  onSaveResult: (success: boolean) => void;
}): ReactNode {
  const { t } = useTranslation();
  const updateAppSettings = useAppSettingsStore(
    (state) => state.updateAppSettings,
  );
  const [boardMode, setBoardMode] = useState<BoardMode>(
    () => settings?.board_mode ?? "todo",
  );
  const [dailyGoals, setDailyGoals] = useState<string[]>(
    () => settings?.daily_goals ?? [],
  );
  const [weeklyGoals, setWeeklyGoals] = useState<string[]>(
    () => settings?.weekly_goals ?? [],
  );
  const [memo, setMemo] = useState(() => settings?.board_memo ?? "");
  const [customTitle, setCustomTitle] = useState(
    () => settings?.custom_board_title ?? "",
  );
  const [customMessage, setCustomMessage] = useState(
    () => settings?.custom_board_message ?? "",
  );
  const [autoRotate, setAutoRotate] = useState(
    () => settings?.board_auto_rotate ?? false,
  );
  const [rotateSeconds, setRotateSeconds] = useState(
    () => settings?.board_rotate_seconds ?? 10,
  );

  const save = async () => {
    const normalizedDailyGoals = dailyGoals
      .map((goal) => goal.trim())
      .filter(Boolean);
    const normalizedWeeklyGoals = weeklyGoals
      .map((goal) => goal.trim())
      .filter(Boolean);
    const updated = await updateAppSettings({
      board_mode: boardMode,
      daily_goals: normalizedDailyGoals,
      weekly_goals: normalizedWeeklyGoals,
      board_memo: memo.trim(),
      custom_board_title: customTitle.trim(),
      custom_board_message: customMessage.trim(),
      board_auto_rotate: autoRotate,
      board_rotate_seconds: Math.max(
        5,
        Math.min(3600, Math.round(rotateSeconds || 10)),
      ),
    });
    onSaveResult(updated !== null);
  };

  return (
    <div className="space-y-5">
      <div>
        <h3 className="text-lg font-bold text-white">ホワイトボード</h3>
        <p className="text-sm text-slate-400">
          AIのTODOとは別に、あなたの目標やメモをオフィスに表示します。
        </p>
      </div>
      <label className="block text-xs font-bold uppercase tracking-wider text-slate-400">
        表示内容
        <select
          value={boardMode}
          onChange={(event) => setBoardMode(event.target.value as BoardMode)}
          className="mt-2 w-full rounded-lg border border-slate-700 bg-slate-800 px-3 py-2 text-white"
        >
          {BOARD_MODES.map((mode) => (
            <option key={mode.value} value={mode.value}>
              {mode.label}
            </option>
          ))}
        </select>
      </label>
      <GoalEditor
        label="今日の目標"
        goals={dailyGoals}
        onChange={setDailyGoals}
      />
      <GoalEditor
        label="今週の目標"
        goals={weeklyGoals}
        onChange={setWeeklyGoals}
      />
      <label className="block text-xs font-bold text-slate-400">
        メモ（500文字まで）
        <textarea
          value={memo}
          maxLength={500}
          rows={3}
          onChange={(event) => setMemo(event.target.value)}
          className="mt-2 w-full resize-y rounded-lg border border-slate-700 bg-slate-800 px-3 py-2 text-sm text-white"
        />
      </label>
      <div className="grid gap-3 sm:grid-cols-2">
        <label className="text-xs font-bold text-slate-400">
          カスタムタイトル（50文字まで）
          <input
            value={customTitle}
            maxLength={50}
            onChange={(event) => setCustomTitle(event.target.value)}
            className="mt-2 w-full rounded-lg border border-slate-700 bg-slate-800 px-3 py-2 text-sm text-white"
          />
        </label>
        <label className="text-xs font-bold text-slate-400">
          カスタム本文（500文字まで）
          <textarea
            value={customMessage}
            maxLength={500}
            rows={2}
            onChange={(event) => setCustomMessage(event.target.value)}
            className="mt-2 w-full resize-y rounded-lg border border-slate-700 bg-slate-800 px-3 py-2 text-sm text-white"
          />
        </label>
      </div>
      <div className="rounded-lg border border-slate-700 bg-slate-800/50 p-3">
        <label className="flex cursor-pointer items-center justify-between gap-3 text-sm text-slate-200">
          <span>
            <span className="font-bold">自動切替</span>
            <span className="mt-1 block text-xs text-slate-500">
              TODO・入力済みの目標・メモを順番に表示します。
            </span>
          </span>
          <input
            type="checkbox"
            checked={autoRotate}
            onChange={(event) => setAutoRotate(event.target.checked)}
            className="h-4 w-4 accent-purple-500"
          />
        </label>
        {autoRotate && (
          <label className="mt-3 block text-xs font-bold text-slate-400">
            切替間隔（5〜3600秒）
            <input
              type="number"
              min={5}
              max={3600}
              value={rotateSeconds}
              onChange={(event) => setRotateSeconds(Number(event.target.value))}
              className="mt-2 w-full rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-sm text-white"
            />
          </label>
        )}
      </div>
      <button
        type="button"
        onClick={() => void save()}
        className="rounded-lg bg-purple-600 px-4 py-2 text-sm font-bold text-white hover:bg-purple-500"
      >
        {t("settings.office.save")}
      </button>
    </div>
  );
}
