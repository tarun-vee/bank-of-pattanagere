"""
Admin/Banker portal routes — Dashboard, Customer Mgmt, Account Mgmt, Transactions, Reports, Audit Logs, TCP Monitor, Settings.
"""

import csv
import io
from datetime import datetime, timedelta
from functools import wraps
from flask import Blueprint, render_template, redirect, url_for, flash, request, Response
from flask_login import login_required, current_user
from models import db, User, Account, Transaction, Log, Notification
from tcp_client import tcp_request

admin_bp = Blueprint('admin', __name__, url_prefix='/admin')


def admin_required(f):
    """Decorator to ensure user is an admin."""
    @wraps(f)
    @login_required
    def decorated_function(*args, **kwargs):
        if current_user.role != 'admin':
            flash('Access denied. Admin accounts only.', 'error')
            return redirect(url_for('auth.login'))
        return f(*args, **kwargs)
    return decorated_function


# ---------- Dashboard ----------

@admin_bp.route('/dashboard')
@admin_required
def dashboard():
    """Admin dashboard with stats and recent activity."""
    total_customers = User.query.filter_by(role='customer').count()
    total_accounts = Account.query.count()
    total_transactions = Transaction.query.count()
    total_balance = db.session.query(db.func.sum(Account.balance)).scalar() or 0.0

    # Today's transfers
    today_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    todays_transfers = Transaction.query.filter(
        Transaction.created_at >= today_start,
        Transaction.transaction_type.in_(['debit', 'transfer'])
    ).count()

    # TCP server stats
    tcp_stats = tcp_request('server_stats', {})
    active_tcp_clients = tcp_stats.get('active_connections', 0) if tcp_stats.get('status') == 'success' else 0

    # Recent transactions
    recent_transactions = Transaction.query.order_by(Transaction.created_at.desc()).limit(5).all()

    # Enrich transactions with account info
    for txn in recent_transactions:
        txn.sender_info = Account.query.filter_by(account_number=txn.sender_account).first()
        txn.receiver_info = Account.query.filter_by(account_number=txn.receiver_account).first()

    # Recent registrations
    recent_registrations = User.query.filter_by(role='customer').order_by(
        User.created_at.desc()).limit(5).all()

    # Pending approvals count
    pending_count = Account.query.filter_by(status='pending').count()

    return render_template('admin/dashboard.html',
                           total_customers=total_customers,
                           total_accounts=total_accounts,
                           total_transactions=total_transactions,
                           total_balance=total_balance,
                           todays_transfers=todays_transfers,
                           active_tcp_clients=active_tcp_clients,
                           recent_transactions=recent_transactions,
                           recent_registrations=recent_registrations,
                           pending_count=pending_count)


# ---------- Customer Management ----------

@admin_bp.route('/customers')
@admin_required
def customers():
    """View and manage customers."""
    search = request.args.get('search', '').strip()
    status_filter = request.args.get('status', '').strip()
    page = request.args.get('page', 1, type=int)

    query = User.query.filter_by(role='customer')

    if search:
        query = query.filter(
            (User.name.ilike(f'%{search}%')) |
            (User.email.ilike(f'%{search}%')) |
            (User.phone.ilike(f'%{search}%'))
        )

    customers_list = query.order_by(User.created_at.desc()).all()

    # Filter by account status
    if status_filter:
        customers_list = [c for c in customers_list if c.account and c.account.status == status_filter]

    return render_template('admin/customers.html',
                           customers=customers_list, search=search, status_filter=status_filter)


@admin_bp.route('/customers/<int:user_id>/edit', methods=['GET', 'POST'])
@admin_required
def edit_customer(user_id):
    """Edit customer details."""
    user = User.query.get_or_404(user_id)
    if user.role != 'customer':
        flash('Invalid customer.', 'error')
        return redirect(url_for('admin.customers'))

    if request.method == 'POST':
        user.name = request.form.get('name', user.name).strip()
        user.phone = request.form.get('phone', user.phone).strip()
        user.address = request.form.get('address', user.address).strip()

        if user.account:
            user.account.account_type = request.form.get('account_type', user.account.account_type)

        db.session.commit()

        log = Log(user_id=current_user.id, action='admin_action',
                  details=f'Edited customer: {user.name} ({user.email})',
                  ip_address=request.remote_addr or '127.0.0.1')
        db.session.add(log)
        db.session.commit()

        flash(f'Customer {user.name} updated.', 'success')
        return redirect(url_for('admin.customers'))

    return render_template('admin/edit_customer.html', customer=user)


@admin_bp.route('/customers/<int:user_id>/approve', methods=['POST'])
@admin_required
def approve_customer(user_id):
    """Approve a pending customer account."""
    user = User.query.get_or_404(user_id)
    if user.account and user.account.status == 'pending':
        user.account.status = 'active'

        # Notify customer
        notif = Notification(
            user_id=user.id,
            title='Account Approved!',
            message='Your bank account has been approved by the admin. You can now access all banking services including fund transfers.',
            type='approval',
        )
        db.session.add(notif)

        log = Log(user_id=current_user.id, action='admin_action',
                  details=f'Approved account for {user.name} ({user.account.account_number})',
                  ip_address=request.remote_addr or '127.0.0.1')
        db.session.add(log)
        db.session.commit()

        flash(f'Account for {user.name} has been approved.', 'success')
    else:
        flash('Account is not pending approval.', 'info')

    return redirect(url_for('admin.customers'))


@admin_bp.route('/customers/<int:user_id>/activate', methods=['POST'])
@admin_required
def activate_customer(user_id):
    """Activate a customer account."""
    user = User.query.get_or_404(user_id)
    if user.account:
        user.account.status = 'active'
        notif = Notification(user_id=user.id, title='Account Activated',
                             message='Your account has been activated.', type='approval')
        db.session.add(notif)
        log = Log(user_id=current_user.id, action='admin_action',
                  details=f'Activated account {user.account.account_number}',
                  ip_address=request.remote_addr or '127.0.0.1')
        db.session.add(log)
        db.session.commit()
        flash(f'Account for {user.name} activated.', 'success')
    return redirect(url_for('admin.customers'))


@admin_bp.route('/customers/<int:user_id>/deactivate', methods=['POST'])
@admin_required
def deactivate_customer(user_id):
    """Deactivate/freeze a customer account."""
    user = User.query.get_or_404(user_id)
    if user.account:
        user.account.status = 'frozen'
        notif = Notification(user_id=user.id, title='Account Frozen',
                             message='Your account has been frozen by the admin. You can still receive funds but cannot initiate transfers.',
                             type='security')
        db.session.add(notif)
        log = Log(user_id=current_user.id, action='admin_action',
                  details=f'Froze account {user.account.account_number}',
                  ip_address=request.remote_addr or '127.0.0.1')
        db.session.add(log)
        db.session.commit()
        flash(f'Account for {user.name} has been frozen.', 'success')
    return redirect(url_for('admin.customers'))


@admin_bp.route('/customers/<int:user_id>/delete', methods=['POST'])
@admin_required
def delete_customer(user_id):
    """Delete a customer."""
    user = User.query.get_or_404(user_id)
    if user.role != 'customer':
        flash('Cannot delete admin accounts.', 'error')
        return redirect(url_for('admin.customers'))

    name = user.name
    # Delete related records
    Notification.query.filter_by(user_id=user.id).delete()
    from models import Beneficiary
    Beneficiary.query.filter_by(user_id=user.id).delete()
    Log.query.filter_by(user_id=user.id).delete()
    if user.account:
        db.session.delete(user.account)
    db.session.delete(user)

    log = Log(user_id=current_user.id, action='admin_action',
              details=f'Deleted customer: {name}',
              ip_address=request.remote_addr or '127.0.0.1')
    db.session.add(log)
    db.session.commit()

    flash(f'Customer {name} deleted.', 'success')
    return redirect(url_for('admin.customers'))


# ---------- Account Management ----------

@admin_bp.route('/accounts')
@admin_required
def accounts():
    """View and manage accounts."""
    search = request.args.get('search', '').strip()

    query = Account.query

    if search:
        query = query.filter(
            (Account.account_number.ilike(f'%{search}%'))
        )

    all_accounts = query.order_by(Account.created_at.desc()).all()
    return render_template('admin/accounts.html', accounts=all_accounts, search=search)


@admin_bp.route('/accounts/<int:account_id>/freeze', methods=['POST'])
@admin_required
def freeze_account(account_id):
    """Freeze an account."""
    account = Account.query.get_or_404(account_id)
    account.status = 'frozen'
    notif = Notification(user_id=account.user_id, title='Account Frozen',
                         message='Your account has been frozen. You can receive funds but cannot initiate transfers.',
                         type='security')
    db.session.add(notif)
    log = Log(user_id=current_user.id, action='admin_action',
              details=f'Froze account {account.account_number}',
              ip_address=request.remote_addr or '127.0.0.1')
    db.session.add(log)
    db.session.commit()
    flash(f'Account {account.account_number} frozen.', 'success')
    return redirect(url_for('admin.accounts'))


@admin_bp.route('/accounts/<int:account_id>/unfreeze', methods=['POST'])
@admin_required
def unfreeze_account(account_id):
    """Unfreeze an account."""
    account = Account.query.get_or_404(account_id)
    account.status = 'active'
    notif = Notification(user_id=account.user_id, title='Account Unfrozen',
                         message='Your account has been unfrozen. All banking services are now available.',
                         type='approval')
    db.session.add(notif)
    log = Log(user_id=current_user.id, action='admin_action',
              details=f'Unfroze account {account.account_number}',
              ip_address=request.remote_addr or '127.0.0.1')
    db.session.add(log)
    db.session.commit()
    flash(f'Account {account.account_number} unfrozen.', 'success')
    return redirect(url_for('admin.accounts'))


# ---------- Transaction Management ----------

@admin_bp.route('/transactions')
@admin_required
def transactions():
    """View all transactions with search, filter, pagination."""
    search = request.args.get('search', '').strip()
    txn_type = request.args.get('type', '').strip()
    status = request.args.get('status', '').strip()
    page = request.args.get('page', 1, type=int)

    query = Transaction.query

    if search:
        query = query.filter(
            (Transaction.transaction_id.ilike(f'%{search}%')) |
            (Transaction.sender_account.ilike(f'%{search}%')) |
            (Transaction.receiver_account.ilike(f'%{search}%')) |
            (Transaction.description.ilike(f'%{search}%'))
        )

    if txn_type:
        query = query.filter(Transaction.transaction_type == txn_type)

    if status:
        query = query.filter(Transaction.status == status)

    pagination = query.order_by(Transaction.created_at.desc()).paginate(page=page, per_page=15, error_out=False)

    return render_template('admin/transactions.html',
                           transactions=pagination.items, pagination=pagination,
                           search=search, txn_type=txn_type, status=status)


@admin_bp.route('/transactions/export')
@admin_required
def export_transactions():
    """Export all transactions as CSV."""
    transactions = Transaction.query.order_by(Transaction.created_at.desc()).all()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['Transaction ID', 'Sender', 'Receiver', 'Type', 'Amount', 'Description', 'Status', 'Date'])
    for txn in transactions:
        writer.writerow([
            txn.transaction_id, txn.sender_account, txn.receiver_account,
            txn.transaction_type, txn.amount, txn.description,
            txn.status, txn.created_at.strftime('%d %b %Y, %I:%M %p')
        ])

    response = Response(output.getvalue(), mimetype='text/csv')
    response.headers['Content-Disposition'] = 'attachment; filename=all_transactions.csv'
    return response


# ---------- Reports ----------

@admin_bp.route('/reports')
@admin_required
def reports():
    """Enhanced reports page."""
    report_type = request.args.get('report', 'daily')

    data = {}

    if report_type == 'daily':
        today = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
        txns = Transaction.query.filter(Transaction.created_at >= today).all()
        data['title'] = f'Daily Report — {today.strftime("%d %b %Y")}'
        data['total_transactions'] = len(txns)
        data['total_amount'] = sum(t.amount for t in txns)
        data['successful'] = len([t for t in txns if t.status == 'success'])
        data['failed'] = len([t for t in txns if t.status == 'failed'])
        data['transactions'] = txns

    elif report_type == 'monthly':
        now = datetime.utcnow()
        month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        txns = Transaction.query.filter(Transaction.created_at >= month_start).all()
        data['title'] = f'Monthly Report — {now.strftime("%B %Y")}'
        data['total_transactions'] = len(txns)
        data['total_amount'] = sum(t.amount for t in txns)
        data['successful'] = len([t for t in txns if t.status == 'success'])
        data['failed'] = len([t for t in txns if t.status == 'failed'])
        data['transactions'] = txns

    elif report_type == 'transfer_volume':
        txns = Transaction.query.filter(Transaction.transaction_type.in_(['debit', 'transfer'])).all()
        data['title'] = 'Transfer Volume Report'
        data['total_transfers'] = len(txns)
        data['total_volume'] = sum(t.amount for t in txns)
        data['transactions'] = txns[:50]

    elif report_type == 'frozen':
        frozen = Account.query.filter_by(status='frozen').all()
        data['title'] = 'Frozen Accounts Report'
        data['accounts'] = frozen
        data['total'] = len(frozen)

    elif report_type == 'inactive':
        # Accounts with no transactions in last 30 days
        thirty_days_ago = datetime.utcnow() - timedelta(days=30)
        all_accounts = Account.query.filter_by(status='active').all()
        inactive = []
        for acc in all_accounts:
            last_txn = Transaction.query.filter(
                (Transaction.sender_account == acc.account_number) |
                (Transaction.receiver_account == acc.account_number)
            ).order_by(Transaction.created_at.desc()).first()
            if not last_txn or last_txn.created_at < thirty_days_ago:
                inactive.append(acc)
        data['title'] = 'Inactive Accounts Report (No activity in 30 days)'
        data['accounts'] = inactive
        data['total'] = len(inactive)

    elif report_type == 'top_customers':
        # Top customers by transaction volume
        accounts = Account.query.filter_by(status='active').all()
        customer_volumes = []
        for acc in accounts:
            total = db.session.query(db.func.sum(Transaction.amount)).filter(
                (Transaction.sender_account == acc.account_number) |
                (Transaction.receiver_account == acc.account_number),
                Transaction.status == 'success'
            ).scalar() or 0
            customer_volumes.append({'account': acc, 'volume': total})
        customer_volumes.sort(key=lambda x: x['volume'], reverse=True)
        data['title'] = 'Top Customers by Transaction Volume'
        data['customers'] = customer_volumes[:20]

    return render_template('admin/reports.html', report_type=report_type, data=data)


# ---------- Audit Logs ----------

@admin_bp.route('/audit-logs')
@admin_required
def audit_logs():
    """View audit logs with filters."""
    log_type = request.args.get('type', '').strip()
    page = request.args.get('page', 1, type=int)

    query = Log.query

    if log_type:
        query = query.filter(Log.action == log_type)

    pagination = query.order_by(Log.timestamp.desc()).paginate(page=page, per_page=20, error_out=False)

    # Enrich logs with user info
    for log_entry in pagination.items:
        if log_entry.user_id:
            log_entry.user_info = User.query.get(log_entry.user_id)
        else:
            log_entry.user_info = None

    return render_template('admin/audit_logs.html',
                           logs=pagination.items, pagination=pagination, log_type=log_type)


# ---------- TCP Monitor ----------

@admin_bp.route('/tcp-monitor')
@admin_required
def tcp_monitor():
    """TCP server monitoring dashboard."""
    tcp_stats = tcp_request('server_stats', {})

    if tcp_stats.get('status') == 'success':
        uptime = tcp_stats.get('uptime_seconds', 0)
        hours = uptime // 3600
        minutes = (uptime % 3600) // 60
        seconds = uptime % 60
        tcp_stats['uptime_formatted'] = f'{hours}h {minutes}m {seconds}s'
    else:
        tcp_stats = {
            'is_running': False,
            'uptime_formatted': 'N/A',
            'total_connections': 0,
            'active_connections': 0,
            'total_requests': 0,
            'successful_transactions': 0,
            'failed_transactions': 0,
        }

    return render_template('admin/tcp_monitor.html', stats=tcp_stats)


# ---------- Settings ----------

@admin_bp.route('/settings', methods=['GET', 'POST'])
@admin_required
def settings():
    """Admin settings — update profile."""
    if request.method == 'POST':
        current_user.name = request.form.get('name', current_user.name).strip()
        current_user.phone = request.form.get('phone', current_user.phone).strip()
        db.session.commit()
        flash('Settings updated.', 'success')
        return redirect(url_for('admin.settings'))

    return render_template('admin/settings.html')


# ---------- Send Announcement ----------

@admin_bp.route('/announce', methods=['POST'])
@admin_required
def send_announcement():
    """Send announcement to all customers."""
    title = request.form.get('title', '').strip()
    message = request.form.get('message', '').strip()

    if not title or not message:
        flash('Title and message are required.', 'error')
        return redirect(url_for('admin.dashboard'))

    customers = User.query.filter_by(role='customer').all()
    for customer in customers:
        notif = Notification(
            user_id=customer.id,
            title=title,
            message=message,
            type='announcement',
        )
        db.session.add(notif)

    log = Log(user_id=current_user.id, action='admin_action',
              details=f'Sent announcement: {title}',
              ip_address=request.remote_addr or '127.0.0.1')
    db.session.add(log)
    db.session.commit()

    flash(f'Announcement sent to {len(customers)} customers.', 'success')
    return redirect(url_for('admin.dashboard'))
