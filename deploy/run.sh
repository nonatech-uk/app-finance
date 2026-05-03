#!/bin/bash
# Build and run the finance-sync container.
#
# Prerequisites:
#   /opt/finance/secrets/.env       — DB creds, Wise token, Monzo client ID/secret
#   /opt/finance/secrets/tokens.json — Monzo OAuth tokens (created on first auth)
#
# Usage:
#   ./deploy/run.sh              # build + run
#   ./deploy/run.sh --build-only # just build

set -euo pipefail

# Run from project root regardless of where this script was invoked from.
cd "$(dirname "$0")/.."

IMAGE="finance-sync:latest"
CONTAINER="finance-sync"
SECRETS_DIR="/zfs/Apps/AppData/finance"

# Stage shared libraries into build context (gitignored peers)
echo "Staging shared libraries..."
rm -rf mees-shared-py mees-shared-ui
cp -r ../mees-shared-py .
cp -r ../mees-shared-ui .
rm -rf mees-shared-py/.git mees-shared-ui/.git mees-shared-ui/node_modules
trap 'rm -rf mees-shared-py mees-shared-ui' EXIT

# Build
echo "Building $IMAGE..."
podman build -t "$IMAGE" -f Containerfile .

if [ "${1:-}" = "--build-only" ]; then
    echo "Build complete."
    exit 0
fi

# Stop existing
if podman container exists "$CONTAINER" 2>/dev/null; then
    echo "Stopping existing container..."
    podman stop "$CONTAINER" || true
    podman rm "$CONTAINER" || true
fi

# Ensure tokens file exists (will be populated on first auth)
touch "$SECRETS_DIR/tokens.json"

echo "Starting $CONTAINER..."
podman run -d \
    --name "$CONTAINER" \
    --restart unless-stopped \
    --network host \
    -v "$SECRETS_DIR/.env:/app/config/.env:ro" \
    -v "$SECRETS_DIR/tokens.json:/app/tokens.json:rw" \
    -e MONZO_TOKEN_FILE=/app/tokens.json \
    -e MONZO_REDIRECT_URI="https://finance.mees.st/oauth/callback" \
    "$IMAGE"

echo "Container started. Auth UI at https://finance.mees.st/"
echo "Logs: podman logs -f $CONTAINER"
