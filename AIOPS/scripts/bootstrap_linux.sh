#!/usr/bin/env bash
# Bootstrap script for setting up the AIOPS remediation platform on Linux.
# Creates a Python virtual environment, installs pip dependencies from requirements.txt,
# and installs the OpenAI Codex CLI via npm if it is not already available.
# Copies the example .env file and creates the required runs and managed_repos directories.
#
# LinuxにおけるAIOPSリメディエーションプラットフォームの初期セットアップスクリプト。
# Pythonの仮想環境を作成し、requirements.txtから依存パッケージをインストールする。
# OpenAI Codex CLIがない場合はnpm経由でインストールし、必要なディレクトリと.envを用意する。
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_DIR="${ROOT_DIR}/.venv-linux"

if ! command -v python3 >/dev/null 2>&1; then
  echo "python3 is required"
  exit 1
fi

if ! command -v npm >/dev/null 2>&1; then
  echo "npm is required to install Codex CLI"
  exit 1
fi

python3 -m venv "${VENV_DIR}"
source "${VENV_DIR}/bin/activate"
python -m pip install --upgrade pip
pip install -r "${ROOT_DIR}/requirements.txt"

if ! command -v codex >/dev/null 2>&1; then
  npm install -g @openai/codex
fi

if [ ! -f "${ROOT_DIR}/.env" ]; then
  cp "${ROOT_DIR}/.env.example" "${ROOT_DIR}/.env"
fi

mkdir -p "${ROOT_DIR}/runs" "${ROOT_DIR}/managed_repos"

echo "Bootstrap complete."
echo "Next:"
echo "1. Edit ${ROOT_DIR}/.env and set CODEX_API_KEY plus repo settings"
echo "2. Run ${ROOT_DIR}/scripts/start_linux.sh"
