# Deploy Training Tracker on a VPS (Ubuntu 22.04)

This guide walks you through running the app on a single Ubuntu 22.04 server with Nginx, Postgres, and (optionally) Redis. Follow the steps in order.

---

## If you're on Windows

You do everything from your Windows PC, but **all the commands in this guide are run on the VPS** (the Linux server), not in PowerShell or Command Prompt.

1. **Open a terminal on Windows**  
   Use **PowerShell** or **Windows Terminal** (built into Windows 10/11). Or install **PuTTY** if you prefer.

2. **Connect to the VPS**  
   In that terminal, run:  
   `ssh root@YOUR_SERVER_IP`  
   (Replace `YOUR_SERVER_IP` with your server’s IP.)  
   After you log in, your prompt will change—you’re now typing on the **Linux server**.

3. **Paste the guide’s commands there**  
   Copy each command or block from this guide and paste them into the SSH session. In most terminals you paste with **right‑click** or **Ctrl+Shift+V**.  
   The server runs Ubuntu, so commands like `apt`, `systemctl`, and `bash` work there.

4. **Editing files on the server**  
   When the guide says “edit this file,” you’ll use a text editor on the server, e.g. `nano .env`. In `nano`, save with **Ctrl+O**, Enter, then exit with **Ctrl+X**.

You’ll push code to GitHub from your Windows machine (e.g. in PowerShell or Cursor). The VPS then pulls that code when you run the deploy script (Step 12).

---

## What you need before starting

- A **VPS** with Ubuntu 22.04 (e.g. DigitalOcean, Linode, Vultr, Hetzner). 1 GB RAM is enough to start.
- **SSH access** (you can log in with `ssh root@YOUR_SERVER_IP` or `ssh ubuntu@YOUR_SERVER_IP`).
- A **domain name** pointing to the server’s IP (optional but recommended for HTTPS). If you don’t have one, you can use the server IP for HTTP only at first.
- Your **GitHub repo** with this project (so the server can `git clone` it).

---

## Step 1: Log in to the server

On your computer, open a terminal and run (use your actual IP or hostname):

```bash
ssh root@YOUR_SERVER_IP
```

If you use a user like `ubuntu` instead of `root`:

```bash
ssh ubuntu@YOUR_SERVER_IP
```

You should see a shell prompt on the server.

---

## Step 2: Update the system and install basics

Copy and paste this whole block:

```bash
sudo apt update && sudo apt upgrade -y
sudo apt install -y git python3 python3-pip python3-venv nginx postgresql postgresql-contrib
```

- **git** – to clone your repo  
- **python3 / pip / venv** – to run the app  
- **nginx** – to serve the site and handle HTTPS  
- **postgresql** – database  

Optional (for Redis cache and background email):

```bash
sudo apt install -y redis-server
sudo systemctl enable redis-server
sudo systemctl start redis-server
```

---

## Step 3: Create the app directory and clone the repo

We’ll put the app in `/opt/training-tracker`. Run:

```bash
sudo mkdir -p /opt/training-tracker
sudo chown $USER:$USER /opt/training-tracker
cd /opt/training-tracker
git clone https://github.com/YOUR_GITHUB_USERNAME/YOUR_REPO_NAME.git .
```

Replace `YOUR_GITHUB_USERNAME` and `YOUR_REPO_NAME` with your real repo (e.g. `adamadkins/Training-Tracker`). So it might be:

```bash
git clone https://github.com/adamadkins/Training-Tracker.git .
```

The `.` at the end clones into the current folder so the app files are directly in `/opt/training-tracker`.

---

## Step 4: Create a Python virtualenv and install dependencies

Still in `/opt/training-tracker`:

```bash
cd /opt/training-tracker
python3 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -r requirements.txt
.venv/bin/pip install gunicorn psycopg2-binary
```

---

## Step 5: Create the Postgres database and user

Switch to the Postgres user and open the Postgres shell:

```bash
sudo -u postgres psql
```

In the `psql` prompt, run the following (change `YOUR_DB_PASSWORD` to a strong password and remember it):

```sql
CREATE USER training_tracker WITH PASSWORD 'YOUR_DB_PASSWORD';
CREATE DATABASE training_tracker OWNER training_tracker;
\q
```

You’re back in the normal shell. Test that the app can connect (replace `YOUR_DB_PASSWORD` with the same password):

```bash
cd /opt/training-tracker
DATABASE_URL=postgresql://training_tracker:YOUR_DB_PASSWORD@localhost/training_tracker .venv/bin/python -c "
from app import create_app, db
app = create_app()
with app.app_context():
    db.create_all()
    print('DB OK')
"
```

If you see `DB OK`, the database is set up correctly.

---

## Step 6: Create the `.env` file

Create the env file that the app will use:

```bash
cd /opt/training-tracker
cp deploy/.env.example .env
nano .env
```

Edit the lines:

- **SECRET_KEY** – replace with a long random string (e.g. run `openssl rand -hex 32` and paste the result).
- **DATABASE_URL** – set to `postgresql://training_tracker:YOUR_DB_PASSWORD@localhost/training_tracker` (same password as in Step 5).

Save and exit (`Ctrl+O`, Enter, then `Ctrl+X`).

If you installed Redis and want to use it, uncomment and set:

```bash
REDIS_URL=redis://localhost:6379/0
```

---

## Step 7: Run migrations and seed (first-time setup)

```bash
cd /opt/training-tracker
export $(grep -v '^#' .env | xargs)
.venv/bin/flask db upgrade
.venv/bin/python init_db.py
```

You should see migration output and then “Init complete.” (and seed messages if the DB was empty).

---

## Step 8: Install the systemd service (so the app runs in the background)

The service file is in your repo; install it and start the app:

```bash
sudo cp /opt/training-tracker/deploy/training-tracker.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable training-tracker
sudo systemctl start training-tracker
sudo systemctl status training-tracker
```

You should see “active (running)”. The app is now listening on `127.0.0.1:8080` (only on the server; Nginx will sit in front of it).

If the service fails, check logs:

```bash
sudo journalctl -u training-tracker -n 50 --no-pager
```

Fix any errors (e.g. wrong path, missing .env, wrong DATABASE_URL) and run `sudo systemctl start training-tracker` again.

---

## Step 9: Give the web server access to the app (and .env)

Nginx runs as `www-data`. The service runs the app as `www-data` too so Nginx doesn’t need to read your repo; the service file already uses `User=www-data`. Ensure ownership is correct:

```bash
sudo chown -R www-data:www-data /opt/training-tracker
```

Ensure `.env` is readable only by the app:

```bash
sudo chmod 600 /opt/training-tracker/.env
```

Restart the service after changing ownership:

```bash
sudo systemctl restart training-tracker
```

---

## Step 10: Configure Nginx

Copy the Nginx config to Nginx’s folder, then replace `YOUR_DOMAIN_OR_IP` with your real domain (e.g. `app.example.com`) or your server IP (e.g. `123.45.67.89`):

```bash
sudo cp /opt/training-tracker/deploy/nginx-training-tracker.conf /etc/nginx/sites-available/training-tracker
sudo sed -i 's/YOUR_DOMAIN_OR_IP/your-actual-domain-or-ip.com/g' /etc/nginx/sites-available/training-tracker
sudo ln -s /etc/nginx/sites-available/training-tracker /etc/nginx/sites-enabled/
sudo nginx -t
sudo systemctl reload nginx
```

Use your real value instead of `your-actual-domain-or-ip.com` (e.g. `app.yourdomain.com` or `123.45.67.89`).

Open a browser and go to `http://YOUR_DOMAIN_OR_IP`. You should see your app. If not, check `sudo nginx -t` and `sudo systemctl status training-tracker` and fix any errors.

---

## Step 11: Add HTTPS with Let’s Encrypt (recommended)

Only do this if you’re using a **domain name** (not just an IP). Install Certbot and get a certificate:

```bash
sudo apt install -y certbot python3-certbot-nginx
sudo certbot --nginx -d your-actual-domain.com
```

Follow the prompts (email, agree to terms). Certbot will adjust Nginx for HTTPS. After that, use `https://your-actual-domain.com` to access the app.

---

## Step 12: Deploy updates later (after you change code and push to GitHub)

On your **local machine**, push changes to GitHub as usual. Then on the **server**, run:

```bash
cd /opt/training-tracker
sudo chmod +x deploy/deploy.sh
sudo ./deploy/deploy.sh
```

`deploy.sh` does: `git pull`, install deps, `flask db upgrade`, `init_db.py`, restarts the service, and fixes ownership for the app. You must run it with `sudo` so it can restart the service.

---

## Quick reference

| Task | Command |
|------|--------|
| Restart app | `sudo systemctl restart training-tracker` |
| App status | `sudo systemctl status training-tracker` |
| App logs | `sudo journalctl -u training-tracker -f` |
| Reload Nginx | `sudo systemctl reload nginx` |
| Test Nginx config | `sudo nginx -t` |

---

## Email notifications

- **Seed accounts** (admin@local, trainer@local, trainee@local, *@demo.local) use **fake addresses** and will never receive real email. To test email, create a user in the app with your real email, or edit an existing user’s email in Settings/Manage team to your real address.
- **Gmail**: If the app uses Gmail (default), the sending account must use an [App Password](https://support.google.com/accounts/answer/185833), not the normal password. You can override the sender in `.env` on the server: add `MAIL_USERNAME=your@gmail.com` and `MAIL_PASSWORD=your-app-password`.
- **Check for SMTP errors**: If email still doesn’t arrive, look at app logs for failures:  
  `journalctl -u training-tracker -n 100 --no-pager | grep -i smtp`  
  You should see either “Email sent to …” or “SMTP failure …” with the reason (e.g. authentication failed, connection refused).

---

## Troubleshooting

- **502 Bad Gateway** – App not running or not on 8080. Run `sudo systemctl status training-tracker` and `sudo journalctl -u training-tracker -n 50`.
- **Can’t connect to database** – Check `DATABASE_URL` in `.env` and that Postgres is running: `sudo systemctl status postgresql`.
- **Permission denied on /opt/training-tracker** – Run `sudo chown -R www-data:www-data /opt/training-tracker` (and ensure the service runs as `www-data`).

If you get stuck, share the exact error message and the last lines of `sudo journalctl -u training-tracker -n 50`.
