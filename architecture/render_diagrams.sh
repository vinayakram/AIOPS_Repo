#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
OUT="$ROOT/out"
CFG="$ROOT/puppeteer-config.json"
mkdir -p "$OUT"

if [ ! -f "$CFG" ]; then
  cat > "$CFG" << 'JSON'
{
  "args": ["--no-sandbox", "--disable-setuid-sandbox"]
}
JSON
fi

mmdc -p "$CFG" -i "$ROOT/aiops_e2e_architecture.mmd" -o "$OUT/aiops_e2e_architecture.svg" -t default -b transparent
mmdc -p "$CFG" -i "$ROOT/aiops_e2e_architecture.mmd" -o "$OUT/aiops_e2e_architecture.png" -t default -b white -w 2600

mmdc -p "$CFG" -i "$ROOT/aiops_e2e_sequence.mmd" -o "$OUT/aiops_e2e_sequence.svg" -t default -b transparent
mmdc -p "$CFG" -i "$ROOT/aiops_e2e_sequence.mmd" -o "$OUT/aiops_e2e_sequence.png" -t default -b white -w 2400

echo "Rendered files in: $OUT"
ls -1 "$OUT"
