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

Backend起動後は、進行中のCodexセッションをバックグラウンドで自動確認します。ViewerをCodex作業の途中から起動しても、保存済みsession metadataから現在のCodex Main、稼働中subagent、未完了tool状態を復元し、その後のglobal lifecycle hooksへ引き継ぎます。状態カードには「確認中…」「2件復元」「復元対象なし」などの結果が表示されます。復元に失敗してもCodex自体の処理には影響しません。

設定画面では次を共通設定として変更できます。

- 会社名とオーナー名（オーナー画像・肩書き・一言は「Web設定を開く」から変更）
- `起動時に進行中のCodexセッションを復元する`（既定ON）
- `復元対象（直近）`（既定30分、1～1440分）

「ホワイトボード設定」はWeb設定の該当タブを直接開きます。今日の目標、今週の目標、
メモ、カスタム表示はAIが更新するTODOとは別に保存されます。

必要な場合は「Codexセッションを再読込」ボタンまたはトレイメニューから手動scanできます。同じ`session_id` / `agent_id`は既存状態へマージされ、重複キャラクターは作成されません。

ManagerはPySide6のタスクトレイ常駐アプリです。×またはAlt+F4ではウィンドウだけが非表示になり、トレイアイコンのダブルクリックまたは「AI Office Viewer Managerを開く」で復元します。完全終了はトレイメニューの「終了」から行います。

`uv sync --extra manager`でPySide6とPyInstallerを導入します。

## Web設定

AI Office Viewerの「設定」から、会社名、オーナー名、肩書き、一言メッセージ、
オーナー画像、Backend/Frontendポート、ブラウザ表示、Codexセッション自動復元の
ON/OFFと対象時間を変更できます。画像はPNG/JPEG/WebP、5MB以下で、管理領域へ安全な
ファイル名で保存されます。「デフォルトに戻す」で標準画像へ戻せます。会社名は製品名とは
別に保存され、画面では「会社名 - AI Office Viewer」として共存します。

「ホワイトボード」タブでは、TODO、今日の目標、今週の目標、メモ、カスタム表示を選べます。
今日・今週の目標は追加、編集、削除、並び替えに対応します。自動切替は既定OFFで、ONの場合は
入力済みの内容とTODOを指定秒数ごとに切り替えます。今日の目標には表示時点の日付を付け、
日付が変わっても入力内容を自動削除しません。

ポート変更後はManagerの「再起動」を押してください。Codex adapterは次のイベントから共有設定のBackendポートへ追従します。

## 直接起動

Managerを使わずに起動する場合は、リポジトリ直下の`start_ai_office_viewer.ps1`を使用してください。互換性のため旧`start_claude_office.ps1`も維持しています。両方とも共有設定を読み込み、FrontendへAPI/WS URLを渡します。

## Codex復元の制限

- Codexが保存したJSONL session metadataと、AI Office Viewer adapterが保存する本文なしのlifecycle metadataを利用します。
- 設定時間より古いsessionと、終了を確認できたsessionは復元しません。復元は新しいactive sessionを最大10件までに制限します。
- JSONLはhead/tailだけを上限付きで読み、本文なしの補助journalは1日16MiB、3日より古いものを次回hook時に削除します。復元scanはバックグラウンドで動作します。
- 書き込み中JSONLの末尾や非常に短い瞬間状態は、完全に再現できない場合があります。
- prompt、command、file content、stdout/stderr、assistant responseはViewerへ転送しません。
- 初期版の起動時復元はCodex専用です。Claude Code / OpenCodeの既存リアルタイム連携は変更しません。
