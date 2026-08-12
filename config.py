import os

class Config:
    """Application configuration."""
    SECRET_KEY = os.environ.get('SECRET_KEY', 'bank-of-pattanagere-secret-key-2024')
    BASE_DIR = os.path.abspath(os.path.dirname(__file__))
    SQLALCHEMY_DATABASE_URI = 'sqlite:///' + os.path.join(BASE_DIR, 'database', 'bank.db')
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    # Session settings
    PERMANENT_SESSION_LIFETIME = 1800  # 30 minutes
    SESSION_PROTECTION = 'strong'

    # TCP Server settings
    TCP_HOST = '0.0.0.0'
    TCP_PORT = 9999

    # Security settings
    MAX_LOGIN_ATTEMPTS = 5
    LOCKOUT_DURATION = 30  # minutes

    # WTF CSRF
    WTF_CSRF_ENABLED = True
