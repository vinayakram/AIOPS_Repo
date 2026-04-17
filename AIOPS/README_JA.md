# Codex Native MVP

Codex Native MVP は API ファーストのリメディエーションサービスです。上流アプリから課題を受け取り、対象リポジトリを解決し、計画作成、Codex CLI による実装、実装サマリーと成果物の返却までを行います。

この README は、通常ユーザーまたは開発者が最初からアプリケーションを起動するための手順です。

## 事前に必要なもの

このサービスを動かすマシンに次をインストールしてください。

- Python 3.11 以上
- Node.js と npm
- Git
- Codex CLI
- 必要に応じて GitHub CLI

Codex CLI のインストール:

```bash
npm install -g @openai/codex
```

GitHub CLI を使う場合:

```bash
gh auth login
```

## 1. リポジトリを取得する

```bash
git clone <your-repo-url>
cd AIOPS
```

## 2. Python 環境を作成する

Windows:

```powershell
python -m venv .pyenv
.pyenv\Scripts\activate
pip install -r requirements.txt
```

Linux:

```bash
python3 -m venv .pyenv
source .pyenv/bin/activate
pip install -r requirements.txt
```

## 3. 環境変数ファイルを作成する

サンプルをコピーします。

Windows:

```powershell
copy .env.example .env
```

Linux:

```bash
cp .env.example .env
```

最低限おすすめの `.env`:

```env
CODEX_API_KEY=your_new_key
CODEX_COMMAND=codex
CODEX_MODEL=gpt-5-codex
DEFAULT_VALIDATION_COMMAND=pytest -q
RUNS_DIR=./runs
MANAGED_REPOS_DIR=./managed_repos
GITHUB_TOKEN=
```

補足:

- `CODEX_API_KEY` はこのサービスで使いたい新しいキーを設定してください。
- `GITHUB_TOKEN` は必須ではありませんが、`gh` を使わずに PR を作成したい場合は推奨です。
- `RUNS_DIR` と `MANAGED_REPOS_DIR` は通常はデフォルトのままで構いません。

## 4. プロジェクト解決設定を行う

作成または更新するファイル:

`config/project_map.json`

このファイルはアプリケーション名とリポジトリの対応を定義します。

例:

```json
{
  "multiagent-support-copilot": {
    "repo_root": "https://github.com/your-org/multiagent-support-copilot.git",
    "allowed_folder": "src/support_copilot",
    "test_command": "pytest -q",
    "base_branch": "main",
    "github_repo": "your-org/multiagent-support-copilot",
    "matchers": ["support copilot", "handoff", "context loss"]
  }
}
```

プロジェクト解決は次のどちらかで動作します。

- `config/project_map.json` にアプリケーション名の設定がある
- 上流アプリが `github_repo`、`upstream_repo`、`repo_root`、`allowed_folder` などのヒントを送る

## 5. サービスを起動する

Windows:

```powershell
python -m uvicorn app.web:app --host 0.0.0.0 --port 8000
```

Linux:

```bash
python -m uvicorn app.web:app --host 0.0.0.0 --port 8000
```

Linux の helper script を使う場合:

```bash
./scripts/start_linux.sh
```

アクセス URL:

```text
http://<host>:8000
```

## 6. 最初の API 呼び出し

上流アプリは次を送ります。

`POST /api/issues`

例:

```json
{
  "application_name": "multiagent-support-copilot",
  "issue_id": "SUP-5001",
  "description": "Payload context is dropped during agent handoff."
}
```

このサービスは次を行います。

- issue を作成する
- 候補リポジトリを解決する
- `resolution_candidates` を返す
- ユーザーの repo 承認を待つ

## 7. 推奨 API フロー

1. `POST /api/issues`
2. `GET /api/issues/{issue_id}/resolution`
3. `POST /api/issues/{issue_id}/project/approve`
4. `POST /api/issues/{issue_id}/plan`
5. `GET /api/issues/{issue_id}/plan`
6. 必要なら `POST /api/issues/{issue_id}/plan/revise`
7. 次のどちらか:
   - `POST /api/issues/{issue_id}/plan/approve`
   - `POST /api/issues/{issue_id}/plan/reject`
8. `GET /api/issues/{issue_id}/status`
9. `GET /api/issues/{issue_id}/implementation/summary`
10. `GET /api/issues/{issue_id}/artifacts`

必要に応じて最後に:

11. `POST /api/issues/{issue_id}/review/approve`
12. `POST /api/issues/{issue_id}/pr`

## 8. issue_id による状態管理

このサービスは `issue_id` ごとに状態を `runs/<issue_id>/` に保存します。

主なファイル:

- `issue.json`
- `state.json`
- `plan_v*.md`
- `plan.md`
- `implementation.json`
- `git_diff.patch`
- `head_show.txt`
- `test_results.json`
- `change_summary.json`

そのため、issue が作成され repo が承認された後は、後続 API は `issue_id` だけで動作できます。

## 9. repo 解決が違う場合

ユーザーが推奨 repo を却下した場合:

1. `GET /api/issues/{issue_id}/resolution` を呼ぶ
2. `resolution_candidates` を表示する
3. ユーザーに familiar repo を選ばせる
4. `POST /api/issues/{issue_id}/project/approve` を呼ぶ

候補に正しい repo がない場合は、上流アプリが approval request に明示的な repo override を送れます。

## 10. フォールバック UI

内蔵 UI はまだありますが、現在はフォールバック用途です。

開き方:

```text
http://<host>:8000/?issue_id=<issue_id>
```

通常運用ではなく、手動確認やリカバリ用途として使ってください。

## 11. 重要なドキュメント

- API reference: [docs/API_REFERENCE_FOR_INTEGRATION.md](C:\Users\vinayakram.r\Downloads\codex-native-mvp-package\codex-native-mvp\docs\API_REFERENCE_FOR_INTEGRATION.md)
- upstream integration contract: [docs/UPSTREAM_INTEGRATION_CONTRACT.md](C:\Users\vinayakram.r\Downloads\codex-native-mvp-package\codex-native-mvp\docs\UPSTREAM_INTEGRATION_CONTRACT.md)
- upstream developer handoff: [docs/UPSTREAM_DEVELOPER_HANDOFF.md](C:\Users\vinayakram.r\Downloads\codex-native-mvp-package\codex-native-mvp\docs\UPSTREAM_DEVELOPER_HANDOFF.md)
- Linux deployment and test: [docs/LINUX_VM_DEPLOYMENT_AND_E2E.md](C:\Users\vinayakram.r\Downloads\codex-native-mvp-package\codex-native-mvp\docs\LINUX_VM_DEPLOYMENT_AND_E2E.md)
- user guide: [USER_GUIDE.md](C:\Users\vinayakram.r\Downloads\codex-native-mvp-package\codex-native-mvp\USER_GUIDE.md)

## 12. トラブルシューティング

解決精度が低い場合:

- `config/project_map.json` を確認する
- `matchers` を増やす
- 上流アプリから repo hints を送る

実装に失敗する場合:

- `GET /api/issues/{issue_id}/status` を確認する
- `GET /api/issues/{issue_id}/implementation/summary` を確認する
- `GET /api/issues/{issue_id}/artifacts` を確認する

PR 作成に失敗する場合:

- `GITHUB_TOKEN` または `gh auth login` を確認する
- リメディエーションホストから対象 repo remote に到達できるか確認する
