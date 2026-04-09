#!/bin/bash
set -euo pipefail

# =============================================================================
# EC2 First-Time Setup (Ubuntu 24.04)
# =============================================================================
# Installs Docker, Docker Compose, Python, and creates a systemd service.
#
# Usage:
#   ssh -i key.pem ubuntu@EC2_IP "bash -s" < deploy/ec2/setup.sh
# =============================================================================

echo "=== HaShomer EC2 Setup ==="

sudo apt-get update -qq
sudo apt-get upgrade -y -qq

# Docker
echo "Installing Docker..."
sudo apt-get install -y -qq docker.io docker-compose-v2
sudo usermod -aG docker ubuntu

# Python (for pipeline trigger)
echo "Installing Python..."
sudo apt-get install -y -qq python3-pip python3-venv

# App directory
mkdir -p ~/app/services

# Systemd service for auto-restart
echo "Creating systemd service..."
sudo tee /etc/systemd/system/hashomer.service > /dev/null << 'EOF'
[Unit]
Description=HaShomer Search Services
After=docker.service
Requires=docker.service

[Service]
Type=oneshot
RemainAfterExit=yes
User=ubuntu
WorkingDirectory=/home/ubuntu/app/services
ExecStart=/usr/bin/docker compose up -d
ExecStop=/usr/bin/docker compose down

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable hashomer

# Pipeline log file
sudo touch /var/log/pipeline.log
sudo chown ubuntu:ubuntu /var/log/pipeline.log

echo ""
echo "=== Setup complete ==="
echo "Log out and back in for docker group to take effect."
