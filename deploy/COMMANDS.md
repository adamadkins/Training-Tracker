# Deploy commands

## On your Mac (push code)

```bash
cd /Users/adamadkins/Training-Tracker
git add -A
git status
git commit -m "Your message"   # only if you have changes
git push origin main
```

## On the server (SSH then deploy)

Replace `root` and your droplet host if you use a different user or hostname.

```bash
ssh root@Training-Tracker-Droplet
```

Then run deploy (fetch latest, reset to main, install, migrate, restart):

```bash
cd /opt/training-tracker && git fetch origin && git reset --hard origin/main && sudo bash deploy/deploy.sh
```

If you get **divergent branches** when using `git pull` somewhere, set a default once:

```bash
git config --global pull.rebase false
```

## If deploy fails with: "Can't locate revision identified by 'e7f8a9b0c1d2'"

The server DB has an old migration revision that no longer exists in the repo. Fix it once on the server:

```bash
cd /opt/training-tracker
set -a && source .env && set +a
.venv/bin/flask db stamp head
```

Then run the full deploy again:

```bash
cd /opt/training-tracker && git fetch origin && git reset --hard origin/main && sudo bash deploy/deploy.sh
```

## Check the app is running

On the server:

```bash
systemctl status training-tracker
```
