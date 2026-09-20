#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PYTHON="$PROJECT_ROOT/.venv/bin/python"
TARGET="${1:-$(rustc --print host-tuple)}"
BUNDLE_DIR="$PROJECT_ROOT/src-tauri/binaries"

"$PYTHON" -m PyInstaller --noconfirm --clean --onefile \
  --name student-ai-checker-backend \
  --paths "$PROJECT_ROOT/src" \
  --add-data "$PROJECT_ROOT/web:web" \
  --add-data "$PROJECT_ROOT/scripts:scripts" \
  --add-data "$PROJECT_ROOT/examples:examples" \
  "$PROJECT_ROOT/scripts/desktop_backend.py"

mkdir -p "$BUNDLE_DIR"
cp "$PROJECT_ROOT/dist/student-ai-checker-backend" "$BUNDLE_DIR/student-ai-checker-backend-$TARGET"
echo "Backend sidecar готов: $BUNDLE_DIR/student-ai-checker-backend-$TARGET"
