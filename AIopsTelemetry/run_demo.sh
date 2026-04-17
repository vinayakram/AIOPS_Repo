#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEMO_PY="$ROOT_DIR/e2e_demo.py"

usage() {
  cat <<'EOF'
Usage:
  ./run_demo.sh <preset> [extra args...]

Presets:
  english-full      Full English demo with slower narration
  japanese-full     Full Japanese demo with slower narration
  executive-short   Faster English RCA-only version
  remediation-focus English demo focused on remediation

Examples:
  ./run_demo.sh english-full
  ./run_demo.sh japanese-full --query "糖尿病の最新治療法は？"
  ./run_demo.sh executive-short --medical-url http://localhost:8002
EOF
}

if [[ $# -lt 1 ]]; then
  usage
  exit 1
fi

preset="$1"
shift

case "$preset" in
  english-full)
    exec python3 "$DEMO_PY" --lang en --demo-speed slow --mode full "$@"
    ;;
  japanese-full)
    exec python3 "$DEMO_PY" --lang ja --demo-speed slow --mode full "$@"
    ;;
  executive-short)
    exec python3 "$DEMO_PY" --lang en --demo-speed fast --mode rca-only "$@"
    ;;
  remediation-focus)
    exec python3 "$DEMO_PY" --lang en --demo-speed normal --mode remediation-only "$@"
    ;;
  -h|--help|help)
    usage
    exit 0
    ;;
  *)
    echo "Unknown preset: $preset" >&2
    echo >&2
    usage
    exit 1
    ;;
esac
