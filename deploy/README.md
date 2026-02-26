# Redeploying Training Tracker (Flask app on server)

After you push changes to GitHub, update the live app on your Ubuntu server as follows.

## 1. SSH into the server

```bash
ssh root@159.203.111.92
```

(Or use your usual user if you deploy with sudo.)

## 2. Go to the app directory and pull + deploy

```bash
cd /opt/training-tracker
```

If **git pull** says you have "divergent branches", reset the repo to match GitHub and then run the deploy script:

```bash
git fetch origin
git reset --hard origin/main
sudo bash deploy/deploy.sh
```

If **git pull** works cleanly, you can do:

```bash
git pull origin main
sudo bash deploy/deploy.sh
```

Use `sudo bash deploy/deploy.sh` (not `sudo ./deploy/deploy.sh`) so the script runs with bash and has correct permissions.

## 3. Confirm the service is running

```bash
systemctl status training-tracker
```

To see recent logs:

```bash
journalctl -u training-tracker -n 50 --no-pager
```

## One-liner (after SSH)

```bash
cd /opt/training-tracker && git fetch origin && git reset --hard origin/main && sudo bash deploy/deploy.sh
```

This always resets to `origin/main` and redeploys. Use it if you don’t care about any local commits on the server.
