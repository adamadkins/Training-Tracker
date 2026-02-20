import os
from datetime import timedelta

# Get the base directory of this folder
basedir = os.path.abspath(os.path.dirname(__file__))


class Config:
    # --- General Security ---
    SECRET_KEY = os.environ.get('SECRET_KEY') or 'you-will-never-guess'

    # --- URL generation ---
    # Set SERVER_NAME in .env (e.g. trainingtracker.me) so url_for(_external=True) in emails
    # and background tasks produces the correct domain instead of the raw IP.
    # ProxyFix in __init__.py handles scheme (http→https) for live requests automatically.
    SERVER_NAME = os.environ.get('SERVER_NAME') or None
    PREFERRED_URL_SCHEME = os.environ.get('PREFERRED_URL_SCHEME', 'https')
    # Main domain for multi-tenant: subdomain is stripped from request.host to resolve Organization (e.g. trainingtracker.me, www.trainingtracker.me).
    MAIN_DOMAIN = os.environ.get('MAIN_DOMAIN') or (SERVER_NAME if SERVER_NAME and '.' in SERVER_NAME else 'trainingtracker.me')

    # --- Session ---
    # Sessions are permanent so Flask respects PERMANENT_SESSION_LIFETIME.
    # Before-request logic in __init__.py enforces idle timeout independently.
    PERMANENT_SESSION_LIFETIME = timedelta(hours=int(os.environ.get('SESSION_LIFETIME_HOURS', 12)))
    SESSION_IDLE_TIMEOUT = int(os.environ.get('SESSION_IDLE_TIMEOUT', 7200))  # seconds (default 2 h)

    # --- Database Configuration ---
    # Use DATABASE_URL as-is so production (Railway) uses the internal URL (fast, private).
    # For "railway run ..." from your machine, set DATABASE_PUBLIC_URL in env and use it locally if needed.
    _db_url = os.environ.get('DATABASE_URL')
    if not _db_url and os.environ.get('DATABASE_PUBLIC_URL'):
        _db_url = os.environ.get('DATABASE_PUBLIC_URL')
    SQLALCHEMY_DATABASE_URI = _db_url or \
                              'sqlite:///' + os.path.join(basedir, 'instance', 'training_tracker.db')

    SQLALCHEMY_TRACK_MODIFICATIONS = False

    # --- Email Configuration ---
    # Override in .env on the server (e.g. MAIL_USERNAME, MAIL_PASSWORD, MAIL_SERVER, MAIL_PORT).
    MAIL_SERVER = os.environ.get('MAIL_SERVER') or 'smtp.gmail.com'
    MAIL_PORT = int(os.environ.get('MAIL_PORT', 465))
    MAIL_USE_TLS = os.environ.get('MAIL_USE_TLS', 'False').lower() in ('true', '1', 'yes')
    MAIL_USE_SSL = os.environ.get('MAIL_USE_SSL', 'True').lower() in ('true', '1', 'yes')
    MAIL_USERNAME = os.environ.get('MAIL_USERNAME') or 'donotreply.trainingtracker@gmail.com'
    MAIL_PASSWORD = os.environ.get('MAIL_PASSWORD') or 'scvvkhtuwpugiums'
    MAIL_DEFAULT_SENDER = os.environ.get('MAIL_DEFAULT_SENDER') or MAIL_USERNAME
    MAIL_TIMEOUT = 15  # seconds; avoid hanging the request if SMTP is slow

    # SendGrid (email via HTTPS — no SMTP port needed). Set in .env on the server.
    SENDGRID_API_KEY = os.environ.get('SENDGRID_API_KEY', '')

    # Redis (queue + cache). If not set, app falls back to thread for email and simple memory cache.
    REDIS_URL = os.environ.get('REDIS_URL') or os.environ.get('REDIS_PRIVATE_URL')
    CACHE_TYPE = 'RedisCache' if (os.environ.get('REDIS_URL') or os.environ.get('REDIS_PRIVATE_URL')) else 'SimpleCache'
    CACHE_REDIS_URL = os.environ.get('REDIS_URL') or os.environ.get('REDIS_PRIVATE_URL') or ''
    CACHE_DEFAULT_TIMEOUT = 90  # seconds for view caches
    # Only pass Redis socket options when actually using RedisCache (SimpleCache rejects them)
    if CACHE_TYPE == 'RedisCache':
        CACHE_OPTIONS = {'socket_connect_timeout': 3, 'socket_timeout': 3}