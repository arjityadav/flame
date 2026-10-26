#!/usr/bin/env bash
# Delete the hackathon-licensed regulation data from this server (LICENSE-DATA.md: for use in the hackathon only).
#
#   scripts/purge.sh            # what the flame-purge.timer runs after the hackathon
#   scripts/purge.sh --now      # the same, by hand
#   scripts/purge.sh --dry-run  # list what would be deleted
#
# Stops the app, then deletes the PDFs, parsed texts, graph, search index, LLM cache, runs and uploads.
set -euo pipefail

APP_DIR="${FLAME_DIR:-}"
LOG="${FLAME_PURGE_LOG:-/var/log/flame-purge.log}"
DRY=0
[[ "${1:-}" == "--dry-run" ]] && DRY=1

TARGETS=(
  "$APP_DIR/industrial-ai-hackathon/regulations"
  "$APP_DIR/regulations"
  "$APP_DIR/subgoal"
  "$APP_DIR/cases"
  "$APP_DIR/data"
  "$APP_DIR/out"
)

if [[ -z "$APP_DIR" ]]; then
  echo "Set FLAME_DIR to the deployed app folder (e.g. FLAME_DIR=/opt/flame); refusing to guess." >&2
  exit 2
fi

if [[ $DRY -eq 1 ]]; then
  for t in "${TARGETS[@]}"; do [[ -e "$t" ]] && du -sh "$t"; done
  exit 0
fi

COMPOSE="$APP_DIR/deploy/oracle/docker-compose.yml"
if [[ -f "$COMPOSE" ]] && command -v docker >/dev/null; then
  docker compose -f "$COMPOSE" down --remove-orphans 2>/dev/null || true
fi
for t in "${TARGETS[@]}"; do
  if [[ -e "$t" || -L "$t" ]]; then rm -rf -- "$t"; fi
done
echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) flame purge: deleted regulation data, indexes, cache, runs and uploads in $APP_DIR" | tee -a "$LOG" 2>/dev/null || true
systemctl disable flame-purge.timer 2>/dev/null || true
