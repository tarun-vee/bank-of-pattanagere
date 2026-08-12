from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from datetime import datetime

db = SQLAlchemy()


class User(UserMixin, db.Model):
    """User model for customers and admins."""
    __tablename__ = 'users'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    phone = db.Column(db.String(15), nullable=False)
    password_hash = db.Column(db.String(256), nullable=False)
    role = db.Column(db.String(20), nullable=False, default='customer')  # 'customer' or 'admin'
    address = db.Column(db.Text, default='')
    security_question = db.Column(db.String(200), default='')
    security_answer_hash = db.Column(db.String(256), default='')
    failed_login_attempts = db.Column(db.Integer, default=0)
    locked_until = db.Column(db.DateTime, nullable=True)
    transaction_pin_hash = db.Column(db.String(256), nullable=True)
    failed_pin_attempts = db.Column(db.Integer, default=0)
    pin_locked_until = db.Column(db.DateTime, nullable=True)
    has_default_pin = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    # Relationships
    account = db.relationship('Account', backref='user', uselist=False, lazy=True)
    beneficiaries = db.relationship('Beneficiary', backref='user', lazy=True)
    notifications = db.relationship('Notification', backref='user', lazy=True,
                                    order_by='Notification.created_at.desc()')
    logs = db.relationship('Log', backref='user', lazy=True)

    def __repr__(self):
        return f'<User {self.name} ({self.email})>'


class Account(db.Model):
    """Bank account model."""
    __tablename__ = 'accounts'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    account_number = db.Column(db.String(20), unique=True, nullable=False)
    account_type = db.Column(db.String(20), nullable=False, default='savings')  # 'savings' or 'current'
    balance = db.Column(db.Float, default=0.0)
    status = db.Column(db.String(20), default='pending')  # 'pending', 'active', 'frozen', 'closed'
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def __repr__(self):
        return f'<Account {self.account_number} ({self.status})>'


class Transaction(db.Model):
    """Transaction record model."""
    __tablename__ = 'transactions'

    id = db.Column(db.Integer, primary_key=True)
    transaction_id = db.Column(db.String(20), unique=True, nullable=False)
    sender_account = db.Column(db.String(20), nullable=True)
    receiver_account = db.Column(db.String(20), nullable=True)
    transaction_type = db.Column(db.String(20), nullable=False)  # 'credit', 'debit', 'transfer'
    amount = db.Column(db.Float, nullable=False)
    description = db.Column(db.String(200), default='')
    status = db.Column(db.String(20), default='success')  # 'success', 'failed', 'pending'
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def __repr__(self):
        return f'<Transaction {self.transaction_id} ({self.status})>'


class Log(db.Model):
    """Audit log model."""
    __tablename__ = 'logs'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    action = db.Column(db.String(50), nullable=False)  # 'login', 'logout', 'transfer', 'admin_action', 'system'
    details = db.Column(db.Text, default='')
    ip_address = db.Column(db.String(45), default='')
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)

    def __repr__(self):
        return f'<Log {self.action} at {self.timestamp}>'


class Beneficiary(db.Model):
    """Saved beneficiary model."""
    __tablename__ = 'beneficiaries'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    beneficiary_account = db.Column(db.String(20), nullable=False)
    beneficiary_name = db.Column(db.String(100), nullable=False)
    bank_name = db.Column(db.String(100), default='Bank of Pattanagere')
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def __repr__(self):
        return f'<Beneficiary {self.beneficiary_name} ({self.beneficiary_account})>'


class Notification(db.Model):
    """User notification model."""
    __tablename__ = 'notifications'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    title = db.Column(db.String(100), nullable=False)
    message = db.Column(db.Text, nullable=False)
    type = db.Column(db.String(20), default='info')  # 'approval', 'transfer', 'security', 'announcement'
    is_read = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def __repr__(self):
        return f'<Notification {self.title} ({"read" if self.is_read else "unread"})>'
