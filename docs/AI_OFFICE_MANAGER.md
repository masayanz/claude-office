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

ManagerからBackend/Frontendを起動、停止、再起動できます。Windowsでは子プロセスをコンソール非表示で起動し、stdout/stderrは`runtime/logs`へ保存します。ログ画面ではManager・Backend・Frontendを切り替えて更新できます。起動後30秒までは「起動中」としてhealth応答を待ち、失敗したサービスだけにログ確認を案内します。

Viewer起動メニューは2種類に分かれています。「ブラウザで開く」は既定のWebブラウザで共有設定のFrontend URLを通常タブとして開きます。「専用画面で開く」はFrontendのhealthを確認してから、Microsoft Edgeを優先し、見つからない場合はGoogle Chromeの`--app=<URL>`で独立ウィンドウを起動します。専用画面はタブ・アドレスバー・ブックマークバーを表示せず、Managerと同じモニターの利用可能領域内に88%サイズで配置します。専用画面用のプロセスだけをManager内で追跡し、通常のEdge/Chrome全体は終了しません。Frontend停止時は起動確認を案内し、起動失敗理由はManagerログとダイアログに表示します。

Backend起動後は、進行中のCodexセッションをバックグラウンドで自動確認します。ViewerをCodex作業の途中から起動しても、保存済みsession metadataから現在のCodex Main、稼働中subagent、未完了tool状態を復元します。その後はglobal lifecycle hooksを主経路にし、既存VS Code sessionなどでhooksが届かない場合はrollout JSONLのtail監視へ自動的にfallbackします。VS Code再起動や新しいchat作成は必要ありません。

「Codex連携状態」ではCLI、8件のGlobal Hooks、Adapter、Backend API、Session Restore、
JSONL Monitor、Live Eventsを個別に判定します。Live表示はHooks、Hybrid、JSONL fallback、
待機を区別します。JSONL監視が生きている場合、hooksが届かないことだけでエラーや「復元のみ」
にはしません。HookとJSONLの同一イベントは1回だけViewerへ送ります。
active sessionがなく未受信の場合は異常ではなく「待機」です。

Codex CLIは`CODEX_CLI_PATH`、Codex設定、Codex Desktop、VS Code OpenAI/ChatGPT拡張、
PATHの順に探索し、実体の`--version`が成功した場合だけ利用可能とします。PATH未登録やCLI未検出
だけではhook経由の連携全体をエラーにしません。「詳細」では検出方法、Version、Hooks件数、
Backend接続先、復元件数、Live件数を表示しますが、ユーザー固有の完全なCLI pathは表示しません。

「Codex連携を診断」は重い構造検査をバックグラウンドで実行し、通常監視は3秒ごとの軽量API
確認だけを行います。「Global Hooksを修復」は既存の他hooksを保ったまま、移動後のViewer
root、launcher、adapter pathを現在位置へ更新します。VS Codeを自動終了・再起動することは
ありません。総合状態はトレイtooltipにも表示され、警告・エラーへの変化時だけ通知します。

設定画面では次を共通設定として変更できます。

- 会社名とオーナー名（オーナー画像・肩書き・一言は「Web設定を開く」から変更）
- `起動時に進行中のCodexセッションを復元する`（既定ON）
- `復元対象（直近）`（既定30分、1～1440分）
- `Manager終了時の動作`（既定ではBackend/Frontendも停止。動作継続も選択可能）

「ホワイトボード設定」はWeb設定の該当タブを直接開きます。今日の目標、今週の目標、
メモ、カスタム表示はAIが更新するTODOとは別に保存されます。

必要な場合は「Codexセッションを再読込」ボタンまたはトレイメニューから手動scanできます。同じ`session_id` / `agent_id`は既存状態へマージされ、重複キャラクターは作成されません。

ManagerはPySide6のタスクトレイ常駐アプリです。×またはAlt+F4ではウィンドウだけが非表示になり、監視は継続します。トレイアイコンのダブルクリックまたは「AI Office Viewer Managerを開く」で復元し、完全終了はトレイメニューの「終了」から行います。通常時の位置とサイズはManager専用設定へ保存します。前回のモニタが外された場合、解像度やDPIが変わった場合、保存位置が現在の作業領域外の場合は、利用可能画面内へ縮小・補正してタイトルバーを表示します。初回または完全に画面外の場合はprimary screenの中央へ配置します。内容部分は小さい画面でもスクロールできます。

既定では完全終了時にManagerが起動したBackend/Frontendも安全に停止します。外部で起動されたプロセスは停止対象にせず、管理対象プロセスの停止を確認できなかった場合はManagerを終了せずに理由を表示します。

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
- 復元は過去から現在状態を再構築し、復元完了後の追記は別のJSONL tail monitorがbyte offsetで追跡します。当日・前日・翌日のsession directoryとbounded session indexだけを軽量pollingし、監視対象は最大10 sessionです。
- JSONLはhead/tailだけを上限付きで読み、本文なしの補助journalは1日16MiB、3日より古いものを次回hook時に削除します。partial line、truncate、rotation、削除、parse errorを安全に扱います。
- 書き込み中JSONLの末尾や非常に短い瞬間状態は、完全に再現できない場合があります。
- prompt、command本文、tool input/output、file content、stdout/stderr、assistant response、secretはViewer・Backend状態・Managerログへ転送しません。
- 初期版の起動時復元はCodex専用です。Claude Code / OpenCodeの既存リアルタイム連携は変更しません。
