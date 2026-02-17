import os

# Get the base directory of this folder
basedir = os.path.abspath(os.path.dirname(__file__))


class Config:
    # --- General Security ---
    SECRET_KEY = os.environ.get('SECRET_KEY') or 'you-will-never-guess'

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
    MAIL_SERVER = os.environ.get('MAIL_SERVER', 'smtp.gmail.com')
    MAIL_PORT = int(os.environ.get('MAIL_PORT', 587))
    MAIL_USE_TLS = os.environ.get('MAIL_USE_TLS', 'True').lower() in ('true', '1', 'yes')
    MAIL_USE_SSL = os.environ.get('MAIL_USE_SSL', 'False').lower() in ('true', '1', 'yes')
    MAIL_USERNAME = os.environ.get('MAIL_USERNAME', '')
    MAIL_PASSWORD = os.environ.get('MAIL_PASSWORD', '')
    MAIL_DEFAULT_SENDER = os.environ.get('MAIL_DEFAULT_SENDER', '')
    MAIL_TIMEOUT = 15  # seconds; avoid hanging the request if SMTP is slow

    # Redis (queue + cache). If not set, app falls back to thread for email and simple memory cache.
    REDIS_URL = os.environ.get('REDIS_URL') or os.environ.get('REDIS_PRIVATE_URL')
    CACHE_TYPE = 'RedisCache' if (os.environ.get('REDIS_URL') or os.environ.get('REDIS_PRIVATE_URL')) else 'SimpleCache'
    CACHE_REDIS_URL = os.environ.get('REDIS_URL') or os.environ.get('REDIS_PRIVATE_URL') or ''
    CACHE_DEFAULT_TIMEOUT = 90  # seconds for view caches
    # Only pass Redis socket options when actually using RedisCache (SimpleCache rejects them)
    if CACHE_TYPE == 'RedisCache':
        CACHE_OPTIONS = {'socket_connect_timeout': 3, 'socket_timeout': 3}