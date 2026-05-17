import os
import sqlite3
from typing import Set

# На Fly.io храним в /data (persistent volume), локально — рядом с ботом
DB_FILE = os.path.join(os.getenv('DATA_DIR', '.'), 'processed_news.db')

def init_db():
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS processed (
            id TEXT PRIMARY KEY
        )
    ''')
    conn.commit()
    conn.close()

def is_processed(news_id: str) -> bool:
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT 1 FROM processed WHERE id=?", (news_id,))
    result = cursor.fetchone()
    conn.close()
    return result is not None

def mark_processed(news_id: str):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    try:
        cursor.execute("INSERT INTO processed (id) VALUES (?)", (news_id,))
        conn.commit()
    except sqlite3.IntegrityError:
        pass  # Already marked
    finally:
        conn.close()
