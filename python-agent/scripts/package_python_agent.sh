#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON_AGENT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
PROJECT_ROOT="$(cd "$PYTHON_AGENT_DIR/.." && pwd)"
DIST_DIR="$PYTHON_AGENT_DIR/dist"
PACKAGE_NAME="${1:-python-agent-demo}"
STAGING_DIR="$(mktemp -d)"

cleanup() {
  rm -rf "$STAGING_DIR"
}
trap cleanup EXIT

mkdir -p "$DIST_DIR"
rm -f "$DIST_DIR/$PACKAGE_NAME.zip"

rsync -a "$PYTHON_AGENT_DIR/" "$STAGING_DIR/$PACKAGE_NAME/python-agent/" \
  --exclude ".DS_Store" \
  --exclude ".git" \
  --exclude ".claude" \
  --exclude ".omc" \
  --exclude ".superpowers" \
  --exclude ".venv" \
  --exclude ".pytest_cache" \
  --exclude ".ruff_cache" \
  --exclude "__pycache__" \
  --exclude "*/__pycache__" \
  --exclude "node_modules" \
  --exclude "dist" \
  --exclude "test-report.json" \
  --exclude "course-test-report.json" \
  --exclude "screenshots" \
  --exclude "docs/superpowers" \
  --exclude "task_plan.md" \
  --exclude "findings.md" \
  --exclude "progress.md" \
  --exclude "config/products.yaml" \
  --exclude "config/crypto_market.yaml" \
  --exclude "config/talkshow.yaml"

if [ -d "$PROJECT_ROOT/frontend" ]; then
  mkdir -p "$STAGING_DIR/$PACKAGE_NAME/frontend"
  rsync -a "$PROJECT_ROOT/frontend/" "$STAGING_DIR/$PACKAGE_NAME/frontend/" \
    --exclude ".DS_Store" \
    --exclude ".git" \
    --exclude ".claude" \
    --exclude ".omc" \
    --exclude ".superpowers" \
    --exclude ".pytest_cache" \
    --exclude ".ruff_cache" \
    --exclude "__pycache__" \
    --exclude "*/__pycache__" \
    --exclude "node_modules" \
    --exclude "dist" \
    --exclude ".vite"
fi

(
  cd "$STAGING_DIR"
  zip -qr "$DIST_DIR/$PACKAGE_NAME.zip" "$PACKAGE_NAME"
)

echo "Wrote $DIST_DIR/$PACKAGE_NAME.zip"
