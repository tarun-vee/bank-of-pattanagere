"""
TCP Server for Bank of Pattanagere — Digital Banking Network
Handles: login, balance, transfer, mini_statement, history, server_stats
Protocol: JSON over TCP (newline-delimited)
"""

import socket
import threading
import json
import sys
import os
import time
from datetime import datetime

# Add parent directory to path for model imports
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config import Config
from models import db, User, Account, Transaction, Log
from werkzeug.security import check_password_hash
from flask import Flask

# ---------- TCP Server Stats ----------
server_stats = {
    'start_time': None,
    'total_connections': 0,
    'active_connections': 0,
    'total_requests': 0,
    'successful_transactions': 0,
    'failed_transactions': 0,
    'is_running': False,
}
stats_lock = threading.Lock()


def create_flask_context():
    """Create a minimal Flask app for database access."""
    app = Flask(__name__)
    app.config.from_object(Config)
    os.makedirs(os.path.join(Config.BASE_DIR, 'database'), exist_ok=True)
    db.init_app(app)
    return app


def get_next_transaction_id(app):
    """Generate the next sequential transaction ID."""
    with app.app_context():
        last_txn = Transaction.query.order_by(Transaction.id.desc()).first()
        if last_txn and last_txn.transaction_id.startswith('TXN'):
            try:
                num = int(last_txn.transaction_id[3:]) + 1
            except ValueError:
                num = 1001
        else:
            num = 1001
        return f'TXN{num}'


def handle_login(data, app):
    """Handle login authentication request."""
    email = data.get('email', '')
    password = data.get('password', '')

    with app.app_context():
        user = User.query.filter_by(email=email).first()
        if not user:
            return {'status': 'error', 'message': 'Invalid email or password'}

        if user.locked_until and user.locked_until > datetime.utcnow():
            return {'status': 'error', 'message': 'Account locked. Try again later.'}

        if not check_password_hash(user.password_hash, password):
            user.failed_login_attempts += 1
            if user.failed_login_attempts >= Config.MAX_LOGIN_ATTEMPTS:
                from datetime import timedelta
                user.locked_until = datetime.utcnow() + timedelta(minutes=Config.LOCKOUT_DURATION)
                db.session.commit()
                return {'status': 'error', 'message': 'Account locked due to too many failed attempts.'}
            db.session.commit()
            return {'status': 'error', 'message': 'Invalid email or password'}

        # Successful login
        user.failed_login_attempts = 0
        user.locked_until = None
        db.session.commit()

        return {
            'status': 'success',
            'user_id': user.id,
            'name': user.name,
            'role': user.role,
        }


def handle_balance(data, app):
    """Handle balance inquiry request."""
    account_number = data.get('account', '')

    with app.app_context():
        account = Account.query.filter_by(account_number=account_number).first()
        if not account:
            return {'status': 'error', 'message': 'Account not found'}

        return {
            'status': 'success',
            'account_number': account.account_number,
            'account_type': account.account_type,
            'balance': account.balance,
            'account_status': account.status,
            'holder_name': account.user.name,
        }


def handle_transfer(data, app):
    """Handle fund transfer request."""
    from_account = data.get('from', '')
    to_account = data.get('to', '')
    amount = data.get('amount', 0)
    remarks = data.get('remarks', 'Fund Transfer')

    try:
        amount = float(amount)
    except (ValueError, TypeError):
        return {'status': 'error', 'message': 'Invalid amount'}

    if amount <= 0:
        return {'status': 'error', 'message': 'Amount must be positive'}

    with app.app_context():
        sender = Account.query.filter_by(account_number=from_account).first()
        receiver = Account.query.filter_by(account_number=to_account).first()

        if not sender:
            return {'status': 'error', 'message': 'Sender account not found'}
        if not receiver:
            return {'status': 'error', 'message': 'Receiver account not found'}
        if sender.status == 'frozen':
            return {'status': 'error', 'message': 'Your account is frozen. Cannot initiate transfers.'}
        if sender.status == 'pending':
            return {'status': 'error', 'message': 'Your account is pending approval. Cannot initiate transfers.'}
        if sender.status == 'closed':
            return {'status': 'error', 'message': 'Your account is closed.'}
        if from_account == to_account:
            return {'status': 'error', 'message': 'Cannot transfer to the same account'}

        # Ensure atomicity and prevent race conditions using atomic UPDATE
        updated_rows = db.session.query(Account).filter(
            Account.account_number == from_account,
            Account.balance >= amount
        ).update({
            Account.balance: Account.balance - amount
        }, synchronize_session=False)

        if updated_rows == 0:
            db.session.rollback()
            return {'status': 'error', 'message': 'Insufficient balance or transfer collision.'}

        # Credit the receiver
        db.session.query(Account).filter(
            Account.account_number == to_account
        ).update({
            Account.balance: Account.balance + amount
        }, synchronize_session=False)
        
        # We need to refresh objects if we plan to access their updated states
        db.session.refresh(sender)
        db.session.refresh(receiver)

        txn_id = get_next_transaction_id(app)

        # Debit transaction for sender
        txn_debit = Transaction(
            transaction_id=txn_id,
            sender_account=from_account,
            receiver_account='',
            transaction_type='debit',
            amount=amount,
            description=f'{remarks} - To {to_account} ({receiver.user.name})',
            status='success',
        )

        # Credit transaction for receiver
        txn_credit_id = get_next_transaction_id(app)
        # We need to save the debit first to get the next ID
        db.session.add(txn_debit)
        db.session.flush()

        txn_credit_id_num = int(txn_id[3:]) + 1
        txn_credit = Transaction(
            transaction_id=f'TXN{txn_credit_id_num}',
            sender_account='',
            receiver_account=to_account,
            transaction_type='credit',
            amount=amount,
            description=f'{remarks} - From {from_account} ({sender.user.name})',
            status='success',
        )
        db.session.add(txn_credit)

        # Log the transfer
        log = Log(
            user_id=sender.user.id,
            action='transfer',
            details=f'Transfer of ₹{amount:.2f} from {from_account} to {to_account}',
            ip_address='TCP',
        )
        db.session.add(log)
        db.session.commit()

        with stats_lock:
            server_stats['successful_transactions'] += 1

        return {
            'status': 'success',
            'transaction_id': txn_id,
            'amount': amount,
            'sender_balance': sender.balance,
            'receiver_name': receiver.user.name,
            'receiver_account': to_account,
            'timestamp': datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S'),
        }


def handle_mini_statement(data, app):
    """Handle mini statement request — last 10 transactions."""
    account_number = data.get('account', '')

    with app.app_context():
        account = Account.query.filter_by(account_number=account_number).first()
        if not account:
            return {'status': 'error', 'message': 'Account not found'}

        transactions = Transaction.query.filter(
            (Transaction.sender_account == account_number) |
            (Transaction.receiver_account == account_number)
        ).order_by(Transaction.created_at.desc()).limit(10).all()

        txn_list = []
        for txn in transactions:
            txn_list.append({
                'transaction_id': txn.transaction_id,
                'type': txn.transaction_type,
                'description': txn.description,
                'amount': txn.amount,
                'date': txn.created_at.strftime('%d %b %Y, %I:%M %p'),
                'status': txn.status,
            })

        return {
            'status': 'success',
            'account_number': account_number,
            'holder_name': account.user.name,
            'balance': account.balance,
            'transactions': txn_list,
        }


def handle_history(data, app):
    """Handle full transaction history with pagination."""
    account_number = data.get('account', '')
    page = data.get('page', 1)
    per_page = data.get('per_page', 20)

    with app.app_context():
        account = Account.query.filter_by(account_number=account_number).first()
        if not account:
            return {'status': 'error', 'message': 'Account not found'}

        query = Transaction.query.filter(
            (Transaction.sender_account == account_number) |
            (Transaction.receiver_account == account_number)
        ).order_by(Transaction.created_at.desc())

        total = query.count()
        transactions = query.offset((page - 1) * per_page).limit(per_page).all()

        txn_list = []
        for txn in transactions:
            txn_list.append({
                'transaction_id': txn.transaction_id,
                'type': txn.transaction_type,
                'description': txn.description,
                'amount': txn.amount,
                'date': txn.created_at.strftime('%d %b %Y, %I:%M %p'),
                'status': txn.status,
            })

        return {
            'status': 'success',
            'account_number': account_number,
            'transactions': txn_list,
            'total': total,
            'page': page,
            'per_page': per_page,
            'total_pages': (total + per_page - 1) // per_page,
        }


def handle_server_stats(data, app):
    """Return TCP server statistics."""
    with stats_lock:
        uptime = 0
        if server_stats['start_time']:
            uptime = int(time.time() - server_stats['start_time'])

        return {
            'status': 'success',
            'is_running': server_stats['is_running'],
            'uptime_seconds': uptime,
            'total_connections': server_stats['total_connections'],
            'active_connections': server_stats['active_connections'],
            'total_requests': server_stats['total_requests'],
            'successful_transactions': server_stats['successful_transactions'],
            'failed_transactions': server_stats['failed_transactions'],
        }


# ---------- Action Dispatcher ----------
ACTION_HANDLERS = {
    'login': handle_login,
    'balance': handle_balance,
    'transfer': handle_transfer,
    'mini_statement': handle_mini_statement,
    'history': handle_history,
    'server_stats': handle_server_stats,
}


def handle_client(client_socket, address, app):
    """Handle a single client connection."""
    with stats_lock:
        server_stats['total_connections'] += 1
        server_stats['active_connections'] += 1

    print(f"[TCP] Client connected: {address}")

    try:
        data = b''
        while True:
            chunk = client_socket.recv(4096)
            if not chunk:
                break
            data += chunk
            if b'\n' in data:
                break

        if data:
            try:
                request = json.loads(data.decode('utf-8').strip())
                action = request.get('action', '')

                with stats_lock:
                    server_stats['total_requests'] += 1

                handler = ACTION_HANDLERS.get(action)
                if handler:
                    response = handler(request, app)
                else:
                    response = {'status': 'error', 'message': f'Unknown action: {action}'}
                    with stats_lock:
                        server_stats['failed_transactions'] += 1

            except json.JSONDecodeError:
                response = {'status': 'error', 'message': 'Invalid JSON format'}
                with stats_lock:
                    server_stats['failed_transactions'] += 1

            response_data = json.dumps(response) + '\n'
            client_socket.sendall(response_data.encode('utf-8'))

    except Exception as e:
        print(f"[TCP] Error handling client {address}: {e}")
        with stats_lock:
            server_stats['failed_transactions'] += 1
        try:
            error_response = json.dumps({'status': 'error', 'message': 'Internal server error'}) + '\n'
            client_socket.sendall(error_response.encode('utf-8'))
        except Exception:
            pass
    finally:
        client_socket.close()
        with stats_lock:
            server_stats['active_connections'] -= 1
        print(f"[TCP] Client disconnected: {address}")


def start_tcp_server(host=None, port=None):
    """Start the TCP server."""
    host = host or Config.TCP_HOST
    port = port or Config.TCP_PORT

    app = create_flask_context()

    server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server_socket.bind((host, port))
    server_socket.listen(5)

    with stats_lock:
        server_stats['start_time'] = time.time()
        server_stats['is_running'] = True

    print(f"[TCP] Bank of Pattanagere TCP Server started on {host}:{port}")
    print(f"[TCP] Waiting for connections...")

    try:
        while True:
            client_socket, address = server_socket.accept()
            client_thread = threading.Thread(
                target=handle_client,
                args=(client_socket, address, app),
                daemon=True,
            )
            client_thread.start()
    except KeyboardInterrupt:
        print("\n[TCP] Server shutting down...")
    finally:
        with stats_lock:
            server_stats['is_running'] = False
        server_socket.close()
        print("[TCP] Server stopped.")


if __name__ == '__main__':
    start_tcp_server()
