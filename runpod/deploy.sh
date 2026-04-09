#!/bin/bash
set -euo pipefail

# =============================================================================
# Deploy Diarization to RunPod Serverless
# =============================================================================
# Builds Docker image and pushes to registry for RunPod.
#
# Usage:
#   ./deploy/runpod/deploy.sh <DOCKER_REGISTRY>
#   ./deploy/runpod/deploy.sh myuser/hashomer-diarize
# =============================================================================

REGISTRY="${1:?Usage: $0 <DOCKER_REGISTRY> (e.g. myuser/hashomer-diarize)}"
TAG="${2:-latest}"
IMAGE="${REGISTRY}:${TAG}"

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SRC_DIR="${SCRIPT_DIR}/src"

echo "=== Building RunPod Diarization Image ==="
echo "Image: ${IMAGE}"

# Build from src/ context using the RunPod Dockerfile
docker build -t "${IMAGE}" -f "${SRC_DIR}/Dockerfile.runpod" "${SRC_DIR}/"

echo ""
echo "=== Pushing to registry ==="
docker push "${IMAGE}"

echo ""
echo "=== Image pushed: ${IMAGE} ==="
echo ""
echo "Next steps (RunPod Dashboard — https://www.runpod.io/console/serverless):"
echo "  1. Create/update Serverless Endpoint"
echo "  2. Container image: ${IMAGE}"
echo "  3. GPU: RTX 3090 or A4000 (min 16GB VRAM)"
echo "  4. Max workers: 1-3, Idle timeout: 60s, Execution timeout: 600s"
echo "  5. Environment variables:"
echo "     AWS_KEY_ID, AWS_SECRET, S3_BUCKET, S3_REGION, HF_TOKEN"
echo "  6. Save endpoint ID → set as RUNPOD_DIARIZE_ENDPOINT in EC2 .env"
