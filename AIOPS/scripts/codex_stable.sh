#!/usr/bin/env bash
# Stable Codex CLI launcher that locates the highest installed version via fnm.
# Searches for the codex binary under the fnm node-versions directory and executes it,
# failing with a clear error message if the CLI has not been installed yet.
#
# fnmのnode-versionsディレクトリから最新のcodexバイナリを検索して実行する安定版ランチャー。
# CLIがインストールされていない場合は分かりやすいエラーメッセージを表示して終了する。
# npm install -g @openai/codex で事前にインストールしておく必要がある。
set -euo pipefail

FNM_HOME="${FNM_HOME:-${HOME}/.local/share/fnm}"

find_codex() {
  if [ -d "${FNM_HOME}/node-versions" ]; then
    find "${FNM_HOME}/node-versions" -path "*/installation/bin/codex" -print 2>/dev/null | sort -V | tail -n 1
  fi
}

CODEX_BIN="${CODEX_BIN:-$(find_codex)}"

if [ -z "${CODEX_BIN}" ] || [ ! -e "${CODEX_BIN}" ]; then
  echo "Codex CLI not found under ${FNM_HOME}/node-versions." >&2
  echo "Install it with: npm install -g @openai/codex" >&2
  exit 127
fi

exec "${CODEX_BIN}" "$@"
