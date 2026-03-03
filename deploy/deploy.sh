#!/bin/bash
# Run on the VPS after you push changes to GitHub.
# Deploys the multi-tenant-admin branch.
# Usage: cd /opt/training-tracker && sudo ./deploy/deploy.sh

set -e
cd /opt/training-tracker
git fetch origin
git reset --hard origin/multi-tenant-admin
.venv/bin/pip install -r requirements.txt --quiet
set -a
source .env
set +a
.venv/bin/flask db upgrade
.venv/bin/python init_db.py
systemctl restart training-tracker
# Restart RQ worker if present (so it picks up new code for email tasks)
systemctl restart training-tracker-worker 2>/dev/null || true
chown -R www-data:www-data /opt/training-tracker
echo "Deploy done. Check: systemctl status training-tracker"
