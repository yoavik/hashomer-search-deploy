#!/bin/bash
set -euo pipefail

# =============================================================================
# Deploy Pipeline to EC2 + Setup Cron
# =============================================================================
# Uploads trigger_pipeline.py to EC2, creates venv, sets up hourly cron.
#
# Usage:
#   ./deploy/pipeline/deploy.sh <EC2_IP> <KEY_PATH>
# =============================================================================

EC2_IP="${1:?Usage: $0 <EC2_IP> <KEY_PATH>}"
KEY_PATH="${2:?Usage: $0 <EC2_IP> <KEY_PATH>}"
EC2_USER="ubuntu"
REMOTE_DIR="~/app"
SSH_OPTS=(-i "${KEY_PATH}" -o StrictHostKeyChecking=no)

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

echo "=== Deploying Pipeline to ${EC2_IP} ==="

# Upload pipeline script
echo "Uploading trigger_pipeline.py..."
scp "${SSH_OPTS[@]}" "${SCRIPT_DIR}/src/trigger_pipeline.py" "${EC2_USER}@${EC2_IP}:${REMOTE_DIR}/trigger_pipeline.py"

# Upload requirements
scp "${SSH_OPTS[@]}" "${SCRIPT_DIR}/requirements.txt" "${EC2_USER}@${EC2_IP}:${REMOTE_DIR}/pipeline-requirements.txt"

# Setup venv + cron on EC2
echo "Setting up venv and cron..."
ssh "${SSH_OPTS[@]}" "${EC2_USER}@${EC2_IP}" << 'REMOTE_SCRIPT'
set -euo pipefail

VENV_DIR="${HOME}/venv-pipeline"
APP_DIR="${HOME}/app"
LOG_FILE="/var/log/pipeline.log"

# Create venv
if [ ! -d "${VENV_DIR}" ]; then
    python3 -m venv "${VENV_DIR}"
fi
source "${VENV_DIR}/bin/activate"
pip install --upgrade pip -q
pip install -r "${APP_DIR}/pipeline-requirements.txt" -q

# Ensure log file
if [ ! -f "${LOG_FILE}" ]; then
    sudo touch "${LOG_FILE}"
    sudo chown "$(whoami):$(whoami)" "${LOG_FILE}"
fi

# Add cron entry if not present
CRON_CMD="0 * * * * cd ${APP_DIR} && ${VENV_DIR}/bin/python trigger_pipeline.py --hours 2 >> ${LOG_FILE} 2>&1"
if ! crontab -l 2>/dev/null | grep -q "trigger_pipeline.py"; then
    (crontab -l 2>/dev/null; echo "${CRON_CMD}") | crontab -
    echo "Cron entry added."
else
    echo "Cron entry already exists."
fi
REMOTE_SCRIPT

echo ""
echo "=== Pipeline deployed ==="
echo ""
echo "Test:"
echo "  ssh -i ${KEY_PATH} ${EC2_USER}@${EC2_IP}"
echo "  source ~/venv-pipeline/bin/activate"
echo "  cd ~/app && python trigger_pipeline.py --dry-run --hours 24"
echo ""
echo "Monitor:"
echo "  ssh -i ${KEY_PATH} ${EC2_USER}@${EC2_IP} tail -f /var/log/pipeline.log"
