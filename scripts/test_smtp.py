"""
Run on the VPS to test SMTP. Loads .env from repo root and tries one send.
Usage: cd /opt/training-tracker && .venv/bin/python scripts/test_smtp.py your@real-email.com
"""
import os
import sys

# Load .env if present
env_path = os.path.join(os.path.dirname(__file__), '..', '.env')
if os.path.isfile(env_path):
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                k, v = line.split('=', 1)
                os.environ.setdefault(k.strip(), v.strip())

# Config same as app
MAIL_SERVER = os.environ.get('MAIL_SERVER') or 'smtp.gmail.com'
MAIL_PORT = int(os.environ.get('MAIL_PORT', 465))
MAIL_USE_SSL = os.environ.get('MAIL_USE_SSL', 'True').lower() in ('true', '1', 'yes')
MAIL_USE_TLS = os.environ.get('MAIL_USE_TLS', 'False').lower() in ('true', '1', 'yes')
MAIL_USERNAME = os.environ.get('MAIL_USERNAME') or 'donotreply.trainingtracker@gmail.com'
MAIL_PASSWORD = os.environ.get('MAIL_PASSWORD') or ''

def main():
    to_email = (sys.argv[1:] or [''])[0]
    if not to_email or '@' not in to_email:
        print("Usage: python scripts/test_smtp.py your@real-email.com")
        sys.exit(1)
    if not MAIL_PASSWORD:
        print("MAIL_PASSWORD is empty. Set it in .env or environment.")
        sys.exit(1)
    import smtplib
    from email.mime.text import MIMEText
    msg = MIMEText("Test from Training Tracker VPS.")
    msg['Subject'] = "Training Tracker SMTP test"
    msg['From'] = MAIL_USERNAME
    msg['To'] = to_email
    print(f"Sending test email to {to_email} via {MAIL_SERVER}:{MAIL_PORT} (SSL={MAIL_USE_SSL})...")
    try:
        if MAIL_USE_SSL:
            with smtplib.SMTP_SSL(MAIL_SERVER, MAIL_PORT, timeout=15) as server:
                server.login(MAIL_USERNAME, MAIL_PASSWORD)
                server.send_message(msg)
        else:
            with smtplib.SMTP(MAIL_SERVER, MAIL_PORT, timeout=15) as server:
                if MAIL_USE_TLS:
                    server.starttls()
                server.login(MAIL_USERNAME, MAIL_PASSWORD)
                server.send_message(msg)
        print("OK — check inbox (and spam) for", to_email)
    except Exception as e:
        print("FAIL:", e)
        sys.exit(1)

if __name__ == "__main__":
    main()
