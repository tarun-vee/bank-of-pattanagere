"""
Bank of Pattanagere — Digital Banking Network
Main application entry point.
"""

import os
import threading
from flask import Flask, redirect, url_for
from flask_login import LoginManager
from flask_wtf.csrf import CSRFProtect
from config import Config
from models import db, User


login_manager = LoginManager()
csrf = CSRFProtect()


def create_app():
    """Application factory."""
    app = Flask(__name__)
    app.config.from_object(Config)

    # Ensure database directory exists
    os.makedirs(os.path.join(Config.BASE_DIR, 'database'), exist_ok=True)

    # Initialise extensions
    db.init_app(app)
    login_manager.init_app(app)
    csrf.init_app(app)

    # Login manager settings
    login_manager.login_view = 'auth.login'
    login_manager.login_message = 'Please log in to access this page.'
    login_manager.login_message_category = 'info'
    login_manager.session_protection = 'strong'

    @login_manager.user_loader
    def load_user(user_id):
        return User.query.get(int(user_id))

    # Register Blueprints
    from routes.auth import auth_bp
    from routes.customer import customer_bp
    from routes.admin import admin_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(customer_bp)
    app.register_blueprint(admin_bp)

    # Root redirect
    @app.route('/')
    def index():
        return redirect(url_for('auth.login'))

    # Context processor — inject unread notification count for templates
    @app.context_processor
    def inject_notifications():
        from flask_login import current_user
        from models import Notification
        unread_count = 0
        if current_user.is_authenticated:
            unread_count = Notification.query.filter_by(
                user_id=current_user.id, is_read=False
            ).count()
        return dict(unread_notification_count=unread_count)

    # Create tables & seed only if database doesn't exist or SEED_DB is set
    db_path = os.path.join(Config.BASE_DIR, 'database', 'bank.db')
    needs_seeding = not os.path.exists(db_path) or os.environ.get('SEED_DB') == '1'

    with app.app_context():
        db.create_all()
        if needs_seeding:
            from seed import seed_database
            seed_database()

    return app


def start_tcp_in_background():
    """Start the TCP server in a background daemon thread."""
    from tcp_server import start_tcp_server
    tcp_thread = threading.Thread(target=start_tcp_server, daemon=True)
    tcp_thread.start()
    print("[App] TCP server started in background thread.")


if __name__ == '__main__':
    app = create_app()

    # Start TCP server in background
    start_tcp_in_background()

    print("[App] Starting Bank of Pattanagere Flask server...")
    print("[App] Access the application at: http://127.0.0.1:5000 (Local) or http://<Your-IP-Address>:5000 (Network)")
    app.run(host='0.0.0.0', debug=True, use_reloader=False, port=5000)
