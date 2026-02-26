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

# If DB has an old revision the repo no longer has, fix it automatically
set +e
upgrade_out=$(.venv/bin/flask db upgrade 2>&1); upgrade_code=$?
set -e
if [ "$upgrade_code" -ne 0 ]; then
  if echo "$upgrade_out" | grep -q "Can't locate revision"; then
    echo "Fixing migration state (stamp head)..."
    .venv/bin/flask db stamp head
  else
    echo "$upgrade_out"
    exit "$upgrade_code"
  fi
fi

.venv/bin/python init_db.py
systemctl restart training-tracker
chown -R www-data:www-data /opt/training-tracker
echo "Deploy done. Check: systemctl status training-tracker"
