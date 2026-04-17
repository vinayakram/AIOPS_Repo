# Demo Script

## Overview

This demo shows how the remediation application helps a user go from a manually entered issue to:
- project identification
- plan generation
- approval
- implementation
- pull request creation

このデモでは、Remediationアプリが手動入力された課題から以下の流れをどのように支援するかを示します。
- 対象プロジェクトの特定
- 修正計画の生成
- 承認
- 実装
- プルリクエスト作成

## Demo Systems

### 1. multiagent-order-orchestrator / 注文オーケストレーター

Simple explanation:
This is an LLM-backed multi-agent order processing system.
A planner agent decides the route, an inventory agent checks stock, and a fulfillment agent prepares dispatch.

簡単な説明:
これは、LLMを活用したマルチエージェント型の注文処理システムです。
Planner Agentがルートを決め、Inventory Agentが在庫を確認し、Fulfillment Agentが出荷準備を行います。

### 2. multiagent-support-copilot / サポート支援コパイロット

Simple explanation:
This is an LLM-backed multi-agent support system.
A triage agent classifies the case, a conversation agent prepares context, and a resolution agent prepares the final response.

簡単な説明:
これは、LLMを活用したマルチエージェント型のサポートシステムです。
Triage Agentがケースを分類し、Conversation Agentが文脈を整え、Resolution Agentが最終回答を作成します。

## Step 1: Show The Sample Repositories

Repositories:
- [multiagent-order-orchestrator](C:\Users\vinayakram.r\Downloads\codex-native-mvp-package\codex-native-mvp\demo_projects\multiagent-order-orchestrator)
- [multiagent-support-copilot](C:\Users\vinayakram.r\Downloads\codex-native-mvp-package\codex-native-mvp\demo_projects\multiagent-support-copilot)

Say:
"These are two separate LLM-backed multi-agent applications with realistic orchestration failures."

日本語:
「これは、現実的なオーケストレーション障害を持つ、2つのLLM活用マルチエージェントアプリケーションです。」

## Step 2: Show The Seeded Errors

### Order project / 注文プロジェクト

```powershell
cd C:\Users\vinayakram.r\Downloads\codex-native-mvp-package\codex-native-mvp\demo_projects\multiagent-order-orchestrator
python -m pytest tests -q
```

Say:
"The fulfillment LLM handoff is failing because the orchestration timeout is incorrectly capped."

日本語:
「FulfillmentへのLLMハンドオフが、オーケストレーション側のタイムアウト設定不備により失敗しています。」

### Support project / サポートプロジェクト

```powershell
cd C:\Users\vinayakram.r\Downloads\codex-native-mvp-package\codex-native-mvp\demo_projects\multiagent-support-copilot
python -m pytest tests -q
```

Say:
"The downstream resolution LLM receives incomplete context because the handoff payload drops important fields."

日本語:
「ハンドオフ時に重要な情報が落ちるため、下流のResolution LLMが不完全なコンテキストを受け取っています。」

## Step 3: Start The Remediation Application

```powershell
cd C:\Users\vinayakram.r\Downloads\codex-native-mvp-package\codex-native-mvp
python -m uvicorn app.web:app --reload
```

Open:
[http://127.0.0.1:8000](http://127.0.0.1:8000)

Say:
"Now I’ll take the same issue through the remediation workflow."

日本語:
「次に、この課題をRemediationワークフローで処理します。」

## Step 4: Enter A Manual Issue

Use one of these:
- [order-timeout-issue.md](C:\Users\vinayakram.r\Downloads\codex-native-mvp-package\codex-native-mvp\demo_assets\order-timeout-issue.md)
- [support-handoff-issue.md](C:\Users\vinayakram.r\Downloads\codex-native-mvp-package\codex-native-mvp\demo_assets\support-handoff-issue.md)

Say:
"The user only provides minimal inputs: application name, issue ID, title, description, and acceptance criteria."

日本語:
「ユーザーは最小限の入力だけを行います。アプリ名、課題ID、タイトル、説明、受け入れ条件です。」

## Step 5: Suggest Projects

Click:
`Suggest projects / 候補を表示`

Say:
"The system compares the issue context against known projects and suggests the most relevant repositories."

日本語:
「システムは課題の内容を既知プロジェクトと照合し、最も関連性の高いリポジトリ候補を提示します。」

Optional ambiguity demo:
- use a specific application name for a strong match
- use a vague handoff or timeout description to show multiple possible matches

日本語:
- 明確なアプリ名を使うと、強い一致候補が出ます
- あいまいなハンドオフやタイムアウト説明を使うと、複数候補を表示できます

## Step 6: Apply The Selected Project

Say:
"Once I select the project, the repository root and allowed folder are populated automatically."

日本語:
「プロジェクトを選択すると、リポジトリルートと編集対象フォルダが自動で入力されます。」

## Step 7: Save The Issue And Generate The Plan

Say:
"Codex now creates a concise, reviewer-friendly remediation plan."

日本語:
「ここでCodexが、レビューしやすい簡潔な修正計画を生成します。」

## Step 8: Approve And Implement

Say:
"After approval, Codex works on the selected repository, proposes the code change, and prepares a pull request."

日本語:
「承認後、Codexは選択されたリポジトリで修正を行い、コード変更案とプルリクエストを準備します。」

## Short Executive Summary

English:
"This solution helps teams move from issue intake to project identification, remediation planning, and code change proposal for LLM-based multi-agent systems."

Japanese:
「このソリューションは、LLMベースのマルチエージェントシステムに対して、課題受付から対象プロジェクト特定、修正計画、コード変更提案までを支援します。」
