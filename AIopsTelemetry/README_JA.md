# AIops Telemetry 日本語ガイド

AIops Telemetry は、AI エージェント向けのオブザーバビリティ、障害検知、RCA、リメディエーションを 1 つのサーバーで扱うためのプラットフォームです。LangGraph / LangChain 系のアプリから Trace を取り込み、NFR ベースで Issue を検知し、必要に応じて外部 RCA サービスや修復フローへつなぎます。

## できること

- Trace / Span / Log を REST API で取り込む
- NFR ベースのルールで Issue を自動検知する
- Issue ごとに RCA を生成して根本原因候補を整理する
- ダッシュボードから Issue、メトリクス、会話型 RCA を確認する
- 必要に応じて外部 remediation / agent プロセスと連携する

## 主要ディレクトリ

- `server/main.py`: FastAPI アプリ本体。API ルーターとダッシュボード配信を束ねます
- `server/api/`: Issue、Chat、Analysis などの API エンドポイント群です
- `server/engine/`: Issue 検知、RCA 連携、エスカレーションなどの実処理です
- `server/database/`: SQLAlchemy の接続設定とモデル定義です
- `server/dashboard/`: FastAPI から配信するフロントエンド静的ファイルです
- `aiops_sdk/`: 外部エージェント側で使う SDK です

## セットアップ

```bash
cd AIopsTelemetry
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pip install -r requirements-dev.txt
pip install -e .
cp .env.example .env
```

`.env` では最低限次の設定を確認してください。

```env
AIOPS_PORT=7000
AIOPS_DATABASE_URL=sqlite:///./aiops.db
AIOPS_RCA_SERVICE_URL=http://localhost:8000
AIOPS_AIOPS_REMEDIATION_URL=http://localhost:8005
OPENAI_API_KEY=
```

## 起動方法

```bash
python run.py --reload
```

または:

```bash
uvicorn server.main:app --host 0.0.0.0 --port 7000 --reload
```

起動後の主な URL:

- `http://localhost:7000/`: 運用ダッシュボード
- `http://localhost:7000/docs`: Swagger UI
- `http://localhost:7000/dashboard/ja`: 日本語向けダッシュボード入口

## よく使う API

- `GET /health`: サーバー生存確認
- `POST /api/ingest/trace`: 1 件の Trace を取り込む
- `GET /api/issues`: Issue 一覧を取得する
- `POST /api/analysis/issues/{id}`: RCA 実行をトリガーする
- `POST /api/chat/rca/live`: 会話ベースでライブ RCA を問い合わせる
- `POST /api/chat/rca/diagram`: RCA の影響フロー図を生成する

## 開発時の見どころ

- `server/api/chat.py`: MCP と保存済み RCA を使って会話 UI 向けの応答を作ります
- `server/api/issues.py`: Issue 作成、重複排除、デモデータ投入を担当します
- `server/engine/issue_detector.py`: NFR ごとの検知ルールをまとめています
- `server/engine/rca_client.py`: 外部 RCA サービス呼び出しと保存処理を担当します
- `server/database/engine.py`: DB 初期化、簡易マイグレーション、初期データ投入を行います

## 補足

- 今回、主要な Python ファイルには日本語コメントを追加してあります
- 既存の英語 README は残しつつ、日本語向けの導線としてこの `README_JA.md` を追加しています
