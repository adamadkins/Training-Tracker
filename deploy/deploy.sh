#!/bin/bash
# Run on the VPS after you push changes to GitHub.
# Usage: cd /opt/training-tracker && sudo ./deploy/deploy.sh

set -e
cd /opt/training-tracker
git fetch origin
git reset --hard origin/main
.venv/bin/pip install -r requirements.txt --quiet
set -a
source .env
set +a
.venv/bin/flask db upgrade
.venv/bin/python init_db.py
systemctl restart training-tracker
chown -R www-data:www-data /opt/training-tracker
echo "Deploy done. Check: systemctl status training-tracker"
