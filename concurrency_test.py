import socket
import json
import threading
import os
import sys

# Add parent directory to path to import app modules
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config import Config
from models import db, User, Account
from flask import Flask
from tcp_server import start_tcp_server

HOST = '127.0.0.1'
PORT = 9998  # Use different port to avoid conflicts with running app

def create_test_app():
    app = Flask(__name__)
    # Override config for testing
    app.config.from_object(Config)
    app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///' + os.path.join(Config.BASE_DIR, 'database', 'test_bank.db')
    app.config['TCP_PORT'] = PORT
    db.init_app(app)
    return app

def setup_test_db(app):
    with app.app_context():
        db.create_all()
        
        # Create users
        u1 = User(name='User A', email='a@test.com', phone='111', password_hash='hash')
        u2 = User(name='User B', email='b@test.com', phone='222', password_hash='hash')
        db.session.add(u1)
        db.session.add(u2)
        db.session.commit()
        
        # Create accounts
        a1 = Account(user_id=u1.id, account_number='ACC_A', balance=10000.0, status='active')
        a2 = Account(user_id=u2.id, account_number='ACC_B', balance=5000.0, status='active')
        db.session.add(a1)
        db.session.add(a2)
        db.session.commit()
        
        return 'ACC_A', 'ACC_B'

def reset_balance(app, acc):
    with app.app_context():
        a = Account.query.filter_by(account_number=acc).first()
        a.balance = 10000.0
        db.session.commit()

def get_balance(app, acc):
    with app.app_context():
        a = Account.query.filter_by(account_number=acc).first()
        return a.balance

def send_transfer(from_acc, to_acc, amount, results, index, barrier):
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.connect((HOST, PORT))
        
        payload = {
            'action': 'transfer',
            'from': from_acc,
            'to': to_acc,
            'amount': amount,
            'remarks': f'Concurrency Test {index}'
        }
        
        # Wait for all threads to be ready to fire simultaneously
        barrier.wait()
        
        s.sendall((json.dumps(payload) + '\n').encode('utf-8'))
        
        data = b''
        while b'\n' not in data:
            chunk = s.recv(4096)
            if not chunk:
                break
            data += chunk
            
        s.close()
        response = json.loads(data.decode('utf-8').strip())
        results[index] = response
    except Exception as e:
        results[index] = {'status': 'error', 'message': str(e)}

def run_concurrency_test(app, num_threads, sender, receiver, amount):
    print(f"\n--- Testing {num_threads} simultaneous transfers of Rs. {amount} ---")
    
    reset_balance(app, sender)
    initial_balance = get_balance(app, sender)
    print(f"Initial Sender Balance: Rs. {initial_balance:.2f}")
    
    threads = []
    results = [None] * num_threads
    barrier = threading.Barrier(num_threads)
    
    for i in range(num_threads):
        t = threading.Thread(target=send_transfer, args=(sender, receiver, amount, results, i, barrier))
        threads.append(t)
        t.start()
        
    for t in threads:
        t.join()
        
    success_count = 0
    for i, res in enumerate(results):
        status = res.get('status')
        msg = res.get('message', 'Success')
        print(f"Request {i+1}: {status.upper()} - {msg}")
        if status == 'success':
            success_count += 1
            
    final_balance = get_balance(app, sender)
    print(f"Final Sender Balance: Rs. {final_balance:.2f}")
    print(f"Result: {success_count} succeeded, {num_threads - success_count} failed.")
    
    if final_balance >= 0:
        print("PASS: Account balance did not become negative.")
    else:
        print("FAIL: Account balance became negative!")
        
    if success_count == 1:
        print("PASS: Only one transfer succeeded, preventing double spend.")
    else:
        print("FAIL: Multiple transfers succeeded, double spend occurred!")

if __name__ == '__main__':
    app = create_test_app()
    
    # Cleanup old test DB if exists
    test_db_path = os.path.join(Config.BASE_DIR, 'database', 'test_bank.db')
    if os.path.exists(test_db_path):
        os.remove(test_db_path)
        
    sender, receiver = setup_test_db(app)
    
    # Start TCP Server in background (we patch create_flask_context in tcp_server to use our test app)
    import tcp_server
    original_create_context = tcp_server.create_flask_context
    tcp_server.create_flask_context = lambda: app
    
    tcp_thread = threading.Thread(target=start_tcp_server, args=(HOST, PORT), daemon=True)
    tcp_thread.start()
    
    import time
    time.sleep(1) # wait for server to bind
    
    print("=== Concurrency Protection Test ===")
    
    # Test 1: 2 simultaneous transfers
    run_concurrency_test(app, 2, sender, receiver, 10000.0)
    
    # Test 2: 5 simultaneous transfers
    run_concurrency_test(app, 5, sender, receiver, 10000.0)
    
    # Test 3: Multiple concurrent clients (20 requests)
    run_concurrency_test(app, 20, sender, receiver, 10000.0)
    
    print("\nTest completed successfully.")
    
    # Cleanup
    if os.path.exists(test_db_path):
        os.remove(test_db_path)
