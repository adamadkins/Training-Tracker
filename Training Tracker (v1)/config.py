import os

# Get the base directory of this folder
basedir = os.path.abspath(os.path.dirname(__file__))


class Config:
    # --- General Security ---
    SECRET_KEY = os.environ.get('SECRET_KEY') or 'you-will-never-guess'

    # --- Database Configuration ---
    # FIXED: Pointing to 'instance/training_tracker.db' instead of 'app.db'
    # We use os.path.join to handle the 'instance' folder correctly if needed,
    # but for simplicity in local dev, this often works best:
    SQLALCHEMY_DATABASE_URI = os.environ.get('DATABASE_URL') or \
                              'sqlite:///' + os.path.join(basedir, 'instance', 'training_tracker.db')

    SQLALCHEMY_TRACK_MODIFICATIONS = False

    # --- Email Configuration (Gmail) ---
    MAIL_SERVER = 'smtp.gmail.com'
    MAIL_PORT = 465
    MAIL_USE_TLS = False
    MAIL_USE_SSL = True
    MAIL_USERNAME = 'donotreply.trainingtracker@gmail.com'
    MAIL_PASSWORD = 'scvvkhtuwpugiums'
    MAIL_DEFAULT_SENDER = 'donotreply.trainingtracker@gmail.com'