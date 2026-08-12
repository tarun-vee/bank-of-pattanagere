"""
Authentication routes — Login, Register, Logout, Forgot Password, Change Password.
"""

import re
import uuid
from datetime import datetime, timedelta
from flask import Blueprint, render_template, redirect, url_for, flash, request
from flask_login import login_user, logout_user, login_required, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from models import db, User, Account, Log, Notification
from config import Config

auth_bp = Blueprint('auth', __name__, url_prefix='/auth')


def validate_password_strength(password):
    """Check password meets strength requirements."""
    if len(password) < 8:
        return False, 'Password must be at least 8 characters long.'
    if not re.search(r'[A-Z]', password):
        return False, 'Password must contain at least one uppercase letter.'
    if not re.search(r'[a-z]', password):
        return False, 'Password must contain at least one lowercase letter.'
    if not re.search(r'[0-9]', password):
        return False, 'Password must contain at least one digit.'
    return True, ''


def get_next_account_number():
    """Generate the next sequential account number."""
    last_account = Account.query.order_by(Account.id.desc()).first()
    if last_account:
        try:
            return str(int(last_account.account_number) + 1)
        except ValueError:
            pass
    return '100001'


@auth_bp.route('/login', methods=['GET', 'POST'])
def login():
    """Handle user login."""
    if current_user.is_authenticated:
        if current_user.role == 'admin':
            return redirect(url_for('admin.dashboard'))
        return redirect(url_for('customer.dashboard'))

    if request.method == 'POST':
        email = request.form.get('email', '').strip()
        password = request.form.get('password', '')
        remember = request.form.get('remember', False)

        user = User.query.filter_by(email=email).first()

        if not user:
            flash('Invalid email or password.', 'error')
            return render_template('auth/login.html')

        # Check if account is locked
        if user.locked_until and user.locked_until > datetime.utcnow():
            remaining = (user.locked_until - datetime.utcnow()).seconds // 60
            flash(f'Account locked. Try again in {remaining + 1} minutes.', 'error')
            return render_template('auth/login.html')

        if not check_password_hash(user.password_hash, password):
            user.failed_login_attempts += 1
            if user.failed_login_attempts >= Config.MAX_LOGIN_ATTEMPTS:
                user.locked_until = datetime.utcnow() + timedelta(minutes=Config.LOCKOUT_DURATION)
                db.session.commit()
                flash('Account locked due to too many failed attempts. Try again in 30 minutes.', 'error')

                # Notification
                notif = Notification(
                    user_id=user.id,
                    title='Account Locked',
                    message='Your account has been locked due to multiple failed login attempts.',
                    type='security',
                )
                db.session.add(notif)
                db.session.commit()
            else:
                db.session.commit()
                remaining = Config.MAX_LOGIN_ATTEMPTS - user.failed_login_attempts
                flash(f'Invalid password. {remaining} attempts remaining.', 'error')
            return render_template('auth/login.html')

        # Successful login
        user.failed_login_attempts = 0
        user.locked_until = None
        db.session.commit()

        login_user(user, remember=bool(remember))

        if user.role == 'customer' and user.has_default_pin:
            flash('Your default Transaction PIN is currently set to 1234. For security reasons, please change your Transaction PIN.', 'warning')

        # Log the login
        log = Log(
            user_id=user.id,
            action='login',
            details=f'Successful login as {user.role}',
            ip_address=request.remote_addr or '127.0.0.1',
        )
        db.session.add(log)

        # Login notification
        notif = Notification(
            user_id=user.id,
            title='Login Alert',
            message=f'You logged in from IP {request.remote_addr or "127.0.0.1"} on {datetime.utcnow().strftime("%d %b %Y, %I:%M %p")}.',
            type='security',
        )
        db.session.add(notif)
        db.session.commit()

        flash(f'Welcome back, {user.name}!', 'success')

        if user.role == 'admin':
            return redirect(url_for('admin.dashboard'))
        return redirect(url_for('customer.dashboard'))

    return render_template('auth/login.html')


@auth_bp.route('/register', methods=['GET', 'POST'])
def register():
    """Handle customer registration."""
    if current_user.is_authenticated:
        return redirect(url_for('customer.dashboard'))

    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        email = request.form.get('email', '').strip()
        phone = request.form.get('phone', '').strip()
        password = request.form.get('password', '')
        confirm_password = request.form.get('confirm_password', '')
        transaction_pin = request.form.get('transaction_pin', '').strip()
        confirm_transaction_pin = request.form.get('confirm_transaction_pin', '').strip()
        account_type = request.form.get('account_type', 'savings')
        address = request.form.get('address', '').strip()
        security_question = request.form.get('security_question', '').strip()
        security_answer = request.form.get('security_answer', '').strip()

        # Validation
        if not all([name, email, phone, password, confirm_password, transaction_pin, confirm_transaction_pin]):
            flash('All required fields must be filled.', 'error')
            return render_template('auth/register.html')

        if password != confirm_password:
            flash('Passwords do not match.', 'error')
            return render_template('auth/register.html')

        if transaction_pin != confirm_transaction_pin:
            flash('Transaction PINs do not match.', 'error')
            return render_template('auth/register.html')

        if not re.match(r'^\d{4}$', transaction_pin):
            flash('Transaction PIN must be exactly 4 digits.', 'error')
            return render_template('auth/register.html')

        is_valid, msg = validate_password_strength(password)
        if not is_valid:
            flash(msg, 'error')
            return render_template('auth/register.html')

        if User.query.filter_by(email=email).first():
            flash('Email already registered.', 'error')
            return render_template('auth/register.html')

        # Create user
        user = User(
            name=name,
            email=email,
            phone=phone,
            password_hash=generate_password_hash(password),
            role='customer',
            address=address,
            security_question=security_question,
            security_answer_hash=generate_password_hash(security_answer.lower()) if security_answer else '',
            transaction_pin_hash=generate_password_hash(transaction_pin),
            has_default_pin=False,
        )
        db.session.add(user)
        db.session.flush()

        # Create account with PENDING status using a temporary unique ID
        account = Account(
            user_id=user.id,
            account_number=str(uuid.uuid4())[:20],
            account_type=account_type,
            balance=0.0,
            status='pending',
        )
        db.session.add(account)
        db.session.flush()
        
        # Set structured account number
        account.account_number = f"101560001{account.id:06d}"

        # Notification to user
        notif = Notification(
            user_id=user.id,
            title='Registration Successful',
            message='Your account has been created and is pending admin approval. You will be notified once approved.',
            type='approval',
        )
        db.session.add(notif)

        # Log
        log = Log(
            user_id=user.id,
            action='system',
            details=f'New customer registration: {email}',
            ip_address=request.remote_addr or '127.0.0.1',
        )
        db.session.add(log)

        db.session.commit()

        flash('Registration successful! Your account is pending admin approval.', 'success')
        return redirect(url_for('auth.login'))

    return render_template('auth/register.html')


@auth_bp.route('/logout')
@login_required
def logout():
    """Handle user logout."""
    log = Log(
        user_id=current_user.id,
        action='logout',
        details=f'{current_user.name} logged out',
        ip_address=request.remote_addr or '127.0.0.1',
    )
    db.session.add(log)
    db.session.commit()

    logout_user()
    flash('You have been logged out.', 'info')
    return redirect(url_for('auth.login'))


@auth_bp.route('/forgot-password', methods=['GET', 'POST'])
def forgot_password():
    """Handle forgot password via security question."""
    if request.method == 'POST':
        email = request.form.get('email', '').strip()
        security_answer = request.form.get('security_answer', '').strip()
        new_password = request.form.get('new_password', '')
        confirm_password = request.form.get('confirm_password', '')

        user = User.query.filter_by(email=email).first()
        if not user:
            flash('Email not found.', 'error')
            return render_template('auth/forgot_password.html')

        if not user.security_answer_hash or not check_password_hash(user.security_answer_hash, security_answer.lower()):
            flash('Incorrect security answer.', 'error')
            return render_template('auth/forgot_password.html', email=email, security_question=user.security_question)

        if new_password != confirm_password:
            flash('Passwords do not match.', 'error')
            return render_template('auth/forgot_password.html', email=email, security_question=user.security_question)

        is_valid, msg = validate_password_strength(new_password)
        if not is_valid:
            flash(msg, 'error')
            return render_template('auth/forgot_password.html', email=email, security_question=user.security_question)

        user.password_hash = generate_password_hash(new_password)
        user.failed_login_attempts = 0
        user.locked_until = None
        db.session.commit()

        flash('Password reset successful. Please login with your new password.', 'success')
        return redirect(url_for('auth.login'))

    return render_template('auth/forgot_password.html')


@auth_bp.route('/change-password', methods=['GET', 'POST'])
@login_required
def change_password():
    """Handle password change for authenticated users."""
    if request.method == 'POST':
        current_password = request.form.get('current_password', '')
        new_password = request.form.get('new_password', '')
        confirm_password = request.form.get('confirm_password', '')

        if not check_password_hash(current_user.password_hash, current_password):
            flash('Current password is incorrect.', 'error')
            return render_template('auth/change_password.html')

        if new_password != confirm_password:
            flash('New passwords do not match.', 'error')
            return render_template('auth/change_password.html')

        is_valid, msg = validate_password_strength(new_password)
        if not is_valid:
            flash(msg, 'error')
            return render_template('auth/change_password.html')

        current_user.password_hash = generate_password_hash(new_password)
        db.session.commit()

        # Notification
        notif = Notification(
            user_id=current_user.id,
            title='Password Changed',
            message='Your password has been changed successfully.',
            type='security',
        )
        db.session.add(notif)

        # Log
        log = Log(
            user_id=current_user.id,
            action='system',
            details='Password changed',
            ip_address=request.remote_addr or '127.0.0.1',
        )
        db.session.add(log)
        db.session.commit()

        flash('Password changed successfully.', 'success')

        if current_user.role == 'admin':
            return redirect(url_for('admin.settings'))
        return redirect(url_for('customer.profile'))

    return render_template('auth/change_password.html')
