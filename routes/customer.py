"""
Customer portal routes — Dashboard, Balance, Transfer, Statements, Beneficiaries, Notifications, Profile.
"""

import csv
import io
import io
from datetime import datetime, timedelta
from functools import wraps
from flask import Blueprint, render_template, redirect, url_for, flash, request, Response, make_response
from flask_login import login_required, current_user
from models import db, Account, Transaction, Beneficiary, Notification, Log
from tcp_client import tcp_request

customer_bp = Blueprint('customer', __name__, url_prefix='/customer')


def customer_required(f):
    """Decorator to ensure user is a customer."""
    @wraps(f)
    @login_required
    def decorated_function(*args, **kwargs):
        if current_user.role != 'customer':
            flash('Access denied. Customer accounts only.', 'error')
            return redirect(url_for('auth.login'))
        return f(*args, **kwargs)
    return decorated_function


def get_next_transaction_id():
    """Generate the next sequential transaction ID."""
    last_txn = Transaction.query.order_by(Transaction.id.desc()).first()
    if last_txn and last_txn.transaction_id.startswith('TXN'):
        try:
            num = int(last_txn.transaction_id[3:]) + 1
        except ValueError:
            num = 1001
    else:
        num = 1001
    return f'TXN{num}'


# ---------- Dashboard ----------

@customer_bp.route('/dashboard')
@customer_required
def dashboard():
    """Customer dashboard with account info, quick actions, and recent transactions."""
    account = current_user.account

    # Calculate metrics
    total_credits = 0.0
    total_debits = 0.0
    last_transaction = None

    if account:
        # Total credits
        credit_txns = Transaction.query.filter(
            Transaction.receiver_account == account.account_number,
            Transaction.transaction_type == 'credit',
            Transaction.status == 'success'
        ).all()
        total_credits = sum(t.amount for t in credit_txns)

        # Total debits
        debit_txns = Transaction.query.filter(
            Transaction.sender_account == account.account_number,
            Transaction.transaction_type == 'debit',
            Transaction.status == 'success'
        ).all()
        total_debits = sum(t.amount for t in debit_txns)

        # Last transaction
        last_transaction = Transaction.query.filter(
            (Transaction.sender_account == account.account_number) |
            (Transaction.receiver_account == account.account_number)
        ).order_by(Transaction.created_at.desc()).first()

        # Recent transactions (last 5)
        recent_transactions = Transaction.query.filter(
            (Transaction.sender_account == account.account_number) |
            (Transaction.receiver_account == account.account_number)
        ).order_by(Transaction.created_at.desc()).limit(5).all()
    else:
        recent_transactions = []

    return render_template('customer/dashboard.html',
                           account=account,
                           total_credits=total_credits,
                           total_debits=total_debits,
                           last_transaction=last_transaction,
                           recent_transactions=recent_transactions)


# ---------- Accounts ----------

@customer_bp.route('/accounts')
@customer_required
def accounts():
    """View account details."""
    account = current_user.account
    return render_template('customer/accounts.html', account=account)


# ---------- Balance ----------

@customer_bp.route('/balance')
@customer_required
def balance():
    """Real-time balance inquiry via TCP."""
    account = current_user.account
    tcp_response = None
    if account:
        tcp_response = tcp_request('balance', {'account': account.account_number})
    return render_template('customer/balance.html', account=account, tcp_response=tcp_response)


# ---------- Transfer ----------

@customer_bp.route('/transfer', methods=['GET', 'POST'])
@customer_required
def transfer():
    """Fund transfer page."""
    account = current_user.account

    if not account:
        flash('No account found.', 'error')
        return redirect(url_for('customer.dashboard'))

    if account.status == 'pending':
        flash('Your account is pending approval. You cannot initiate transfers.', 'warning')
        return redirect(url_for('customer.dashboard'))

    if account.status == 'frozen':
        flash('Your account is frozen. You cannot initiate transfers.', 'warning')
        return redirect(url_for('customer.dashboard'))

    beneficiaries = Beneficiary.query.filter_by(user_id=current_user.id).all()

    if request.method == 'POST':
        to_account = request.form.get('to_account', '').strip()
        beneficiary_name = request.form.get('beneficiary_name', '').strip()
        amount = request.form.get('amount', '0').strip()
        remarks = request.form.get('remarks', 'Fund Transfer').strip()
        transaction_pin = request.form.get('transaction_pin', '').strip()

        # Check PIN lockout
        if current_user.pin_locked_until and current_user.pin_locked_until > datetime.utcnow():
            remaining = (current_user.pin_locked_until - datetime.utcnow()).seconds // 60
            flash(f'Transfers temporarily disabled due to too many incorrect PIN attempts. Try again in {remaining + 1} minutes.', 'error')
            return redirect(url_for('customer.transfer'))

        try:
            amount = float(amount)
        except ValueError:
            flash('Invalid amount.', 'error')
            return render_template('customer/transfer.html', account=account, beneficiaries=beneficiaries)

        if amount <= 0:
            flash('Amount must be positive.', 'error')
            return render_template('customer/transfer.html', account=account, beneficiaries=beneficiaries)

        if account.account_number == to_account:
            flash('Cannot transfer to the same account.', 'error')
            return render_template('customer/transfer.html', account=account, beneficiaries=beneficiaries)

        # Validate receiver
        receiver = Account.query.filter_by(account_number=to_account).first()
        if not receiver:
            flash('Receiver account not found.', 'error')
            return render_template('customer/transfer.html', account=account, beneficiaries=beneficiaries)

        if account.balance < amount:
            flash('Insufficient balance.', 'error')
            return render_template('customer/transfer.html', account=account, beneficiaries=beneficiaries)

        # PIN Verification
        from werkzeug.security import check_password_hash
        if not current_user.transaction_pin_hash or not check_password_hash(current_user.transaction_pin_hash, transaction_pin):
            current_user.failed_pin_attempts += 1
            if current_user.failed_pin_attempts >= 3:
                current_user.pin_locked_until = datetime.utcnow() + timedelta(minutes=15)
                # Log lockout
                log = Log(user_id=current_user.id, action='security', details='Account locked due to 3 failed PIN attempts', ip_address=request.remote_addr or '127.0.0.1')
                db.session.add(log)
                db.session.commit()
                flash('Too many incorrect PIN attempts. Transfers are temporarily disabled for 15 minutes.', 'error')
            else:
                log = Log(user_id=current_user.id, action='security', details='Failed PIN verification during transfer', ip_address=request.remote_addr or '127.0.0.1')
                db.session.add(log)
                db.session.commit()
                remaining = 3 - current_user.failed_pin_attempts
                flash(f'Incorrect Transaction PIN. {remaining} attempts remaining.', 'error')
            return redirect(url_for('customer.transfer'))
            
        # Successful PIN verification
        current_user.failed_pin_attempts = 0
        log_success = Log(user_id=current_user.id, action='security', details='Successful PIN verification for transfer', ip_address=request.remote_addr or '127.0.0.1')
        db.session.add(log_success)
        db.session.commit()

        # Process transfer via TCP
        tcp_response = tcp_request('transfer', {
            'from': account.account_number,
            'to': to_account,
            'amount': amount,
            'remarks': remarks,
        })

        if tcp_response.get('status') == 'success':
            # Refresh account balance
            db.session.refresh(account)

            # Notify sender
            notif_sender = Notification(
                user_id=current_user.id,
                title='Transfer Successful',
                message=f'You have successfully transferred ₹{amount:,.2f} to account {to_account} ({beneficiary_name or receiver.user.name}).',
                type='transfer',
            )
            db.session.add(notif_sender)

            # Notify receiver
            notif_receiver = Notification(
                user_id=receiver.user.id,
                title='Amount Received',
                message=f'You have received ₹{amount:,.2f} from {current_user.name} (Account: {account.account_number}).',
                type='transfer',
            )
            db.session.add(notif_receiver)
            db.session.commit()

            return redirect(url_for('customer.transfer_success', transaction_id=tcp_response.get('transaction_id', '')))
        else:
            flash(tcp_response.get('message', 'Transfer failed.'), 'error')

    return render_template('customer/transfer.html', account=account, beneficiaries=beneficiaries)


# ---------- Transfer Success ----------

@customer_bp.route('/transfer/success/<transaction_id>')
@customer_required
def transfer_success(transaction_id):
    """View dedicated transfer success screen."""
    txn = Transaction.query.filter_by(transaction_id=transaction_id).first()
    if not txn:
        flash('Transaction not found.', 'error')
        return redirect(url_for('customer.dashboard'))

    account = current_user.account
    if account and (txn.sender_account != account.account_number):
        flash('Access denied.', 'error')
        return redirect(url_for('customer.dashboard'))

    receiver_name = ''
    receiver_acc = Account.query.filter_by(account_number=txn.receiver_account).first()
    if receiver_acc:
        receiver_name = receiver_acc.user.name
        
    reference_number = f"REF-{txn.id:08d}"

    return render_template('customer/transfer_success.html', txn=txn, receiver_name=receiver_name, reference_number=reference_number)


# ---------- Receipt ----------

@customer_bp.route('/receipt/<transaction_id>')
@customer_required
def receipt(transaction_id):
    """View transaction receipt."""
    txn = Transaction.query.filter_by(transaction_id=transaction_id).first()
    if not txn:
        flash('Transaction not found.', 'error')
        return redirect(url_for('customer.dashboard'))

    # Verify this transaction belongs to the current user
    account = current_user.account
    if account and (txn.sender_account != account.account_number and txn.receiver_account != account.account_number):
        flash('Access denied.', 'error')
        return redirect(url_for('customer.dashboard'))

    sender_name = ''
    receiver_name = ''
    sender_acc = Account.query.filter_by(account_number=txn.sender_account).first()
    receiver_acc = Account.query.filter_by(account_number=txn.receiver_account).first()
    if sender_acc:
        sender_name = sender_acc.user.name
    if receiver_acc:
        receiver_name = receiver_acc.user.name
        
    reference_number = f"REF-{txn.id:08d}"

    return render_template('customer/receipt.html', txn=txn,
                           sender_name=sender_name, receiver_name=receiver_name, reference_number=reference_number)


@customer_bp.route('/receipt/<transaction_id>/pdf')
@customer_required
def receipt_pdf(transaction_id):
    """Download transaction receipt as PDF."""
    from reportlab.lib.pagesizes import A4
    from reportlab.lib import colors
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import inch

    txn = Transaction.query.filter_by(transaction_id=transaction_id).first()
    if not txn:
        flash('Transaction not found.', 'error')
        return redirect(url_for('customer.dashboard'))

    account = current_user.account
    if account and (txn.sender_account != account.account_number and txn.receiver_account != account.account_number):
        flash('Access denied.', 'error')
        return redirect(url_for('customer.dashboard'))

    sender_name = ''
    receiver_name = ''
    sender_acc = Account.query.filter_by(account_number=txn.sender_account).first()
    receiver_acc = Account.query.filter_by(account_number=txn.receiver_account).first()
    if sender_acc:
        sender_name = sender_acc.user.name
    if receiver_acc:
        receiver_name = receiver_acc.user.name

    # Build PDF
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4, topMargin=0.5 * inch)
    styles = getSampleStyleSheet()
    elements = []

    # Bank title
    title_style = ParagraphStyle('BankTitle', parent=styles['Title'],
                                 textColor=colors.HexColor('#2563EB'), fontSize=20)
    elements.append(Paragraph('Bank of Pattanagere', title_style))
    elements.append(Paragraph('Digital Banking Network', styles['Normal']))
    elements.append(Spacer(1, 20))
    elements.append(Paragraph('Transaction Receipt', styles['Heading2']))
    elements.append(Spacer(1, 15))
    
    reference_number = f"REF-{txn.id:08d}"

    # Transaction details table
    data = [
        ['Reference Number', reference_number],
        ['Transaction ID', txn.transaction_id],
        ['Date & Time', txn.created_at.strftime('%d %b %Y, %I:%M %p')],
        ['Type', txn.transaction_type.capitalize()],
        ['Sender Account', txn.sender_account or 'N/A'],
        ['Sender Name', sender_name or 'N/A'],
        ['Receiver Account', txn.receiver_account or 'N/A'],
        ['Receiver Name', receiver_name or 'N/A'],
        ['Amount', f'₹ {txn.amount:,.2f}'],
        ['Description', txn.description],
        ['Status', txn.status.capitalize()],
    ]

    table = Table(data, colWidths=[2.5 * inch, 4 * inch])
    table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (0, -1), colors.HexColor('#DBEAFE')),
        ('TEXTCOLOR', (0, 0), (0, -1), colors.HexColor('#1E40AF')),
        ('FONTNAME', (0, 0), (0, -1), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, -1), 10),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#E5E7EB')),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('TOPPADDING', (0, 0), (-1, -1), 8),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 8),
        ('LEFTPADDING', (0, 0), (-1, -1), 10),
    ]))
    elements.append(table)
    elements.append(Spacer(1, 30))
    elements.append(Paragraph('This is a computer-generated receipt and does not require a signature.',
                              ParagraphStyle('Disclaimer', parent=styles['Normal'],
                                             textColor=colors.grey, fontSize=8)))

    doc.build(elements)
    buffer.seek(0)

    response = make_response(buffer.read())
    response.headers['Content-Type'] = 'application/pdf'
    response.headers['Content-Disposition'] = f'attachment; filename=receipt_{transaction_id}.pdf'
    return response


# ---------- Mini Statement ----------

@customer_bp.route('/mini-statement')
@customer_required
def mini_statement():
    """Display last 10 transactions."""
    account = current_user.account
    transactions = []

    if account:
        transactions = Transaction.query.filter(
            (Transaction.sender_account == account.account_number) |
            (Transaction.receiver_account == account.account_number)
        ).order_by(Transaction.created_at.desc()).limit(10).all()

    return render_template('customer/mini_statement.html', account=account, transactions=transactions)


@customer_bp.route('/download-statement/<fmt>')
@customer_required
def download_statement(fmt):
    """Download statement as PDF or CSV."""
    account = current_user.account
    if not account:
        flash('No account found.', 'error')
        return redirect(url_for('customer.dashboard'))

    transactions = Transaction.query.filter(
        (Transaction.sender_account == account.account_number) |
        (Transaction.receiver_account == account.account_number)
    ).order_by(Transaction.created_at.desc()).limit(10).all()

    if fmt == 'csv':
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(['Date', 'Transaction ID', 'Type', 'Description', 'Amount (₹)', 'Status'])
        for txn in transactions:
            prefix = '-' if txn.transaction_type == 'debit' else '+'
            writer.writerow([
                txn.created_at.strftime('%d %b %Y, %I:%M %p'),
                txn.transaction_id,
                txn.transaction_type.capitalize(),
                txn.description,
                f'{prefix} {txn.amount:,.2f}',
                txn.status.capitalize(),
            ])
        response = Response(output.getvalue(), mimetype='text/csv')
        response.headers['Content-Disposition'] = f'attachment; filename=mini_statement_{account.account_number}.csv'
        return response

    elif fmt == 'pdf':
        from reportlab.lib.pagesizes import A4
        from reportlab.lib import colors
        from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
        from reportlab.lib.units import inch

        buffer = io.BytesIO()
        doc = SimpleDocTemplate(buffer, pagesize=A4, topMargin=0.5 * inch)
        styles = getSampleStyleSheet()
        elements = []

        title_style = ParagraphStyle('BankTitle', parent=styles['Title'],
                                     textColor=colors.HexColor('#2563EB'), fontSize=18)
        elements.append(Paragraph('Bank of Pattanagere', title_style))
        elements.append(Paragraph('Digital Banking Network', styles['Normal']))
        elements.append(Spacer(1, 10))
        elements.append(Paragraph(f'Mini Statement — Account: {account.account_number}', styles['Heading2']))
        elements.append(Paragraph(f'Account Holder: {current_user.name}', styles['Normal']))
        elements.append(Paragraph(f'Generated: {datetime.utcnow().strftime("%d %b %Y, %I:%M %p")}', styles['Normal']))
        elements.append(Spacer(1, 15))

        data = [['Date', 'ID', 'Type', 'Description', 'Amount (₹)', 'Status']]
        for txn in transactions:
            prefix = '-' if txn.transaction_type == 'debit' else '+'
            data.append([
                txn.created_at.strftime('%d %b %Y'),
                txn.transaction_id,
                txn.transaction_type.capitalize(),
                txn.description[:30],
                f'{prefix} {txn.amount:,.2f}',
                txn.status.capitalize(),
            ])

        table = Table(data, colWidths=[1 * inch, 0.8 * inch, 0.7 * inch, 1.8 * inch, 1 * inch, 0.8 * inch])
        table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#2563EB')),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, -1), 8),
            ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#E5E7EB')),
            ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#F9FAFB')]),
            ('TOPPADDING', (0, 0), (-1, -1), 6),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
        ]))
        elements.append(table)
        elements.append(Spacer(1, 20))
        elements.append(Paragraph(f'Current Balance: ₹ {account.balance:,.2f}',
                                  ParagraphStyle('Balance', parent=styles['Normal'],
                                                 textColor=colors.HexColor('#2563EB'),
                                                 fontSize=12, fontName='Helvetica-Bold')))

        doc.build(elements)
        buffer.seek(0)

        response = make_response(buffer.read())
        response.headers['Content-Type'] = 'application/pdf'
        response.headers['Content-Disposition'] = f'attachment; filename=mini_statement_{account.account_number}.pdf'
        return response

    flash('Invalid format.', 'error')
    return redirect(url_for('customer.mini_statement'))


# ---------- Transaction History ----------

@customer_bp.route('/transactions')
@customer_required
def transaction_history():
    """Full transaction history with search, filter, sort, pagination."""
    account = current_user.account
    if not account:
        return render_template('customer/transaction_history.html', account=None, transactions=[], pagination=None)

    page = request.args.get('page', 1, type=int)
    per_page = 10
    search = request.args.get('search', '').strip()
    txn_type = request.args.get('type', '').strip()
    status = request.args.get('status', '').strip()
    sort_by = request.args.get('sort', 'date_desc')

    query = Transaction.query.filter(
        (Transaction.sender_account == account.account_number) |
        (Transaction.receiver_account == account.account_number)
    )

    if search:
        query = query.filter(
            (Transaction.transaction_id.ilike(f'%{search}%')) |
            (Transaction.description.ilike(f'%{search}%'))
        )

    if txn_type:
        query = query.filter(Transaction.transaction_type == txn_type)

    if status:
        query = query.filter(Transaction.status == status)

    if sort_by == 'date_asc':
        query = query.order_by(Transaction.created_at.asc())
    elif sort_by == 'amount_desc':
        query = query.order_by(Transaction.amount.desc())
    elif sort_by == 'amount_asc':
        query = query.order_by(Transaction.amount.asc())
    else:
        query = query.order_by(Transaction.created_at.desc())

    pagination = query.paginate(page=page, per_page=per_page, error_out=False)

    return render_template('customer/transaction_history.html',
                           account=account,
                           transactions=pagination.items,
                           pagination=pagination,
                           search=search, txn_type=txn_type, status=status, sort_by=sort_by)


# ---------- Beneficiaries ----------

@customer_bp.route('/beneficiaries')
@customer_required
def beneficiaries():
    """View saved beneficiaries."""
    bens = Beneficiary.query.filter_by(user_id=current_user.id).all()
    return render_template('customer/beneficiaries.html', beneficiaries=bens)


@customer_bp.route('/beneficiaries/add', methods=['POST'])
@customer_required
def add_beneficiary():
    """Add a new beneficiary."""
    ben_account = request.form.get('beneficiary_account', '').strip()
    ben_name = request.form.get('beneficiary_name', '').strip()

    if not ben_account or not ben_name:
        flash('Account number and name are required.', 'error')
        return redirect(url_for('customer.beneficiaries'))

    # Validate account exists
    account = Account.query.filter_by(account_number=ben_account).first()
    if not account:
        flash('Beneficiary account not found in our bank.', 'error')
        return redirect(url_for('customer.beneficiaries'))

    # Check not self
    if current_user.account and ben_account == current_user.account.account_number:
        flash('Cannot add your own account as a beneficiary.', 'error')
        return redirect(url_for('customer.beneficiaries'))

    # Check duplicate
    existing = Beneficiary.query.filter_by(user_id=current_user.id, beneficiary_account=ben_account).first()
    if existing:
        flash('This beneficiary already exists.', 'info')
        return redirect(url_for('customer.beneficiaries'))

    ben = Beneficiary(
        user_id=current_user.id,
        beneficiary_account=ben_account,
        beneficiary_name=ben_name,
    )
    db.session.add(ben)
    db.session.commit()

    flash(f'Beneficiary {ben_name} added successfully.', 'success')
    return redirect(url_for('customer.beneficiaries'))


@customer_bp.route('/beneficiaries/delete/<int:ben_id>', methods=['POST'])
@customer_required
def delete_beneficiary(ben_id):
    """Delete a beneficiary."""
    ben = Beneficiary.query.get_or_404(ben_id)
    if ben.user_id != current_user.id:
        flash('Access denied.', 'error')
        return redirect(url_for('customer.beneficiaries'))

    db.session.delete(ben)
    db.session.commit()
    flash('Beneficiary removed.', 'success')
    return redirect(url_for('customer.beneficiaries'))


# ---------- Notifications ----------

@customer_bp.route('/notifications')
@customer_required
def notifications():
    """View notifications."""
    notifs = Notification.query.filter_by(user_id=current_user.id).order_by(
        Notification.created_at.desc()).all()

    # Mark all as read
    unread = Notification.query.filter_by(user_id=current_user.id, is_read=False).all()
    for n in unread:
        n.is_read = True
    db.session.commit()

    return render_template('customer/notifications.html', notifications=notifs)


# ---------- Profile ----------

@customer_bp.route('/profile')
@customer_required
def profile():
    """View profile."""
    return render_template('customer/profile.html')


@customer_bp.route('/profile/edit', methods=['GET', 'POST'])
@customer_required
def edit_profile():
    """Edit profile."""
    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        phone = request.form.get('phone', '').strip()
        address = request.form.get('address', '').strip()

        if not name or not phone:
            flash('Name and phone are required.', 'error')
            return render_template('customer/edit_profile.html')

        current_user.name = name
        current_user.phone = phone
        current_user.address = address
        db.session.commit()

        flash('Profile updated successfully.', 'success')
        return redirect(url_for('customer.profile'))

    return render_template('customer/edit_profile.html')

# ---------- Security Settings ----------

@customer_bp.route('/security', methods=['GET', 'POST'])
@customer_required
def security():
    """Security settings to change password or transaction PIN."""
    from werkzeug.security import check_password_hash, generate_password_hash
    from routes.auth import validate_password_strength

    if request.method == 'POST':
        action = request.form.get('action')

        if action == 'change_password':
            current_password = request.form.get('current_password', '')
            new_password = request.form.get('new_password', '')
            confirm_password = request.form.get('confirm_password', '')

            if not check_password_hash(current_user.password_hash, current_password):
                flash('Current password is incorrect.', 'error')
                return redirect(url_for('customer.security'))

            if new_password != confirm_password:
                flash('New passwords do not match.', 'error')
                return redirect(url_for('customer.security'))

            is_valid, msg = validate_password_strength(new_password)
            if not is_valid:
                flash(msg, 'error')
                return redirect(url_for('customer.security'))

            current_user.password_hash = generate_password_hash(new_password)
            db.session.commit()

            notif = Notification(user_id=current_user.id, title='Password Changed', message='Your password has been changed successfully.', type='security')
            log = Log(user_id=current_user.id, action='security', details='Password changed', ip_address=request.remote_addr or '127.0.0.1')
            db.session.add(notif)
            db.session.add(log)
            db.session.commit()

            flash('Password changed successfully.', 'success')
            return redirect(url_for('customer.security'))

        elif action == 'change_pin':
            current_pin = request.form.get('current_pin', '')
            new_pin = request.form.get('new_pin', '')
            confirm_pin = request.form.get('confirm_pin', '')
            
            if not current_user.transaction_pin_hash or not check_password_hash(current_user.transaction_pin_hash, current_pin):
                flash('Current Transaction PIN is incorrect.', 'error')
                return redirect(url_for('customer.security'))

            if new_pin != confirm_pin:
                flash('New Transaction PINs do not match.', 'error')
                return redirect(url_for('customer.security'))

            import re
            if not re.match(r'^\d{4}$', new_pin):
                flash('Transaction PIN must be exactly 4 digits.', 'error')
                return redirect(url_for('customer.security'))

            current_user.transaction_pin_hash = generate_password_hash(new_pin)
            current_user.has_default_pin = False
            current_user.failed_pin_attempts = 0
            current_user.pin_locked_until = None
            db.session.commit()

            notif = Notification(user_id=current_user.id, title='Transaction PIN Changed', message='Your Transaction PIN has been changed successfully.', type='security')
            log = Log(user_id=current_user.id, action='security', details='Transaction PIN changed', ip_address=request.remote_addr or '127.0.0.1')
            db.session.add(notif)
            db.session.add(log)
            db.session.commit()

            flash('Transaction PIN changed successfully.', 'success')
            return redirect(url_for('customer.security'))

    return render_template('customer/security.html')

@customer_bp.route('/security/reset-pin', methods=['GET', 'POST'])
@customer_required
def reset_pin():
    """Reset Transaction PIN using login password."""
    from werkzeug.security import check_password_hash, generate_password_hash
    
    if request.method == 'POST':
        password = request.form.get('password', '')
        new_pin = request.form.get('new_pin', '')
        confirm_pin = request.form.get('confirm_pin', '')
        
        if not check_password_hash(current_user.password_hash, password):
            flash('Login password is incorrect.', 'error')
            return redirect(url_for('customer.reset_pin'))
            
        if new_pin != confirm_pin:
            flash('New Transaction PINs do not match.', 'error')
            return redirect(url_for('customer.reset_pin'))
            
        import re
        if not re.match(r'^\d{4}$', new_pin):
            flash('Transaction PIN must be exactly 4 digits.', 'error')
            return redirect(url_for('customer.reset_pin'))
            
        current_user.transaction_pin_hash = generate_password_hash(new_pin)
        current_user.has_default_pin = False
        current_user.failed_pin_attempts = 0
        current_user.pin_locked_until = None
        db.session.commit()
        
        notif = Notification(user_id=current_user.id, title='Transaction PIN Reset', message='Your Transaction PIN has been successfully reset.', type='security')
        log = Log(user_id=current_user.id, action='security', details='Transaction PIN reset via password', ip_address=request.remote_addr or '127.0.0.1')
        db.session.add(notif)
        db.session.add(log)
        db.session.commit()
        
        flash('Transaction PIN reset successfully.', 'success')
        return redirect(url_for('customer.security'))
        
    return render_template('customer/reset_pin.html')
