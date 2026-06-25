# Analytics Inventory

複数のGmailアカウントが所有する **GA4プロパティ・GTMコンテナ・Search Consoleサイト** を横断管理するインベントリツール。

- 自動データ収集（3日ごと）
- 100点満点のヘルススコアリング
- 機械的なアラート検出
- Claude APIによるAI分析（個別・一括・自動）
- 外部AIへの作業委任プロンプト自動生成

## 主な機能

| 機能 | 概要 |
|---|---|
| **GA4 / GTM / SC 一覧** | 全アカウント横断、フィルタ・ソート・タグ付け |
| **ヘルススコア** | 各アセットを0〜100点で評価、A〜Fグレード |
| **AI分析** | Claude API直接コール、課題リスト+優先度付きアクション+委任プロンプト |
| **自動診断** | 3日ごとの更新後、新規アラート発生プロパティを自動分析 |
| **一括AI分析** | 複数プロパティをまとめてジョブ実行 |
| **横断検索** | イベント・CD/CM・タグ・トリガー・変数名で全件検索 |
| **クライアント管理** | お気に入り・タグ・メモ |
| **差分検知** | スナップショット保存、前回比表示 |
| **CSV出力** | プロパティ・コンテナ一覧 |

## セットアップ

### 1. 依存ライブラリ

```powershell
cd path\to\Analytics-Inventory
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

### 2. Google Cloud Console（手動・一度きり）

1. https://console.cloud.google.com/ でプロジェクトを選択（または新規作成）
2. **APIライブラリ** で以下を有効化:
   - Google Analytics Admin API
   - Google Analytics Data API
   - Tag Manager API
   - Search Console API
3. **OAuth同意画面**:
   - User Type: 「外部」
   - スコープ: `analytics.readonly`, `analytics.edit`, `analytics.manage.users.readonly`, `tagmanager.readonly`, `webmasters.readonly`, `userinfo.email`, `openid`
   - テストユーザー: 使用する全Gmailアドレスを登録
4. **OAuthクライアントID**（アプリの種類: **デスクトップアプリ**）を作成し、JSONをダウンロード → `client_secret.json` として配置

### 3. Anthropic APIキー

`.env` ファイルを作成:
```
ANTHROPIC_API_KEY=sk-ant-api03-...
```

### 4. 各Gmail で認証（人数分繰り返す）

```powershell
python auth.py add
```

### 5. データ収集

```powershell
python indexer.py
```

すべてのアカウントを巡回し、`data/inventory.json` と詳細データを生成。20〜45分。

### 6. UI起動

```powershell
python server.py
```

→ http://127.0.0.1:8788

## 自動起動・スケジュール（Windows）

### サーバ自動起動（ログイン時）
`start_server_silent.bat` を Windowsスタートアップフォルダにコピー:
```
%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\
```

### 3日ごとの自動データ更新
```powershell
schtasks /Create /TN Analytics-Inventory-AutoRefresh ^
  /TR "<repoパス>\run_indexer.bat" ^
  /SC DAILY /MO 3 /ST 03:00 /F
```

## ファイル構成

| パス | 用途 |
|------|------|
| `server.py` | Flask UI（port 8788） |
| `indexer.py` | 全アカウント巡回データ収集 |
| `auth.py` | OAuthフロー（add/list/remove） |
| `auto_diagnose.py` | 新規アラート検出+自動AI分析 |
| `bulk_analyzer.py` | 一括AI分析ジョブ管理 |
| `ai_executor.py` | Claude API呼び出し（GA4/GTM分析） |
| `ai_prompts.py` | AI分析プロンプトテンプレート |
| `crossref.py` | GA4↔GTM紐付け計算 |
| `diff.py` | スナップショット差分計算 |
| `health.py` | ヘルススコア・アラート判定 |
| `annotations.py` | お気に入り・タグ・メモ |
| `search_index.py` | 横串検索 |
| `collect_sc_only.py` | SCのみ再収集（API有効化後等） |
| `retry_errors.py` | 収集エラー再試行 |
| `collectors/ga4_admin.py` | GA4 Admin API |
| `collectors/ga4_data.py` | GA4 Data API |
| `collectors/gtm.py` | Tag Manager API |
| `collectors/sc.py` | Search Console API |
| `templates/` | Jinja2テンプレート |
| `data/` | 収集結果（gitignore） |
| `tokens/` | OAuthトークン（gitignore） |

## 使い方ドキュメント

サーバ起動後、ヘッダから:
- **使い方** (`/usage`) — フロー解説・チェックリスト・シナリオ集
- **ヘルプ** (`/help`) — 用語集・スコア基準・トラブルシューティング

## ライセンス・配布

個人/組織内利用を想定したPrivateリポジトリです。第三者への配布時はOAuthトークン・APIキー・収集データを必ず削除してください（gitignore済の `tokens/`, `data/`, `.env`, `client_secret.json` が該当）。

## 技術スタック

- Python 3.10+
- Flask
- Google API Python Client（Analytics Admin / Data / Tag Manager / Search Console）
- Anthropic Python SDK（Claude API）
- Jinja2 / Vanilla JS / CSS Variables
