import random
from datetime import datetime, timedelta
from werkzeug.security import generate_password_hash
from models import db, User, Account, Transaction, Log, Notification

# Realistic Data Arrays
FIRST_NAMES = ['Rahul', 'Neha', 'Amit', 'Priya', 'Ravi', 'Anjali', 'Vikram', 'Pooja', 'Suresh', 'Kavita',
               'Karan', 'Sneha', 'Manish', 'Ritu', 'Arun', 'Simran', 'Sanjay', 'Divya', 'Deepak', 'Nisha',
               'Rajesh', 'Geeta', 'Anil', 'Sonia', 'Sunil', 'Rekha', 'Prakash', 'Meena', 'Ajay', 'Aarti',
               'Vijay', 'Swati', 'Manoj', 'Jyoti', 'Rakesh', 'Preeti', 'Dinesh', 'Kiran', 'Ramesh', 'Shweta',
               'Ashok', 'Seema', 'Harish', 'Komal', 'Gaurav', 'Rachna', 'Tarun', 'Anu', 'Nitin', 'Madhu',
               'Vikas', 'Payal', 'Rohit', 'Richa', 'Sachin', 'Shruti', 'Saurabh', 'Neha', 'Sumit', 'Sakshi']

LAST_NAMES = ['Sharma', 'Singh', 'Verma', 'Patel', 'Kumar', 'Gupta', 'Yadav', 'Joshi', 'Mishra', 'Chauhan',
              'Reddy', 'Rao', 'Nair', 'Menon', 'Pillai', 'Iyer', 'Das', 'Bose', 'Chatterjee', 'Sen',
              'Bhat', 'Shetty', 'Hegde', 'Kamat', 'Prabhu', 'Deshmukh', 'Kadam', 'Pawar', 'More', 'Jadhav',
              'Garg', 'Bansal', 'Agarwal', 'Goyal', 'Jain', 'Mehta', 'Shah', 'Desai', 'Parikh', 'Kapoor',
              'Malhotra', 'Ahuja', 'Chopra', 'Sethi', 'Suri', 'Ahluwalia', 'Sinha', 'Thakur', 'Rajput', 'Tiwari']

ADDRESS_AREAS = ['MG Road', 'JP Nagar', 'Koramangala', 'Whitefield', 'Indiranagar', 'Jayanagar', 'HSR Layout',
                 'Malleswaram', 'Rajajinagar', 'Basavanagudi', 'BTM Layout', 'Banashankari', 'Yelahanka',
                 'Electronic City', 'Marathahalli', 'Bellandur', 'Hebbal', 'RT Nagar', 'Sahakar Nagar', 'Kalyan Nagar']

CITIES = ['Bangalore', 'Mysore', 'Hubli', 'Mangalore', 'Belgaum']

DESCRIPTIONS = {
    'credit': ['Salary Credit', 'Freelance Payment', 'Interest Credited', 'Refund Received', 'Dividend Received', 'Bonus Credit'],
    'debit': ['ATM Withdrawal', 'Grocery Shopping', 'Electricity Bill', 'Water Bill', 'Internet Bill', 'Fuel Payment', 'Online Purchase', 'Insurance Premium', 'DTH Recharge', 'Mobile Postpaid Bill', 'Dining at Restaurant', 'Movie Tickets'],
    'transfer': ['UPI Transfer', 'Rent Payment', 'Loan EMI', 'Transfer to Friend', 'Payment to Vendor']
}

def generate_random_date(start_date, end_date):
    time_between_dates = end_date - start_date
    days_between_dates = time_between_dates.days
    random_number_of_days = random.randrange(days_between_dates)
    random_time = timedelta(hours=random.randint(0, 23), minutes=random.randint(0, 59))
    return start_date + timedelta(days=random_number_of_days) + random_time

def seed_database():
    """Seed the database with realistic customers and transactions."""
    if User.query.first() is not None:
        print("[Seed] Database already seeded. Skipping.")
        return

    print("[Seed] Seeding database... This may take a few seconds.")

    # --- Default Admin ---
    admin = User(
        name='Admin',
        email='admin@bankofpattanagere.com',
        phone='9876543210',
        password_hash=generate_password_hash('admin123'),
        role='admin',
        address='Bank of Pattanagere, Head Office',
        security_question='What is the name of your bank?',
        security_answer_hash=generate_password_hash('pattanagere'),
        transaction_pin_hash=None,
        has_default_pin=False,
    )
    db.session.add(admin)
    db.session.flush()

    # --- Generate Customers ---
    print("[Seed] Generating 300 customers...")
    customers = []
    
    for i in range(300):
        first_name = random.choice(FIRST_NAMES)
        last_name = random.choice(LAST_NAMES)
        name = f"{first_name} {last_name}"
        email = f"{first_name.lower()}.{last_name.lower()}{i}@example.com"
        phone = f"9{random.randint(100000000, 999999999)}"
        address = f"{random.randint(1, 999)}, {random.choice(ADDRESS_AREAS)}, {random.choice(CITIES)}"
        
        user = User(
            name=name,
            email=email,
            phone=phone,
            password_hash=generate_password_hash(f"{first_name}@123"),
            role='customer',
            address=address,
            security_question='What is your pet name?',
            security_answer_hash=generate_password_hash('pet'),
            transaction_pin_hash=generate_password_hash('1234'),
            has_default_pin=True,
        )
        db.session.add(user)
        db.session.flush()

        # Determine Account Status
        rand_stat = random.random()
        if rand_stat < 0.85:
            status = 'active'
        elif rand_stat < 0.95:
            status = 'pending'
        else:
            status = 'frozen'

        # Determine Account Type and Balance
        account_type = random.choices(['savings', 'current'], weights=[0.8, 0.2])[0]
        if account_type == 'savings':
            balance = round(random.uniform(10000, 500000), 2)
        else:
            balance = round(random.uniform(50000, 2000000), 2)

        account = Account(
            user_id=user.id,
            account_number=f"temp_{user.id}", # Temporary, will update after flush
            account_type=account_type,
            balance=balance,
            status=status,
            created_at=datetime.utcnow() - timedelta(days=random.randint(30, 365))
        )
        db.session.add(account)
        db.session.flush()
        
        # Set structured account number
        account.account_number = f"101560001{account.id:06d}"
        
        customers.append((user, account))

    db.session.commit()
    print("[Seed] 300 customers generated.")

    # --- Generate Transactions ---
    print("[Seed] Generating 18,000 - 24,000 transactions...")
    transactions_to_insert = []
    end_date = datetime.utcnow()
    start_date = end_date - timedelta(days=365)

    all_account_numbers = [acc.account_number for _, acc in customers]
    txn_counter = 100000

    for user, account in customers:
        num_txns = random.randint(60, 80)
        
        for _ in range(num_txns):
            txn_date = generate_random_date(start_date, end_date)
            txn_type = random.choices(['credit', 'debit', 'transfer'], weights=[0.3, 0.5, 0.2])[0]
            
            # Determine Transaction Amount (Mix of regular and high value)
            amount_category = random.choices(['small', 'medium', 'large'], weights=[0.6, 0.35, 0.05])[0]
            if amount_category == 'small':
                amount = round(random.uniform(100, 2000), 2)
            elif amount_category == 'medium':
                amount = round(random.uniform(2000, 15000), 2)
            else:
                # High value transaction
                amount = round(random.choice([50000, 100000, 150000, 250000]) + random.uniform(0, 1000), 2)
            
            txn_id = f"TXN{txn_counter}"
            txn_counter += 1
            
            sender_account = ''
            receiver_account = ''
            description = random.choice(DESCRIPTIONS[txn_type])
            
            if txn_type == 'credit':
                receiver_account = account.account_number
            elif txn_type == 'debit':
                sender_account = account.account_number
            elif txn_type == 'transfer':
                sender_account = account.account_number
                receiver_account = random.choice(all_account_numbers)
                # Ensure not transferring to self
                while receiver_account == account.account_number:
                    receiver_account = random.choice(all_account_numbers)
            
            status = 'success'
            if random.random() < 0.02: # 2% failure rate
                status = 'failed'

            txn = Transaction(
                transaction_id=txn_id,
                sender_account=sender_account,
                receiver_account=receiver_account,
                transaction_type=txn_type,
                amount=amount,
                description=description,
                status=status,
                created_at=txn_date
            )
            transactions_to_insert.append(txn)

    # Bulk insert transactions
    db.session.bulk_save_objects(transactions_to_insert)
    db.session.commit()
    print(f"[Seed] {len(transactions_to_insert)} transactions generated and saved.")

    # --- Sample Notifications ---
    notifs = []
    for user, account in customers[:10]:
        notifs.append(Notification(
            user_id=user.id,
            title='Welcome to Bank of Pattanagere',
            message='Thank you for opening an account with us.',
            type='info',
            is_read=False,
            created_at=account.created_at
        ))
    db.session.bulk_save_objects(notifs)
    
    # --- Sample Log ---
    log = Log(
        user_id=admin.id,
        action='system',
        details='Initial database seed completed with 300 users and realistic transactions.',
        ip_address='127.0.0.1',
    )
    db.session.add(log)

    db.session.commit()
    print("[Seed] Database seeded successfully!")
    print(f"  Admin: admin@bankofpattanagere.com / admin123")
    print(f"  Demo Customer 1: {customers[0][0].email} / {customers[0][0].name.split()[0]}@123")
    print(f"  Demo Customer 2: {customers[1][0].email} / {customers[1][0].name.split()[0]}@123")
