"""Tkinter-based Windows GUI for everyday Claude Office operation."""

from __future__ import annotations

import tkinter as tk
from tkinter import messagebox, ttk

from .process_manager import ServiceManager
from .settings import load_settings, save_settings


class ManagerWindow(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("Claude Office Manager")
        self.geometry("760x520")
        self.manager = ServiceManager()
        self.status_labels: dict[str, ttk.Label] = {}
        self._build()
        self._refresh()

    def _build(self) -> None:
        root = ttk.Frame(self, padding=16)
        root.pack(fill=tk.BOTH, expand=True)
        ttk.Label(root, text="Claude Office", font=("Segoe UI", 20, "bold")).pack(anchor=tk.W)
        ttk.Label(root, text="AIオフィス管理マネージャー").pack(anchor=tk.W, pady=(0, 16))
        cards = ttk.Frame(root)
        cards.pack(fill=tk.X)
        for service, label in (("backend", "Backend"), ("frontend", "Frontend")):
            frame = ttk.LabelFrame(cards, text=label, padding=12)
            frame.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 8))
            status = ttk.Label(frame, text="確認中…")
            status.pack(anchor=tk.W)
            self.status_labels[service] = status
        hooks_frame = ttk.LabelFrame(cards, text="Codex global hooks", padding=12)
        hooks_frame.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self.status_labels["hooks"] = ttk.Label(hooks_frame, text="確認中…")
        self.status_labels["hooks"].pack(anchor=tk.W)
        buttons = ttk.Frame(root)
        buttons.pack(fill=tk.X, pady=16)
        for text, command in (
            ("起動", self._start),
            ("停止", self._stop),
            ("再起動", self._restart),
            ("AIオフィスを開く", self._open),
            ("専用表示", lambda: self._open(True)),
            ("設定", self._settings_dialog),
            ("ログ", self._logs),
        ):
            ttk.Button(buttons, text=text, command=command).pack(side=tk.LEFT, padx=(0, 6))
        ttk.Label(root, text="共通設定: config\\app-settings.json", foreground="#666").pack(
            anchor=tk.W, pady=(8, 0)
        )

    def _refresh(self) -> None:
        for service, label in self.status_labels.items():
            if service == "hooks":
                label.configure(
                    text="インストール済み" if self.manager.hooks_installed() else "未インストール"
                )
                continue
            status = self.manager.status(service)
            state = "稼働中" if status.healthy else ("起動中" if status.running else "停止中")
            label.configure(text=f"{state}  PID={status.pid or '-'}")
        self.after(3000, self._refresh)

    def _start(self) -> None:
        self.manager.start("backend")
        self.manager.start("frontend")

    def _stop(self) -> None:
        self.manager.stop("frontend")
        self.manager.stop("backend")

    def _restart(self) -> None:
        self._stop()
        self._start()

    def _open(self, app_mode: bool = False) -> None:
        self.manager.open_office(app_mode)

    def _logs(self) -> None:
        window = tk.Toplevel(self)
        window.title("Claude Office ログ")
        text = tk.Text(window, wrap=tk.NONE, width=120, height=30)
        text.pack(fill=tk.BOTH, expand=True)
        text.insert(
            "1.0", self.manager.read_logs("backend") + "\n" + self.manager.read_logs("frontend")
        )
        text.configure(state=tk.DISABLED)

    def _settings_dialog(self) -> None:
        settings, warning = load_settings()
        dialog = tk.Toplevel(self)
        dialog.title("AIオフィス設定")
        fields: dict[str, tk.Entry] = {}
        for row, (key, label) in enumerate(
            (
                ("company_name", "会社名"),
                ("owner_name", "オーナー名"),
                ("backend_port", "Backendポート"),
                ("frontend_port", "Frontendポート"),
            )
        ):
            ttk.Label(dialog, text=label).grid(row=row, column=0, sticky=tk.W, padx=12, pady=6)
            entry = ttk.Entry(dialog, width=32)
            entry.insert(0, str(settings[key]))
            entry.grid(row=row, column=1, padx=12, pady=6)
            fields[key] = entry
        mode = tk.StringVar(value=str(settings["browser_mode"]))
        ttk.Label(dialog, text="ブラウザ表示").grid(row=4, column=0, sticky=tk.W, padx=12, pady=6)
        ttk.Combobox(
            dialog, textvariable=mode, values=("normal", "app"), state="readonly", width=29
        ).grid(row=4, column=1, padx=12, pady=6)

        def save() -> None:
            try:
                save_settings(
                    {
                        "company_name": fields["company_name"].get(),
                        "owner_name": fields["owner_name"].get(),
                        "backend_port": int(fields["backend_port"].get()),
                        "frontend_port": int(fields["frontend_port"].get()),
                        "browser_mode": mode.get(),
                    }
                )
            except ValueError as exc:
                messagebox.showerror("設定エラー", str(exc), parent=dialog)
                return
            dialog.destroy()

        ttk.Button(dialog, text="保存", command=save).grid(
            row=5, column=1, sticky=tk.E, padx=12, pady=12
        )
        if warning:
            ttk.Label(dialog, text=f"警告: {warning}", foreground="#a00").grid(
                row=6, column=0, columnspan=2, padx=12, pady=6
            )


def run() -> None:
    ManagerWindow().mainloop()


if __name__ == "__main__":
    run()
