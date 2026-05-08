#!/usr/bin/env bash
# Starts the AIOPS remediation FastAPI server in the foreground on Linux.
# Activates the .venv-linux virtual environment and launches uvicorn with
# PYTHONUNBUFFERED set for real-time log streaming on the configured host and port.
#
# Linuxでの仮想環境(.venv-linux)をアクティベートし、AIOPSリメディエーションの
# FastAPIサーバーをフォアグラウンドで起動する。
# PYTHONUNBUFFEREDを設定してリアルタイムログ出力を有効にし、uvicornで起動する。
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_DIR="${ROOT_DIR}/.venv-linux"

if [ ! -d "${VENV_DIR}" ]; then
  echo "Virtual environment not found. Run scripts/bootstrap_linux.sh first."
  exit 1
fi

source "${VENV_DIR}/bin/activate"

export PYTHONUNBUFFERED=1
export APP_HOST="${APP_HOST:-0.0.0.0}"
export APP_PORT="${APP_PORT:-8005}"

python -m uvicorn app.web:app --host "${APP_HOST}" --port "${APP_PORT}"
