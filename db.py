import mysql.connector
from datetime import datetime

# 🔧 Update with your MySQL DB credentials
DB_CONFIG = {
    "host": "localhost",
    "user": "root",
    "password": "accenture",
    "database": "your_database"
}

def get_connection():
    return mysql.connector.connect(**DB_CONFIG)

#  Create required tables if not exist
def create_tables():
    conn = get_connection()
    cursor = conn.cursor()

    # users table (email = primary key)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            email VARCHAR(255) PRIMARY KEY,
            username VARCHAR(100) NOT NULL
        )
    """)

    #  messages table (no ID column)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS messages (
            session_id VARCHAR(100),
            email VARCHAR(255),
            sender VARCHAR(10),
            text TEXT,
            created_at DATETIME,
            FOREIGN KEY (email) REFERENCES users(email)
        )
    """)

    conn.commit()
    conn.close()

#  Add or check user

def register_user(username: str, email: str):
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT * FROM users WHERE email = %s", (email,))
    user = cursor.fetchone()

    if not user:
        cursor.execute("INSERT INTO users (email, username) VALUES (%s, %s)", (email, username))
        conn.commit()

    conn.close()

#  Store single message (bot/user)

def store_message(session_id: str, email: str, sender: str, text: str):
    conn = get_connection()
    cursor = conn.cursor()
    now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")

    cursor.execute("""
        INSERT INTO messages (session_id, email, sender, text, created_at)
        VALUES (%s, %s, %s, %s, %s)
    """, (session_id, email, sender, text, now))

    conn.commit()
    conn.close()

#  Get all sessions and messages for a user

def get_user_sessions(email: str):
    conn = get_connection()
    cursor = conn.cursor(dictionary=True)

    cursor.execute("SELECT * FROM messages WHERE email = %s ORDER BY created_at", (email,))
    rows = cursor.fetchall()
    conn.close()

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