import mysql.connector
from datetime import datetime

# === DB CONNECTION ===
def get_connection():
    return mysql.connector.connect(
        host="localhost",
        user="your_mysql_user",       
        password="your_mysql_password", 
        database="your_database"         
    )

# === TABLE CREATION ===
def create_tables():
    conn = get_connection()
    cursor = conn.cursor()

    # ✅ Create users table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            email VARCHAR(255) PRIMARY KEY,
            username VARCHAR(100)
        )
    ''')

    # ✅ Create messages table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS messages (
            session_id VARCHAR(100),
            email VARCHAR(255),
            sender VARCHAR(10),
            text TEXT,
            created_at DATETIME,
            FOREIGN KEY (email) REFERENCES users(email)
        )
    ''')

    conn.commit()
    conn.close()

# === REGISTER USER ===
def register_user(username, email):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM users WHERE email = %s", (email,))
    if not cursor.fetchone():
        cursor.execute("INSERT INTO users (email, username) VALUES (%s, %s)", (email, username))
    conn.commit()
    conn.close()

# === STORE MESSAGE ===
def store_message(session_id, email, sender, text):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO messages (session_id, email, sender, text, created_at)
        VALUES (%s, %s, %s, %s, %s)
    ''', (session_id, email, sender, text, datetime.utcnow()))
    conn.commit()
    conn.close()

# === LOAD USER SESSIONS ===
def get_user_sessions(email):
    conn = get_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute('''
        SELECT session_id, sender, text, created_at FROM messages
        WHERE email = %s
        ORDER BY created_at ASC
    ''', (email,))
    rows = cursor.fetchall()
    conn.close()

    # Group messages by session ID
    sessions = {}
    for row in rows:
        sid = row['session_id']
        if sid not in sessions:
            sessions[sid] = []
        sessions[sid].append({
            "sender": row['sender'],
            "text": row['text'],
            "created_at": row['created_at'].isoformat()
        })
    return sessions