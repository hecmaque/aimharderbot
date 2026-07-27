#!/bin/bash
set -euo pipefail

# Root of the repo, regardless of where the script is invoked from.
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

# Ensure cron has a usable PATH.
export PATH="/usr/local/bin:/usr/bin:/bin:$PATH"

LOG_DIR="$REPO_ROOT/logs"
CONFIG_DIR="$REPO_ROOT/config"
CONFIG_FILE='example_config.yaml'
IMAGE='aimharderbot:v1'

mkdir -p "$LOG_DIR"

docker run --rm \
  -v "$LOG_DIR:/usr/src/app/logs" \
  -v "$CONFIG_DIR:/usr/src/app/config" \
  --name aimharderbot \
  "$IMAGE" \
  --config-filename="$CONFIG_FILE"
