#!/bin/bash
set -euo pipefail

# =============================================================================
# Deploy Audio Processing to RunPod Serverless
# =============================================================================
# Builds Docker image for audio segmentation + noise map generation.
#
# Usage:
#   ./deploy/audio-processing/deploy.sh <DOCKER_REGISTRY>
#   ./deploy/audio-processing/deploy.sh myuser/hashomer-audio-segmenter
# =============================================================================

REGISTRY="${1:?Usage: $0 <DOCKER_REGISTRY> (e.g. myuser/hashomer-audio-segmenter)}"
TAG="${2:-latest}"
IMAGE="${REGISTRY}:${TAG}"

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SRC_DIR="${SCRIPT_DIR}/src"

echo "=== Building RunPod Audio Segmenter Image ==="
echo "Image: ${IMAGE}"

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
echo "  3. GPU: RTX 3090 or similar (Silero VAD benefits from GPU)"
echo "  4. Max workers: 1-2, Idle timeout: 60s, Execution timeout: 600s"
echo "  5. Environment variables:"
echo "     AWS_KEY_ID, AWS_SECRET, S3_BUCKET, S3_REGION"
echo "  6. Save endpoint ID → set as RUNPOD_AUDIO_ENDPOINT in pipeline .env"
