#!/bin/bash
set -euo pipefail

# =============================================================================
# Deploy EC2 Services (Embed + Index + Search + Caddy + Frontend)
# =============================================================================
# Uploads all source code and (re)builds Docker containers on the EC2 instance.
#
# Usage:
#   ./deploy/ec2/deploy.sh <EC2_IP> <KEY_PATH>
#   ./deploy/ec2/deploy.sh 18.212.73.65 ./amir1.pem
# =============================================================================

EC2_IP="${1:?Usage: $0 <EC2_IP> <KEY_PATH>}"
KEY_PATH="${2:?Usage: $0 <EC2_IP> <KEY_PATH>}"
EC2_USER="ubuntu"
REMOTE_DIR="~/app"
SSH_OPTS=(-i "${KEY_PATH}" -o StrictHostKeyChecking=no)

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SRC_DIR="${SCRIPT_DIR}/src"

echo "=== Deploying EC2 services to ${EC2_IP} ==="

# Ensure remote directory structure
ssh "${SSH_OPTS[@]}" "${EC2_USER}@${EC2_IP}" "mkdir -p ${REMOTE_DIR}/services"

# Upload frontend
echo "Uploading frontend..."
scp "${SSH_OPTS[@]}" "${SRC_DIR}/frontend/index.html" "${EC2_USER}@${EC2_IP}:${REMOTE_DIR}/index.html"

# Upload service source code
echo "Uploading services..."
for svc in embed search index shared; do
    scp -r "${SSH_OPTS[@]}" "${SRC_DIR}/${svc}/" "${EC2_USER}@${EC2_IP}:${REMOTE_DIR}/services/${svc}/"
done

# Upload docker-compose and Caddyfile
scp "${SSH_OPTS[@]}" "${SRC_DIR}/docker-compose.yml" "${EC2_USER}@${EC2_IP}:${REMOTE_DIR}/services/docker-compose.yml"
scp "${SSH_OPTS[@]}" "${SRC_DIR}/Caddyfile" "${EC2_USER}@${EC2_IP}:${REMOTE_DIR}/services/Caddyfile"

# Upload .env if it exists locally (won't overwrite remote)
if [ -f "${SCRIPT_DIR}/.env" ]; then
    echo "Uploading .env..."
    scp "${SSH_OPTS[@]}" "${SCRIPT_DIR}/.env" "${EC2_USER}@${EC2_IP}:${REMOTE_DIR}/services/.env"
fi

# Check if Docker is installed
echo "Checking Docker..."
DOCKER_INSTALLED=$(ssh "${SSH_OPTS[@]}" "${EC2_USER}@${EC2_IP}" "which docker 2>/dev/null && echo yes || echo no")

if [ "$DOCKER_INSTALLED" = "no" ]; then
    echo "Docker not found. Running first-time setup..."
    scp "${SSH_OPTS[@]}" "${SCRIPT_DIR}/setup.sh" "${EC2_USER}@${EC2_IP}:~/setup.sh"
    ssh "${SSH_OPTS[@]}" "${EC2_USER}@${EC2_IP}" "bash ~/setup.sh"
    echo ""
    echo "Setup complete. Log out, log back in, then run this script again."
    exit 0
fi

# Build and restart
echo "Building and restarting services..."
ssh "${SSH_OPTS[@]}" "${EC2_USER}@${EC2_IP}" \
    "cd ${REMOTE_DIR}/services && docker compose build --no-cache && docker compose up -d --force-recreate"

# Health checks
echo "Waiting for services to start..."
sleep 10

echo "Health checks (via Caddy):"
ssh "${SSH_OPTS[@]}" "${EC2_USER}@${EC2_IP}" \
    "curl -sf http://localhost/api/health && echo ' ← caddy OK' || echo 'caddy: FAILED'"

echo "Service health (direct):"
for svc in embed search index; do
    ssh "${SSH_OPTS[@]}" "${EC2_USER}@${EC2_IP}" \
        "docker compose -f ${REMOTE_DIR}/services/docker-compose.yml exec -T ${svc} curl -sf http://localhost:8000/health 2>/dev/null && echo \" ← ${svc} OK\" || echo \"${svc}: FAILED\""
done

echo ""
echo "=== Deploy complete ==="
echo ""
echo "Endpoints:"
echo "  Frontend:     https://your-domain.com"
echo "  Search API:   https://your-domain.com/api/search"
echo "  Embed API:    https://your-domain.com/api/embed/{embed-query,embed-texts,rerank}"
echo "  Index API:    https://your-domain.com/api/index/index"
echo "  Health check: https://your-domain.com/api/health"
