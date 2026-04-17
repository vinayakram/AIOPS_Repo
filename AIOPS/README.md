# Codex Native MVP

Codex Native MVP is an API-first remediation service. It receives an issue from an upstream application, resolves the target repository, generates a plan, runs implementation with Codex CLI, and returns summaries and artifacts through APIs.

This README is for a normal user or developer who wants to run the application from scratch.

## What You Need Before Starting

Install these tools on the machine that will run the remediation service:

- Python 3.11+
- Node.js and npm
- Git
- Codex CLI
- optional GitHub CLI if you want PR creation through `gh`

Install Codex CLI:

```bash
npm install -g @openai/codex
```

Optional GitHub CLI login:

```bash
gh auth login
```

## 1. Clone The Repository

```bash
git clone <your-repo-url>
cd AIOPS
```

## 2. Create A Python Environment

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

## 3. Create The Environment File

Copy the example file:

Windows:

```powershell
copy .env.example .env
```

Linux:

```bash
cp .env.example .env
```

Minimum recommended `.env` values:

```env
CODEX_API_KEY=your_new_key
CODEX_COMMAND=codex
CODEX_MODEL=gpt-5-codex
DEFAULT_VALIDATION_COMMAND=pytest -q
RUNS_DIR=./runs
MANAGED_REPOS_DIR=./managed_repos
GITHUB_TOKEN=
```

Notes:

- `CODEX_API_KEY` should be the key you want this service to use.
- `GITHUB_TOKEN` is optional, but recommended if you want automatic PR creation without `gh`.
- `RUNS_DIR` and `MANAGED_REPOS_DIR` can be left as defaults unless you want custom paths.

## 4. Configure Project Resolution

Create or update:

`config/project_map.json`

This file maps application names to repositories.

Example:

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

Project resolution works if at least one of these is true:

- the application name matches an entry in `config/project_map.json`
- the upstream application sends repository hints such as `github_repo`, `upstream_repo`, `repo_root`, or `allowed_folder`

## 5. Start The Service

Windows:

```powershell
python -m uvicorn app.web:app --host 0.0.0.0 --port 8000
```

Linux:

```bash
python -m uvicorn app.web:app --host 0.0.0.0 --port 8000
```

If you are using the Linux helper script:

```bash
./scripts/start_linux.sh
```

If you want to keep the service running in the background after logout:

```bash
chmod +x ./scripts/run_background_linux.sh
./scripts/run_background_linux.sh
```

This writes:
- PID file: `.runtime/codex-remediation.pid`
- log file: `.runtime/codex-remediation.log`

Service URL:

```text
http://<host>:8000
```

## 6. First API Call

Send the issue from the upstream application:

`POST /api/issues`

Example:

```json
{
  "application_name": "multiagent-support-copilot",
  "issue_id": "SUP-5001",
  "description": "Payload context is dropped during agent handoff."
}
```

The service will:

- create the issue
- resolve candidate repositories
- return `resolution_candidates`
- wait for user approval of the repo

## 7. Normal API Flow

1. `POST /api/issues`
2. `GET /api/issues/{issue_id}/resolution`
3. `POST /api/issues/{issue_id}/project/approve`
4. `POST /api/issues/{issue_id}/plan`
5. `GET /api/issues/{issue_id}/plan`
6. Optional: `POST /api/issues/{issue_id}/plan/revise`
7. Either:
   - `POST /api/issues/{issue_id}/plan/approve`
   - or `POST /api/issues/{issue_id}/plan/reject`
8. `GET /api/issues/{issue_id}/status`
9. `GET /api/issues/{issue_id}/implementation/summary`
10. `GET /api/issues/{issue_id}/artifacts`

Optional final delivery steps:

11. `POST /api/issues/{issue_id}/review/approve`
12. `POST /api/issues/{issue_id}/pr`

## 8. How Issue State Works

The service stores workflow state by `issue_id` under `runs/<issue_id>/`.

Important files:

- `issue.json`
- `state.json`
- `plan_v*.md`
- `plan.md`
- `implementation.json`
- `git_diff.patch`
- `head_show.txt`
- `test_results.json`
- `change_summary.json`

Because of this, once the issue is created and the repository is approved, later APIs can work using only `issue_id`.

## 9. What To Do If Repo Resolution Is Wrong

If the user rejects the recommended repo:

1. call `GET /api/issues/{issue_id}/resolution`
2. show the `resolution_candidates` list
3. let the user choose one of the familiar repos
4. call `POST /api/issues/{issue_id}/project/approve`

If none of the candidates are correct, the upstream app can send an explicit repo override in the approval request.

## 10. Fallback UI

The built-in UI still exists, but it is fallback only.

Open:

```text
http://<host>:8000/?issue_id=<issue_id>
```

Use it only if you need manual recovery or manual inspection.

## 11. Important Docs

- API reference: [docs/API_REFERENCE_FOR_INTEGRATION.md](C:\Users\vinayakram.r\Downloads\codex-native-mvp-package\codex-native-mvp\docs\API_REFERENCE_FOR_INTEGRATION.md)
- upstream integration contract: [docs/UPSTREAM_INTEGRATION_CONTRACT.md](C:\Users\vinayakram.r\Downloads\codex-native-mvp-package\codex-native-mvp\docs\UPSTREAM_INTEGRATION_CONTRACT.md)
- upstream developer handoff: [docs/UPSTREAM_DEVELOPER_HANDOFF.md](C:\Users\vinayakram.r\Downloads\codex-native-mvp-package\codex-native-mvp\docs\UPSTREAM_DEVELOPER_HANDOFF.md)
- Linux deployment and end-to-end test: [docs/LINUX_VM_DEPLOYMENT_AND_E2E.md](C:\Users\vinayakram.r\Downloads\codex-native-mvp-package\codex-native-mvp\docs\LINUX_VM_DEPLOYMENT_AND_E2E.md)
- user guide: [USER_GUIDE.md](C:\Users\vinayakram.r\Downloads\codex-native-mvp-package\codex-native-mvp\USER_GUIDE.md)

## 12. Troubleshooting

If the service starts but resolution is weak:

- check `config/project_map.json`
- improve `matchers`
- pass repo hints from the upstream application

If implementation fails:

- check `GET /api/issues/{issue_id}/status`
- check `GET /api/issues/{issue_id}/implementation/summary`
- review `GET /api/issues/{issue_id}/artifacts`

If PR creation fails:

- verify `GITHUB_TOKEN` or `gh auth login`
- verify the repository remote is reachable from the remediation host
