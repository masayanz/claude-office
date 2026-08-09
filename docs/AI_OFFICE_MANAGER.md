# AI Office Viewer Manager

**AIエージェントの活動をリアルタイムで可視化**

AI Office Viewerは、`config/app-settings.json`をBackend・Frontend・Codex adapter・Windows Managerで共有します。ファイルがない、または壊れている場合は安全な既定値へ戻ります。

## 起動

```powershell
.\start_ai_office_viewer_manager.ps1
```

### 1ファイルEXE

PyInstallerを用意してから、次のコマンドでGUIを1ファイルEXE化できます。

```powershell
uv sync --extra manager
.\build_manager.ps1
```

生成物は`dist/AI-Office-Viewer-Manager.exe`です。`start_ai_office_viewer_manager.ps1`はEXEが存在すればEXEを優先して起動し、未ビルド時はPython版へフォールバックします。旧`start_ai_office_manager.ps1`は互換wrapperとして維持します。EXEはAI Office Viewerルートまたはルート直下の`dist`に置いて使用してください。

アプリケーションアイコンは`manager/assets/claude-office-manager.ico`に指定しています。

ManagerからBackend/Frontendを起動、停止、再起動できます。状態カードはhealth応答と管理対象プロセスを確認し、ログ画面では`runtime/logs`を表示します。ブラウザ表示は通常ブラウザとEdge/Chromeのアプリ表示を選べます。

ManagerはPySide6のタスクトレイ常駐アプリです。×またはAlt+F4ではウィンドウだけが非表示になり、トレイアイコンのダブルクリックまたは「AI Office Viewer Managerを開く」で復元します。完全終了はトレイメニューの「終了」から行います。

`uv sync --extra manager`でPySide6とPyInstallerを導入します。

## Web設定

AI Office Viewerの「設定」→「AI Office Viewer」から、会社名、オーナー名、オーナー画像、Backend/Frontendポート、ブラウザ表示を変更できます。画像はPNG/JPEG/WebP、5MB以下です。会社名は製品名とは別に保存され、画面では「会社名 - AI Office Viewer」として共存します。

ポート変更後はManagerの「再起動」を押してください。Codex adapterは次のイベントから共有設定のBackendポートへ追従します。

## 直接起動

Managerを使わずに起動する場合は、リポジトリ直下の`start_ai_office_viewer.ps1`を使用してください。互換性のため旧`start_claude_office.ps1`も維持しています。両方とも共有設定を読み込み、FrontendへAPI/WS URLを渡します。
