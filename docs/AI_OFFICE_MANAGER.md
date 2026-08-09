# AI Office Manager

Claude Officeは、`config/app-settings.json`をBackend・Frontend・Codex adapter・Windows Managerで共有します。ファイルがない、または壊れている場合は安全な既定値へ戻ります。

## 起動

```powershell
.\start_ai_office_manager.ps1
```

### 1ファイルEXE

PyInstallerを用意してから、次のコマンドでGUIを1ファイルEXE化できます。

```powershell
uv sync --extra manager
.\build_manager.ps1
```

生成物は`dist/manager/ClaudeOfficeManager.exe`です。`start_ai_office_manager.ps1`はEXEが存在すればEXEを優先して起動し、未ビルド時はPython版へフォールバックします。EXEはClaude Officeルートまたはルート直下の`dist/manager`に置いて使用してください。

アプリケーションアイコンは`manager/assets/claude-office-manager.ico`に指定しています。

ManagerからBackend/Frontendを起動、停止、再起動できます。状態カードはhealth応答と管理対象プロセスを確認し、ログ画面では`runtime/logs`を表示します。ブラウザ表示は通常ブラウザとEdge/Chromeのアプリ表示を選べます。

GUIはPython標準のTkinterを使用するため、PySide6などの追加依存は不要です。

## Web設定

Claude Officeの「設定」→「AIオフィス」から、会社名、オーナー名、オーナー画像、Backend/Frontendポート、ブラウザ表示を変更できます。画像はPNG/JPEG/WebP、5MB以下です。

ポート変更後はManagerの「再起動」を押してください。Codex adapterは次のイベントから共有設定のBackendポートへ追従します。

## 直接起動

Managerを使わずに起動する場合は、リポジトリ直下の`start_claude_office.ps1`を使用してください。このスクリプトも共有設定を読み込み、FrontendへAPI/WS URLを渡します。
